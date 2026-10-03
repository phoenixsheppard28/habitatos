import re
from collections.abc import Iterable

import psycopg
from psycopg.types.json import Jsonb
from shapely import wkt

from habitat.catalog.footprint import area_share
from habitat.contracts import DatasetMatch, DatasetVersion, SearchFilters, Tag, TagOrigin


class MemoryCatalog:
    """In-process catalog for tests and notebooks. PostgresCatalog is the shared one."""

    def __init__(self):
        self.records: list[DatasetVersion] = []

    def register_dataset(self, descriptor: DatasetVersion) -> None:
        key = (descriptor.dataset_id, descriptor.version)
        if any((r.dataset_id, r.version) == key for r in self.records):
            raise ValueError(f"dataset version {key} already exists; published versions are immutable")

        self.records.append(descriptor.model_copy(deep=True))

    def latest(self, dataset_id: str) -> DatasetVersion | None:
        versions = [r for r in self.records if r.dataset_id == dataset_id]
        return max(versions, key=lambda r: r.version).model_copy(deep=True) if versions else None

    def search_datasets(self, filters: SearchFilters) -> list[DatasetMatch]:
        latest: dict[str, DatasetVersion] = {}
        for record in self.records:
            if record.dataset_id not in latest or record.version > latest[record.dataset_id].version:
                latest[record.dataset_id] = record
        return rank_matches((record.model_copy(deep=True) for record in latest.values()), filters)


class PostgresCatalog:
    """Catalog in the `datasets` and `dataset_tags` tables. SQL narrows the candidates; Python scores them."""

    def __init__(self, connection: psycopg.Connection):
        self.connection = connection

    def register_dataset(self, descriptor: DatasetVersion) -> None:
        coverage = descriptor.coverage
        try:
            with self.connection.transaction():
                self.connection.execute(
                    """
                    INSERT INTO datasets (dataset_id, version, created_at, access_scope, family, source_id, status,
                                          description, summary, footprint, time_range, variables, species_keys,
                                          descriptor)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                            ST_Multi(ST_GeomFromText(%s, 4326)), tstzrange(%s, %s, '[]'), %s, %s, %s)
                    """,
                    (
                        descriptor.dataset_id, descriptor.version, descriptor.created_at, descriptor.access_scope,
                        descriptor.family, descriptor.source_id, descriptor.status, descriptor.description,
                        descriptor.summary, descriptor.footprint_wkt, coverage.start, coverage.end,
                        descriptor.variables, [taxon.gbif_key for taxon in descriptor.species],
                        Jsonb(descriptor.model_dump(mode="json")),
                    ),
                )
                with self.connection.cursor() as cursor:
                    cursor.executemany(
                        """
                        INSERT INTO dataset_tags (dataset_id, version, key, value, origin, model, confidence, evidence)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        [
                            (descriptor.dataset_id, descriptor.version, t.key, t.value, t.origin.value, t.model,
                             t.confidence, t.evidence)
                            for t in descriptor.tags
                        ],
                    )
        except psycopg.errors.UniqueViolation as error:
            key = (descriptor.dataset_id, descriptor.version)
            raise ValueError(f"dataset version {key} already exists; published versions are immutable") from error

    def latest(self, dataset_id: str) -> DatasetVersion | None:
        row = self.connection.execute(
            "SELECT descriptor FROM latest_datasets WHERE dataset_id = %s", (dataset_id,)
        ).fetchone()
        return DatasetVersion.model_validate(row[0]) if row else None

    def search_datasets(self, filters: SearchFilters) -> list[DatasetMatch]:
        rows = self.connection.execute(
            """
            SELECT descriptor FROM latest_datasets
            WHERE access_scope = ANY(%(scopes)s)
              AND status = %(status)s
              AND (%(families)s::text[] IS NULL OR family = ANY(%(families)s))
              AND (%(variables)s::text[] IS NULL OR variables && %(variables)s)
              AND (%(species)s::bigint[] IS NULL OR species_keys && %(species)s::bigint[])
              AND (%(region)s::text IS NULL OR footprint IS NULL
                   OR ST_Intersects(footprint, ST_GeomFromText(%(region)s, 4326)))
              AND ((%(start)s::timestamptz IS NULL AND %(end)s::timestamptz IS NULL) OR time_range IS NULL
                   OR time_range && tstzrange(%(start)s, %(end)s, '[]'))
            """,
            {
                "scopes": filters.access_scope,
                "status": filters.status,
                "families": filters.family or None,
                "variables": filters.variables or None,
                "species": [taxon.gbif_key for taxon in filters.species] if filters.species else None,
                "region": filters.region_wkt,
                "start": filters.start,
                "end": filters.end,
            },
        ).fetchall()
        return rank_matches((DatasetVersion.model_validate(row[0]) for row in rows), filters)


def rank_matches(datasets: Iterable[DatasetVersion], filters: SearchFilters) -> list[DatasetMatch]:
    matches = [match for dataset in datasets if (match := match_dataset(dataset, filters)) is not None]
    return sorted(matches, key=lambda m: (m.score, len(m.matched_tags)), reverse=True)


def match_dataset(dataset: DatasetVersion, filters: SearchFilters) -> DatasetMatch | None:
    if dataset.access_scope not in filters.access_scope or dataset.status != filters.status:
        return None

    if filters.family and dataset.family not in filters.family:
        return None

    if filters.variables and not set(filters.variables) & set(dataset.variables):
        return None

    if filters.species:
        wanted = {taxon.gbif_key for taxon in filters.species}
        if not wanted & {taxon.gbif_key for taxon in dataset.species}:
            return None

    spatial_overlap = spatial_share(dataset, filters)
    if spatial_overlap == 0.0:
        return None

    temporal_overlap = temporal_share(dataset, filters)
    if temporal_overlap == 0.0:
        return None

    usable_tags = [t for t in dataset.tags if filters.include_ai_tags or t.origin is not TagOrigin.AI]
    if filters.tags_all and not all(has_tag(usable_tags, wanted) for wanted in filters.tags_all):
        return None

    if filters.tags_any and not any(has_tag(usable_tags, wanted) for wanted in filters.tags_any):
        return None

    wanted_tags = (filters.tags_all or []) + (filters.tags_any or [])
    matched_tags = [t for t in usable_tags if any(t.matches(wanted) for wanted in wanted_tags)]

    if filters.text and not text_matches(dataset, filters.text):
        return None

    return DatasetMatch(
        dataset=dataset,
        spatial_overlap=spatial_overlap,
        temporal_overlap=temporal_overlap,
        matched_tags=matched_tags,
    )


def spatial_share(dataset: DatasetVersion, filters: SearchFilters) -> float | None:
    """None means unknown coverage. Search keeps the dataset but does not count it as covered."""
    if filters.region_wkt is None:
        return 1.0

    if dataset.footprint_wkt is None:
        return None

    return area_share(wkt.loads(dataset.footprint_wkt), wkt.loads(filters.region_wkt))


def temporal_share(dataset: DatasetVersion, filters: SearchFilters) -> float | None:
    if filters.start is None and filters.end is None:
        return 1.0

    if dataset.coverage.start is None or dataset.coverage.end is None:
        return None

    if filters.end is None:
        return float(dataset.coverage.end >= filters.start)
    if filters.start is None:
        return float(dataset.coverage.start <= filters.end)

    overlap = min(dataset.coverage.end, filters.end) - max(dataset.coverage.start, filters.start)
    requested = filters.end - filters.start
    if requested.total_seconds() <= 0:
        return 1.0 if dataset.coverage.start <= filters.start <= dataset.coverage.end else 0.0

    return max(overlap.total_seconds(), 0.0) / requested.total_seconds()


def has_tag(tags: list[Tag], wanted: Tag) -> bool:
    return any(tag.matches(wanted) for tag in tags)


def text_matches(dataset: DatasetVersion, text: str) -> bool:
    haystack = f"{dataset.description} {dataset.summary or ''}".lower()
    return all(word in haystack for word in re.findall(r"\w+", text.lower()))

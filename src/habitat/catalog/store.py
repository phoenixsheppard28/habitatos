import json
import re
from pathlib import Path

from shapely import wkt

from habitat.catalog.footprint import area_share
from habitat.contracts import DatasetMatch, DatasetVersion, SearchFilters, Tag, TagOrigin


class LocalCatalog:
    """File-backed catalog for development. `migrations/001_catalog.sql` is the PostgreSQL form of the same data."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def register_dataset(self, descriptor: DatasetVersion) -> None:
        records = self.load()
        key = (descriptor.dataset_id, descriptor.version)
        if any((r.dataset_id, r.version) == key for r in records):
            raise ValueError(f"dataset version {key} already exists; published versions are immutable")

        records.append(descriptor)
        self.save(records)

    def latest(self, dataset_id: str) -> DatasetVersion | None:
        versions = [r for r in self.load() if r.dataset_id == dataset_id]
        return max(versions, key=lambda r: r.version) if versions else None

    def search_datasets(self, filters: SearchFilters) -> list[DatasetMatch]:
        matches = [
            match
            for dataset in self.latest_versions()
            if (match := match_dataset(dataset, filters)) is not None
        ]
        return sorted(matches, key=lambda m: (m.score, len(m.matched_tags)), reverse=True)

    def latest_versions(self) -> list[DatasetVersion]:
        latest: dict[str, DatasetVersion] = {}
        for record in self.load():
            if record.dataset_id not in latest or record.version > latest[record.dataset_id].version:
                latest[record.dataset_id] = record
        return list(latest.values())

    def load(self) -> list[DatasetVersion]:
        if not self.path.exists():
            return []

        return [DatasetVersion.model_validate(item) for item in json.loads(self.path.read_text())]

    def save(self, records: list[DatasetVersion]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps([r.model_dump(mode="json") for r in records], indent=2))
        temporary.rename(self.path)


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
    if filters.start is None or filters.end is None:
        return 1.0

    if dataset.coverage.start is None or dataset.coverage.end is None:
        return None

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

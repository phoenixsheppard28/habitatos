from collections.abc import Iterable
from datetime import UTC, datetime

from shapely import wkt
from shapely.geometry.base import BaseGeometry

from habitat.catalog.ai import CatalogAssistant
from habitat.catalog.footprint import footprint_from_cells, grows_materially
from habitat.catalog.store import MemoryCatalog, PostgresCatalog
from habitat.catalog.tags import deterministic_tags, merge_tags
from habitat.contracts import Coverage, DatasetVersion, StorageRef, TagOrigin, TaxonRef
from habitat.grid import Grid
from habitat.normalize.rows import ANIMAL_LOCATIONS
from habitat.storage.series import SeriesStore

SAMPLE_ROW_COUNT = 20

ROW_GRAIN = {
    "cell_observations": "one row per cell, variable and acquisition",
    ANIMAL_LOCATIONS: "one row per animal fix",
}


def publish_series_version(
    store: SeriesStore,
    catalog: MemoryCatalog | PostgresCatalog,
    grid: Grid,
    series_id: str,
    source_id: str,
    description: str,
    access_scope: str,
    assistant: CatalogAssistant | None = None,
    region_layers: dict[str, Iterable[tuple[str, BaseGeometry]]] | None = None,
) -> DatasetVersion | None:
    """Register the latest series version in the catalog, with coverage and tags. Returns None when it is already registered."""
    version = store.latest_version(series_id)
    previous = catalog.latest(series_id)
    if version is None or (previous is not None and previous.version >= version.version):
        return None

    summary = store.summary(version)
    footprint = footprint_from_cells(grid, summary.cell_ids)

    descriptor = DatasetVersion(
        dataset_id=series_id,
        version=version.version,
        created_at=datetime.now(UTC),
        access_scope=access_scope,
        family=version.family,
        source_id=source_id,
        description=description,
        status="ready",
        storage=StorageRef(
            uri=f"postgres://{version.family}?series_id={series_id}&version={version.version}", format="postgres"
        ),
        row_grain=ROW_GRAIN[version.family],
        row_count=summary.row_count,
        raw_artifact_refs=[batch.source_item_id for batch in version.batches],
        mapping_version=",".join(sorted({batch.mapping_version for batch in version.batches})),
        coverage=Coverage(
            bbox=footprint.bounds if footprint else None,
            start=summary.start,
            end=summary.end,
            species=[name for _, name in summary.taxa],
        ),
        footprint_wkt=footprint.wkt if footprint else None,
        variables=summary.variables,
        species=[TaxonRef(gbif_key=key, name=name) for key, name in summary.taxa],
    )
    computed_tags = deterministic_tags(source_id, summary.variables, summary.start, summary.end, footprint, region_layers)
    descriptor.tags = merge_tags(computed_tags, previous_ai_tags(previous))
    descriptor.summary = previous.summary if previous else None

    if assistant is not None and needs_ai_labels(previous, footprint, summary.start, summary.end):
        sample = store.sample_rows(version, SAMPLE_ROW_COUNT)
        labels = assistant.label_dataset(descriptor, sample)
        descriptor.tags = merge_tags(computed_tags, labels.tags)
        descriptor.summary = labels.summary

    catalog.register_dataset(descriptor)
    return descriptor


def previous_ai_tags(previous: DatasetVersion | None):
    return [tag for tag in previous.tags if tag.origin is TagOrigin.AI] if previous else []


def needs_ai_labels(
    previous: DatasetVersion | None, footprint: BaseGeometry | None, start: datetime | None, end: datetime | None
) -> bool:
    if previous is None or previous.summary is None:
        return True

    previous_footprint = wkt.loads(previous.footprint_wkt) if previous.footprint_wkt else None
    if grows_materially(previous_footprint, footprint):
        return True

    return covers_new_season(previous.coverage.start, previous.coverage.end, start, end)


def covers_new_season(
    previous_start: datetime | None, previous_end: datetime | None, start: datetime | None, end: datetime | None
) -> bool:
    if None in (previous_start, previous_end, start, end):
        return True

    return quarters_between(start, end) - quarters_between(previous_start, previous_end) != set()


def quarters_between(start: datetime, end: datetime) -> set[tuple[int, int]]:
    quarters = set()
    year, quarter = start.year, (start.month - 1) // 3
    while (year, quarter) <= (end.year, (end.month - 1) // 3):
        quarters.add((year, quarter))
        year, quarter = (year + 1, 0) if quarter == 3 else (year, quarter + 1)
    return quarters


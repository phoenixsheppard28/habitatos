"""Shared steps of the point event normalizers. See docs/ingestion/EVENTS.md."""

import pandas as pd
import pyarrow as pa

from habitat.contracts import POINT_EVENTS_SCHEMA, BBox, RawManifest
from habitat.grid import Grid
from habitat.normalize.rows import POINT_EVENTS, NormalizedBatch, series_id
from habitat.normalize.sources.movebank import cell_ids_for_points

EVENT_TYPES = frozenset({
    "species_occurrence",
    "camera_trap_detection",
    "active_fire",
    "wildlife_mortality",
    "disease_outbreak",
    "human_wildlife_conflict",
})
SAMPLING_DESIGNS = frozenset({"presence_only", "systematic", "effort_known"})
OCCURRENCE_STATUSES = frozenset({"present", "absent"})

MAX_COORDINATE_UNCERTAINTY_M = 2000.0
MAX_PRECISE_INTERVAL = pd.Timedelta(days=31)

FLAG_ORDER = [
    "geospatial_issue",
    "date_issue",
    "coordinate_uncertainty_too_large",
    "imprecise_date",
    "captive_record",
    "not_live_observation",
    "taxon_unresolved",
    "low_confidence",
    "non_vegetation_fire",
    "available_at_from_dataset",
]


def quality_flags(rows: pd.DataFrame, source_rules: dict[str, pd.Series]) -> pd.Series:
    """The first matching reason in FLAG_ORDER, else `ok`. The two generic rules apply to every source."""
    rules = {
        **source_rules,
        "coordinate_uncertainty_too_large": pd.to_numeric(rows["coordinate_uncertainty_m"]).gt(
            MAX_COORDINATE_UNCERTAINTY_M
        ),
        "imprecise_date": (
            pd.to_datetime(rows["time_end"], utc=True) - pd.to_datetime(rows["time_start"], utc=True)
        ) > MAX_PRECISE_INTERVAL,
    }
    unknown = set(rules) - set(FLAG_ORDER)
    if unknown:
        raise ValueError(f"unknown quality flag(s) {sorted(unknown)}")

    flags = pd.Series("ok", index=rows.index, dtype=object)
    for reason in reversed(FLAG_ORDER):
        if reason in rules:
            flags[rules[reason].fillna(False).to_numpy(dtype=bool)] = reason
    return flags


def within_bbox(rows: pd.DataFrame, bbox: BBox | None) -> pd.DataFrame:
    if bbox is None:
        return rows

    west, south, east, north = bbox
    return rows[rows["longitude"].between(west, east) & rows["latitude"].between(south, north)]


def to_point_events(rows: pd.DataFrame, manifest: RawManifest, grid: Grid, mapping_version: str) -> NormalizedBatch:
    """Add the provenance columns and the grid cell, then check the vocabularies. An unknown value is a bug."""
    for column, allowed in (
        ("event_type", EVENT_TYPES),
        ("sampling_design", SAMPLING_DESIGNS),
        ("occurrence_status", OCCURRENCE_STATUSES),
    ):
        unknown = set(rows[column]) - allowed
        if unknown:
            raise ValueError(f"{column} value(s) {sorted(unknown)} are not in the vocabulary")

    item = manifest.extensions
    rows = rows.assign(
        dataset_id=series_id(manifest, grid),
        source_id=item.source_id,
        source_item_id=item.source_item_id,
        processing_version=item.processing_version,
        product_status=item.product_status.value,
        mapping_version=mapping_version,
        cell_id=cell_ids_for_points(grid, rows["longitude"].to_numpy(float), rows["latitude"].to_numpy(float)),
    )
    for name in POINT_EVENTS_SCHEMA.names:
        if name not in rows.columns:
            rows[name] = None

    table = pa.Table.from_pandas(
        typed_columns(rows[POINT_EVENTS_SCHEMA.names]), schema=POINT_EVENTS_SCHEMA, preserve_index=False
    )
    return NormalizedBatch(table, mapping_version, family=POINT_EVENTS)


def typed_columns(rows: pd.DataFrame) -> pd.DataFrame:
    """Columns with missing values arrive as object or float columns. Give each the dtype of its schema field."""
    typed = rows.copy()
    for field in POINT_EVENTS_SCHEMA:
        column = typed[field.name]
        if pa.types.is_integer(field.type):
            typed[field.name] = pd.array(pd.to_numeric(column), dtype="Int64")
        elif pa.types.is_floating(field.type):
            typed[field.name] = pd.to_numeric(column).astype(float)
        elif pa.types.is_timestamp(field.type):
            typed[field.name] = pd.to_datetime(column, utc=True)
        else:
            typed[field.name] = column.astype(object).where(column.notna(), None)
    return typed

"""Shared rules of the `population_counts` family: vocabularies, checks, comparability groups and quality flags."""

import json
import math
from typing import Any

import pandas as pd
import pyarrow as pa

from habitat.contracts import COUNT_AREAS_SCHEMA, POPULATION_COUNTS_SCHEMA, RawManifest
from habitat.grid import Grid
from habitat.normalize.rows import COUNT_AREAS, POPULATION_COUNTS, NormalizedBatch, QuarantineError, series_id

METRIC_UNITS = {
    "count": frozenset({"individuals"}),
    "population_estimate": frozenset({"individuals"}),
    "density": frozenset({"individuals_per_km2"}),
    "index": frozenset({"index"}),
    "relative_abundance": frozenset(
        {"detections_per_100_trap_nights", "individuals_per_hour", "individuals_per_km_transect"}
    ),
    "presence": frozenset({"boolean"}),
}
METHODS = frozenset({
    "aerial_total", "aerial_sample", "aerial_photo", "ground_total", "ground_transect", "camera_trap", "point_count",
    "model", "compiled",
})
SAMPLE_OR_MODEL_METHODS = frozenset({"aerial_sample", "ground_transect", "model"})
AREA_TYPES = frozenset({"survey_block", "park", "admin_unit", "ecosystem", "site", "region"})
UNCERTAINTY_COLUMNS = ["se", "ci_low", "ci_high"]


def comparability_group(
    source_id: str, area_id: str, taxon_name: str, metric: str, method: str, unit: str, protocol: str | None = None
) -> str:
    """Two rows are comparable without a model only when they have the same group."""
    parts = [source_id, area_id, taxon_name, metric, method, unit]
    return ":".join(parts + [protocol] if protocol else parts)


def population_batch(
    records: pd.DataFrame, areas: pd.DataFrame, manifest: RawManifest, grid: Grid, mapping_version: str
) -> NormalizedBatch:
    """Check the canonical records of one source item, flag them, and build the family and `count_areas` tables.

    `records` has the row columns of `POPULATION_COUNTS_SCHEMA` that come from the source, plus the booleans
    `source_outlier` and `read_from_figure`, an optional `protocol` and an `attributes` dict per row.
    `areas` has the columns of `COUNT_AREAS_SCHEMA`, with an `attributes` dict per row.
    """
    if records.empty:
        raise QuarantineError("the item has no population rows")

    check_areas(areas)
    check_records(records, set(areas["area_id"]))

    item = manifest.extensions
    rows = records.copy()
    rows["source_id"] = item.source_id
    rows["source_item_id"] = item.source_item_id
    rows["processing_version"] = item.processing_version
    rows["mapping_version"] = mapping_version
    rows["dataset_id"] = series_id(manifest, grid)
    rows["time_precision"] = item.time_precision.value
    rows["available_at"] = pd.Timestamp(item.available_at)
    protocols = rows["protocol"] if "protocol" in rows else pd.Series(None, index=rows.index, dtype=object)
    rows["comparability_group"] = [
        comparability_group(
            item.source_id, row.area_id, row.taxon_name, row.metric, row.method, row.unit,
            None if is_missing(protocol) else protocol,
        )
        for row, protocol in zip(rows.itertuples(), protocols)
    ]
    check_groups(rows)

    located = set(areas.loc[areas["geometry_wkt"].notna(), "area_id"])
    rows["quality_flag"] = quality_flags(rows, located)
    rows["gbif_taxon_key"] = rows["gbif_taxon_key"].astype("Int64")
    rows["attributes"] = rows["attributes"].map(json_text)

    return NormalizedBatch(
        pa.Table.from_pandas(
            rows[POPULATION_COUNTS_SCHEMA.names], schema=POPULATION_COUNTS_SCHEMA, preserve_index=False
        ),
        mapping_version,
        family=POPULATION_COUNTS,
        references={COUNT_AREAS: count_areas_table(areas)},
    )


def check_areas(areas: pd.DataFrame) -> None:
    unknown = sorted(set(areas["area_type"]) - AREA_TYPES)
    if unknown:
        raise QuarantineError(f"unknown area_type {unknown}; allowed: {sorted(AREA_TYPES)}")

    nameless = areas["area_name"].fillna("").str.strip().eq("") & areas["geometry_wkt"].isna()
    if nameless.any():
        raise QuarantineError(f"areas {sorted(areas.loc[nameless, 'area_id'])} have no name and no coordinates")


def check_records(records: pd.DataFrame, area_ids: set[str]) -> None:
    unknown_metrics = sorted(set(records["metric"]) - METRIC_UNITS.keys())
    if unknown_metrics:
        raise QuarantineError(f"unknown metric {unknown_metrics}; allowed: {sorted(METRIC_UNITS)}")

    unknown_methods = sorted(set(records["method"]) - METHODS)
    if unknown_methods:
        raise QuarantineError(f"unknown method {unknown_methods}; allowed: {sorted(METHODS)}")

    for metric, unit in records[["metric", "unit"]].drop_duplicates().itertuples(index=False):
        if unit not in METRIC_UNITS[metric]:
            raise QuarantineError(f"unit {unit!r} is not allowed for metric {metric!r}")

    if (records["value"] < 0).any() or (records["se"] < 0).any():
        raise QuarantineError("a value or a standard error is negative")

    if (records["ci_low"] > records["ci_high"]).any():
        raise QuarantineError("ci_low is greater than ci_high")

    if (records["time_start"] > records["time_end"]).any():
        raise QuarantineError("time_start is after time_end")

    missing_areas = sorted(set(records["area_id"]) - area_ids)
    if missing_areas:
        raise QuarantineError(f"rows refer to no count area: {missing_areas}")

    duplicates = records.loc[records["source_record_id"].duplicated(), "source_record_id"]
    if not duplicates.empty:
        raise QuarantineError(f"source_record_id is not unique: {sorted(set(duplicates))[:5]}")


def check_groups(rows: pd.DataFrame) -> None:
    """A group with two metrics, units or methods mixes values that are not comparable."""
    mixed = rows.groupby("comparability_group")[["metric", "unit", "method"]].nunique()
    mixed = mixed[(mixed > 1).any(axis=1)]
    if not mixed.empty:
        raise QuarantineError(f"comparability groups mix metrics, units or methods: {list(mixed.index)[:5]}")


def quality_flags(rows: pd.DataFrame, located_area_ids: set[str]) -> pd.Series:
    """The first rule that applies gives the flag of a row."""
    rules = [
        ("taxon_unresolved", rows["gbif_taxon_key"].isna()),
        ("area_unlocated", ~rows["area_id"].isin(located_area_ids)),
        ("source_outlier", rows["source_outlier"].astype(bool)),
        ("digitized_from_figure", rows["read_from_figure"].astype(bool)),
        (
            "no_uncertainty",
            rows["method"].isin(SAMPLE_OR_MODEL_METHODS) & rows[UNCERTAINTY_COLUMNS].isna().all(axis=1),
        ),
        ("zero_count", rows["value"] == 0),
        ("interval_unknown", rows["time_start"] == rows["time_end"]),
    ]
    flags = pd.Series("ok", index=rows.index)
    for flag, applies in reversed(rules):
        flags[applies.to_numpy()] = flag
    return flags


def count_areas_table(areas: pd.DataFrame) -> pa.Table:
    table = areas.copy()
    table["attributes"] = table["attributes"].map(json_text)
    table["area_km2"] = table["area_km2"].astype("float64")
    for column in ("valid_from", "valid_to"):
        table[column] = pd.to_datetime(table[column], utc=True)
    return pa.Table.from_pandas(table[COUNT_AREAS_SCHEMA.names], schema=COUNT_AREAS_SCHEMA, preserve_index=False)


def json_text(attributes: dict[str, Any]) -> str:
    """JSON without null values. A missing source value is absent, not null."""
    return json.dumps({key: value for key, value in attributes.items() if not is_missing(value)}, sort_keys=True)


def is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))

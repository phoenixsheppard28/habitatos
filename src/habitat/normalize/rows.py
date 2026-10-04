from dataclasses import dataclass, field

import pandas as pd
import pyarrow as pa

from habitat.contracts import CELL_OBSERVATIONS_SCHEMA, ProductStatus, RawManifest
from habitat.grid import Grid

CELL_OBSERVATIONS = "cell_observations"
ANIMAL_LOCATIONS = "animal_locations"
ANIMAL_ENTITIES = "animal_entities"


@dataclass
class NormalizedBatch:
    """Rows of one family. `references` maps a reference table name to rows that the family rows point to."""

    table: pa.Table
    mapping_version: str
    family: str = CELL_OBSERVATIONS
    references: dict[str, pa.Table] = field(default_factory=dict)


class QuarantineError(ValueError):
    """The raw item cannot be normalized without guessing. Keep it in quarantine with this reason."""


def series_id(manifest: RawManifest, grid: Grid) -> str:
    return series_id_for(manifest.extensions.source_id, manifest.extensions.product, grid)


def series_id_for(source_id: str, product: str, grid: Grid) -> str:
    return f"{source_id}--{product}--{grid.grid_id}"


def to_cell_observations(
    stats: pd.DataFrame,
    manifest: RawManifest,
    grid: Grid,
    mapping_version: str,
    stat: str,
    units: dict[str, str],
    source_resolution_m: float,
) -> NormalizedBatch:
    item = manifest.extensions
    rows = stats.copy()

    rows["time_start"] = pd.Timestamp(item.time_start)
    rows["time_end"] = pd.Timestamp(item.time_end)
    rows["time_precision"] = item.time_precision.value
    rows["available_at"] = pd.Timestamp(item.available_at)
    rows["source_id"] = item.source_id
    rows["source_item_id"] = item.source_item_id
    rows["processing_version"] = item.processing_version
    rows["product_status"] = item.product_status.value
    rows["dataset_id"] = series_id(manifest, grid)
    rows["mapping_version"] = mapping_version
    rows["stat"] = stat
    rows["unit"] = rows["variable"].map(units)
    rows["source_resolution_m"] = float(source_resolution_m)
    rows["quality_flag"] = quality_flags(rows, item.product_status)

    table = pa.Table.from_pandas(
        rows[CELL_OBSERVATIONS_SCHEMA.names], schema=CELL_OBSERVATIONS_SCHEMA, preserve_index=False
    )
    return NormalizedBatch(table, mapping_version)


def quality_flags(rows: pd.DataFrame, product_status: ProductStatus) -> pd.Series:
    flags = pd.Series("ok", index=rows.index)
    flags[rows["value"].isna()] = "low_valid_fraction"
    if product_status is ProductStatus.PRELIMINARY:
        flags[flags == "ok"] = "preliminary"
    return flags

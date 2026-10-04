from datetime import UTC, datetime

import numpy as np
import pyarrow as pa

from habitat.archive.store import ArtifactStore
from habitat.contracts import BBox, RawManifest
from habitat.grid import Grid
from habitat.normalize.indices import normalized_difference
from habitat.normalize.raster_io import RasterBlock, iter_aligned_blocks
from habitat.normalize.rows import NormalizedBatch, QuarantineError, to_cell_observations
from habitat.normalize.zonal import aggregate_blocks

MAPPING_VERSION = "landsat-c2-l2-v1"
REQUIRED_ASSETS = ("blue", "red", "nir08", "swir16", "qa_pixel")
UNITS = {"ndvi": "index", "ndmi": "index", "bare_soil_index": "index"}
SOURCE_RESOLUTION_M = 30.0

# Collection 2 Level-2 surface reflectance, from the STAC `raster:bands` and the USGS product guide.
# https://www.usgs.gov/landsat-missions/landsat-collection-2-level-2-science-products
REFLECTANCE_SCALE = 0.0000275
REFLECTANCE_OFFSET = -0.2
REFLECTANCE_NODATA = 0

# QA_PIXEL bits from the STAC `classification:bitfields`: 0 fill, 1 dilated cloud, 3 cloud, 4 cloud shadow,
# 5 snow, 6 clear.
QA_REJECT_BITS = (0, 1, 3, 4, 5)
QA_CLEAR_BIT = 6

LANDSAT_7 = "landsat-7"
SLC_FAILURE = datetime(2003, 5, 31, tzinfo=UTC)
TIER_2 = "T2"


def landsat_reflectance(digital_numbers: np.ndarray) -> np.ndarray:
    reflectance = digital_numbers.astype(np.float64) * REFLECTANCE_SCALE + REFLECTANCE_OFFSET
    reflectance[(digital_numbers == REFLECTANCE_NODATA) | (reflectance < 0) | (reflectance > 1)] = np.nan
    return reflectance


def landsat_clear_mask(qa: np.ndarray) -> np.ndarray:
    qa = qa.astype(np.uint16)
    reject = np.zeros(qa.shape, bool)
    for bit in QA_REJECT_BITS:
        reject |= (qa >> bit) & 1 == 1

    return ((qa >> QA_CLEAR_BIT) & 1 == 1) & ~reject


def bare_soil_index(blue: np.ndarray, red: np.ndarray, nir: np.ndarray, swir: np.ndarray) -> np.ndarray:
    return normalized_difference(swir + red, nir + blue)


def landsat_indices(
    blue: np.ndarray, red: np.ndarray, nir: np.ndarray, swir: np.ndarray, qa: np.ndarray
) -> dict[str, np.ndarray]:
    blue, red, nir, swir = (landsat_reflectance(band) for band in (blue, red, nir, swir))
    clear = landsat_clear_mask(qa)

    indices = {
        "ndvi": normalized_difference(nir, red),
        "ndmi": normalized_difference(nir, swir),
        "bare_soil_index": bare_soil_index(blue, red, nir, swir),
    }

    for values in indices.values():
        values[~clear] = np.nan
    return indices


def check_collection(manifest: RawManifest) -> None:
    item = manifest.extensions
    properties = item.properties
    if properties.get("collection_number") != "02":
        raise QuarantineError(f"{item.source_item_id}: not a Landsat Collection 2 item")

    scale, offset = properties.get("reflectance_scale"), properties.get("reflectance_offset")
    if scale is None or offset is None or not np.isclose([scale, offset], [REFLECTANCE_SCALE, REFLECTANCE_OFFSET]).all():
        raise QuarantineError(
            f"{item.source_item_id}: reflectance scale {scale} and offset {offset} differ from "
            f"{REFLECTANCE_SCALE} and {REFLECTANCE_OFFSET}"
        )


def scene_flag(manifest: RawManifest) -> str | None:
    item = manifest.extensions
    if item.properties.get("platform") == LANDSAT_7 and item.time_start > SLC_FAILURE:
        return "slc_off"

    if item.properties.get("collection_category") == TIER_2:
        return "tier2_geometry"

    return None


def with_scene_flag(batch: NormalizedBatch, flag: str | None) -> NormalizedBatch:
    if flag is None:
        return batch

    index = batch.table.schema.get_field_index("quality_flag")
    flags = [flag if value == "ok" else value for value in batch.table.column(index).to_pylist()]
    batch.table = batch.table.set_column(index, batch.table.schema.field(index), pa.array(flags, pa.string()))
    return batch


def normalize_landsat(manifest: RawManifest, store: ArtifactStore, grid: Grid, aoi: BBox | None = None) -> NormalizedBatch:
    item = manifest.extensions
    missing = [name for name in REQUIRED_ASSETS if name not in item.assets]
    if missing:
        raise QuarantineError(f"{item.source_item_id}: missing assets {missing}")

    check_collection(manifest)

    def compute(block: RasterBlock):
        bands = block.bands
        return landsat_indices(
            blue=bands["blue"], red=bands["red"], nir=bands["nir08"], swir=bands["swir16"], qa=bands["qa_pixel"]
        )

    assets = {name: str(store.open(manifest, name)) for name in REQUIRED_ASSETS}
    blocks = iter_aligned_blocks(assets, reference="red", aoi=aoi)
    stats = aggregate_blocks(grid, blocks, compute)

    batch = to_cell_observations(stats, manifest, grid, MAPPING_VERSION, "mean", UNITS, SOURCE_RESOLUTION_M)
    return with_scene_flag(batch, scene_flag(manifest))

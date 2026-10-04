import numpy as np

from habitat.archive.store import ArtifactStore
from habitat.contracts import BBox, RawManifest
from habitat.grid import Grid
from habitat.normalize.raster_io import RasterBlock, iter_aligned_blocks
from habitat.normalize.rows import NormalizedBatch, QuarantineError, to_cell_observations
from habitat.normalize.zonal import aggregate_blocks

MAPPING_VERSION = "modis-mcd64a1-v1"
UNITS = {"burned_fraction": "fraction"}
SOURCE_RESOLUTION_M = 463.312717

# MCD64A1 C6.1 user guide: Burn_Date 1-366 is the burn day, 0 unburned land, -1 unmapped, -2 water.
FIRST_BURN_DAY, LAST_BURN_DAY = 1, 366
UNBURNED = 0
LOWEST_CODE = -2


def burned_fraction(burn_date: np.ndarray) -> np.ndarray:
    outside = np.unique(burn_date[(burn_date < LOWEST_CODE) | (burn_date > LAST_BURN_DAY)])
    if outside.size:
        raise QuarantineError(f"Burn_Date values {outside.tolist()} are outside -2 to 366")

    burned = np.full(burn_date.shape, np.nan)
    burned[burn_date == UNBURNED] = 0.0
    burned[(burn_date >= FIRST_BURN_DAY) & (burn_date <= LAST_BURN_DAY)] = 1.0
    return burned


def normalize_mcd64a1(
    manifest: RawManifest, store: ArtifactStore, grid: Grid, aoi: BBox | None = None
) -> NormalizedBatch:
    item = manifest.extensions
    if "burn_date" not in item.assets:
        raise QuarantineError(f"{item.source_item_id}: missing asset 'burn_date'")

    def compute(block: RasterBlock):
        return {"burned_fraction": burned_fraction(block.bands["burn_date"])}

    blocks = iter_aligned_blocks({"burn_date": str(store.open(manifest, "burn_date"))}, reference="burn_date", aoi=aoi)
    stats = aggregate_blocks(grid, blocks, compute)

    return to_cell_observations(stats, manifest, grid, MAPPING_VERSION, "fraction", UNITS, SOURCE_RESOLUTION_M)

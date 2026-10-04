import numpy as np
import rasterio

from habitat.archive.store import ArtifactStore
from habitat.contracts import BBox, RawManifest
from habitat.grid import Grid
from habitat.normalize.raster_io import RasterBlock, iter_warped_blocks
from habitat.normalize.rows import NormalizedBatch, QuarantineError, to_cell_observations
from habitat.normalize.zonal import aggregate_blocks

MAPPING_VERSION = "cgls-lwq300-v1"
VARIABLES = {"water_turbidity": "turbidity", "trophic_state_index": "trophic_state_index"}
UNITS = {"water_turbidity": "NTU", "trophic_state_index": "index"}
SOURCE_RESOLUTION_M = 300.0
# The product holds values on water only, so one valid pixel gives a cell value.
MIN_VALID_FRACTION = 0.0


def normalize_cgls_lwq(
    manifest: RawManifest, store: ArtifactStore, grid: Grid, aoi: BBox | None = None
) -> NormalizedBatch:
    item = manifest.extensions
    missing = [asset for asset in VARIABLES.values() if asset not in item.assets]
    if missing:
        raise QuarantineError(f"{item.source_item_id}: missing assets {missing}")

    assets = {asset: str(store.open(manifest, asset)) for asset in VARIABLES.values()}
    fill_values = {}
    for asset, path in assets.items():
        with rasterio.open(path) as source:
            fill_values[asset] = source.nodata

    def compute(block: RasterBlock):
        return {variable: valid_values(block.bands[asset], fill_values[asset]) for variable, asset in VARIABLES.items()}

    blocks = iter_warped_blocks(assets, reference="turbidity", crs=grid.crs, aoi=aoi)
    stats = aggregate_blocks(grid, blocks, compute, MIN_VALID_FRACTION)
    return to_cell_observations(stats, manifest, grid, MAPPING_VERSION, "mean", UNITS, SOURCE_RESOLUTION_M)


def valid_values(raw: np.ndarray, fill_value: float | None) -> np.ndarray:
    values = raw.astype(np.float64)
    if fill_value is not None and not np.isnan(fill_value):
        values[raw == np.asarray(fill_value, dtype=raw.dtype)] = np.nan
    return values

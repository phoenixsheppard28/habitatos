from habitat.contracts import BBox, RawManifest
from habitat.grid import Grid
from habitat.normalize.indices import modis_vegetation_index
from habitat.normalize.raster_io import RasterBlock, iter_aligned_blocks
from habitat.normalize.rows import NormalizedBatch, QuarantineError, to_cell_observations
from habitat.normalize.zonal import aggregate_blocks

MAPPING_VERSION = "modis-mod13q1-v1"
REQUIRED_ASSETS = ("ndvi", "evi", "pixel_reliability")
UNITS = {"ndvi": "index", "evi": "index"}
SOURCE_RESOLUTION_M = 231.656358


def normalize_modis(manifest: RawManifest, grid: Grid, aoi: BBox | None = None) -> NormalizedBatch:
    item = manifest.extensions
    missing = [name for name in REQUIRED_ASSETS if name not in item.assets]
    if missing:
        raise QuarantineError(f"{item.source_item_id}: missing assets {missing}")

    def compute(block: RasterBlock):
        reliability = block.bands["pixel_reliability"]
        return {
            "ndvi": modis_vegetation_index(block.bands["ndvi"], reliability),
            "evi": modis_vegetation_index(block.bands["evi"], reliability),
        }

    assets = {name: item.assets[name] for name in REQUIRED_ASSETS}
    blocks = iter_aligned_blocks(assets, reference="ndvi", aoi=aoi)
    stats = aggregate_blocks(grid, blocks, compute)

    return to_cell_observations(stats, manifest, grid, MAPPING_VERSION, "mean", UNITS, SOURCE_RESOLUTION_M)

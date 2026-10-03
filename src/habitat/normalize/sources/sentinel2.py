from habitat.archive.store import ArtifactStore
from habitat.contracts import BBox, RawManifest
from habitat.grid import Grid
from habitat.normalize.indices import sentinel2_indices
from habitat.normalize.raster_io import RasterBlock, iter_aligned_blocks
from habitat.normalize.rows import NormalizedBatch, QuarantineError, to_cell_observations
from habitat.normalize.zonal import aggregate_blocks

MAPPING_VERSION = "sentinel2-l2a-v1"
REQUIRED_ASSETS = ("green", "red", "nir", "swir16", "scl")
UNITS = {"ndvi": "index", "mndwi": "index", "ndmi": "index"}
SOURCE_RESOLUTION_M = 10.0


def normalize_sentinel2(
    manifest: RawManifest, store: ArtifactStore, grid: Grid, aoi: BBox | None = None
) -> NormalizedBatch:
    item = manifest.extensions
    missing = [name for name in REQUIRED_ASSETS if name not in item.assets]
    if missing:
        raise QuarantineError(f"{item.source_item_id}: missing assets {missing}")

    if "boa_add_offset" not in item.properties:
        raise QuarantineError(f"{item.source_item_id}: unknown BOA_ADD_OFFSET; reflectance scale is ambiguous")
    boa_add_offset = float(item.properties["boa_add_offset"])

    def compute(block: RasterBlock):
        bands = block.bands
        return sentinel2_indices(
            b3=bands["green"], b4=bands["red"], b8=bands["nir"], b11=bands["swir16"], scl=bands["scl"],
            boa_add_offset=boa_add_offset,
        )

    assets = {name: str(store.open(manifest, name)) for name in REQUIRED_ASSETS}
    blocks = iter_aligned_blocks(assets, reference="red", aoi=aoi)
    stats = aggregate_blocks(grid, blocks, compute)

    return to_cell_observations(stats, manifest, grid, MAPPING_VERSION, "mean", UNITS, SOURCE_RESOLUTION_M)

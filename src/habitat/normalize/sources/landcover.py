import numpy as np

from habitat.archive.store import ArtifactStore
from habitat.contracts import BBox, RawManifest
from habitat.grid import Grid
from habitat.normalize.raster_io import RasterBlock, iter_aligned_blocks, iter_warped_blocks
from habitat.normalize.rows import NormalizedBatch, QuarantineError, to_cell_observations
from habitat.normalize.zonal import aggregate_blocks, class_fractions

VARIABLE_PREFIX = "landcover_fraction_"
NO_DATA = 0

# Common classes of HABITAT_DEGRADATION.md section 6.1. `rangeland` joins shrub and grass, because IO LULC has no
# split. A change of these tables is a new mapping version.
ESA_CCI_CLASSES = {
    "tree": (50, 60, 61, 62, 70, 71, 72, 80, 81, 82, 90, 100, 160, 170),
    "rangeland": (40, 110, 120, 121, 122, 130, 150, 151, 152, 153),
    "cropland": (10, 11, 12, 20, 30),
    "wetland": (180,),
    "built": (190,),
    "bare": (140, 200, 201, 202),
    "water": (210,),
    "snow": (220,),
}
IO_LULC_CLASSES = {
    "tree": (2,),
    "rangeland": (11,),
    "cropland": (5,),
    "wetland": (4,),
    "built": (7,),
    "bare": (8,),
    "water": (1,),
    "snow": (9,),
}

ESA_CCI_MAPPING_VERSION = "esa-cci-lc-v1"
ESA_CCI_RESOLUTION_M = 300.0
ESA_CCI_PROCESSED = 1

IO_LULC_MAPPING_VERSION = "io-lulc-annual-v1"
IO_LULC_RESOLUTION_M = 10.0
IO_LULC_CLOUDS = 10

UNITS = {f"{VARIABLE_PREFIX}{name}": "fraction" for name in ESA_CCI_CLASSES}


def fraction_layers(codes: np.ndarray, classes: dict[str, tuple[int, ...]]) -> dict[str, np.ndarray]:
    layers = class_fractions(codes, classes, NO_DATA)
    return {f"{VARIABLE_PREFIX}{name}": layer for name, layer in layers.items()}


def require_assets(manifest: RawManifest, names: tuple[str, ...]) -> None:
    item = manifest.extensions
    missing = [name for name in names if name not in item.assets]
    if missing:
        raise QuarantineError(f"{item.source_item_id}: missing assets {missing}")


def normalize_esa_cci_lc(
    manifest: RawManifest, store: ArtifactStore, grid: Grid, aoi: BBox | None = None
) -> NormalizedBatch:
    require_assets(manifest, ("lccs_class", "processed_flag"))

    def compute(block: RasterBlock):
        codes = np.where(block.bands["processed_flag"] == ESA_CCI_PROCESSED, block.bands["lccs_class"], NO_DATA)
        return fraction_layers(codes, ESA_CCI_CLASSES)

    assets = {name: str(store.open(manifest, name)) for name in ("lccs_class", "processed_flag")}
    blocks = iter_warped_blocks(assets, reference="lccs_class", crs=grid.crs, aoi=aoi)
    stats = aggregate_blocks(grid, blocks, compute)

    return to_cell_observations(
        stats, manifest, grid, ESA_CCI_MAPPING_VERSION, "fraction", UNITS, ESA_CCI_RESOLUTION_M
    )


def normalize_io_lulc(
    manifest: RawManifest, store: ArtifactStore, grid: Grid, aoi: BBox | None = None
) -> NormalizedBatch:
    require_assets(manifest, ("data",))

    def compute(block: RasterBlock):
        codes = block.bands["data"]
        return fraction_layers(np.where(codes == IO_LULC_CLOUDS, NO_DATA, codes), IO_LULC_CLASSES)

    blocks = iter_aligned_blocks({"data": str(store.open(manifest, "data"))}, reference="data", aoi=aoi)
    stats = aggregate_blocks(grid, blocks, compute)

    return to_cell_observations(
        stats, manifest, grid, IO_LULC_MAPPING_VERSION, "fraction", UNITS, IO_LULC_RESOLUTION_M
    )

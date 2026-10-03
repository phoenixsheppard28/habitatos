import rasterio

from habitat.archive.store import ArtifactStore
from habitat.contracts import BBox, RawManifest
from habitat.grid import Grid
from habitat.normalize.indices import chirps_rainfall
from habitat.normalize.rows import NormalizedBatch, QuarantineError, to_cell_observations
from habitat.normalize.zonal import sample_cell_centres

MAPPING_VERSION = "chirps-v2-daily-v1"
UNITS = {"rainfall_mm": "mm"}
SOURCE_RESOLUTION_M = 5566.0


def normalize_chirps(manifest: RawManifest, store: ArtifactStore, grid: Grid, aoi: BBox | None = None) -> NormalizedBatch:
    item = manifest.extensions
    if aoi is None:
        raise QuarantineError("CHIRPS is quasi-global; an area of interest is required to bound the cell count")

    if "precipitation" not in item.assets:
        raise QuarantineError(f"{item.source_item_id}: missing asset 'precipitation'")

    path = store.open(manifest, "precipitation")
    gdal_path = f"/vsigzip/{path}" if path.name.endswith(".gz") else str(path)
    with rasterio.open(gdal_path) as source:
        sampled = sample_cell_centres(grid, source, aoi)

    sampled["value"] = chirps_rainfall(sampled.pop("raw").to_numpy())
    stats = sampled.dropna(subset=["value"]).assign(
        variable="rainfall_mm", std=None, valid_fraction=1.0, pixel_count=1
    )

    return to_cell_observations(stats, manifest, grid, MAPPING_VERSION, "sum", UNITS, SOURCE_RESOLUTION_M)

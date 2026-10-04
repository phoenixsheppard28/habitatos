import numpy as np
import pandas as pd
import pyarrow as pa
import rasterio
import shapely

from habitat.archive.store import ArtifactStore
from habitat.contracts import CELL_OBSERVATIONS_SCHEMA, BBox, RawManifest
from habitat.grid import Grid, parse_cell_id, transformer
from habitat.normalize.raster_io import iter_warped_blocks, pixel_centres
from habitat.normalize.rows import NormalizedBatch, QuarantineError, to_cell_observations
from habitat.normalize.zonal import MIN_VALID_FRACTION, aggregate_blocks, class_fractions

MAPPING_VERSION = "jrc-gsw-monthly-v1"
SOURCE_RESOLUTION_M = 30.0
NO_DATA, NOT_WATER, WATER = 0, 1, 2
FRACTION = "surface_water_fraction"
DISTANCE = "distance_to_surface_water_m"
UNITS = {FRACTION: "fraction", DISTANCE: "m"}


def normalize_jrc_gsw_monthly(
    manifest: RawManifest, store: ArtifactStore, grid: Grid, aoi: BBox | None = None
) -> NormalizedBatch:
    item = manifest.extensions
    if "water" not in item.assets:
        raise QuarantineError(f"{item.source_item_id}: missing asset 'water'")

    path = str(store.open(manifest, "water"))
    with rasterio.open(path) as source:
        if source.crs is None or source.crs.to_epsg() != 4326:
            raise QuarantineError(f"{item.source_item_id}: expected a tile in EPSG:4326, got {source.crs}")
        codes = source.read(1)
        transform = source.transform

    unexpected = sorted(set(np.unique(codes).tolist()) - {NO_DATA, NOT_WATER, WATER})
    if unexpected:
        raise QuarantineError(f"{item.source_item_id}: pixel values {unexpected} are not 0, 1 or 2")

    fractions = aggregate_blocks(
        grid,
        iter_warped_blocks({"water": path}, "water", grid.crs, aoi),
        lambda block: {FRACTION: class_fractions(block.bands["water"], {FRACTION: [WATER], "land": [NOT_WATER]},
                                                 nodata=NO_DATA)[FRACTION]},
    )
    distances = fractions.assign(
        variable=DISTANCE, std=None, value=distance_to_water_m(grid, fractions["cell_id"], codes, transform)
    )

    tables = [
        to_cell_observations(fractions, manifest, grid, MAPPING_VERSION, "mean", UNITS, SOURCE_RESOLUTION_M).table,
        with_distance_flags(
            to_cell_observations(distances, manifest, grid, MAPPING_VERSION, "centroid", UNITS, SOURCE_RESOLUTION_M)
            .table
        ),
    ]
    return NormalizedBatch(pa.concat_tables(tables), MAPPING_VERSION)


def distance_to_water_m(grid: Grid, cell_ids: pd.Series, codes: np.ndarray, transform) -> np.ndarray:
    """Distance from each cell centroid to the centre of the nearest water pixel, in a local equidistant projection.

    EASE-Grid 2.0 is equal-area, not equidistant: near the equator it stretches east-west distances by about 15 %.
    """
    if cell_ids.empty:
        return np.array([], dtype=float)

    water_lon, water_lat = pixel_centres(transform, codes.shape)
    is_water = codes.ravel() == WATER
    if not is_water.any():
        return np.full(len(cell_ids), np.nan)

    rows, cols = np.array([parse_cell_id(cell_id) for cell_id in cell_ids]).T
    centre_x, centre_y = grid.cell_centres_xy(rows, cols)
    cell_lon, cell_lat = transformer(grid.crs, "EPSG:4326").transform(centre_x, centre_y)

    local = local_projection(float(np.mean(cell_lon)), float(np.mean(cell_lat)))
    water_x, water_y = transformer("EPSG:4326", local).transform(water_lon[is_water], water_lat[is_water])
    cell_x, cell_y = transformer("EPSG:4326", local).transform(cell_lon, cell_lat)

    water = shapely.points(np.asarray(water_x), np.asarray(water_y))
    cells = shapely.points(np.asarray(cell_x), np.asarray(cell_y))
    (cell_index, water_index), distances = shapely.STRtree(water).query_nearest(
        cells, return_distance=True, all_matches=False
    )
    nearest = np.full(len(cell_ids), np.nan)
    nearest[cell_index] = distances
    return nearest


def local_projection(longitude: float, latitude: float) -> str:
    return f"+proj=aeqd +lat_0={latitude:.6f} +lon_0={longitude:.6f} +datum=WGS84 +units=m +no_defs"


def with_distance_flags(table: pa.Table) -> pa.Table:
    """A distance needs water somewhere in the tile, not a clear view of the cell itself."""
    values = table.column("value").to_numpy(zero_copy_only=False)
    valid_fraction = table.column("valid_fraction").to_numpy()
    flags = np.where(
        np.isnan(values.astype(float)), "no_water_observed",
        np.where(valid_fraction < MIN_VALID_FRACTION, "low_valid_fraction", "ok"),
    )
    index = CELL_OBSERVATIONS_SCHEMA.get_field_index("quality_flag")
    return table.set_column(index, CELL_OBSERVATIONS_SCHEMA.field(index), pa.array(flags, pa.string()))

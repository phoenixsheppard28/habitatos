import numpy as np
import pytest
from pyproj import CRS
from rasterio.transform import from_origin

from conftest import write_raster
from habitat.normalize.raster_io import iter_warped_blocks, pixel_area_m2
from habitat.normalize.rows import QuarantineError
from habitat.normalize.zonal import aggregate_blocks, class_fractions

# 0.2 x 0.2 degrees in northern Namibia at 0.0025 degrees, about 260 m x 280 m per pixel.
WEST, NORTH, PIXEL_DEGREES, SIZE = 17.0, -19.0, 0.0025, 80
TREE, CROPLAND, NO_DATA = 10, 20, 0


@pytest.fixture
def land_cover(tmp_path):
    """West half tree, east half cropland, in EPSG:4326 as ESA CCI and WorldCover are."""
    codes = np.full((SIZE, SIZE), TREE, np.uint8)
    codes[:, SIZE // 2 :] = CROPLAND
    transform = from_origin(WEST, NORTH, PIXEL_DEGREES, PIXEL_DEGREES)
    return write_raster(tmp_path / "landcover.tif", codes, "EPSG:4326", transform, NO_DATA)


def test_a_geographic_raster_is_warped_to_the_grid_crs_at_its_native_pixel_size(land_cover, grid):
    blocks = list(iter_warped_blocks({"landcover": land_cover}, "landcover", grid.crs, block_rows=16))

    assert len(blocks) > 1
    assert all(block.crs == grid.crs for block in blocks)
    assert CRS.from_user_input(blocks[0].crs).is_projected
    assert 250 < pixel_area_m2(blocks[0].transform) ** 0.5 < 290
    assert {int(code) for block in blocks for code in np.unique(block.bands["landcover"])} <= {TREE, CROPLAND, NO_DATA}


def test_an_aoi_limits_the_warped_pixels(land_cover, grid):
    def pixel_count(aoi):
        blocks = iter_warped_blocks({"landcover": land_cover}, "landcover", grid.crs, aoi=aoi)
        return sum(block.shape[0] * block.shape[1] for block in blocks)

    west_quarter = (WEST, NORTH - 0.1, WEST + 0.05, NORTH)

    assert 0 < pixel_count(west_quarter) < pixel_count(None) / 4


def test_class_fractions_of_warped_land_cover_sum_to_one_per_cell(land_cover, grid):
    classes = {"tree": [TREE], "cropland": [CROPLAND]}
    blocks = iter_warped_blocks({"landcover": land_cover}, "landcover", grid.crs)

    stats = aggregate_blocks(grid, blocks, lambda block: class_fractions(block.bands["landcover"], classes, NO_DATA))

    reliable = stats.dropna(subset=["value"]).pivot(index="cell_id", columns="variable", values="value").dropna()
    assert not reliable.empty
    assert np.allclose(reliable["tree"] + reliable["cropland"], 1.0)
    assert (reliable["tree"] == 1.0).any() and (reliable["cropland"] == 1.0).any()


def test_class_fractions_are_nan_where_the_pixel_has_no_data():
    codes = np.array([[TREE, CROPLAND], [NO_DATA, TREE]], np.uint8)

    layers = class_fractions(codes, {"tree": [TREE], "cropland": [CROPLAND]}, NO_DATA)

    assert np.array_equal(layers["tree"], np.array([[1.0, 0.0], [np.nan, 1.0]]), equal_nan=True)
    assert np.array_equal(layers["cropland"], np.array([[0.0, 1.0], [np.nan, 0.0]]), equal_nan=True)


def test_a_class_code_outside_the_mapping_table_is_quarantined():
    codes = np.array([[TREE, 99]], np.uint8)

    with pytest.raises(QuarantineError, match="99"):
        class_fractions(codes, {"tree": [TREE]}, NO_DATA)

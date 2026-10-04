from datetime import UTC, datetime

import numpy as np
import pytest
from rasterio.transform import from_origin

from conftest import UTM_33S, UTM_ORIGIN, make_manifest, write_raster
from habitat.archive.store import LocalArtifactStore
from habitat.fetch.connectors.stac import SENTINEL2_ASSETS
from habitat.normalize.router import normalize
from habitat.normalize.water_indices import MIN_WATER_PIXELS, erode, sentinel2_water_mask

SIZE_10M, SIZE_20M = 300, 150
# Reflectance after the -1000 offset: green 0.05, red 0.03, red edge 0.04, swir 0.01 over water.
GREEN, RED, REDEDGE, NIR, SWIR = 1500, 1300, 1400, 1100, 1100
NDTI = (0.03 - 0.05) / (0.03 + 0.05)
NDCI = (0.04 - 0.03) / (0.04 + 0.03)


def test_erosion_removes_a_one_pixel_river_and_keeps_the_core_of_a_wide_one():
    narrow = np.zeros((7, 7), bool)
    narrow[:, 3] = True
    wide = np.zeros((7, 7), bool)
    wide[:, 1:6] = True

    assert not erode(narrow).any()
    assert erode(wide)[1:-1, 2:5].all()
    assert not erode(wide)[:, [1, 5]].any()


def test_the_water_mask_needs_clear_water_and_a_positive_mndwi():
    green = np.full((5, 5), GREEN)
    swir = np.full((5, 5), SWIR)
    scl = np.full((5, 5), 6)
    scl[0, 0] = 9
    swir[4, 4] = 4000

    mask = sentinel2_water_mask(green, swir, scl, boa_add_offset=-1000.0)

    assert mask[2, 2]
    assert not mask[1, 1]
    assert not mask[3, 3]


def water_scene(tmp_path, water_10m: np.ndarray, with_rededge=True, item_id="S2_WATER"):
    """A 3 km scene with water where `water_10m` is true and bare soil elsewhere."""
    transform_10m = from_origin(*UTM_ORIGIN, 10, 10)
    transform_20m = from_origin(*UTM_ORIGIN, 20, 20)
    water_20m = water_10m[::2, ::2] & water_10m[1::2, 1::2]

    def band(name, water_value, land_value, water, transform):
        data = np.where(water, water_value, land_value).astype(np.uint16)
        return write_raster(tmp_path / f"{name}.tif", data, UTM_33S, transform, 0)

    assets = {
        "green": band("B03", GREEN, 1500, water_10m, transform_10m),
        "red": band("B04", RED, 2000, water_10m, transform_10m),
        "nir": band("B08", NIR, 6000, water_10m, transform_10m),
        "swir16": band("B11", SWIR, 4000, water_20m, transform_20m),
        "scl": band("SCL", 6, 5, water_20m, transform_20m),
    }
    if with_rededge:
        assets["rededge"] = band("B05", REDEDGE, 3000, water_20m, transform_20m)
    return make_manifest(
        "sentinel2", assets, datetime(2024, 3, 9, 8, 47, tzinfo=UTC), item_id=item_id, product="sentinel-2-l2a",
        processing_version="05.10", properties={"boa_add_offset": -1000.0},
    )


def lake(size=SIZE_10M):
    water = np.zeros((size, size), bool)
    water[50:250, 50:250] = True
    return water


def variable_rows(table, variable):
    rows = table.to_pandas()
    return rows[rows["variable"] == variable]


def test_the_connector_fetches_the_red_edge_band():
    assert SENTINEL2_ASSETS["rededge"] == "B05"


def test_a_lake_gives_ndti_and_ndci_on_water_pixels(tmp_path, grid):
    table = normalize(water_scene(tmp_path, lake()), LocalArtifactStore(), grid).table

    ndti, ndci = variable_rows(table, "ndti"), variable_rows(table, "ndci")
    assert not ndti.empty and not ndci.empty
    assert np.allclose(ndti["value"].dropna(), NDTI)
    assert np.allclose(ndci["value"].dropna(), NDCI)
    assert set(ndci["source_resolution_m"]) == {20.0}
    assert set(ndti["unit"]) == {"index"}
    assert set(variable_rows(table, "ndvi")["variable"]) == {"ndvi"}


def test_a_one_pixel_river_gives_no_value_after_erosion(tmp_path, grid):
    river = np.zeros((SIZE_10M, SIZE_10M), bool)
    river[:, 150] = True

    table = normalize(water_scene(tmp_path, river), LocalArtifactStore(), grid).table

    assert variable_rows(table, "ndti").empty
    assert variable_rows(table, "ndci").empty


def test_a_scene_without_the_red_edge_band_keeps_the_old_variables_and_ndti(tmp_path, grid):
    table = normalize(water_scene(tmp_path, lake(), with_rededge=False), LocalArtifactStore(), grid).table

    assert set(table.column("variable").to_pylist()) == {"ndvi", "mndwi", "ndmi", "ndti"}


def test_few_water_pixels_are_flagged(tmp_path, grid):
    pond = np.zeros((SIZE_10M, SIZE_10M), bool)
    pond[100:104, 100:104] = True

    table = normalize(water_scene(tmp_path, pond), LocalArtifactStore(), grid).table

    ndti = variable_rows(table, "ndti")
    assert (ndti["pixel_count"] < MIN_WATER_PIXELS).all()
    assert set(ndti["quality_flag"]) == {"few_water_pixels"}
    assert ndti["value"].notna().all()


@pytest.mark.parametrize("variable", ["ndti", "ndci"])
def test_water_values_are_kept_for_cells_that_are_mostly_land(tmp_path, grid, variable):
    table = normalize(water_scene(tmp_path, lake()), LocalArtifactStore(), grid).table

    rows = variable_rows(table, variable)
    assert (rows["valid_fraction"] < 0.5).any()
    assert rows["value"].notna().all()

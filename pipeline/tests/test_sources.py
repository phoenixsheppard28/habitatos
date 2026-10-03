from datetime import UTC, datetime

import numpy as np
import pytest
from rasterio.transform import from_origin

from conftest import make_manifest, write_raster
from habitat.contracts import CELL_OBSERVATIONS_SCHEMA, ProductStatus, TimePrecision
from habitat.normalize.router import normalize
from habitat.normalize.rows import QuarantineError

MODIS_SINUSOIDAL = "+proj=sinu +lon_0=0 +x_0=0 +y_0=0 +R=6371007.181 +units=m +no_defs"


def test_sentinel2_precomputes_three_indices_per_cell(sentinel2_scene, grid):
    table = normalize(sentinel2_scene, grid).table
    rows = table.to_pandas()

    assert table.schema.equals(CELL_OBSERVATIONS_SCHEMA)
    assert set(rows["variable"]) == {"ndvi", "mndwi", "ndmi"}
    assert (rows["time_precision"] == "instant").all()

    reliable = rows[rows["value"].notna() & (rows["variable"] == "ndvi")]
    assert not reliable.empty
    assert np.allclose(reliable["value"], 0.4 / 0.6)
    assert np.allclose(reliable["std"], 0.0, atol=1e-9)
    assert (reliable["valid_fraction"] >= 0.5).all()


def test_sentinel2_cloudy_cells_are_null_not_zero(sentinel2_scene, grid):
    rows = normalize(sentinel2_scene, grid).table.to_pandas()

    cloudy = rows[rows["value"].isna()]
    assert not cloudy.empty
    assert (cloudy["quality_flag"] == "low_valid_fraction").all()
    assert (cloudy["valid_fraction"] < 0.5).all()


def test_sentinel2_without_offset_metadata_is_quarantined(sentinel2_scene, grid):
    sentinel2_scene.extensions.properties = {}

    with pytest.raises(QuarantineError, match="BOA_ADD_OFFSET"):
        normalize(sentinel2_scene, grid)


def test_chirps_samples_the_pixel_under_each_cell_centre(tmp_path, grid):
    rainfall = np.arange(100, dtype=np.float32).reshape(10, 10)
    rainfall[0, 0] = -9999
    path = write_raster(tmp_path / "chirps.tif", rainfall, "EPSG:4326", from_origin(16.0, -19.0, 0.05, 0.05), -9999)
    day = datetime(2024, 3, 5, tzinfo=UTC)
    manifest = make_manifest(
        "chirps", {"precipitation": path}, day, datetime(2024, 3, 6, tzinfo=UTC),
        precision=TimePrecision.DAY, status=ProductStatus.PRELIMINARY, storage_format="geotiff",
    )

    rows = normalize(manifest, grid, aoi=(16.0, -19.5, 16.5, -19.0)).table.to_pandas()

    assert set(rows["variable"]) == {"rainfall_mm"}
    assert rows["value"].between(0, 99).all()
    assert (rows["quality_flag"] == "preliminary").all()
    assert (rows["time_end"] - rows["time_start"]).dt.days.eq(1).all()
    assert rows["value"].nunique() > 50


def test_chirps_requires_an_area_of_interest(tmp_path, grid):
    manifest = make_manifest("chirps", {"precipitation": "x"}, datetime(2024, 3, 5, tzinfo=UTC), storage_format="geotiff")

    with pytest.raises(QuarantineError, match="area of interest"):
        normalize(manifest, grid)


def test_modis_keeps_given_ndvi_and_evi(tmp_path, grid):
    transform = from_origin(1_680_000.0, -2_110_000.0, 231.656358, 231.656358)
    size = 20
    ndvi = np.full((size, size), 6000, np.int16)
    evi = np.full((size, size), 3000, np.int16)
    reliability = np.zeros((size, size), np.int8)
    assets = {
        "ndvi": write_raster(tmp_path / "ndvi.tif", ndvi, MODIS_SINUSOIDAL, transform, -3000),
        "evi": write_raster(tmp_path / "evi.tif", evi, MODIS_SINUSOIDAL, transform, -3000),
        "pixel_reliability": write_raster(tmp_path / "rel.tif", reliability, MODIS_SINUSOIDAL, transform, -1),
    }
    manifest = make_manifest(
        "modis_mod13q1", assets, datetime(2024, 2, 18, tzinfo=UTC), datetime(2024, 3, 5, tzinfo=UTC),
        precision=TimePrecision.COMPOSITE,
    )

    rows = normalize(manifest, grid).table.to_pandas()
    reliable = rows[rows["value"].notna()]

    assert set(rows["variable"]) == {"ndvi", "evi"}
    assert np.allclose(reliable[reliable["variable"] == "ndvi"]["value"], 0.6)
    assert np.allclose(reliable[reliable["variable"] == "evi"]["value"], 0.3)
    assert (rows["time_precision"] == "composite").all()


def test_unknown_source_is_quarantined(grid):
    manifest = make_manifest("landsat", {}, datetime(2024, 3, 5, tzinfo=UTC))

    with pytest.raises(QuarantineError, match="no normalizer"):
        normalize(manifest, grid)


def test_fully_cloudy_scene_keeps_null_observations(sentinel2_scene, grid):
    import rasterio
    with rasterio.open(sentinel2_scene.extensions.assets['scl'], 'r+') as source:
        source.write(np.full((source.height, source.width), 9, dtype=np.uint8), 1)
    rows = normalize(sentinel2_scene, grid).table.to_pandas()
    assert not rows.empty
    assert rows['value'].isna().all()
    assert rows['valid_fraction'].eq(0).all()
    assert rows['pixel_count'].eq(0).all()
    assert rows['quality_flag'].eq('low_valid_fraction').all()


def test_aoi_outside_raster_returns_empty_batch(sentinel2_scene, grid):
    assert normalize(sentinel2_scene, grid, aoi=(-80, 30, -79, 31)).table.num_rows == 0


def test_assets_with_different_crs_are_quarantined(sentinel2_scene, grid):
    import rasterio
    with rasterio.open(sentinel2_scene.extensions.assets['green'], 'r+') as source:
        source.crs = 'EPSG:4326'
    with pytest.raises(QuarantineError, match='same known CRS'):
        normalize(sentinel2_scene, grid)

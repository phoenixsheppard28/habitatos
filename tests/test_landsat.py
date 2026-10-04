import json
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pystac
import pytest
from rasterio.transform import from_origin

from conftest import write_raster
from habitat.archive import Archive
from habitat.fetch.connectors import ConnectorRequest, landsat, stac
from habitat.normalize.router import normalize
from habitat.normalize.rows import QuarantineError
from habitat.normalize.sources.landsat import (
    bare_soil_index,
    landsat_clear_mask,
    landsat_indices,
    landsat_reflectance,
)
from habitat.sources import SOURCES

STAC_FIXTURES = Path(__file__).parent / "fixtures" / "stac"
UTM_37S = "EPSG:32737"
ORIGIN = (255_000.0, 9_845_000.0)
BBOX = (36.80, -1.45, 36.85, -1.405)
CLEAR = 1 << 6
CLOUD = 1 << 3


def recorded_item() -> pystac.Item:
    return pystac.Item.from_dict(json.loads((STAC_FIXTURES / "landsat-c2-l2.json").read_text()))


@pytest.fixture
def local_scene(tmp_path):
    """6 km x 6 km at 30 m. The west half is clear, the east half is cloud."""
    size = 200
    qa = np.full((size, size), CLEAR, np.uint16)
    qa[:, size // 2 :] = CLOUD

    def band(name, reflectance):
        digital_number = np.full((size, size), round((reflectance + 0.2) / 0.0000275), np.uint16)
        return write_raster(tmp_path / f"{name}.tif", digital_number, UTM_37S, from_origin(*ORIGIN, 30, 30), 0)

    scene = recorded_item()
    paths = {
        "blue": band("SR_B1", 0.05), "red": band("SR_B3", 0.1), "nir08": band("SR_B4", 0.3),
        "swir16": band("SR_B5", 0.2),
        "qa_pixel": write_raster(tmp_path / "QA_PIXEL.tif", qa, UTM_37S, from_origin(*ORIGIN, 30, 30), 1),
    }
    for name, path in paths.items():
        scene.assets[name].href = path
    return scene


def test_reflectance_uses_the_collection_2_scale_and_masks_nodata_and_saturation():
    digital_numbers = np.array([10000, 0, 43636 + 1000, 1000], np.uint16)

    reflectance = landsat_reflectance(digital_numbers)

    assert np.isclose(reflectance[0], 10000 * 0.0000275 - 0.2)
    assert np.isnan(reflectance[1])
    assert np.isnan(reflectance[2])
    assert np.isnan(reflectance[3])


@pytest.mark.parametrize("bit", [0, 1, 3, 4, 5])
def test_a_flagged_qa_bit_masks_the_pixel(bit):
    assert not landsat_clear_mask(np.array([CLEAR | (1 << bit)], np.uint16))[0]


def test_a_pixel_is_valid_only_with_the_clear_bit():
    qa = np.array([CLEAR, 0, CLEAR | (1 << 7)], np.uint16)

    assert landsat_clear_mask(qa).tolist() == [True, False, True]


def test_bare_soil_index_on_known_values():
    result = bare_soil_index(blue=np.array([0.05]), red=np.array([0.1]), nir=np.array([0.3]), swir=np.array([0.2]))

    assert np.isclose(result[0], ((0.2 + 0.1) - (0.3 + 0.05)) / ((0.2 + 0.1) + (0.3 + 0.05)))


def test_indices_are_nan_under_cloud():
    def digital_number(reflectance):
        return np.full(2, round((reflectance + 0.2) / 0.0000275), np.uint16)

    indices = landsat_indices(
        blue=digital_number(0.05), red=digital_number(0.1), nir=digital_number(0.3), swir=digital_number(0.2),
        qa=np.array([CLEAR, CLOUD], np.uint16),
    )

    assert set(indices) == {"ndvi", "ndmi", "bare_soil_index"}
    assert np.isclose(indices["ndvi"][0], 0.2 / 0.4, atol=1e-4)
    assert np.isclose(indices["ndmi"][0], 0.1 / 0.5, atol=1e-4)
    assert all(np.isnan(values[1]) for values in indices.values())


def test_describe_landsat_reads_the_recorded_item():
    description = landsat.describe_landsat(recorded_item())

    assert description["source_id"] == "landsat_c2_l2"
    assert description["available_at"] == datetime(2022, 5, 6, 16, 45, 17, 342440, tzinfo=UTC)
    assert description["processing_version"] == "02.T1"
    assert description["time_start"] == description["time_end"] == datetime(2012, 12, 22, 7, 39, 19, 180002, tzinfo=UTC)
    assert description["properties"] == {
        "platform": "landsat-7", "wrs_path": "168", "wrs_row": "061", "cloud_cover_land": 35.0,
        "collection_number": "02", "collection_category": "T1", "reflectance_scale": 2.75e-05,
        "reflectance_offset": -0.2,
    }


def test_a_landsat_item_without_created_has_no_publication_date():
    scene = recorded_item()
    del scene.properties["created"]

    assert landsat.describe_landsat(scene)["available_at"] is None


def fetch_local(scene, monkeypatch) -> tuple:
    monkeypatch.setattr(stac, "search_items", lambda *a, **k: [scene])
    monkeypatch.setattr(stac, "check_asset_href", lambda href: None)
    archive = Archive()
    request = ConnectorRequest(bbox=BBOX, start=date(2012, 12, 22), end=date(2012, 12, 22))
    return landsat.fetch_landsat(request, archive), archive


def test_a_scene_is_clipped_archived_and_normalized(local_scene, monkeypatch, grid):
    result, archive = fetch_local(local_scene, monkeypatch)

    assert not result.errors
    [manifest] = result.manifests
    assert manifest.storage.format == SOURCES["landsat_c2_l2"].storage_format
    assert manifest.extensions.source_key == f"landsat_c2_l2:{local_scene.id}:{stac.bbox_key(BBOX)}"
    assert set(manifest.extensions.assets) == {"blue", "red", "nir08", "swir16", "qa_pixel"}

    rows = normalize(manifest, archive.store, grid, BBOX).table.to_pandas()
    clear = rows[rows["value"].notna()]
    assert set(rows["variable"]) == {"ndvi", "ndmi", "bare_soil_index"}
    assert np.allclose(clear[clear["variable"] == "ndvi"]["value"], 0.5, atol=1e-3)
    assert (rows["source_resolution_m"] == 30.0).all()
    assert (rows["time_precision"] == "instant").all()


def test_landsat_7_after_the_slc_failure_is_flagged(local_scene, monkeypatch, grid):
    result, archive = fetch_local(local_scene, monkeypatch)

    rows = normalize(result.manifests[0], archive.store, grid, BBOX).table.to_pandas()

    assert set(rows[rows["value"].notna()]["quality_flag"]) == {"slc_off"}
    assert set(rows[rows["value"].isna()]["quality_flag"]) == {"low_valid_fraction"}


def test_a_tier_2_scene_is_flagged(local_scene, monkeypatch, grid):
    local_scene.properties["platform"] = "landsat-8"
    local_scene.properties["landsat:collection_category"] = "T2"
    result, archive = fetch_local(local_scene, monkeypatch)

    rows = normalize(result.manifests[0], archive.store, grid, BBOX).table.to_pandas()

    assert set(rows[rows["value"].notna()]["quality_flag"]) == {"tier2_geometry"}


@pytest.mark.parametrize(
    "properties, reason",
    [
        ({"collection_number": "01"}, "Collection 2"),
        ({"reflectance_scale": 0.0001}, "scale"),
        ({"reflectance_offset": None}, "scale"),
    ],
)
def test_an_unexpected_collection_or_scale_is_quarantined(local_scene, monkeypatch, grid, properties, reason):
    result, archive = fetch_local(local_scene, monkeypatch)
    manifest = result.manifests[0]
    manifest.extensions.properties.update(properties)

    with pytest.raises(QuarantineError, match=reason):
        normalize(manifest, archive.store, grid, BBOX)


def test_a_scene_without_created_is_skipped_before_download(local_scene, monkeypatch):
    del local_scene.properties["created"]
    monkeypatch.setattr(stac, "clip_to_bbox", lambda *args: pytest.fail("downloaded"))

    result, _ = fetch_local(local_scene, monkeypatch)

    assert result.manifests == []
    assert any("no publication date" in warning for warning in result.warnings)


def test_landsat_assets_come_from_the_landsat_host():
    stac.check_asset_href(recorded_item().assets["red"].href)


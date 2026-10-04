from datetime import UTC, date, datetime

import numpy as np
import pystac
import pytest
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds

from conftest import write_raster
from habitat import config
from habitat.archive import Archive
from habitat.archive.store import LocalArtifactStore
from habitat.contracts import RawManifest
from habitat.fetch.connectors import ConnectorRequest
from habitat.fetch.connectors import stac
from habitat.normalize.router import normalize
from habitat.sources import SOURCES

RETRIEVED = datetime(2026, 10, 3, tzinfo=UTC)
UTM_37S = "EPSG:32737"
# 20 km x 20 km at 10 m around the Athi-Kaputiei plains; the 20 m bands cover the same ground.
ORIGIN = (255_000.0, 9_845_000.0)
BBOX = (36.85, -1.55, 36.95, -1.45)
REAL_SCENE = config.load_settings().raw_dir / "S2B_MSIL2A_20240217T074009_R092_T37MBU_20240217T113157"


def item(item_id="x", properties=None, assets=None, when=datetime(2024, 2, 2, 7, 41, tzinfo=UTC)):
    result = pystac.Item(item_id, None, [36.0, -2.0, 38.0, -1.0], when, properties or {})
    for key, href in (assets or {}).items():
        result.add_asset(key, pystac.Asset(href))
    return result


@pytest.fixture
def local_scene(tmp_path):
    def band(name, value, resolution, dtype=np.uint16):
        size = int(20_000 / resolution)
        data = np.full((size, size), value, dtype)
        return write_raster(tmp_path / f"{name}.tif", data, UTM_37S, from_origin(*ORIGIN, resolution, resolution), 0)

    assets = {
        "B03": band("B03", 1500, 10), "B04": band("B04", 2000, 10), "B08": band("B08", 6000, 10),
        "B11": band("B11", 4000, 20), "SCL": band("SCL", 4, 20, np.uint8),
    }
    return item(
        "S2B_MSIL2A_20240217T074009_R092_T37MBU_20240217T113157",
        {"s2:processing_baseline": "05.10", "s2:generation_time": "2024-02-17T11:31:57Z", "eo:cloud_cover": 1.0},
        assets,
        when=datetime(2024, 2, 17, 7, 50, tzinfo=UTC),
    )


def test_sentinel2_is_public_from_its_generation_time():
    generated = item(properties={"s2:generation_time": "2024-02-02T13:12:12.579537Z"})

    assert stac.published_at(generated) == datetime(2024, 2, 2, 13, 12, 12, 579537, tzinfo=UTC)


def test_the_retrieval_time_is_never_the_publication_date():
    assert stac.published_at(item()) is None


def test_modis_is_public_from_its_production_time():
    composite = item(
        "MOD13Q1.A2024033.h21v09.061.2024051124114",
        {"start_datetime": "2024-02-02T00:00:00Z", "end_datetime": "2024-02-17T23:59:59Z"},
    )

    assert stac.describe_modis(composite)["available_at"] == datetime(2024, 2, 20, 12, 41, 14, tzinfo=UTC)


@pytest.mark.parametrize("baseline, offset", [("02.14", 0.0), ("03.01", 0.0), ("04.00", -1000.0), ("05.10", -1000.0)])
def test_boa_add_offset_starts_at_baseline_04_00(baseline, offset):
    assert stac.boa_add_offset(baseline) == offset


def test_modis_search_keeps_only_terra_items(monkeypatch):
    found = [item("MOD13Q1.A2024033.h21v09.061.2024051124114"), item("MYD13Q1.A2024041.h21v09.061.2024058000000")]
    monkeypatch.setattr(stac, "search_items", lambda *args, **kwargs: found)

    kept = stac.search_modis_terra(BBOX, RETRIEVED, RETRIEVED)

    assert [i.id for i in kept] == ["MOD13Q1.A2024033.h21v09.061.2024051124114"]


def test_clipped_output_is_inside_the_bbox_plus_one_pixel(local_scene, tmp_path):
    target = tmp_path / "clipped.tif"

    stac.clip_to_bbox(local_scene.assets["B04"].href, BBOX, target)

    with rasterio.open(target) as clipped, rasterio.open(local_scene.assets["B04"].href) as full:
        west, south, east, north = transform_bounds("EPSG:4326", clipped.crs, *BBOX)
        pixel = clipped.res[0]
        assert clipped.bounds.left >= west - pixel and clipped.bounds.right <= east + pixel
        assert clipped.bounds.bottom >= south - pixel and clipped.bounds.top <= north + pixel
        assert clipped.width * clipped.height < full.width * full.height
        assert clipped.read(1).min() == 2000


def test_a_raster_outside_the_bbox_has_no_overlap(local_scene, tmp_path):
    with pytest.raises(stac.NoOverlap):
        stac.clip_to_bbox(local_scene.assets["B04"].href, (10.0, 10.0, 10.1, 10.1), tmp_path / "x.tif")


def test_remote_assets_must_come_from_the_provider_hosts():
    stac.check_asset_href("https://sentinel2l2a01.blob.core.windows.net/a/B04.tif?sig=x")
    for href in ["http://sentinel2l2a01.blob.core.windows.net/a.tif", "https://evil.example/a.tif", "/etc/passwd"]:
        with pytest.raises(ValueError):
            stac.check_asset_href(href)


def test_invalid_requests_fail_before_any_search(monkeypatch):
    monkeypatch.setattr(stac, "search_items", lambda *a, **k: pytest.fail("searched"))
    bad = [
        ConnectorRequest(bbox=(0, 0, 0, 1), start=date(2025, 1, 1), end=date(2025, 1, 2)),
        ConnectorRequest(bbox=(float("nan"), 0, 1, 1), start=date(2025, 1, 1), end=date(2025, 1, 2)),
        ConnectorRequest(bbox=BBOX, start=date(2025, 2, 1), end=date(2025, 1, 1)),
        ConnectorRequest(bbox=BBOX, start=date(2025, 1, 1), end=date(2025, 1, 2), max_items=0),
    ]

    for request in bad:
        result = stac.fetch_sentinel2(request, Archive())
        assert [e.code for e in result.errors] == ["invalid_request"]


def test_sentinel2_scene_is_one_clipped_artifact_that_normalizes(local_scene, monkeypatch, grid):
    monkeypatch.setattr(stac, "search_items", lambda *a, **k: [local_scene])
    monkeypatch.setattr(stac, "check_asset_href", lambda href: None)
    archive = Archive()
    request = ConnectorRequest(bbox=BBOX, start=date(2024, 2, 17), end=date(2024, 2, 17))

    result = stac.fetch_sentinel2(request, archive)

    assert not result.errors
    [manifest] = result.manifests
    assert RawManifest.model_validate(manifest.model_dump(mode="json")) == manifest
    assert manifest.storage.uri == f"artifact://{local_scene.id}/1"
    assert manifest.storage.format == SOURCES["sentinel2"].storage_format
    assert sorted(manifest.extensions.assets.values()) == ["B03.tif", "B04.tif", "B08.tif", "B11.tif", "SCL.tif"]
    assert manifest.extensions.properties["boa_add_offset"] == -1000.0
    assert manifest.extensions.available_at == datetime(2024, 2, 17, 11, 31, 57, tzinfo=UTC)

    rows = normalize(manifest, archive.store, grid, BBOX).table.to_pandas()
    assert np.allclose(rows[rows["variable"] == "ndvi"]["value"].dropna(), 0.4 / 0.6)


def test_a_scene_without_a_publication_date_is_skipped_before_download(local_scene, monkeypatch):
    del local_scene.properties["s2:generation_time"]
    monkeypatch.setattr(stac, "search_items", lambda *a, **k: [local_scene])
    monkeypatch.setattr(stac, "clip_to_bbox", lambda *args: pytest.fail("downloaded"))
    request = ConnectorRequest(bbox=BBOX, start=date(2024, 2, 17), end=date(2024, 2, 17))

    result = stac.fetch_sentinel2(request, Archive())

    assert result.manifests == []
    assert any("no publication date" in warning for warning in result.warnings)


def test_a_second_fetch_uses_the_cache(local_scene, monkeypatch):
    monkeypatch.setattr(stac, "search_items", lambda *a, **k: [local_scene])
    monkeypatch.setattr(stac, "check_asset_href", lambda href: None)
    clips = []
    original = stac.clip_to_bbox
    monkeypatch.setattr(stac, "clip_to_bbox", lambda *args: clips.append(args) or original(*args))
    archive = Archive()
    request = ConnectorRequest(bbox=BBOX, start=date(2024, 2, 17), end=date(2024, 2, 17))

    first = stac.fetch_sentinel2(request, archive).manifests
    second = stac.fetch_sentinel2(request, archive).manifests

    assert first == second
    assert len(clips) == 5


def test_already_ingested_scenes_are_not_downloaded(local_scene, monkeypatch):
    monkeypatch.setattr(stac, "search_items", lambda *a, **k: [local_scene])
    request = ConnectorRequest(bbox=BBOX, start=date(2024, 2, 17), end=date(2024, 2, 17))

    result = stac.fetch_sentinel2(request, Archive(), lambda item_id, version, status: version == "05.10")

    assert result.manifests == []


@pytest.mark.skipif(not REAL_SCENE.is_dir(), reason="no real Sentinel-2 scene in data/raw")
def test_clip_of_a_real_tile_is_far_smaller_than_the_tile(tmp_path):
    full = REAL_SCENE / "B04.tif"
    target = tmp_path / "B04.tif"

    size = stac.clip_to_bbox(str(full), (36.85, -1.60, 37.10, -1.35), target)

    assert size < full.stat().st_size / 10
    with rasterio.open(target) as clipped:
        assert clipped.count == 1 and clipped.width > 100
    assert LocalArtifactStore().list_files() == []

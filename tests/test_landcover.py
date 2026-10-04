import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import numpy as np
import pystac
import pytest
from rasterio.transform import from_origin

from conftest import write_raster
from habitat.archive import Archive
from habitat.fetch.connectors import ConnectorRequest, landcover, stac
from habitat.normalize.router import normalize
from habitat.normalize.rows import QuarantineError
from habitat.normalize.sources.landcover import ESA_CCI_CLASSES, IO_LULC_CLASSES
from habitat.sources import SOURCES

STAC_FIXTURES = Path(__file__).parent / "fixtures" / "stac"
# Every class value in the `classification:classes` of the ESA CCI `lccs_class` asset, without 0 (no data).
ESA_CCI_LEGEND = [
    10, 11, 12, 20, 30, 40, 50, 60, 61, 62, 70, 71, 72, 80, 81, 82, 90, 100, 110, 120, 121, 122, 130, 140, 150, 151,
    152, 153, 160, 170, 180, 190, 200, 201, 202, 210, 220,
]
IO_LULC_LEGEND = [1, 2, 4, 5, 7, 8, 9, 11]
# 0.03 x 0.03 degrees of ESA CCI pixels (1/360 degree) near Athi-Kaputiei: about 3.3 km x 3.3 km.
CCI_WEST, CCI_NORTH, CCI_PIXEL, CCI_SIZE = 36.9, -1.4, 1 / 360, 12
CCI_BBOX = (CCI_WEST, CCI_NORTH - CCI_SIZE * CCI_PIXEL, CCI_WEST + CCI_SIZE * CCI_PIXEL, CCI_NORTH)
UTM_37S = "EPSG:32737"
IO_ORIGIN = (255_000.0, 9_845_000.0)
IO_BBOX = (36.80, -1.45, 36.85, -1.405)
GRASSLAND, CROPLAND_RAINFED = 130, 10
IO_RANGELAND, IO_BUILT, IO_CLOUDS = 11, 7, 10


def recorded_item(collection: str) -> pystac.Item:
    return pystac.Item.from_dict(json.loads((STAC_FIXTURES / f"{collection}.json").read_text()))


def fetch_local(fetch, scene, monkeypatch, bbox, day):
    monkeypatch.setattr(stac, "search_items", lambda *a, **k: [scene])
    monkeypatch.setattr(stac, "check_asset_href", lambda href: None)
    archive = Archive()
    request = ConnectorRequest(bbox=bbox, start=day, end=day)
    return fetch(request, archive), archive


@pytest.fixture
def esa_cci_scene(tmp_path):
    """West half grassland, east half rainfed cropland. The two north rows are not processed."""
    codes = np.full((CCI_SIZE, CCI_SIZE), GRASSLAND, np.uint8)
    codes[:, CCI_SIZE // 2 :] = CROPLAND_RAINFED
    processed = np.ones((CCI_SIZE, CCI_SIZE), np.uint8)
    processed[:2] = 0
    transform = from_origin(CCI_WEST, CCI_NORTH, CCI_PIXEL, CCI_PIXEL)

    scene = recorded_item("esa-cci-lc")
    scene.assets["lccs_class"].href = write_raster(tmp_path / "lccs_class.tif", codes, "EPSG:4326", transform, 0)
    scene.assets["processed_flag"].href = write_raster(
        tmp_path / "processed_flag.tif", processed, "EPSG:4326", transform, 255
    )
    return scene


@pytest.fixture
def io_lulc_scene(tmp_path):
    """6 km x 6 km at 10 m. West half rangeland, east half built area, a cloud strip in the north."""
    size = 600
    codes = np.full((size, size), IO_RANGELAND, np.uint8)
    codes[:, size // 2 :] = IO_BUILT
    codes[:30] = IO_CLOUDS

    scene = recorded_item("io-lulc-annual-v02")
    scene.assets["data"].href = write_raster(tmp_path / "data.tif", codes, UTM_37S, from_origin(*IO_ORIGIN, 10, 10), 0)
    return scene


def test_every_esa_cci_legend_code_has_one_common_class():
    mapped = [code for codes in ESA_CCI_CLASSES.values() for code in codes]

    assert sorted(mapped) == ESA_CCI_LEGEND


def test_every_io_lulc_land_code_has_one_common_class():
    mapped = [code for codes in IO_LULC_CLASSES.values() for code in codes]

    assert sorted(mapped) == IO_LULC_LEGEND
    assert set(ESA_CCI_CLASSES) == set(IO_LULC_CLASSES)


def test_describe_esa_cci_lc_reads_the_recorded_item():
    description = landcover.describe_esa_cci_lc(recorded_item("esa-cci-lc"))

    assert description["source_id"] == "esa_cci_lc"
    assert description["time_start"] == datetime(2012, 1, 1, tzinfo=UTC)
    assert description["time_end"] == datetime(2012, 12, 31, 23, 59, 59, tzinfo=UTC)
    assert description["precision"].value == "composite"
    assert description["available_at"] == datetime(2023, 1, 11, 23, 50, 49, 905710, tzinfo=UTC)
    assert description["processing_version"] == "v2.0.7cds"
    assert description["properties"] == {"tile": "S45E000"}


def test_describe_io_lulc_uses_the_creation_time_of_the_file():
    created = datetime(2023, 8, 2, 19, 53, 45, tzinfo=UTC)

    description = landcover.describe_io_lulc(recorded_item("io-lulc-annual-v02"), lambda href: created)

    assert description["source_id"] == "io_lulc_annual"
    assert description["time_start"] == datetime(2020, 1, 1, tzinfo=UTC)
    assert description["time_end"] == datetime(2021, 1, 1, tzinfo=UTC)
    assert description["available_at"] == created
    assert description["processing_version"] == "v02"
    assert description["properties"] == {"tile": "37M"}


def test_the_creation_time_comes_from_the_blob_headers(mock_http):
    href = recorded_item("io-lulc-annual-v02").assets["data"].href
    requests = mock_http(lambda request: httpx.Response(200, headers={
        "x-ms-creation-time": "Wed, 02 Aug 2023 19:53:45 GMT", "last-modified": "Thu, 08 Feb 2024 14:59:08 GMT",
    }))

    created = landcover.blob_created_at(href)

    assert created == datetime(2023, 8, 2, 19, 53, 45, tzinfo=UTC)
    assert [request.method for request in requests] == ["HEAD"]


def test_a_blob_without_dates_has_no_publication_date(mock_http):
    mock_http(lambda request: httpx.Response(200))

    assert landcover.blob_created_at(recorded_item("io-lulc-annual-v02").assets["data"].href) is None


def test_the_blob_date_is_read_only_from_an_allowed_host():
    with pytest.raises(ValueError, match="not an allowed"):
        landcover.blob_created_at("https://evil.example/data.tif")


def test_io_lulc_search_drops_the_tiles_of_other_utm_zones(monkeypatch):
    found = [pystac.Item(tile, None, [-180, -8, 180, 0], None, {"start_datetime": "2020-01-01T00:00:00Z",
                                                                    "end_datetime": "2021-01-01T00:00:00Z"})
             for tile in ("60M-2020", "37M-2020", "01M-2020")]
    monkeypatch.setattr(stac, "search_items", lambda *a, **k: found)

    kept = landcover.search_io_lulc(IO_BBOX, datetime(2020, 1, 1, tzinfo=UTC), datetime(2020, 12, 31, tzinfo=UTC))

    assert [item.id for item in kept] == ["37M-2020"]


def test_esa_cci_gives_class_fractions_per_cell(esa_cci_scene, monkeypatch, grid):
    result, archive = fetch_local(landcover.fetch_esa_cci_lc, esa_cci_scene, monkeypatch, CCI_BBOX, date(2012, 6, 1))

    [manifest] = result.manifests
    assert manifest.storage.format == SOURCES["esa_cci_lc"].storage_format
    rows = normalize(manifest, archive.store, grid, CCI_BBOX).table.to_pandas()

    assert set(rows["variable"]) == {f"landcover_fraction_{name}" for name in ESA_CCI_CLASSES}
    assert (rows["unit"] == "fraction").all() and (rows["stat"] == "fraction").all()
    assert (rows["time_precision"] == "composite").all()
    reliable = rows.dropna(subset=["value"]).pivot(index="cell_id", columns="variable", values="value")
    assert not reliable.empty
    assert np.allclose(reliable.sum(axis=1), 1.0)
    assert np.allclose(reliable["landcover_fraction_tree"], 0.0)
    assert (reliable["landcover_fraction_rangeland"] == 1.0).any()
    assert (reliable["landcover_fraction_cropland"] == 1.0).any()


def test_unprocessed_esa_cci_pixels_are_no_data(esa_cci_scene, monkeypatch, grid):
    north_strip = (CCI_WEST, CCI_NORTH - 2 * CCI_PIXEL, CCI_WEST + CCI_SIZE * CCI_PIXEL, CCI_NORTH)
    result, archive = fetch_local(landcover.fetch_esa_cci_lc, esa_cci_scene, monkeypatch, CCI_BBOX, date(2012, 6, 1))

    rows = normalize(result.manifests[0], archive.store, grid, north_strip).table.to_pandas()

    assert rows.empty or rows["value"].isna().all()


def test_an_unknown_esa_cci_code_is_quarantined(esa_cci_scene, monkeypatch, grid, tmp_path):
    codes = np.full((CCI_SIZE, CCI_SIZE), 99, np.uint8)
    transform = from_origin(CCI_WEST, CCI_NORTH, CCI_PIXEL, CCI_PIXEL)
    esa_cci_scene.assets["lccs_class"].href = write_raster(tmp_path / "unknown.tif", codes, "EPSG:4326", transform, 0)
    result, archive = fetch_local(landcover.fetch_esa_cci_lc, esa_cci_scene, monkeypatch, CCI_BBOX, date(2012, 6, 1))

    with pytest.raises(QuarantineError, match="99"):
        normalize(result.manifests[0], archive.store, grid, CCI_BBOX)


def test_io_lulc_gives_class_fractions_and_clouds_are_no_data(io_lulc_scene, monkeypatch, grid):
    monkeypatch.setattr(landcover, "blob_created_at", lambda href: datetime(2023, 8, 2, tzinfo=UTC))
    result, archive = fetch_local(landcover.fetch_io_lulc, io_lulc_scene, monkeypatch, IO_BBOX, date(2020, 6, 1))

    [manifest] = result.manifests
    assert manifest.extensions.available_at == datetime(2023, 8, 2, tzinfo=UTC)
    rows = normalize(manifest, archive.store, grid, IO_BBOX).table.to_pandas()

    reliable = rows.dropna(subset=["value"]).pivot(index="cell_id", columns="variable", values="value")
    assert np.allclose(reliable.sum(axis=1), 1.0)
    assert (reliable["landcover_fraction_rangeland"] == 1.0).any()
    assert (reliable["landcover_fraction_built"] == 1.0).any()
    assert (rows["source_resolution_m"] == 10.0).all()


def test_an_io_lulc_tile_without_a_file_date_is_skipped(io_lulc_scene, monkeypatch):
    monkeypatch.setattr(landcover, "blob_created_at", lambda href: None)

    result, _ = fetch_local(landcover.fetch_io_lulc, io_lulc_scene, monkeypatch, IO_BBOX, date(2020, 6, 1))

    assert result.manifests == []
    assert any("no publication date" in warning for warning in result.warnings)


def test_an_unknown_io_lulc_code_is_quarantined(io_lulc_scene, monkeypatch, grid, tmp_path):
    codes = np.full((600, 600), 3, np.uint8)
    io_lulc_scene.assets["data"].href = write_raster(tmp_path / "x.tif", codes, UTM_37S, from_origin(*IO_ORIGIN, 10, 10), 0)
    monkeypatch.setattr(landcover, "blob_created_at", lambda href: datetime(2023, 8, 2, tzinfo=UTC))
    result, archive = fetch_local(landcover.fetch_io_lulc, io_lulc_scene, monkeypatch, IO_BBOX, date(2020, 6, 1))

    with pytest.raises(QuarantineError, match="3"):
        normalize(result.manifests[0], archive.store, grid, IO_BBOX)

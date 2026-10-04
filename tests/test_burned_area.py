import json
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pystac
import pytest
from rasterio.transform import from_origin

from conftest import write_raster
from habitat.archive import Archive
from habitat.fetch.connectors import ConnectorRequest, burned_area, stac
from habitat.normalize.router import normalize
from habitat.normalize.rows import QuarantineError
from habitat.normalize.sources.burned_area import burned_fraction

STAC_FIXTURES = Path(__file__).parent / "fixtures" / "stac"
MODIS_SINUSOIDAL = "+proj=sinu +lon_0=0 +x_0=0 +y_0=0 +R=6371007.181 +units=m +no_defs"
# 10 km x 10 km of 463 m sinusoidal pixels near Athi-Kaputiei.
ORIGIN = (4_100_000.0, -150_000.0)
PIXEL = 463.3127165279165
SIZE = 22
BBOX = (36.86, -1.43, 36.96, -1.37)


def recorded_item() -> pystac.Item:
    return pystac.Item.from_dict(json.loads((STAC_FIXTURES / "modis-64A1-061.json").read_text()))


@pytest.fixture
def burn_scene(tmp_path):
    """West half burned on day 340, east half unburned, the south rows water."""
    burn_date = np.full((SIZE, SIZE), 340, np.int16)
    burn_date[:, SIZE // 2 :] = 0
    burn_date[-4:] = -2

    scene = recorded_item()
    scene.assets["Burn_Date"].href = write_raster(
        tmp_path / "Burn_Date.tif", burn_date, MODIS_SINUSOIDAL, from_origin(*ORIGIN, PIXEL, PIXEL)
    )
    return scene


def fetch_local(scene, monkeypatch):
    monkeypatch.setattr(stac, "search_items", lambda *a, **k: [scene])
    monkeypatch.setattr(stac, "check_asset_href", lambda href: None)
    archive = Archive()
    request = ConnectorRequest(bbox=BBOX, start=date(2012, 12, 1), end=date(2012, 12, 31))
    return burned_area.fetch_mcd64a1(request, archive), archive


def test_burn_dates_become_burned_or_unburned_and_unmapped_or_water_is_no_data():
    result = burned_fraction(np.array([1, 366, 0, -1, -2], np.int16))

    assert np.array_equal(result, np.array([1.0, 1.0, 0.0, np.nan, np.nan]), equal_nan=True)


@pytest.mark.parametrize("value", [367, -3, -32768])
def test_a_burn_date_outside_the_documented_range_is_quarantined(value):
    with pytest.raises(QuarantineError, match=str(value)):
        burned_fraction(np.array([0, value], np.int16))


def test_describe_mcd64a1_reads_the_recorded_item():
    description = burned_area.describe_mcd64a1(recorded_item())

    assert description["source_id"] == "modis_mcd64a1"
    assert description["time_start"] == datetime(2012, 12, 1, tzinfo=UTC)
    assert description["time_end"] == datetime(2012, 12, 31, 23, 59, 59, tzinfo=UTC)
    assert description["precision"].value == "composite"
    assert description["available_at"] == datetime(2021, 11, 5, 5, 5, 7, tzinfo=UTC)
    assert description["processing_version"] == "061.2021309010507"


def test_the_search_keeps_only_mcd64a1_items(monkeypatch):
    found = [recorded_item(), pystac.Item("MCD64A1_other", None, None, datetime(2012, 12, 1, tzinfo=UTC), {})]
    found[1].id = "MOD13Q1.A2012336.h21v09.061.2021309010507"
    monkeypatch.setattr(stac, "search_items", lambda *a, **k: found)

    kept = burned_area.search_mcd64a1(BBOX, datetime(2012, 12, 1, tzinfo=UTC), datetime(2012, 12, 31, tzinfo=UTC))

    assert [item.id for item in kept] == [recorded_item().id]


def test_a_month_gives_the_burned_fraction_per_cell(burn_scene, monkeypatch, grid):
    result, archive = fetch_local(burn_scene, monkeypatch)

    [manifest] = result.manifests
    rows = normalize(manifest, archive.store, grid, BBOX).table.to_pandas()

    assert set(rows["variable"]) == {"burned_fraction"}
    assert (rows["unit"] == "fraction").all() and (rows["stat"] == "fraction").all()
    assert (rows["time_precision"] == "composite").all()
    reliable = rows["value"].dropna()
    assert (reliable == 1.0).any() and (reliable == 0.0).any()
    assert reliable.between(0, 1).all()

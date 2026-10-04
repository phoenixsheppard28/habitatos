import json
from datetime import UTC, datetime

import pystac
import pytest

from habitat.derive import run as derive_run
from habitat.fetch import service, tools
from habitat.fetch.connectors import stac
from habitat.normalize.rows import QuarantineError
from habitat.sources import sources_for_kind

BBOX = [36.85, -1.55, 36.95, -1.45]
DEGRADATION_SOURCES = ["landsat_c2_l2", "esa_cci_lc", "io_lulc_annual", "modis_mcd64a1"]


def scene(item_id: str) -> pystac.Item:
    return pystac.Item(item_id, None, None, datetime(2012, 6, 1, tzinfo=UTC), {})


@pytest.fixture
def searched(monkeypatch):
    collections = []

    def search_items(collection, *args, **kwargs):
        collections.append(collection)
        return [scene({"esa-cci-lc": "ESACCI-2012", "io-lulc-annual-v02": "37M-2020",
                       "modis-64A1-061": "MCD64A1.A2012153.h21v09.061.2021309010507"}.get(collection, "LE07_X"))]

    monkeypatch.setattr(stac, "search_items", search_items)
    monkeypatch.setattr(stac, "search_sentinel2", lambda *a, **k: [])
    monkeypatch.setattr(stac, "search_modis_terra", lambda *a, **k: [])
    return collections


def test_the_degradation_sources_can_be_discovered(searched):
    result = service.fetch_environment(
        BBOX, "2012-01-01", "2012-12-31", sources=DEGRADATION_SOURCES, discover_only=True
    )

    assert [found["source_id"] for found in result["discovered"]] == DEGRADATION_SOURCES
    assert searched == ["landsat-c2-l2", "esa-cci-lc", "io-lulc-annual-v02", "modis-64A1-061"]


def test_the_default_sources_stay_sentinel2_modis_and_chirps(searched):
    result = service.fetch_environment(BBOX, "2012-01-01", "2012-01-01", discover_only=True)

    assert {found["source_id"] for found in result["discovered"]} == {"chirps"}
    assert searched == []


@pytest.mark.parametrize("kind, expected", [
    ("land_cover", {"esa_cci_lc", "io_lulc_annual"}),
    ("fire_observations", {"modis_mcd64a1"}),
    ("surface_reflectance", {"sentinel2", "landsat_c2_l2"}),
])
def test_the_new_data_kinds_select_the_new_sources(kind, expected):
    assert {source.source_id for source in sources_for_kind(kind)} == expected


def test_the_agent_tool_names_the_degradation_sources(searched):
    arguments = {"bbox": BBOX, "start": "2012-01-01", "end": "2012-12-31", "sources": ["esa_cci_lc"],
                 "discover_only": True}

    result = json.loads(tools.fetch_environment.call(arguments))

    assert result["discovered"] == [{"source_id": "esa_cci_lc", "item": "ESACCI-2012"}]
    assert all(source_id in tools.fetch_environment.description for source_id in DEGRADATION_SOURCES)


def test_the_derivation_tool_is_offered_to_the_agent():
    assert tools.derive_habitat_indicators in tools.FETCH_AGENT_TOOLS
    assert "never" in tools.derive_habitat_indicators.description.lower()


@pytest.mark.parametrize("arguments, message", [
    ({"bbox": [0, 0, 0, 1], "window_start": 2001, "window_end": 2020}, "bbox"),
    ({"bbox": BBOX, "window_start": 2020, "window_end": 2001}, "window"),
    ({"bbox": BBOX, "window_start": 1970, "window_end": 2001}, "window"),
])
def test_the_derivation_tool_rejects_a_bad_request_before_any_database_call(monkeypatch, arguments, message):
    monkeypatch.setattr(derive_run, "connect", lambda: pytest.fail("connected"))

    result = json.loads(tools.derive_habitat_indicators.call(arguments))

    assert result["status"] == "error"
    assert message in result["message"]


def test_the_derivation_tool_reports_missing_inputs_as_insufficient_data(monkeypatch):
    def no_inputs(workspace, bbox, window, **options):
        raise QuarantineError("no dataset version of the input chirps--x")

    monkeypatch.setattr(derive_run, "derive_vegetation_indicators", no_inputs)
    monkeypatch.setattr(derive_run, "connect", lambda: FakeConnection())

    result = json.loads(tools.derive_habitat_indicators.call({"bbox": BBOX, "window_start": 2001, "window_end": 2020}))

    assert result == {"status": "insufficient_data", "message": "no dataset version of the input chirps--x"}


class FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

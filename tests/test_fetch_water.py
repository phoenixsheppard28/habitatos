import json
import shutil
from pathlib import Path

import httpx
import numpy as np
import pytest
from rasterio.transform import from_origin

from conftest import write_raster
from habitat.fetch import tools, water
from habitat.fetch.connectors import jrc_gsw_monthly

FIXTURES = Path(__file__).parent / "fixtures" / "water"
BBOX = [36.9, -1.5, 37.0, -1.4]


def test_discovery_plans_the_items_on_the_buffered_bbox_without_network(mock_http):
    calls = mock_http(lambda request: httpx.Response(500))

    result = water.fetch_water(BBOX, "2011-02-01", "2011-03-31", discover_only=True)

    assert calls == []
    assert result["status"] == "discovered"
    west, south, east, north = result["buffered_bbox"]
    assert west < 36.9 - 0.17 and east > 37.0 + 0.17 and south < -1.5 - 0.17 and north > -1.4 + 0.17
    planned = {(item["source_id"], item["item"]) for item in result["discovered"]}
    assert ("jrc_gsw_monthly", "2011_02-0000320000-0000840000") in planned
    assert ("jrc_gsw_monthly", "2011_03-0000320000-0000840000") in planned
    assert any(source == "osm_overpass" and item.endswith("@2012-09-12") for source, item in planned)
    assert any(source == "wpdx" for source, _ in planned)


def test_dates_outside_the_source_history_give_warnings():
    early = water.fetch_water(BBOX, "2010-01-01", "2010-12-31", sources=["osm_overpass"], discover_only=True)
    late = water.fetch_water(BBOX, "2022-01-01", "2022-03-31", sources=["jrc_gsw_monthly"], discover_only=True)

    assert any("2012-09-12" in warning for warning in early["warnings"])
    assert any("2021-12" in warning for warning in late["warnings"])
    assert late["discovered"] == []


def test_unknown_sources_and_buffers_are_rejected():
    with pytest.raises(ValueError, match="sources"):
        water.fetch_water(BBOX, "2011-01-01", "2011-01-31", sources=["hydrolakes"])
    with pytest.raises(ValueError, match="buffer_km"):
        water.fetch_water(BBOX, "2011-01-01", "2011-01-31", buffer_km=500)


def test_fetch_runs_each_source_on_the_buffered_bbox(mock_http, monkeypatch, tmp_path):
    tile = write_raster(tmp_path / "tile.tif", np.ones((40, 40), np.uint8), "EPSG:4326",
                        from_origin(36.9, -1.4, 0.00025, 0.00025))
    monkeypatch.setattr(jrc_gsw_monthly, "clip_to_bbox",
                        lambda href, bbox, target: shutil.copyfile(tile, target) and target.stat().st_size)
    wpdx_rows = json.loads((FIXTURES / "wpdx_athi.json").read_text())

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "overpass-api.de":
            return httpx.Response(200, content=(FIXTURES / "overpass_athi.json").read_bytes())
        if request.url.host == "data.waterpointdata.org":
            return httpx.Response(200, json=wpdx_rows if request.url.params["$offset"] == "0" else [])
        return httpx.Response(200, headers={"last-modified": "Sat, 05 Jan 2019 10:21:36 GMT"})

    mock_http(handle)

    result = water.fetch_water(BBOX, "2013-03-01", "2013-03-31")

    assert result["status"] == "ok", result["warnings"]
    assert sorted(a["source_id"] for a in result["raw_artifacts"]) == ["jrc_gsw_monthly", "osm_overpass", "wpdx"]


def test_the_agent_has_the_water_tool():
    assert tools.fetch_water in tools.FETCH_AGENT_TOOLS
    assert "buffer_km" in json.dumps(tools.fetch_water.to_dict())

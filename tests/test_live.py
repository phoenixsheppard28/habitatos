"""Live checks against the real providers. Run with HABITAT_LIVE_TESTS=1. They write only to a throwaway schema."""

import time
from datetime import date

import pytest

from habitat import config
from habitat.ingest import Workspace
from habitat.pipeline import build_request, run

BBOX = (36.8, -1.6, 37.1, -1.3)
FULL_TILES_BYTES = 3.4 * 1024**3

pytestmark = pytest.mark.live


def archive_bytes() -> int:
    return sum(path.stat().st_size for path in config.settings().raw_dir.rglob("*") if path.is_file())


def report(name, result, started):
    print(
        f"\n[{name}] status={result.status} artifacts={len(result.fetch.output.raw_artifacts)} "
        f"outcomes={[(o.artifact_id, o.status, o.reason) for o in result.outcomes]} "
        f"published={result.published} archive_bytes={archive_bytes()} seconds={time.monotonic() - started:.1f} "
        f"warnings={result.warnings}"
    )


@pytest.fixture
def workspace(database, grid):
    return Workspace(database, grid)


def test_live_sentinel2_is_clipped(workspace):
    started = time.monotonic()

    result = run(build_request("sentinel2", BBOX, date(2024, 2, 15), date(2024, 2, 20), None, None), False, workspace)

    report("sentinel2", result, started)
    assert result.status in ("ok", "partial")
    assert result.fetch.output.raw_artifacts
    assert archive_bytes() < FULL_TILES_BYTES / 20


def test_live_modis(workspace):
    started = time.monotonic()

    result = run(build_request("modis_mod13q1", BBOX, date(2024, 2, 1), date(2024, 2, 28), None, None), False, workspace)

    report("modis_mod13q1", result, started)
    assert result.status in ("ok", "partial")
    assert all(a.artifact_id.startswith("MOD13Q1.") for a in result.fetch.output.raw_artifacts)


def test_live_chirps(workspace):
    started = time.monotonic()

    result = run(build_request("chirps", BBOX, date(2024, 2, 17), date(2024, 2, 18), None, None), False, workspace)

    report("chirps", result, started)
    assert result.status in ("ok", "partial")
    assert len(result.fetch.output.raw_artifacts) == 2


def test_live_agent_path(workspace):
    started = time.monotonic()
    question = "Find rainfall and vegetation for bbox 36.8,-1.6,37.1,-1.3 from 2024-02-15 to 2024-02-20"

    result = run(build_request(None, None, None, None, None, question), True, workspace)

    report("agent", result, started)
    print(f"[agent] summary: {result.fetch.extensions.get('agent_summary')}")
    assert result.status in ("ok", "partial")
    assert result.fetch.output.raw_artifacts


NAIROBI_BBOX = (36.85, -1.45, 36.90, -1.40)


def test_live_osm_overpass_current_snapshot(workspace):
    started = time.monotonic()
    today = date.today()

    result = run(build_request("osm_overpass", NAIROBI_BBOX, today, today, None, None), False, workspace)

    report("osm_overpass", result, started)
    assert result.status in ("ok", "partial")
    assert [o.status for o in result.outcomes if o.source_id == "osm_overpass"] == ["appended"]


def test_live_jrc_gsw_monthly_tile_exists():
    from habitat.fetch import http
    from habitat.fetch.connectors import jrc_gsw_monthly

    url = jrc_gsw_monthly.tile_url(date(2011, 3, 1), (320000, 840000))
    with http.client() as client:
        response = client.head(url)

    assert response.status_code == 200
    assert response.headers["last-modified"]


def test_live_wpdx_one_row():
    from habitat.fetch import http
    from habitat.fetch.connectors import wpdx

    [row] = http.get_json(wpdx.ENDPOINT, params={"$where": wpdx.bbox_filter((36.6, -1.9, 37.3, -1.2)), "$limit": 1})

    assert {"row_id", "wpdx_id", "lat_deg", "lon_deg", "report_date", "updated", "status_clean",
            "water_source_clean"} <= set(row)

import json
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import parse_qs

import httpx

from habitat.archive import Archive
from habitat.contracts import RawManifest, TimePrecision
from habitat.fetch.connectors import ConnectorRequest
from habitat.fetch.connectors import osm_overpass
from habitat.normalize.router import normalize
from habitat.sources import SOURCES

FIXTURE = Path(__file__).parent / "fixtures" / "water" / "overpass_athi.json"
BBOX = (36.8, -1.55, 37.0, -1.4)
TODAY = date(2026, 10, 4)


def overpass(body: bytes = FIXTURE.read_bytes(), status: int = 200, queries: list | None = None):
    def handle(request: httpx.Request) -> httpx.Response:
        if queries is not None:
            queries.append(parse_qs(request.content.decode())["data"][0])
        return httpx.Response(status, content=body)

    return handle


def request(start=date(2010, 1, 1), end=date(2013, 12, 31)):
    return ConnectorRequest(bbox=BBOX, start=start, end=end)


def test_query_asks_for_a_past_snapshot_in_overpass_bbox_order(mock_http):
    queries = []
    mock_http(overpass(queries=queries))

    [manifest] = osm_overpass.fetch_osm_overpass(request(), Archive()).manifests

    [query] = queries
    assert '[date:"2013-12-31T23:59:59Z"]' in query
    assert 'nwr["natural"~"^(water|spring|wetland)$"](-1.55,36.8,-1.4,37.0);' in query
    assert "out meta geom;" in query
    item = manifest.extensions
    assert item.kind == "vector" and item.time_precision is TimePrecision.STATIC
    assert item.processing_version == "2013-12-31T23:59:59Z"
    assert item.source_item_id == "36.80000,-1.55000,37.00000,-1.40000@2013-12-31"
    assert item.available_at == datetime(2026, 10, 4, 2, 35, 56, tzinfo=UTC)


def test_a_current_query_has_no_date_and_uses_the_database_time(mock_http, monkeypatch):
    queries = []
    mock_http(overpass(queries=queries))
    monkeypatch.setattr(osm_overpass, "today", lambda: TODAY)

    [manifest] = osm_overpass.fetch_osm_overpass(request(date(2026, 9, 1), TODAY), Archive()).manifests

    assert "[date:" not in queries[0]
    assert manifest.extensions.processing_version == "2026-10-04T02:35:56Z"
    assert manifest.extensions.source_item_id.endswith("@2026-10-04")


def test_dates_before_the_history_start_use_the_oldest_snapshot(mock_http):
    queries = []
    mock_http(overpass(queries=queries))

    result = osm_overpass.fetch_osm_overpass(request(date(2010, 1, 1), date(2011, 6, 30)), Archive())

    assert '[date:"2012-09-12T06:55:00Z"]' in queries[0]
    assert any("2012-09-12" in warning for warning in result.warnings)


def test_manifest_keeps_the_json_and_normalizes(mock_http, grid):
    mock_http(overpass())
    archive = Archive()

    [manifest] = osm_overpass.fetch_osm_overpass(request(), archive).manifests

    assert RawManifest.model_validate(manifest.model_dump(mode="json")) == manifest
    assert manifest.storage.format == SOURCES["osm_overpass"].storage_format
    assert manifest.rights.license == "ODbL-1.0"
    assert manifest.extensions.properties["requested_bbox"] == list(BBOX)
    assert json.loads(archive.store.open(manifest, "features").read_text())["elements"]
    batch = normalize(manifest, archive.store, grid, BBOX)
    assert batch.table.num_rows == 19


def test_a_past_snapshot_is_cached_and_skipped_when_ingested(mock_http):
    queries = []
    mock_http(overpass(queries=queries))
    archive = Archive()

    first = osm_overpass.fetch_osm_overpass(request(), archive)
    again = osm_overpass.fetch_osm_overpass(request(), archive)
    skipped = osm_overpass.fetch_osm_overpass(request(), Archive(), lambda item, version, status: True)

    assert again.manifests == first.manifests
    assert skipped.manifests == []
    assert len(queries) == 1


def test_a_busy_server_gives_a_retryable_error(mock_http):
    mock_http(overpass(b"<html><p>runtime error: The server is probably too busy</p></html>"))

    result = osm_overpass.fetch_osm_overpass(request(), Archive())

    assert result.manifests == []
    assert result.errors[0].retryable and "busy" in result.errors[0].message


def test_a_runtime_remark_is_an_error_not_an_empty_result(mock_http):
    document = json.loads(FIXTURE.read_text()) | {"elements": [], "remark": "runtime error: Query timed out"}
    mock_http(overpass(json.dumps(document).encode()))

    result = osm_overpass.fetch_osm_overpass(request(), Archive())

    assert result.manifests == [] and "timed out" in result.errors[0].message


def test_a_too_large_response_is_refused(mock_http):
    mock_http(overpass())

    result = osm_overpass.fetch_osm_overpass(
        ConnectorRequest(bbox=BBOX, start=date(2013, 1, 1), end=date(2013, 12, 31), max_file_bytes=1000), Archive()
    )

    assert result.manifests == [] and "size" in result.errors[0].message

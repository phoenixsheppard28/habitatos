import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from conftest import make_manifest
from habitat.archive import Archive
from habitat.archive.store import LocalArtifactStore
from habitat.contracts import RawManifest, TimePrecision
from habitat.fetch.connectors import ConnectorRequest
from habitat.fetch.connectors import wpdx
from habitat.normalize.router import normalize
from habitat.normalize.rows import SITE_FEATURES, QuarantineError
from habitat.normalize.sources.wpdx import feature_type_and_permanence, normalize_wpdx
from habitat.sources import SOURCES

FIXTURE = Path(__file__).parent / "fixtures" / "water" / "wpdx_athi.json"
BBOX = (36.6, -1.9, 37.3, -1.2)
LATEST_UPDATE = datetime(2022, 11, 14, 20, 33, 38, tzinfo=UTC)


def wpdx_manifest(tmp_path, rows=None):
    path = tmp_path / "wpdx.json"
    path.write_text(json.dumps(rows) if rows is not None else FIXTURE.read_text())
    return make_manifest(
        "wpdx", {"water_points": str(path)}, LATEST_UPDATE, item_id="eqje-vguj:36.60000,-1.90000,37.30000,-1.20000",
        product="wpdx-plus", precision=TimePrecision.STATIC, processing_version="2022-11-14T20:33:38.000",
        available_at=LATEST_UPDATE, storage_format="json",
    )


def rows_by_record(tmp_path, grid, rows=None):
    batch = normalize_wpdx(wpdx_manifest(tmp_path, rows), LocalArtifactStore(), grid, None)
    return batch, {row["source_record_id"]: row for row in batch.table.to_pylist()}


def fixture_rows():
    return json.loads(FIXTURE.read_text())


def test_status_and_source_mapping(tmp_path, grid):
    batch, rows = rows_by_record(tmp_path, grid)

    def mapping(row_id):
        row = rows[row_id]
        return row["feature_type"], row["origin"], row["permanence"], row["status"]

    assert batch.family == SITE_FEATURES
    assert mapping("760290") == ("borehole", "artificial", "permanent", "functional")
    assert mapping("362382") == ("sand_dam", "artificial", "seasonal", "functional_needs_repair")
    assert mapping("760332") == ("well", "artificial", "unknown", "functional_not_in_use")
    assert mapping("363490") == ("borehole", "artificial", "permanent", "non_functional")
    assert mapping("363255") == ("sand_dam", "artificial", "seasonal", "non_functional_dry_season")
    assert mapping("760333") == ("rainwater_tank", "artificial", "seasonal", "abandoned")
    assert mapping("760388") == ("spring", "natural", "unknown", "functional")
    assert {row["feature_class"] for row in rows.values()} == {"water_point"}


def test_dry_season_failure_makes_a_permanent_source_seasonal(tmp_path, grid):
    rows = fixture_rows()
    rows[0] |= {"water_source_clean": "Borehole/Tubewell", "status_clean": "Non-Functional, dry season"}

    _, mapped = rows_by_record(tmp_path, grid, rows)

    assert mapped[rows[0]["row_id"]]["permanence"] == "seasonal"


def test_feature_type_table():
    assert feature_type_and_permanence("Piped Water") == ("tap", "permanent")
    assert feature_type_and_permanence("Delivered Water") == ("delivered", "intermittent")
    assert feature_type_and_permanence(None) == ("unknown", "unknown")


def test_a_row_without_wpdx_id_gets_a_row_feature_id_and_a_flag(tmp_path, grid):
    _, rows = rows_by_record(tmp_path, grid)

    assert rows["362981"]["feature_id"] == "wpdx:row:362981"
    assert rows["362981"]["quality_flag"] == "feature_id_missing"
    assert rows["760290"]["feature_id"] == "wpdx:6GCRHW63+83C"


def test_times_point_and_attributes(tmp_path, grid):
    _, rows = rows_by_record(tmp_path, grid)
    borehole = rows["760290"]

    assert borehole["time_start"] == datetime(2012, 12, 27, tzinfo=UTC)
    assert borehole["available_at"] == datetime(2020, 8, 7, 18, 6, 14, tzinfo=UTC)
    assert borehole["longitude"] is not None and borehole["cell_id"].startswith("E1K-")
    attributes = json.loads(borehole["attributes"])
    assert attributes["install_year"] == "2005" and attributes["dataset_title"]


@pytest.mark.parametrize(
    ("change", "message"),
    [({"status_clean": "Mostly fine"}, "status_clean"), ({"water_source_clean": "Lake"}, "water_source_clean"),
     ({"lat_deg": None}, "coordinates"), ({"report_date": None}, "report_date")],
)
def test_unknown_values_and_missing_fields_are_quarantined(tmp_path, grid, change, message):
    rows = fixture_rows()
    rows[0] |= change

    with pytest.raises(QuarantineError, match=message):
        rows_by_record(tmp_path, grid, rows)


def socrata(pages: list[list[dict]], params: list | None = None):
    def handle(request: httpx.Request) -> httpx.Response:
        if params is not None:
            params.append(dict(request.url.params))
        offset = int(request.url.params["$offset"])
        index = offset // wpdx.PAGE_ROWS
        return httpx.Response(200, json=pages[index] if index < len(pages) else [])

    return handle


def request():
    return ConnectorRequest(bbox=BBOX, start=date(2010, 1, 1), end=date(2013, 12, 31))


def test_connector_pages_with_a_bbox_filter(mock_http, monkeypatch):
    monkeypatch.setattr(wpdx, "PAGE_ROWS", 5)
    rows = fixture_rows()
    params = []
    mock_http(socrata([rows[:5], rows[5:]], params))

    [manifest] = wpdx.fetch_wpdx(request(), Archive()).manifests

    assert [p["$offset"] for p in params] == ["0", "5"]
    assert params[0]["$where"] == (
        "lat_deg between -1.9 and -1.2 AND lon_deg between 36.6 and 37.3 AND lat_deg IS NOT NULL"
    )
    assert params[0]["$order"] == "row_id"
    item = manifest.extensions
    assert item.kind == "vector" and item.time_precision is TimePrecision.STATIC
    assert item.processing_version == "2022-11-14T20:33:38.000"
    assert item.available_at == LATEST_UPDATE
    assert item.source_item_id == "eqje-vguj:36.60000,-1.90000,37.30000,-1.20000"
    assert item.source_key.endswith(":2022-11-14T20:33:38.000")


def test_manifest_keeps_the_rows_and_normalizes(mock_http, grid):
    mock_http(socrata([fixture_rows()]))
    archive = Archive()

    [manifest] = wpdx.fetch_wpdx(request(), archive).manifests

    assert RawManifest.model_validate(manifest.model_dump(mode="json")) == manifest
    assert manifest.storage.format == SOURCES["wpdx"].storage_format
    assert manifest.rights.license == "CC-BY-4.0"
    assert len(json.loads(archive.store.open(manifest, "water_points").read_text())) == 8
    assert normalize(manifest, archive.store, grid, BBOX).table.num_rows == 8


def test_no_rows_gives_a_warning(mock_http):
    mock_http(socrata([[]]))

    result = wpdx.fetch_wpdx(request(), Archive())

    assert result.manifests == [] and "no water points" in result.warnings[0]


def test_an_ingested_version_is_skipped(mock_http):
    mock_http(socrata([fixture_rows()]))

    result = wpdx.fetch_wpdx(request(), Archive(), lambda item, version, status: True)

    assert result.manifests == []


def test_a_server_error_is_retryable(mock_http):
    mock_http(lambda request: httpx.Response(503))

    result = wpdx.fetch_wpdx(request(), Archive())

    assert result.manifests == [] and result.errors[0].retryable

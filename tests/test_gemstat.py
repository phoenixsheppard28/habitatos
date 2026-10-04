import hashlib
import json
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from conftest import make_manifest
from habitat.archive import Archive
from habitat.contracts import TimePrecision
from habitat.fetch.connectors import ConnectorRequest
from habitat.fetch.connectors import gemstat as connector
from habitat.normalize.router import normalize
from habitat.normalize.rows import MONITORING_SITES, SITE_OBSERVATIONS, QuarantineError
from habitat.normalize.sources import gemstat
from habitat.sources import SOURCES

FIXTURES = Path(__file__).parent / "fixtures" / "water_quality" / "gemstat"
RECORD_ID = "18459694"
ARGENTINA = (-63.0, -27.5, -58.0, -22.0)


def fixture_zip(path: Path) -> Path:
    """The fixture CSVs in the layout of GFQA_v3.zip. The files are Latin-1 text, as in the archive."""
    with zipfile.ZipFile(path, "w") as archive:
        for csv in sorted(FIXTURES.glob("*.csv")):
            archive.write(csv, csv.name)
    return path


def gemstat_manifest(tmp_path, archive_path=None):
    archive_path = archive_path or fixture_zip(tmp_path / "GFQA_v3.zip")
    return make_manifest(
        "gemstat", {"archive": str(archive_path)}, datetime(2011, 9, 6, tzinfo=UTC), datetime(2019, 2, 22, tzinfo=UTC),
        item_id="gemstat:18459694:test", product=connector.PRODUCT, precision=TimePrecision.COMPOSITE,
        processing_version="v3", available_at=datetime(2026, 2, 2, tzinfo=UTC), storage_format="zip",
    )


@pytest.fixture
def batch(tmp_path, grid):
    return normalize(gemstat_manifest(tmp_path), Archive().store, grid)


def rows(batch, **match):
    return [row for row in batch.table.to_pylist() if all(row[key] == value for key, value in match.items())]


def test_gemstat_is_registered_for_water_quality_samples():
    source = SOURCES["gemstat"]

    assert "water_quality_samples" in source.data_kinds
    assert source.storage_format == "zip"


def test_the_fixture_normalizes_to_site_observations(batch):
    assert batch.family == SITE_OBSERVATIONS
    assert batch.table.num_rows == 11
    assert sorted(batch.references[MONITORING_SITES].column("site_id").to_pylist()) == [
        "gemstat:ARG00003", "gemstat:ARG00014", "gemstat:AUT00007",
    ]


def test_an_unmapped_parameter_code_is_dropped(batch):
    assert not rows(batch, parameter="faecal_coliforms")


def test_the_parameter_code_gives_the_fraction(batch):
    lead = rows(batch, site_id="gemstat:ARG00014", time_start=datetime(2011, 9, 6, tzinfo=UTC))

    by_fraction = {row["fraction"]: (row["censored"], round(row["value"], 6)) for row in lead}
    assert by_fraction == {"dissolved": ("left", 1.0), "total": ("none", 17.0)}
    assert {row["unit"] for row in lead} == {"ug/L"}


def test_a_censored_limit_of_zero_gives_no_value(batch):
    [zero] = rows(batch, site_id="gemstat:AUT00007")

    assert zero["censored"] == "left"
    assert zero["value"] is None
    assert zero["quality_flag"] == "suspect"


def test_an_estimated_flag(batch):
    [estimated] = rows(batch, parameter="nitrate_n", time_start=datetime(2018, 11, 15, tzinfo=UTC))

    assert estimated["quality_flag"] == "estimated"
    assert estimated["censored"] == "none"
    assert estimated["unit"] == "mg/L as N"


def test_local_dates_are_days_and_a_default_time_is_flagged(batch):
    [default_time] = rows(batch, parameter="ph", time_start=datetime(2018, 11, 15, tzinfo=UTC))
    [measured_time] = rows(batch, parameter="ph", time_start=datetime(2018, 8, 22, tzinfo=UTC))

    assert default_time["time_precision"] == "day"
    assert default_time["time_end"] == datetime(2018, 11, 16, tzinfo=UTC)
    assert default_time["quality_flag"] == "time_default"
    assert json.loads(default_time["attributes"])["local_sample_time"] == "00:00"
    assert measured_time["quality_flag"] == "ok"
    assert measured_time["sample_depth_m"] == pytest.approx(0.25)


def test_every_row_is_public_from_the_archive_publication_date(batch):
    assert set(batch.table.column("available_at").to_pylist()) == {datetime(2026, 2, 2, tzinfo=UTC)}
    assert set(batch.table.column("product_status").to_pylist()) == {"final"}


def test_station_metadata_is_latin_1(batch):
    sites = {row["site_id"]: row for row in batch.references[MONITORING_SITES].to_pylist()}

    argentina = sites["gemstat:ARG00003"]
    assert argentina["water_body_type"] == "river"
    assert argentina["water_body_name"] == "RIO PARAGUAI"
    assert argentina["elevation_m"] == pytest.approx(54.0)
    assert json.loads(argentina["attributes"])["country"] == "Argentina"
    assert "Públicas" in json.loads(argentina["attributes"])["responsible_agency"]


def test_an_aoi_keeps_only_its_stations(tmp_path, grid):
    batch = gemstat.normalize_gemstat(gemstat_manifest(tmp_path), Archive().store, grid, ARGENTINA)

    assert {row["site_id"] for row in batch.table.to_pylist()} == {"gemstat:ARG00003", "gemstat:ARG00014"}


def test_an_unknown_unit_quarantines_the_item(tmp_path, grid):
    broken = tmp_path / "broken.zip"
    with zipfile.ZipFile(broken, "w") as archive:
        for csv in FIXTURES.glob("*.csv"):
            content = csv.read_bytes()
            archive.writestr(csv.name, content.replace(b",mg/l,", b",ppm,") if csv.name == "Lead.csv" else content)

    with pytest.raises(QuarantineError, match="ppm"):
        gemstat.normalize_gemstat(gemstat_manifest(tmp_path, broken), Archive().store, grid)


def zenodo_server(archive_bytes: bytes):
    checksum = hashlib.md5(archive_bytes).hexdigest()
    record = {
        "id": int(RECORD_ID),
        "revision": 4,
        "metadata": {"title": "UNEP GEMS/Water Global Freshwater Quality Archive", "publication_date": "2026-02-02",
                     "license": {"id": "cc-by-4.0"}},
        "files": [{"key": "GFQA_v3.zip", "size": len(archive_bytes), "checksum": f"md5:{checksum}",
                   "links": {"self": f"https://zenodo.org/api/records/{RECORD_ID}/files/GFQA_v3.zip/content"}}],
    }

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "zenodo.org"
        if request.url.path.endswith("/content"):
            return httpx.Response(200, content=archive_bytes)
        return httpx.Response(200, json=record)

    return handle


def request(bbox=ARGENTINA, start=date(2011, 1, 1), end=date(2018, 10, 1)):
    return ConnectorRequest(bbox=bbox, start=start, end=end)


def test_the_connector_keeps_the_archive_and_an_extract_for_the_area(mock_http, tmp_path):
    requests = mock_http(zenodo_server(fixture_zip(tmp_path / "full.zip").read_bytes()))
    archive = Archive()

    result = connector.fetch_gemstat(request(), archive)

    assert not result.errors, result.errors
    [manifest] = result.manifests
    item = manifest.extensions
    assert item.available_at == datetime(2026, 2, 2, tzinfo=UTC)
    assert item.processing_version == "v3"
    assert item.source_item_id == f"gemstat:{RECORD_ID}:-63.00000,-27.50000,-58.00000,-22.00000:2011-01-01:2018-10-01"
    assert manifest.rights.license == "CC-BY-4.0"
    with zipfile.ZipFile(archive.store.open(manifest, "archive")) as extract:
        stations = extract.read("GEMStat_station_metadata.csv").decode("latin-1")
        lead = extract.read("Lead.csv").decode("latin-1")
        ph = extract.read("pH.csv").decode("latin-1")
    assert "AUT00007" not in stations and "ARG00014" in stations
    assert "AUT00007" not in lead and lead.count("ARG00014") == 4
    assert "2018-08-22" in ph and "2018-11-15" not in ph
    assert archive.cached(f"gemstat:{RECORD_ID}:md5:{hashlib.md5((tmp_path / 'full.zip').read_bytes()).hexdigest()}")
    assert len([r for r in requests if r.url.path.endswith("/content")]) == 1


def test_a_second_area_reuses_the_archived_file(mock_http, tmp_path):
    requests = mock_http(zenodo_server(fixture_zip(tmp_path / "full.zip").read_bytes()))
    archive = Archive()

    connector.fetch_gemstat(request(), archive)
    austria = request(bbox=(16.0, 46.5, 16.5, 47.5), start=date(1990, 1, 1), end=date(2018, 12, 31))
    second = connector.fetch_gemstat(austria, archive)

    assert len([r for r in requests if r.url.path.endswith("/content")]) == 1
    assert len(second.manifests) == 1


def test_an_area_without_open_stations_is_a_coverage_gap(mock_http, tmp_path):
    mock_http(zenodo_server(fixture_zip(tmp_path / "full.zip").read_bytes()))

    result = connector.fetch_gemstat(request(bbox=(36.8, -1.6, 37.1, -1.3)), Archive())

    assert not result.manifests and not result.errors
    assert "coverage gap" in result.warnings[0]


def test_the_normalizer_reads_the_extract(mock_http, tmp_path, grid):
    mock_http(zenodo_server(fixture_zip(tmp_path / "full.zip").read_bytes()))
    archive = Archive()
    [manifest] = connector.fetch_gemstat(request(), archive).manifests

    batch = normalize(manifest, archive.store, grid)

    assert batch.table.num_rows == 8
    assert set(batch.table.column("source_item_id").to_pylist()) == {manifest.extensions.source_item_id}

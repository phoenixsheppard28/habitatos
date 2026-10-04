import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pandas as pd
import pytest

from habitat.archive import Archive
from habitat.contracts import POINT_EVENTS_SCHEMA, RawManifest
from habitat.fetch.connectors import ConnectorRequest
from habitat.fetch.connectors.firms import countries_for, fetch_firms_modis, fetch_firms_viirs
from habitat.normalize.router import normalize
from habitat.normalize.rows import QuarantineError
from habitat.sources import SOURCES

FIXTURES = Path(__file__).parent / "fixtures"
MODIS_CSV = (FIXTURES / "firms_modis_2012_Kenya.csv").read_bytes()
VIIRS_CSV = (FIXTURES / "firms_viirs-snpp_2012_Kenya.csv").read_bytes()
BBOX = (36.7, -1.6, 37.2, -1.25)
LAST_MODIFIED = "Wed, 04 Dec 2024 21:17:10 GMT"


def firms_server(files: dict[str, bytes], gets: list | None = None, last_modified: str | None = LAST_MODIFIED):
    """Serve country files by name, for example `modis_2012_Kenya.csv`. Other files do not exist."""

    def handle(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        if name not in files:
            return httpx.Response(404)

        headers = {"content-length": str(len(files[name]))}
        if last_modified:
            headers["last-modified"] = last_modified
        if request.method == "HEAD":
            return httpx.Response(200, headers=headers)
        if gets is not None:
            gets.append(name)
        return httpx.Response(200, content=files[name], headers=headers)

    return handle


def request(start=date(2012, 3, 1), end=date(2012, 3, 31), **changes):
    return ConnectorRequest(bbox=BBOX, start=start, end=end, **changes)


def fetched(mock_http, files, connector=fetch_firms_modis, **changes):
    mock_http(firms_server(files))
    archive = Archive()
    return connector(request(**changes), archive), archive


def normalized_rows(mock_http, grid, csv: bytes = MODIS_CSV, connector=fetch_firms_modis, name="modis_2012_Kenya.csv"):
    result, archive = fetched(mock_http, {name: csv}, connector)
    [manifest] = result.manifests
    return normalize(manifest, archive.store, grid).table.to_pandas()


def edited_csv(**changes) -> bytes:
    rows = pd.read_csv(FIXTURES / "firms_modis_2012_Kenya.csv", dtype=str, keep_default_na=False)
    for column, (index, value) in changes.items():
        rows.loc[index, column] = value
    return rows.to_csv(index=False).encode()


def test_the_countries_of_a_bbox_include_each_overlapping_neighbour():
    countries = countries_for(BBOX)

    assert {"Kenya", "Tanzania"} <= set(countries)
    assert "Uganda" not in countries and "Brazil" not in countries


def test_the_country_file_is_archived_as_downloaded(mock_http):
    result, archive = fetched(mock_http, {"modis_2012_Kenya.csv": MODIS_CSV})

    [manifest] = result.manifests
    item = manifest.extensions
    assert RawManifest.model_validate(manifest.model_dump(mode="json")) == manifest
    assert manifest.storage.format == SOURCES["firms_modis"].storage_format == "csv"
    assert archive.store.open(manifest, "detections").read_bytes() == MODIS_CSV
    assert item.source_item_id == "firms:modis:Kenya:2012:36.70000,-1.60000,37.20000,-1.25000"
    assert item.processing_version == "6.2"
    assert item.kind == "tabular"
    assert item.available_at == datetime(2024, 12, 4, 21, 17, 10, tzinfo=UTC)
    assert (item.time_start, item.time_end) == (datetime(2012, 1, 1, tzinfo=UTC), datetime(2013, 1, 1, tzinfo=UTC))
    assert item.properties["requested_bbox"] == list(BBOX)
    assert "FIRMS" in manifest.rights.attribution
    assert any("Tanzania" in warning for warning in result.warnings)


def test_rows_are_clipped_to_the_requested_bbox_and_keep_the_schema(mock_http, grid):
    result, archive = fetched(mock_http, {"modis_2012_Kenya.csv": MODIS_CSV})

    table = normalize(result.manifests[0], archive.store, grid).table

    assert table.schema.equals(POINT_EVENTS_SCHEMA)
    rows = table.to_pandas()
    assert len(rows) == 11
    assert rows["longitude"].between(36.7, 37.2).all() and rows["latitude"].between(-1.6, -1.25).all()
    assert set(rows["event_type"]) == {"active_fire"}
    assert set(rows["sampling_design"]) == {"systematic"}
    assert set(rows["basis"]) == {"satellite_detection"}
    assert set(rows["method"]) == {"MODIS 6.2"}
    assert set(rows["unit"]) == {"MW"}
    assert rows["origin_record_id"].isna().all() and rows["taxon_name"].isna().all()


def test_acquisition_time_is_utc_and_the_record_id_keeps_the_source_text(mock_http, grid):
    rows = normalized_rows(mock_http, grid).set_index("source_record_id")

    row = rows.loc["Terra:2012-05-24T0824Z:-1.344:36.8836"]
    assert row["time_start"] == pd.Timestamp("2012-05-24T08:24Z")
    assert row["time_end"] == row["time_start"]
    assert row["time_precision"] == "instant"
    assert row["available_at"] == pd.Timestamp("2024-12-04T21:17:10Z")
    assert row["value"] == pytest.approx(22.3)
    assert row["coordinate_uncertainty_m"] == pytest.approx(500 * (2.0**2 + 1.4**2) ** 0.5)
    assert json.loads(row["attributes"]) == {
        "satellite": "Terra", "confidence": 30, "daynight": "D", "type": 0, "brightness": 316.8, "bright_t31": 294.6,
    }


def test_each_fire_flag(mock_http, grid):
    csv = edited_csv(confidence=(12, "29"), type=(14, "1"))

    flags = normalized_rows(mock_http, grid, csv).set_index("source_record_id")["quality_flag"]

    assert flags["Aqua:2012-03-12T2349Z:-1.2753:36.837"] == "coordinate_uncertainty_too_large"
    assert flags["Terra:2012-07-08T0754Z:-1.3111:36.9072"] == "low_confidence"
    assert flags["Aqua:2012-11-18T1108Z:-1.3215:36.9039"] == "non_vegetation_fire"
    assert flags["Terra:2012-05-24T0824Z:-1.344:36.8836"] == "ok"


def test_viirs_confidence_letters_and_types(mock_http, grid):
    rows = normalized_rows(mock_http, grid, VIIRS_CSV, fetch_firms_viirs, "viirs-snpp_2012_Kenya.csv")

    by_confidence = rows.assign(confidence=rows["attributes"].map(lambda a: json.loads(a)["confidence"]))
    assert set(by_confidence[by_confidence["confidence"] == "l"]["quality_flag"]) == {"low_confidence"}
    assert set(rows["method"]) == {"VIIRS 2"}
    assert "non_vegetation_fire" in set(rows["quality_flag"])
    assert len(rows) == 16


@pytest.mark.parametrize(
    "csv, reason",
    [
        (edited_csv(confidence=(13, "high")), "confidence"),
        (edited_csv(confidence=(13, "101")), "confidence"),
        (edited_csv(latitude=(13, "")), "coordinates"),
        (pd.read_csv(FIXTURES / "firms_modis_2012_Kenya.csv", dtype=str).drop(columns="acq_time").to_csv(index=False)
         .encode(), "acq_time"),
    ],
)
def test_a_file_that_needs_a_guess_is_quarantined(mock_http, grid, csv, reason):
    result, archive = fetched(mock_http, {"modis_2012_Kenya.csv": csv})

    with pytest.raises(QuarantineError, match=reason):
        normalize(result.manifests[0], archive.store, grid)


def test_a_text_reply_is_not_archived(mock_http):
    result, _ = fetched(mock_http, {"modis_2012_Kenya.csv": b"Invalid MAP_KEY."})

    assert result.manifests == []
    assert any("not a FIRMS CSV" in warning for warning in result.warnings)


def test_a_year_without_a_file_gives_a_warning(mock_http):
    result, _ = fetched(mock_http, {}, start=date(2026, 3, 1), end=date(2026, 3, 31))

    assert result.manifests == []
    assert any("Kenya 2026" in warning and "no file" in warning for warning in result.warnings)


def test_a_file_without_a_publication_date_is_skipped(mock_http):
    mock_http(firms_server({"modis_2012_Kenya.csv": MODIS_CSV}, last_modified=None))

    result = fetch_firms_modis(request(), Archive())

    assert result.manifests == []
    assert any("publication date" in warning for warning in result.warnings)


def test_the_day_limit_shortens_the_date_range(mock_http):
    gets = []
    mock_http(firms_server({"modis_2012_Kenya.csv": MODIS_CSV, "modis_2013_Kenya.csv": MODIS_CSV}, gets))

    result = fetch_firms_modis(request(date(2012, 12, 1), date(2013, 1, 31), max_days=31), Archive())

    assert gets == ["modis_2012_Kenya.csv"]
    assert any("day limit" in warning for warning in result.warnings)


def test_a_file_above_the_size_limit_is_not_downloaded(mock_http):
    gets = []
    mock_http(firms_server({"modis_2012_Kenya.csv": MODIS_CSV}, gets))

    result = fetch_firms_modis(request(max_file_bytes=100), Archive())

    assert result.manifests == [] and gets == []
    assert any("size" in warning for warning in result.warnings)


def test_a_second_fetch_uses_the_cache(mock_http):
    gets = []
    mock_http(firms_server({"modis_2012_Kenya.csv": MODIS_CSV}, gets))
    archive = Archive()

    first = fetch_firms_modis(request(), archive).manifests
    second = fetch_firms_modis(request(), archive).manifests

    assert first == second and gets == ["modis_2012_Kenya.csv"]


def test_an_already_ingested_file_gives_no_manifest(mock_http):
    mock_http(firms_server({"modis_2012_Kenya.csv": MODIS_CSV}))

    result = fetch_firms_modis(request(), Archive(), lambda item, version, status: version == "6.2")

    assert result.manifests == []


def test_viirs_has_no_data_before_its_first_day(mock_http):
    calls = mock_http(lambda request: httpx.Response(404))

    result = fetch_firms_viirs(request(date(2011, 12, 1), date(2011, 12, 31)), Archive())

    assert result.manifests == [] and calls == []
    assert any("2012-01-20" in warning for warning in result.warnings)


def test_a_bbox_at_sea_selects_no_country(mock_http):
    calls = mock_http(lambda request: httpx.Response(404))

    result = fetch_firms_modis(ConnectorRequest(bbox=(-30.0, -30.0, -29.0, -29.0), start=date(2012, 3, 1),
                                                end=date(2012, 3, 2)), Archive())

    assert result.manifests == [] and calls == []
    assert any("no FIRMS country" in warning for warning in result.warnings)

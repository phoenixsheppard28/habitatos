import json
from copy import deepcopy
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pandas as pd
import pytest

from habitat.archive import Archive
from habitat.contracts import POINT_EVENTS_SCHEMA, RawManifest
from habitat.fetch.connectors import ConnectorRequest
from habitat.fetch.connectors.gbif_occurrence import fetch_gbif_occurrence
from habitat.normalize.router import normalize
from habitat.normalize.rows import QuarantineError, series_id
from habitat.normalize.sources.gbif_occurrence import MORTALITY_DATASET_KEYS
from habitat.sources import SOURCES
from habitat.storage.series import SeriesStore

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "gbif_occurrence_search.json").read_text())
BBOX = (36.7, -1.6, 37.2, -1.25)
EBIRD = "4fa7b334-ce0d-4e88-aaae-2e0c138d049e"
EBIRD_RECORD = "947644580"
INATURALIST_RECORD = "1586090932"
LOCAL_TIME_RECORD = "3314208232"
NATURGUCKER_RECORD = "5012603408"
WILDEBEEST_RECORD = "1807329146"


def records() -> list[dict]:
    return deepcopy(FIXTURE["records"])


def edited(gbif_id: str, **changes) -> list[dict]:
    pool = records()
    for record in pool:
        if record["gbifID"] == gbif_id:
            for key, value in changes.items():
                if value is None:
                    record.pop(key, None)
                else:
                    record[key] = value
    return pool


def gbif_server(pool: list[dict], count: int | None = None, searches: list | None = None):
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/occurrence/search":
            if searches is not None:
                searches.append(request.url.params)
            offset = int(request.url.params.get("offset", 0))
            limit = int(request.url.params["limit"])
            total = len(pool) if count is None else count
            return httpx.Response(200, json={
                "offset": offset, "limit": limit, "endOfRecords": offset + limit >= total, "count": total,
                "results": pool[offset:offset + limit],
            })
        if request.url.path.startswith("/v1/dataset/"):
            key = request.url.path.rsplit("/", 1)[-1]
            if key in FIXTURE["datasets"]:
                return httpx.Response(200, json=FIXTURE["datasets"][key])
            return httpx.Response(404)
        return httpx.Response(404)

    return handle


def request(**changes):
    return ConnectorRequest(bbox=BBOX, start=date(2012, 1, 1), end=date(2013, 12, 31), **changes)


def fetched(mock_http, pool=None, **changes):
    mock_http(gbif_server(records() if pool is None else pool))
    archive = Archive()
    result = fetch_gbif_occurrence(request(**changes), archive)
    return result, archive


def rows_for(mock_http, grid, pool=None) -> pd.DataFrame:
    result, archive = fetched(mock_http, pool)
    [manifest] = result.manifests
    return normalize(manifest, archive.store, grid).table.to_pandas().set_index("source_record_id")


def test_the_search_pages_are_archived_with_the_dataset_metadata(mock_http):
    result, archive = fetched(mock_http)

    [manifest] = result.manifests
    item = manifest.extensions
    assert RawManifest.model_validate(manifest.model_dump(mode="json")) == manifest
    assert manifest.storage.format == SOURCES["gbif_occurrence"].storage_format == "json"
    assert set(item.assets) == {"page-000", "datasets"}
    page = json.loads(archive.store.open(manifest, "page-000").read_text())
    assert len(page["results"]) == 8
    assert item.source_item_id.startswith("gbif-search:")
    assert item.processing_version.startswith("sha256:")
    assert item.source_key == f"gbif_occurrence:{item.source_item_id}:{item.processing_version}"
    assert item.properties["requested_bbox"] == list(BBOX)
    assert item.properties["record_count"] == 8


def test_rights_give_the_most_restrictive_record_license(mock_http):
    result, _ = fetched(mock_http)

    assert result.manifests[0].rights.license == "CC-BY-NC-4.0"
    assert "GBIF.org" in result.manifests[0].rights.attribution


def test_rows_have_the_point_events_schema(mock_http, grid):
    result, archive = fetched(mock_http)

    table = normalize(result.manifests[0], archive.store, grid).table

    assert table.schema.equals(POINT_EVENTS_SCHEMA)
    rows = table.to_pandas()
    assert len(rows) == 8
    assert set(rows["event_type"]) == {"species_occurrence"}
    assert set(rows["sampling_design"]) == {"presence_only"}
    assert set(rows["license"]) == {"CC-BY-4.0", "CC-BY-NC-4.0"}


def test_species_names_and_keys(mock_http, grid):
    rows = rows_for(mock_http, grid)

    row = rows.loc[WILDEBEEST_RECORD]
    assert (row["taxon_name"], row["gbif_taxon_key"]) == ("Connochaetes taurinus", 2441105)
    assert rows.loc[EBIRD_RECORD, "basis"] == "HUMAN_OBSERVATION"
    assert rows.loc[EBIRD_RECORD, "individual_count"] == 1


def test_a_date_gives_a_utc_day(mock_http, grid):
    row = rows_for(mock_http, grid).loc[EBIRD_RECORD]

    assert row["time_precision"] == "day"
    assert row["time_start"] == pd.Timestamp("2012-03-30T00:00Z")
    assert row["time_end"] == pd.Timestamp("2012-03-31T00:00Z")


def test_a_time_without_an_offset_keeps_the_local_date(mock_http, grid):
    row = rows_for(mock_http, grid).loc[LOCAL_TIME_RECORD]

    assert row["time_precision"] == "day"
    assert row["time_start"] == pd.Timestamp("2013-01-06T00:00Z")
    assert json.loads(row["attributes"])["local_time"] == "13:43"


def test_a_time_with_an_offset_is_an_instant(mock_http, grid):
    pool = edited(LOCAL_TIME_RECORD, eventDate="2013-01-06T13:43:00+03:00")

    row = rows_for(mock_http, grid, pool).loc[LOCAL_TIME_RECORD]

    assert row["time_precision"] == "instant"
    assert row["time_start"] == row["time_end"] == pd.Timestamp("2013-01-06T10:43Z")


def test_a_year_or_an_interval_is_composite(mock_http, grid):
    pool = edited(EBIRD_RECORD, eventDate="2012")
    pool = [r if r["gbifID"] != INATURALIST_RECORD else {**r, "eventDate": "2013-01-20/2013-01-26"} for r in pool]

    rows = rows_for(mock_http, grid, pool)

    year = rows.loc[EBIRD_RECORD]
    assert (year["time_precision"], year["quality_flag"]) == ("composite", "imprecise_date")
    assert year["time_end"] == pd.Timestamp("2013-01-01T00:00Z")
    week = rows.loc[INATURALIST_RECORD]
    assert week["time_precision"] == "composite"
    assert (week["time_start"], week["time_end"]) == (pd.Timestamp("2013-01-20T00:00Z"), pd.Timestamp("2013-01-27T00:00Z"))


def test_origin_record_ids(mock_http, grid):
    origins = rows_for(mock_http, grid)["origin_record_id"]

    assert origins[INATURALIST_RECORD] == "inaturalist:5498066"
    assert origins[EBIRD_RECORD] == f"gbif:{EBIRD}:URN:catalog:CLO:EBIRD:OBS191228918"
    assert pd.isna(origins[NATURGUCKER_RECORD])


def test_available_at_prefers_the_record_then_the_dataset_then_the_crawl(mock_http, grid):
    rows = rows_for(mock_http, grid)

    assert rows.loc[INATURALIST_RECORD, "available_at"] == pd.Timestamp("2017-10-06T18:04:45Z")
    assert rows.loc[INATURALIST_RECORD, "quality_flag"] == "ok"
    assert rows.loc[EBIRD_RECORD, "available_at"] == pd.Timestamp("2025-08-08T00:00Z")
    assert rows.loc[EBIRD_RECORD, "quality_flag"] == "available_at_from_dataset"
    naturgucker = records()[[r["gbifID"] for r in records()].index(NATURGUCKER_RECORD)]
    assert rows.loc[NATURGUCKER_RECORD, "available_at"] == pd.Timestamp(naturgucker["lastCrawled"])
    assert json.loads(rows.loc[NATURGUCKER_RECORD, "attributes"])["available_at_source"] == "last_crawled"


@pytest.mark.parametrize(
    "changes, flag",
    [
        ({"issues": ["ZERO_COORDINATE"]}, "geospatial_issue"),
        ({"issues": ["RECORDED_DATE_UNLIKELY"]}, "date_issue"),
        ({"coordinateUncertaintyInMeters": 5000.0}, "coordinate_uncertainty_too_large"),
        ({"basisOfRecord": "LIVING_SPECIMEN"}, "captive_record"),
        ({"basisOfRecord": "FOSSIL_SPECIMEN"}, "not_live_observation"),
        ({"species": None, "speciesKey": None, "genus": None, "genusKey": None, "family": None, "familyKey": None,
          "order": None, "orderKey": None, "class": None, "classKey": None, "phylum": None, "phylumKey": None,
          "kingdom": None, "kingdomKey": None}, "taxon_unresolved"),
        ({}, "ok"),
    ],
)
def test_each_occurrence_flag(mock_http, grid, changes, flag):
    row = rows_for(mock_http, grid, edited(INATURALIST_RECORD, **changes)).loc[INATURALIST_RECORD]

    assert row["quality_flag"] == flag


def test_an_absence_is_kept_and_not_flagged(mock_http, grid):
    row = rows_for(mock_http, grid, edited(INATURALIST_RECORD, occurrenceStatus="ABSENT")).loc[INATURALIST_RECORD]

    assert (row["occurrence_status"], row["quality_flag"]) == ("absent", "ok")


@pytest.mark.parametrize(
    "changes, reason",
    [
        ({"gbifID": None, "key": None}, "gbifID"),
        ({"decimalLatitude": None}, "coordinates"),
        ({"eventDate": None, "year": None, "month": None, "day": None}, "time"),
        ({"license": "http://creativecommons.org/licenses/by-sa/4.0/legalcode"}, "license"),
    ],
)
def test_a_record_that_needs_a_guess_quarantines_the_item(mock_http, grid, changes, reason):
    result, archive = fetched(mock_http, edited(INATURALIST_RECORD, **changes))

    with pytest.raises(QuarantineError, match=reason):
        normalize(result.manifests[0], archive.store, grid)


def test_a_mortality_dataset_gives_mortality_events(mock_http, grid):
    roadkill = sorted(MORTALITY_DATASET_KEYS)[0]
    pool = [{**record, "datasetKey": roadkill} for record in records()[:1]]
    mock_http(gbif_server(pool))
    archive = Archive()

    [manifest] = fetch_gbif_occurrence(request(item=roadkill), archive).manifests
    row = normalize(manifest, archive.store, grid).table.to_pylist()[0]

    assert row["event_type"] == "wildlife_mortality"
    assert row["available_at"] == datetime.fromisoformat(pool[0]["lastCrawled"])


def test_the_record_limit_bounds_the_pages_and_warns(mock_http):
    searches = []
    mock_http(gbif_server(records(), count=5000, searches=searches))

    result = fetch_gbif_occurrence(request(max_records=1000, taxon_keys=(2441105, 5229154)), Archive())

    pages = [params for params in searches if params["limit"] != "0"]
    assert len(pages) == 4
    assert [params["limit"] for params in pages] == ["300", "300", "300", "100"]
    assert all(params.get_list("taxonKey") == ["2441105", "5229154"] for params in searches)
    assert searches[0]["geometry"] == "POLYGON((36.7 -1.6,37.2 -1.6,37.2 -1.25,36.7 -1.25,36.7 -1.6))"
    assert searches[0]["eventDate"] == "2012-01-01,2013-12-31"
    assert any("1000 of 5000" in warning for warning in result.warnings)


@pytest.mark.parametrize("changes", [{"max_records": 0}, {"max_records": 10_001}, {"item": "../dataset"}])
def test_an_invalid_request_makes_no_call(mock_http, changes):
    calls = mock_http(lambda request: httpx.Response(500))

    result = fetch_gbif_occurrence(request(**changes), Archive())

    assert [error.code for error in result.errors] == ["invalid_request"] and calls == []


def test_no_records_gives_a_warning_and_no_manifest(mock_http):
    result, _ = fetched(mock_http, [])

    assert result.manifests == [] and "no GBIF records" in result.warnings[0]


def test_the_same_record_in_two_searches_is_one_current_row(database, mock_http, grid):
    store = SeriesStore(database, grid)
    archive = Archive()
    mock_http(gbif_server(records()))
    first = fetch_gbif_occurrence(request(), archive).manifests[0]
    mock_http(gbif_server(records()[:3]))
    second = fetch_gbif_occurrence(request(taxon_keys=(5229154,)), archive).manifests[0]

    for manifest in (first, second):
        store.append_batch(series_id(manifest, grid), manifest, normalize(manifest, archive.store, grid))

    current = database.execute(
        "SELECT source_record_id, source_item_id FROM current_point_events ORDER BY source_record_id"
    ).fetchall()
    assert len(current) == 8
    assert {item for record, item in current if record == EBIRD_RECORD} == {second.extensions.source_item_id}

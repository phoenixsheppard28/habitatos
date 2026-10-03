from datetime import UTC, datetime

import httpx

from habitat import config
from habitat.archive import Archive
from habitat.contracts import RawManifest
from habitat.fetch.connectors import ConnectorRequest
from habitat.fetch.connectors.movebank_study import (
    ACCOUNT_SCOPE,
    check_movebank_access,
    fetch_movebank_study,
    inspect_movebank,
    search_movebank,
)
from habitat.normalize.router import normalize
from habitat.sources import SOURCES

ALBATROSS = "movebank:2911040"
PREVIEW = {
    "individuals": [
        {
            "individual_local_identifier": "bird-1",
            "individual_taxon_canonical_name": "Phoebastria irrorata",
            "locations": [
                {"timestamp": 1212240602001, "location_long": -89.62, "location_lat": -1.38},
                {"timestamp": 1212244202001, "location_long": -89.61, "location_lat": -1.37},
            ],
        }
    ]
}
DIRECT_READ_CSV = (
    "event_id,individual_local_identifier,individual_taxon_canonical_name,timestamp,location_long,location_lat\n"
    "1001,bird-1,Phoebastria irrorata,2008-05-31 13:30:02.001,-89.62,-1.38\n"
    "1002,bird-1,Phoebastria irrorata,2008-05-31 14:30:02.001,-89.61,-1.37\n"
)


def without_credentials():
    config.configure(movebank_username=None, movebank_password=None)


def with_credentials():
    config.configure(movebank_username="someone", movebank_password="secret")


def test_search_finds_the_public_albatross_study():
    without_credentials()

    assert any(hit["dataset_id"] == ALBATROSS for hit in search_movebank("albatross galapagos"))


def test_inspect_known_public_study_skips_the_network():
    info = inspect_movebank(ALBATROSS)

    assert info["found"] and "Phoebastria" in info["species"][0]


def test_credentials_do_not_prove_permission():
    with_credentials()

    assert check_movebank_access(ALBATROSS)["status"] == "unknown"


def test_public_preview_is_a_csv_that_normalizes_without_quarantine(mock_http, grid):
    without_credentials()
    mock_http(lambda request: httpx.Response(200, json=PREVIEW))
    archive = Archive()

    [manifest] = fetch_movebank_study(ConnectorRequest(item=ALBATROSS), archive).manifests

    assert RawManifest.model_validate(manifest.model_dump(mode="json")) == manifest
    assert manifest.storage.uri.startswith("artifact://movebank-study-2911040-public_preview/")
    assert manifest.storage.format == SOURCES["movebank_study"].storage_format
    assert manifest.extensions.properties["movebank_download_mode"] == "public_preview"
    assert manifest.access_scope == "public"
    assert manifest.extensions.time_start == datetime(2008, 5, 31, 13, 30, 2, 1000, tzinfo=UTC)

    batch = normalize(manifest, archive.store, grid)
    rows = batch.table.to_pylist()
    assert len(rows) == 2
    assert rows[0]["entity_id"] == "movebank:2911040:bird-1"
    assert rows[0]["source_record_id"].startswith("synthetic:")


def test_authenticated_csv_keeps_event_ids_and_is_account_scoped(mock_http, grid):
    with_credentials()
    requests = mock_http(lambda request: httpx.Response(200, text=DIRECT_READ_CSV))
    archive = Archive()

    [manifest] = fetch_movebank_study(ConnectorRequest(item=ALBATROSS), archive).manifests

    assert requests[0].headers["authorization"].startswith("Basic ")
    assert manifest.access_scope == ACCOUNT_SCOPE
    assert manifest.extensions.properties["movebank_download_mode"] == "full_csv"
    rows = normalize(manifest, archive.store, grid).table.to_pylist()
    assert [row["source_record_id"] for row in rows] == ["1001", "1002"]


def test_empty_preview_is_not_success(mock_http):
    without_credentials()
    mock_http(lambda request: httpx.Response(200, json={"individuals": []}))

    result = fetch_movebank_study(ConnectorRequest(item=ALBATROSS), Archive())

    assert [e.code for e in result.errors] == ["empty"]


def test_login_page_is_not_csv(mock_http):
    with_credentials()
    mock_http(lambda request: httpx.Response(200, text="<html>login</html>"))

    result = fetch_movebank_study(ConnectorRequest(item=ALBATROSS), Archive())

    assert [e.code for e in result.errors] == ["restricted"]


def test_license_terms_are_reported(mock_http):
    with_credentials()
    mock_http(lambda request: httpx.Response(403, text="License Terms: accept first"))

    result = fetch_movebank_study(ConnectorRequest(item=ALBATROSS), Archive())

    assert "license" in result.errors[0].message.lower()


def test_unknown_study_without_credentials_is_restricted():
    without_credentials()

    result = fetch_movebank_study(ConnectorRequest(item="movebank:123"), Archive())

    assert [e.code for e in result.errors] == ["restricted"]

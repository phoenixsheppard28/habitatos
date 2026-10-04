from datetime import UTC, datetime

import httpx

from fixtures import movebank_package
from habitat import config
from habitat.archive import Archive
from habitat.contracts import RawManifest
from habitat.fetch.connectors import ConnectorRequest
from habitat.fetch.connectors.movebank_repository import fetch_movebank_repository
from habitat.normalize.router import normalize
from habitat.sources import SOURCES

REQUEST = ConnectorRequest(item=movebank_package.PACKAGE_UUID)


def test_package_becomes_one_artifact_with_locations_and_reference(mock_http, grid):
    mock_http(movebank_package.handler())
    archive = Archive()

    [manifest] = fetch_movebank_repository(REQUEST, archive).manifests

    assert RawManifest.model_validate(manifest.model_dump(mode="json")) == manifest
    assert manifest.storage.uri == "artifact://10255_move.1095_Wildebeest/1"
    assert manifest.storage.format == SOURCES["movebank_repository"].storage_format
    assert manifest.extensions.assets == {"locations": "Wildebeest.csv", "reference": "Wildebeest-reference-data.csv"}
    assert manifest.extensions.properties["study_id"] == "208413731"
    batch = normalize(manifest, archive.store, grid)
    assert batch.table.num_rows == 4
    assert {e.local_identifier: e.sex for e in batch.entities} == {"Naboisho": "f", "Olope": "m"}


def test_a_newer_package_takes_date_license_and_citation_from_its_other_fields(mock_http):
    mock_http(movebank_package.handler(newer_metadata=True))

    [manifest] = fetch_movebank_repository(REQUEST, Archive()).manifests

    assert manifest.extensions.available_at == datetime(2026, 5, 21, tzinfo=UTC)
    assert manifest.rights.license == movebank_package.NEWER_METADATA["dc.rights.uri"]
    assert manifest.rights.attribution == movebank_package.NEWER_METADATA["mdr.citation.CSE"]


def test_a_remote_name_cannot_escape_the_archive(mock_http, tmp_path):
    mock_http(movebank_package.handler("../../../../evil.csv"))
    archive = Archive()

    [manifest] = fetch_movebank_repository(REQUEST, archive).manifests

    assert manifest.extensions.assets["locations"] == "evil.csv"
    data_dir = config.settings().data_dir
    assert all(p.resolve().is_relative_to(data_dir) for p in tmp_path.rglob("*") if p.is_file())
    assert not (tmp_path.parent / "evil.csv").exists()


def test_second_fetch_uses_the_cache(mock_http):
    requests = mock_http(movebank_package.handler())
    archive = Archive()

    first = fetch_movebank_repository(REQUEST, archive).manifests
    downloads = len([r for r in requests if r.url.path.endswith("/content")])
    second = fetch_movebank_repository(REQUEST, archive).manifests

    assert first == second
    assert len([r for r in requests if r.url.path.endswith("/content")]) == downloads


def test_a_package_id_must_be_a_uuid(mock_http):
    calls = mock_http(lambda request: httpx.Response(500))

    result = fetch_movebank_repository(ConnectorRequest(item="../items"), Archive())

    assert [e.code for e in result.errors] == ["invalid_request"] and calls == []

import httpx

from habitat.archive import Archive
from habitat.contracts import RawManifest
from habitat.fetch.connectors import ConnectorRequest
from habitat.fetch.connectors.zenodo import fetch_zenodo, inspect_zenodo, search_zenodo
from habitat.normalize.router import normalize
from habitat.normalize.rows import QuarantineError

RECORD = {
    "id": 1,
    "revision": 3,
    "metadata": {
        "title": "Elephant GPS", "description": "D", "license": {"id": "cc-by-4.0"}, "publication_date": "2023-05-01",
    },
    "files": [
        {"key": "../../tracks.csv", "size": 12, "links": {"self": "https://zenodo.org/api/records/1/files/tracks.csv/content"}},
        {"key": "huge.zip", "size": 10**10, "links": {"self": "https://zenodo.org/api/records/1/files/huge.zip/content"}},
    ],
    "links": {"html": "https://zenodo.org/records/1"},
}


def zenodo(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/records":
        return httpx.Response(200, json={"hits": {"hits": [{"id": 999, "metadata": {"title": "GPS tracks"}}]}})
    if request.url.path == "/api/records/1":
        return httpx.Response(200, json=RECORD)
    if request.url.path.endswith("/content"):
        return httpx.Response(200, text="a,b\n1,2\n")
    return httpx.Response(404)


def test_search_parses_hits(mock_http):
    mock_http(zenodo)

    assert [hit["dataset_id"] for hit in search_zenodo("gps")] == ["zenodo:999"]


def test_inspect_lists_files(mock_http):
    mock_http(zenodo)

    info = inspect_zenodo("zenodo:1")

    assert info["found"] and info["rights"]["license"] == "cc-by-4.0"


def test_download_uses_a_safe_name_and_is_quarantined_without_mapping(mock_http, grid):
    mock_http(zenodo)
    archive = Archive()

    [manifest] = fetch_zenodo(ConnectorRequest(item="zenodo:1"), archive).manifests

    assert RawManifest.model_validate(manifest.model_dump(mode="json")) == manifest
    assert manifest.storage.uri == "artifact://zenodo-1/1"
    assert manifest.extensions.kind == "tabular"
    assert manifest.extensions.assets == {"data": "tracks.csv"}
    try:
        normalize(manifest, archive.store, grid)
    except QuarantineError as error:
        assert "no canonical mapping" in str(error)
    else:
        raise AssertionError("zenodo has no normalizer")

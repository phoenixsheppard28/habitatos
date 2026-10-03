from __future__ import annotations

from unittest.mock import patch

from fetch.catalog import search_catalog
from fetch.connectors.zenodo import inspect_zenodo


def test_search_catalog_offline_by_default() -> None:
    hits = search_catalog("zenodo elephant", include_internet=False)
    assert all(not h.get("dataset_id", "").startswith("zenodo:") for h in hits)


@patch("fetch.connectors.zenodo.fetch_json")
def test_search_zenodo_parses_hits(mock_fetch) -> None:
    mock_fetch.return_value = {
        "hits": {
            "hits": [
                {
                    "id": 999,
                    "metadata": {"title": "GPS tracks", "description": "test"},
                    "links": {"html": "https://zenodo.org/records/999"},
                }
            ]
        }
    }
    hits = search_catalog("gps", include_internet=True, include_zenodo=True)
    zenodo = [h for h in hits if h.get("dataset_id") == "zenodo:999"]
    assert len(zenodo) == 1


@patch("fetch.connectors.zenodo.fetch_json")
def test_inspect_zenodo(mock_fetch) -> None:
    mock_fetch.return_value = {
        "metadata": {"title": "T", "description": "D", "license": {"id": "cc-by-4.0"}},
        "files": [{"key": "a.csv", "size": 100, "links": {"self": "https://example.com/a.csv"}}],
        "links": {"html": "https://zenodo.org/records/1"},
    }
    info = inspect_zenodo("zenodo:1")
    assert info["found"] is True
    assert info["files"][0]["filename"] == "a.csv"


def record(access='open'):
    return {'metadata': {'title': 'Tracks', 'license': {'id': 'cc-by-4.0'}, 'access_right': access},
            'files': [{'key':'tracks.csv', 'size':100, 'links':{'self':'https://zenodo.org/records/1/files/tracks.csv'}},
                      {'key':'large.tif', 'size':20*1024*1024, 'links':{'self':'https://zenodo.org/records/1/files/large.tif'}}],
            'links': {'html':'https://zenodo.org/records/1'}}


def test_zenodo_download_stores_original_and_metadata(isolated_data_dir):
    from fetch.connectors.zenodo import download_zenodo
    from fetch.archive import resolve_artifact_path
    with patch('fetch.connectors.zenodo.fetch_json', return_value=record()), patch('fetch.connectors.zenodo.fetch_bytes', return_value=b'original,data\n'):
        manifest = download_zenodo('zenodo:1')
    assert manifest.storage.format == 'csv'
    assert manifest.source.study_id == '1'
    assert manifest.rights.license == 'cc-by-4.0'
    assert next(resolve_artifact_path(manifest).iterdir()).read_bytes() == b'original,data\n'


def test_requested_file_is_never_silently_replaced(isolated_data_dir):
    from fetch.connectors.zenodo import download_zenodo
    with patch('fetch.connectors.zenodo.fetch_json', return_value=record()), patch('fetch.connectors.zenodo.fetch_bytes') as transfer:
        assert download_zenodo('zenodo:1', prefer_filename='missing.csv')['code'] == 'file_not_found'
        assert download_zenodo('zenodo:1', prefer_filename='large.tif')['code'] == 'no_suitable_file'
    transfer.assert_not_called()


def test_restricted_record_cannot_be_downloaded(isolated_data_dir):
    from fetch.connectors.zenodo import check_zenodo_access, download_zenodo
    with patch('fetch.connectors.zenodo.fetch_json', return_value=record('restricted')), patch('fetch.connectors.zenodo.fetch_bytes') as transfer:
        assert check_zenodo_access('zenodo:1')['status'] == 'restricted'
        assert download_zenodo('zenodo:1')['code'] == 'restricted'
    transfer.assert_not_called()


def test_zenodo_transfer_error_is_structured(isolated_data_dir):
    from fetch.connectors.zenodo import download_zenodo
    with patch('fetch.connectors.zenodo.fetch_json', return_value=record()), patch('fetch.connectors.zenodo.fetch_bytes', side_effect=TimeoutError('offline')):
        assert download_zenodo('zenodo:1')['code'] == 'download_failed'

from __future__ import annotations

from unittest.mock import patch

from fetch.connectors.movebank import (
    download_movebank,
    inspect_movebank,
    search_movebank,
)
from fetch.catalog import search_catalog


def test_search_movebank_finds_albatross() -> None:
    hits = search_movebank("albatross galapagos")
    assert any(h.get("dataset_id") == "movebank:2911040" for h in hits)


def test_catalog_movebank_before_zenodo_order() -> None:
    with patch("fetch.catalog.search_zenodo", return_value=[{"dataset_id": "zenodo:1"}]):
        with patch(
            "fetch.catalog.search_movebank",
            return_value=[{"dataset_id": "movebank:2911040"}],
        ):
            hits = search_catalog("birds", include_internet=True)
    assert hits[0]["dataset_id"] == "movebank:2911040"


def test_inspect_movebank_known_public_skips_network() -> None:
    info = inspect_movebank("movebank:2911040")
    assert info["found"] is True
    assert "Phoebastria" in info["species"][0]


@patch("fetch.connectors.movebank.fetch_json")
def test_download_public_preview(mock_json, isolated_data_dir) -> None:
    mock_json.return_value = {
        "individuals": [
            {
                "individual_local_identifier": "bird-1",
                "individual_taxon_canonical_name": "Test species",
                "locations": [
                    {"timestamp": 1, "location_long": 1.0, "location_lat": 2.0},
                ],
            }
        ]
    }
    result = download_movebank("movebank:2911040")
    assert hasattr(result, "artifact_id")
    assert result.extensions.get("movebank_download_mode") == "public_preview"


def test_empty_preview_is_not_success(isolated_data_dir, monkeypatch):
    for key in ['MOVEBANK_USERNAME', 'MOVEBANK_PASSWORD', 'MOVEBANK_USER', 'MOVEBANK_PASS']:
        monkeypatch.delenv(key, raising=False)
    with patch('fetch.connectors.movebank.fetch_json', return_value={'individuals': []}):
        result = download_movebank('movebank:2911040')
    assert result['code'] == 'empty'


def test_credentials_do_not_prove_permission(monkeypatch):
    from fetch.connectors.movebank import check_movebank_access
    monkeypatch.setenv('MOVEBANK_USERNAME', 'test')
    monkeypatch.setenv('MOVEBANK_PASSWORD', 'test')
    assert check_movebank_access('movebank:2911040')['status'] == 'unknown'


def test_login_html_is_not_csv(isolated_data_dir, monkeypatch):
    monkeypatch.setenv('MOVEBANK_USERNAME', 'test')
    monkeypatch.setenv('MOVEBANK_PASSWORD', 'test')
    with patch('fetch.connectors.movebank._download_authenticated_gps_csv', return_value=b'<html>login</html>'):
        result = download_movebank('movebank:2911040')
    assert result['status'] == 'error'

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

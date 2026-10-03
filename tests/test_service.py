from unittest.mock import patch

import httpx
import pytest

from habitat.fetch import catalog, service
from habitat.fetch.connectors import chirps, stac

BBOX = [10, -2, 11, -1]


def test_discovery_downloads_nothing(mock_http):
    calls = mock_http(lambda request: httpx.Response(500))

    result = service.fetch_environment(BBOX, "2024-02-28", "2024-03-01", sources=["chirps"], discover_only=True)

    assert result["status"] == "discovered"
    assert [x["item"] for x in result["discovered"]] == [
        "chirps-v2.0.2024.02.28", "chirps-v2.0.2024.02.29", "chirps-v2.0.2024.03.01",
    ]
    assert calls == []
    assert service.list_downloaded_files() == []


def test_a_failed_source_does_not_stop_the_others(monkeypatch):
    def failing_search(*args, **kwargs):
        raise TimeoutError()

    monkeypatch.setattr(stac, "search_items", failing_search)

    result = service.fetch_environment(BBOX, "2025-01-01", "2025-01-01", sources=["modis_mod13q1", "chirps"], discover_only=True)

    assert any(x["source_id"] == "chirps" for x in result["discovered"])
    assert any("modis_mod13q1" in warning for warning in result["warnings"])


def test_old_source_names_are_rejected():
    with pytest.raises(ValueError):
        service.fetch_environment(BBOX, "2025-01-01", "2025-01-01", sources=["modis"])


def test_invalid_area_is_rejected_before_network(mock_http):
    calls = mock_http(lambda request: httpx.Response(500))

    with pytest.raises(ValueError):
        service.fetch_environment([0, 0, 0, 1], "2025-01-01", "2025-01-02")
    assert calls == []


def test_movebank_comes_before_zenodo():
    with patch.object(catalog, "search_zenodo", return_value=[{"dataset_id": "zenodo:1"}]), \
            patch.object(catalog, "search_repository", return_value=[]), \
            patch.object(catalog, "search_movebank", return_value=[{"dataset_id": "movebank:2911040"}]):
        hits = catalog.search_catalog("birds", include_internet=True, include_zenodo=True)

    assert [hit["dataset_id"] for hit in hits] == ["movebank:2911040", "zenodo:1"]


def test_search_is_offline_by_default():
    hits = catalog.search_catalog("zenodo elephant", include_internet=False)

    assert all(not hit.get("dataset_id", "").startswith("zenodo:") for hit in hits)


@pytest.mark.parametrize("dataset_id, expected", [
    ("movebank:2911040", ("movebank_study", "movebank:2911040")),
    ("movebank-repository:5b6706c8-e7e5-46e4-82ba-da5a82324298", ("movebank_repository", "5b6706c8-e7e5-46e4-82ba-da5a82324298")),
    ("zenodo:12", ("zenodo", "zenodo:12")),
    ("fixture-movement-001", ("fixture", "fixture-movement-001")),
    ("nothing", None),
])
def test_dataset_ids_resolve_to_a_source(dataset_id, expected):
    assert service.resolve_dataset(dataset_id) == expected


def test_unknown_dataset_is_an_error_object():
    assert service.download_dataset("nothing")["code"] == "not_found"


def test_chirps_item_ids_are_stable():
    from datetime import date

    assert chirps.item_id(date(2024, 2, 29)) == "chirps-v2.0.2024.02.29"

"""Catalog search over the supported sources."""

from typing import Any

from habitat.fetch.connectors.fixture import search_fixtures
from habitat.fetch.connectors.movebank_repository import search_data_packages
from habitat.fetch.connectors.movebank_study import search_movebank
from habitat.fetch.connectors.zenodo import search_zenodo

REPOSITORY_PREFIX = "movebank-repository:"


def search_repository(query: str) -> list[dict[str, Any]]:
    try:
        packages = search_data_packages(query, size=5)
    except Exception as error:
        return [{"error": True, "source_id": "movebank_repository", "message": type(error).__name__}]

    return [
        {
            "dataset_id": f"{REPOSITORY_PREFIX}{package['uuid']}",
            "source_id": "movebank_repository",
            "title": package["title"],
            "description": f"Published Movebank data package (study {package['study_id']}).",
            "species": [package["taxon"]] if package["taxon"] else [],
            "data_kind": "animal_locations",
        }
        for package in packages
    ]


def search_catalog(
    query: str,
    species: list[str] | None = None,
    *,
    include_internet: bool = False,
    include_zenodo: bool = False,
) -> list[dict[str, Any]]:
    """Search fixtures; with include_internet also Movebank studies and packages; Zenodo (slower) on request."""
    results = search_fixtures(query, species)
    if include_internet and query.strip():
        results.extend(search_movebank(query))
        results.extend(search_repository(query))
        if include_zenodo:
            results.extend(search_zenodo(query))
    return results

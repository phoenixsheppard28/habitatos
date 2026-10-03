"""Catalog search over supported sources (fixture MVP)."""

from __future__ import annotations

from fetch.connectors.fixture import FIXTURE_CATALOG
from fetch.connectors.movebank import search_movebank
from fetch.connectors.zenodo import search_zenodo


def _search_fixtures(query: str, species: list[str] | None) -> list[dict]:
    tokens = [t for t in query.lower().split() if t]
    species_filter = {s.lower() for s in (species or [])}
    results: list[dict] = []

    for entry in FIXTURE_CATALOG:
        haystack = " ".join(
            [
                entry.dataset_id,
                entry.title,
                entry.description,
                entry.data_kind,
                " ".join(entry.species),
            ]
        ).lower()
        if tokens and not all(token in haystack for token in tokens):
            continue
        if species_filter:
            if not entry.species:
                continue
            if not species_filter.intersection({s.lower() for s in entry.species}):
                continue
        results.append(
            {
                "dataset_id": entry.dataset_id,
                "title": entry.title,
                "description": entry.description,
                "species": entry.species,
                "data_kind": entry.data_kind,
                "source_name": entry.source.name,
            }
        )
    return results


def search_catalog(
    query: str,
    species: list[str] | None = None,
    *,
    include_internet: bool = False,
    include_zenodo: bool = False,
) -> list[dict]:
    """Search fixtures; optional Movebank (fast index) and Zenodo (slower API)."""
    results = _search_fixtures(query, species)
    if include_internet and query.strip():
        results.extend(search_movebank(query))
        if include_zenodo:
            results.extend(search_zenodo(query))
    return results

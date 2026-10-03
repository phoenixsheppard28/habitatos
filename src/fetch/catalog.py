"""Catalog search over supported sources (fixture MVP)."""

from __future__ import annotations

from fetch.connectors.fixture import FIXTURE_CATALOG


def search_catalog(query: str, species: list[str] | None = None) -> list[dict]:
    """
    Search known dataset entries. Plain Python — safe to call from tests without an LLM.
    """
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

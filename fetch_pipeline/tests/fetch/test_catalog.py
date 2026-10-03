from __future__ import annotations

from fetch.catalog import search_catalog


def test_search_finds_movement_fixture() -> None:
    hits = search_catalog("antelope movement", species=["example-antelope"])
    ids = {h["dataset_id"] for h in hits}
    assert "fixture-movement-001" in ids


def test_search_respects_species_filter() -> None:
    hits = search_catalog("rainfall", species=["example-antelope"])
    assert hits == []

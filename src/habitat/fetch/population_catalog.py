"""Catalog search over the population sources. Both sources are fixed, so the search needs no network."""

from typing import Any

from habitat.fetch.connectors.literature_counts import catalog_entries as literature_entries
from habitat.fetch.connectors.ogutu_kenya_rangelands import catalog_entry as ogutu_entry

SEARCH_WORDS = "population counts estimates census survey abundance numbers"


def search_population_sources(
    query: str, species: list[str] | None = None, bbox: list[float] | None = None
) -> list[dict[str, Any]]:
    """Entries that contain every query word, name a wanted species, and overlap the bbox when they have one."""
    words = [word for word in query.lower().split() if word]
    wanted = [name.lower() for name in species or []]
    hits = []
    for entry in [ogutu_entry(), *literature_entries()]:
        names = [name.lower() for name in [*entry["species"], *entry.get("common_names", [])]]
        if not all(word in searchable_text(entry) for word in words):
            continue
        if wanted and not any(want in name for want in wanted for name in names):
            continue
        if bbox and entry.get("bbox") and not overlaps(entry["bbox"], bbox):
            continue
        hits.append(entry)
    return hits


def searchable_text(entry: dict[str, Any]) -> str:
    parts = [
        entry["dataset_id"], entry["title"], entry["description"], entry.get("region", ""), entry["data_kind"],
        *entry["species"], *entry.get("common_names", []), *entry.get("areas", []), SEARCH_WORDS,
    ]
    return " ".join(parts).lower()


def overlaps(first: list[float], second: list[float]) -> bool:
    west, south, east, north = first
    other_west, other_south, other_east, other_north = second
    return west <= other_east and other_west <= east and south <= other_north and other_south <= north

"""Bounded point event retrieval for the fetch agent: GBIF occurrences and NASA FIRMS fires."""

from dataclasses import replace
from datetime import date
from typing import Any

from habitat.catalog.taxa import resolve_taxon
from habitat.fetch.connectors import ConnectorRequest, validate_area_and_dates
from habitat.fetch.service import run_connector
from habitat.normalize.sources.gbif_occurrence import MORTALITY_DATASET_KEYS

EVENT_SOURCES = {
    "species_occurrence": ("gbif_occurrence",),
    "wildlife_mortality": ("gbif_occurrence",),
    "active_fire": ("firms_modis", "firms_viirs"),
}
LIMITATIONS = {
    "species_occurrence": "GBIF records are presence-only: a missing record is not an absence, and observer effort "
                          "is uneven.",
    "wildlife_mortality": "Roadkill records are presence-only and follow the roads that observers drive.",
    "active_fire": "FIRMS has no cloud mask: clouds hide fires, so a cell without a detection is not a cell "
                   "without fire.",
}


def fetch_events(
    bbox: list[float],
    start: str,
    end: str,
    event_types: list[str],
    species: list[str] | None = None,
    max_records: int = 1000,
    max_days: int = 31,
) -> dict[str, Any]:
    unknown = set(event_types) - set(EVENT_SOURCES)
    if not event_types or unknown:
        raise ValueError(f"event_types must be some of {sorted(EVENT_SOURCES)}; got {sorted(event_types)}")

    request = ConnectorRequest(
        bbox=tuple(bbox), start=date.fromisoformat(start), end=date.fromisoformat(end),
        max_records=max_records, max_days=max_days,
    )
    validate_area_and_dates(request)

    resolutions = [resolve_taxon(name) for name in species or []]
    unresolved = [resolution for resolution in resolutions if resolution.status != "resolved"]
    if unresolved:
        return {
            "status": "species_not_resolved",
            "species": [
                {"name": r.name, "status": r.status, "candidates": [c.model_dump() for c in r.candidates]}
                for r in unresolved
            ],
            "message": "Ask the user to choose a species. Nothing was fetched.",
        }

    request = replace(request, taxon_keys=tuple(taxon.gbif_key for r in resolutions for taxon in r.taxa))
    runs = connector_runs(event_types, request)

    artifacts, warnings = [], []
    for source_id, source_request in runs:
        result = run_connector(source_id, source_request)
        artifacts.extend(result.manifests)
        warnings.extend(result.warnings)
        warnings.extend(f"{source_id}: {error.message}" for error in result.errors)

    status = ("partial" if warnings else "ok") if artifacts else "insufficient_data"
    return {
        "status": status,
        "raw_artifacts": [
            {"artifact_id": a.artifact_id, "source_id": a.extensions.source_id, "storage": a.storage.uri,
             "coverage": a.coverage.model_dump(mode="json")}
            for a in artifacts
        ],
        "warnings": warnings,
        "limitations": [LIMITATIONS[event_type] for event_type in sorted(set(event_types))],
    }


def connector_runs(event_types: list[str], request: ConnectorRequest) -> list[tuple[str, ConnectorRequest]]:
    runs = []
    if "species_occurrence" in event_types:
        runs.append(("gbif_occurrence", request))
    if "wildlife_mortality" in event_types:
        runs += [("gbif_occurrence", replace(request, item=key)) for key in sorted(MORTALITY_DATASET_KEYS)]
    if "active_fire" in event_types:
        runs += [(source_id, request) for source_id in EVENT_SOURCES["active_fire"]]
    return runs

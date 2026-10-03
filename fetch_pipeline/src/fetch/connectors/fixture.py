"""Deterministic fixture source for local development and tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fetch.archive import register_raw_artifact_from_path, save_manifest
from fetch.models import Coverage, RawManifest, Rights, SourceRef

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "tests" / "fetch" / "fixtures"


@dataclass(frozen=True)
class CatalogEntry:
    dataset_id: str
    title: str
    description: str
    species: list[str]
    data_kind: str
    fixture_filename: str
    source: SourceRef
    rights: Rights
    bbox: list[float] | None
    start: str | None
    end: str | None


FIXTURE_CATALOG: list[CatalogEntry] = [
    CatalogEntry(
        dataset_id="fixture-movement-001",
        title="Demo antelope tracks (fixture)",
        description="Small CSV of animal movement tracks (timestamped locations) for integration tests.",
        species=["example-antelope"],
        data_kind="animal_locations",
        fixture_filename="sample_tracks.csv",
        source=SourceRef(
            name="fixture",
            url="https://example.org/studies/demo-antelope",
            study_id="study-demo-1",
        ),
        rights=Rights(
            license="fixture-only",
            retention_allowed=True,
            reuse_allowed=True,
            attribution="Habitat Watch demo fixture",
        ),
        bbox=[36.0, -2.0, 37.0, -1.0],
        start="2025-01-01T00:00:00Z",
        end="2025-01-07T23:59:59Z",
    ),
    CatalogEntry(
        dataset_id="fixture-rainfall-001",
        title="Demo rainfall grid (fixture)",
        description="Daily rainfall cells for overlap tests with movement data.",
        species=[],
        data_kind="rainfall_observations",
        fixture_filename="sample_rainfall.csv",
        source=SourceRef(
            name="fixture",
            url="https://example.org/rain/demo-grid",
            study_id="rain-demo-1",
        ),
        rights=Rights(
            license="fixture-only",
            retention_allowed=True,
            reuse_allowed=True,
            attribution="Habitat Watch demo fixture",
        ),
        bbox=[36.0, -2.0, 37.0, -1.0],
        start="2025-01-01T00:00:00Z",
        end="2025-01-07T23:59:59Z",
    ),
]


def get_catalog_entry(dataset_id: str) -> CatalogEntry | None:
    for entry in FIXTURE_CATALOG:
        if entry.dataset_id == dataset_id:
            return entry
    return None


def inspect_fixture(dataset_id: str) -> dict:
    entry = get_catalog_entry(dataset_id)
    if entry is None:
        return {"found": False, "dataset_id": dataset_id}
    return {
        "found": True,
        "dataset_id": entry.dataset_id,
        "title": entry.title,
        "description": entry.description,
        "species": entry.species,
        "data_kind": entry.data_kind,
        "coverage": {
            "bbox": entry.bbox,
            "start": entry.start,
            "end": entry.end,
        },
        "source": entry.source.model_dump(),
        "rights": entry.rights.model_dump(),
    }


def check_fixture_access(dataset_id: str) -> dict:
    entry = get_catalog_entry(dataset_id)
    if entry is None:
        return {
            "dataset_id": dataset_id,
            "status": "not_found",
            "message": "Unknown dataset id.",
        }
    return {
        "dataset_id": dataset_id,
        "status": "available",
        "retention_allowed": entry.rights.retention_allowed,
        "reuse_allowed": entry.rights.reuse_allowed,
        "license": entry.rights.license,
    }


def download_fixture(dataset_id: str) -> RawManifest | dict:
    entry = get_catalog_entry(dataset_id)
    if entry is None:
        return {
            "status": "error",
            "code": "not_found",
            "message": f"No fixture dataset {dataset_id!r}.",
        }

    src = FIXTURES_DIR / entry.fixture_filename
    if not src.is_file():
        return {
            "status": "error",
            "code": "missing_fixture_file",
            "message": f"Fixture file missing: {src.name}",
        }

    coverage = Coverage(
        species=list(entry.species),
        bbox=entry.bbox,
        start=entry.start,
        end=entry.end,
    )
    source_key = f"fixture:{entry.dataset_id}:{entry.fixture_filename}"
    manifest = register_raw_artifact_from_path(
        src_path=src,
        source=entry.source,
        coverage=coverage,
        rights=entry.rights,
        source_key=source_key,
        file_format="csv",
    )
    manifest.extensions["data_kind"] = entry.data_kind
    save_manifest(manifest)
    return manifest

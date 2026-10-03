"""Deterministic synthetic datasets for local development and tests."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from habitat.archive import Archive
from habitat.archive.index import AlreadyIngested, never_ingested
from habitat.config import PROJECT_ROOT
from habitat.contracts import BBox, Coverage, RawManifest, Rights, SourceItem, SourceRef, TimePrecision
from habitat.fetch.connectors import ConnectorRequest, ConnectorResult

FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures"
PRODUCT = "habitat-fixture"
STORAGE_FORMAT = "csv"
FIXTURE_RIGHTS = Rights(
    license="fixture-only", retention_allowed=True, reuse_allowed=True, attribution="Habitat Watch demo fixture"
)


@dataclass(frozen=True)
class CatalogEntry:
    dataset_id: str
    title: str
    description: str
    species: list[str]
    data_kind: str
    fixture_filename: str
    source: SourceRef
    bbox: BBox
    start: datetime
    end: datetime


FIXTURE_CATALOG: list[CatalogEntry] = [
    CatalogEntry(
        dataset_id="fixture-movement-001",
        title="Demo antelope tracks (fixture)",
        description="Small CSV of animal movement tracks (timestamped locations) for integration tests.",
        species=["example-antelope"],
        data_kind="animal_locations",
        fixture_filename="sample_tracks.csv",
        source=SourceRef(name="fixture", url="https://example.org/studies/demo-antelope", study_id="study-demo-1"),
        bbox=(36.0, -2.0, 37.0, -1.0),
        start=datetime(2025, 1, 1, tzinfo=UTC),
        end=datetime(2025, 1, 7, 23, 59, 59, tzinfo=UTC),
    ),
    CatalogEntry(
        dataset_id="fixture-rainfall-001",
        title="Demo rainfall grid (fixture)",
        description="Daily rainfall cells for overlap tests with movement data.",
        species=[],
        data_kind="rainfall_observations",
        fixture_filename="sample_rainfall.csv",
        source=SourceRef(name="fixture", url="https://example.org/rain/demo-grid", study_id="rain-demo-1"),
        bbox=(36.0, -2.0, 37.0, -1.0),
        start=datetime(2025, 1, 1, tzinfo=UTC),
        end=datetime(2025, 1, 7, 23, 59, 59, tzinfo=UTC),
    ),
]


def get_catalog_entry(dataset_id: str) -> CatalogEntry | None:
    return next((entry for entry in FIXTURE_CATALOG if entry.dataset_id == dataset_id), None)


def search_fixtures(query: str, species: list[str] | None) -> list[dict[str, Any]]:
    tokens = [token for token in query.lower().split() if token]
    wanted_species = {name.lower() for name in species or []}
    results = []
    for entry in FIXTURE_CATALOG:
        haystack = " ".join([entry.dataset_id, entry.title, entry.description, entry.data_kind, *entry.species]).lower()
        if tokens and not all(token in haystack for token in tokens):
            continue
        if wanted_species and not wanted_species & {name.lower() for name in entry.species}:
            continue
        results.append({
            "dataset_id": entry.dataset_id,
            "source_id": "fixture",
            "title": entry.title,
            "description": entry.description,
            "species": entry.species,
            "data_kind": entry.data_kind,
        })
    return results


def inspect_fixture(dataset_id: str) -> dict[str, Any]:
    entry = get_catalog_entry(dataset_id)
    if entry is None:
        return {"found": False, "dataset_id": dataset_id}

    return {
        "found": True,
        "dataset_id": entry.dataset_id,
        "source_id": "fixture",
        "title": entry.title,
        "description": entry.description,
        "species": entry.species,
        "data_kind": entry.data_kind,
        "coverage": {"bbox": list(entry.bbox), "start": entry.start.isoformat(), "end": entry.end.isoformat()},
        "source": entry.source.model_dump(),
        "rights": FIXTURE_RIGHTS.model_dump(),
    }


def check_fixture_access(dataset_id: str) -> dict[str, Any]:
    if get_catalog_entry(dataset_id) is None:
        return {"dataset_id": dataset_id, "status": "not_found", "message": "Unknown dataset id."}

    return {
        "dataset_id": dataset_id,
        "status": "available",
        "retention_allowed": True,
        "reuse_allowed": True,
        "license": FIXTURE_RIGHTS.license,
    }


def fetch_fixture(
    request: ConnectorRequest, archive: Archive, already_ingested: AlreadyIngested = never_ingested
) -> ConnectorResult:
    result = ConnectorResult()
    entry = get_catalog_entry(request.item or "")
    if entry is None:
        return result.fail("not_found", f"No fixture dataset {request.item!r}.")

    path = FIXTURES_DIR / entry.fixture_filename
    if not path.is_file():
        return result.fail("missing_fixture_file", f"Fixture file missing: {path.name}")

    source_key = f"fixture:{entry.dataset_id}:{entry.fixture_filename}"
    if already_ingested(entry.dataset_id, "1", "final"):
        return result
    if (cached := archive.cached(source_key)) is not None:
        result.manifests.append(cached)
        return result

    retrieved_at = datetime.now(UTC)
    version, stored = archive.put(entry.dataset_id, {path.name: path}, STORAGE_FORMAT)
    manifest = RawManifest(
        artifact_id=entry.dataset_id,
        version=version,
        created_at=retrieved_at,
        access_scope=request.access_scope,
        source=entry.source,
        storage=stored.storage,
        checksum=stored.checksum,
        retrieved_at=retrieved_at,
        coverage=Coverage(species=entry.species, bbox=entry.bbox, start=entry.start, end=entry.end),
        rights=FIXTURE_RIGHTS,
        extensions=SourceItem(
            source_id="fixture",
            product=PRODUCT,
            source_item_id=entry.dataset_id,
            source_key=source_key,
            kind="tabular",
            time_start=entry.start,
            time_end=entry.end,
            time_precision=TimePrecision.INSTANT,
            available_at=entry.start,
            processing_version="1",
            assets={"data": path.name},
            properties={"data_kind": entry.data_kind, "synthetic": True},
        ),
    )
    result.manifests.append(archive.record(manifest))
    return result

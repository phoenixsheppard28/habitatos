"""Deterministic fetch operations: the connectors and the archive of the current session."""

from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from habitat.contracts import RawManifest
from habitat.fetch import session
from habitat.fetch.catalog import REPOSITORY_PREFIX, search_catalog
from habitat.fetch.connectors import ConnectorRequest, ConnectorResult, validate_area_and_dates
from habitat.fetch.connectors import chirps, stac
from habitat.fetch.connectors.fixture import check_fixture_access, get_catalog_entry, inspect_fixture
from habitat.fetch.connectors.literature_counts import DATASET_PREFIX as LITERATURE_PREFIX
from habitat.fetch.connectors.literature_counts import check_literature_access, inspect_literature
from habitat.fetch.connectors.movebank_study import check_movebank_access, inspect_movebank
from habitat.fetch.connectors.ogutu_kenya_rangelands import DATASET_ID as OGUTU_DATASET_ID
from habitat.fetch.connectors.ogutu_kenya_rangelands import check_ogutu_access, inspect_ogutu
from habitat.fetch.connectors.zenodo import check_zenodo_access, inspect_zenodo
from habitat.sources import get_source

ENVIRONMENT_SOURCES = ("sentinel2", "modis_mod13q1", "chirps")


def resolve_dataset(dataset_id: str) -> tuple[str, str] | None:
    """The source id and item of a catalog dataset id."""
    if dataset_id.startswith("movebank:"):
        return "movebank_study", dataset_id
    if dataset_id.startswith(REPOSITORY_PREFIX):
        return "movebank_repository", dataset_id.removeprefix(REPOSITORY_PREFIX)
    if dataset_id.startswith("zenodo:"):
        return "zenodo", dataset_id
    if dataset_id == OGUTU_DATASET_ID:
        return "ogutu_kenya_rangelands", dataset_id
    if dataset_id.startswith(LITERATURE_PREFIX):
        return "literature_counts", dataset_id
    if get_catalog_entry(dataset_id) is not None:
        return "fixture", dataset_id
    return None


def inspect_source(dataset_id: str) -> dict[str, Any]:
    resolved = resolve_dataset(dataset_id)
    if resolved is None:
        return {"found": False, "dataset_id": dataset_id}

    source_id, item = resolved
    if source_id == "movebank_study":
        return inspect_movebank(item)
    if source_id == "zenodo":
        return inspect_zenodo(item)
    if source_id == "fixture":
        return inspect_fixture(item)
    if source_id == "ogutu_kenya_rangelands":
        return inspect_ogutu(item)
    if source_id == "literature_counts":
        return inspect_literature(item)
    return {"found": True, "dataset_id": dataset_id, "source_id": source_id, "package": item}


def check_access(dataset_id: str) -> dict[str, Any]:
    resolved = resolve_dataset(dataset_id)
    if resolved is None:
        return {"dataset_id": dataset_id, "status": "not_found", "message": "Unknown dataset id."}

    source_id, item = resolved
    if source_id == "movebank_study":
        return check_movebank_access(item)
    if source_id == "zenodo":
        return check_zenodo_access(item)
    if source_id == "fixture":
        return check_fixture_access(item)
    if source_id == "ogutu_kenya_rangelands":
        return check_ogutu_access(item)
    if source_id == "literature_counts":
        return check_literature_access(item)
    return {"dataset_id": dataset_id, "status": "available", "license": "see package metadata"}


def run_connector(source_id: str, request: ConnectorRequest) -> ConnectorResult:
    result = get_source(source_id).fetch(request, session.archive())
    for manifest in result.manifests:
        session.record(manifest)
    for error in result.errors:
        session.record({"status": "error", "message": f"{source_id}: {error.message}"})
    for warning in result.warnings:
        session.record({"status": "unavailable", "message": warning})
    return result


def download_dataset(dataset_id: str) -> RawManifest | dict[str, Any]:
    resolved = resolve_dataset(dataset_id)
    if resolved is None:
        error = {"status": "error", "code": "not_found", "message": f"Unknown dataset id {dataset_id!r}."}
        session.record(error)
        return error

    source_id, item = resolved
    result = run_connector(source_id, ConnectorRequest(item=item))
    if result.manifests:
        return result.manifests[0]
    if result.errors:
        error = result.errors[0]
        return {"status": "error", "code": error.code, "message": error.message}
    return {"status": "error", "code": "empty", "message": "; ".join(result.warnings) or "Nothing was downloaded."}


def list_downloaded_files() -> list[str]:
    return session.archive().store.list_files()


def fetch_environment(
    bbox: list[float],
    start: str,
    end: str,
    *,
    sources: list[str] | None = None,
    max_items: int = 1,
    max_days: int = 3,
    discover_only: bool = False,
) -> dict[str, Any]:
    """Bounded Sentinel-2, MODIS Terra and CHIRPS retrieval for one area and an inclusive date range."""
    sources = list(dict.fromkeys(sources or ENVIRONMENT_SOURCES))
    unknown = set(sources) - set(ENVIRONMENT_SOURCES)
    if unknown:
        raise ValueError(f"sources must be some of {', '.join(ENVIRONMENT_SOURCES)}; got {sorted(unknown)}")

    request = ConnectorRequest(
        bbox=tuple(bbox), start=date.fromisoformat(start), end=date.fromisoformat(end),
        max_items=max_items, max_days=max_days,
    )
    validate_area_and_dates(request)
    if discover_only:
        return discover_environment(request, sources)

    artifacts, warnings, errors = [], [], []
    for source_id in sources:
        result = run_connector(source_id, request)
        artifacts.extend(result.manifests)
        warnings.extend(result.warnings)
        errors.extend(f"{source_id}: {error.message}" for error in result.errors)

    status = ("partial" if warnings or errors else "ok") if artifacts else "insufficient_data"
    return {
        "status": status,
        "raw_artifacts": [
            {"artifact_id": a.artifact_id, "source_id": a.extensions.source_id, "storage": a.storage.uri,
             "coverage": a.coverage.model_dump(mode="json")}
            for a in artifacts
        ],
        "warnings": warnings + errors,
        "limitations": ["Satellite rasters are clipped to the bbox; scene selection does not guarantee full coverage."],
    }


def discover_environment(request: ConnectorRequest, sources: list[str]) -> dict[str, Any]:
    start = datetime.combine(request.start, time.min, tzinfo=UTC)
    end = datetime.combine(request.end, time.max, tzinfo=UTC)
    found, warnings = [], []
    for source_id in sources:
        try:
            if source_id == "chirps":
                days = min((request.end - request.start).days + 1, request.max_days)
                found += [{"source_id": "chirps", "item": chirps.item_id(request.start + timedelta(d))} for d in range(days)]
            elif source_id == "sentinel2":
                found += [{"source_id": source_id, "item": i.id} for i in stac.search_sentinel2(request.bbox, start, end)]
            else:
                found += [{"source_id": source_id, "item": i.id} for i in stac.search_modis_terra(request.bbox, start, end)]
        except Exception as error:
            warnings.append(f"{source_id}: catalog search failed ({type(error).__name__})")
    return {"status": "discovered" if found else "insufficient_data", "discovered": found, "warnings": warnings}


__all__ = [
    "search_catalog",
    "inspect_source",
    "check_access",
    "download_dataset",
    "list_downloaded_files",
    "fetch_environment",
]

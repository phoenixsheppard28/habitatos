"""Deterministic fetch operations (connectors + archive)."""

from __future__ import annotations

from typing import Any

from fetch.archive import list_downloaded_files, register_raw_artifact_from_path
from fetch.catalog import search_catalog
from fetch.connectors.fixture import (
    check_fixture_access,
    download_fixture,
    inspect_fixture,
)
from fetch.models import RawManifest


def inspect_source(dataset_id: str) -> dict[str, Any]:
    info = inspect_fixture(dataset_id)
    if info.get("found"):
        return info
    return {"found": False, "dataset_id": dataset_id}


def check_access(dataset_id: str) -> dict[str, Any]:
    return check_fixture_access(dataset_id)


def download_dataset(dataset_id: str) -> RawManifest | dict[str, Any]:
    result = download_fixture(dataset_id)
    if isinstance(result, RawManifest):
        return result
    return result


def register_raw_artifact_from_local_file(
    path: str,
    source_name: str,
    source_url: str,
    study_id: str | None = None,
    source_key: str | None = None,
) -> RawManifest:
    """Register an on-disk file into the archive (admin / connector helper)."""
    from pathlib import Path

    from fetch.models import Coverage, Rights, SourceRef

    p = Path(path)
    key = source_key or f"manual:{p.resolve()}"
    return register_raw_artifact_from_path(
        src_path=p,
        source=SourceRef(name=source_name, url=source_url, study_id=study_id),
        coverage=Coverage(),
        rights=Rights(license=None, retention_allowed=None, reuse_allowed=None),
        source_key=key,
    )


__all__ = [
    "search_catalog",
    "inspect_source",
    "check_access",
    "download_dataset",
    "list_downloaded_files",
    "register_raw_artifact_from_local_file",
]

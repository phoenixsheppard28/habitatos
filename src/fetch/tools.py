"""Agent-facing tools — thin wrappers over fetch.service."""

from __future__ import annotations

import json

from agents import function_tool

from fetch import service


@function_tool
def list_downloaded_files() -> list[str]:
    """List files currently stored in the local raw data archive."""
    return service.list_downloaded_files()


@function_tool
def search_catalog(query: str, species: list[str] | None = None) -> str:
    """
    Search supported dataset catalogs for entries matching a natural-language query.
    Optional species filter restricts to datasets tagged with those species.
    """
    results = service.search_catalog(query, species=species)
    return json.dumps(results, indent=2)


@function_tool
def inspect_source(dataset_id: str) -> str:
    """Return metadata, coverage, and rights for a catalog dataset id."""
    return json.dumps(service.inspect_source(dataset_id), indent=2)


@function_tool
def check_access(dataset_id: str) -> str:
    """Check whether a dataset can be downloaded under its license and access rules."""
    return json.dumps(service.check_access(dataset_id), indent=2)


@function_tool
def download_dataset(dataset_id: str) -> str:
    """
    Download a dataset by id into the raw archive and return its RawManifest JSON.
    On failure returns a JSON error object instead of raising.
    """
    result = service.download_dataset(dataset_id)
    if hasattr(result, "model_dump"):
        return json.dumps(result.model_dump(mode="json"), indent=2, default=str)
    return json.dumps(result, indent=2)


FETCH_AGENT_TOOLS = [
    list_downloaded_files,
    search_catalog,
    inspect_source,
    check_access,
    download_dataset,
]

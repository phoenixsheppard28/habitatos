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
def search_catalog(
    query: str,
    species: list[str] | None = None,
    include_internet: bool = True,
    include_zenodo: bool = False,
) -> str:
    """
    Search fixtures and Movebank when include_internet is true.
    Set include_zenodo true only if Movebank has no match (Zenodo is slower).
    """
    results = service.search_catalog(
        query,
        species=species,
        include_internet=include_internet,
        include_zenodo=include_zenodo,
    )
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


@function_tool
def fetch_environment(
    bbox: list[float], start: str, end: str,
    sources: list[str] | None = None, max_items: int = 1, max_days: int = 3,
    discover_only: bool = False,
) -> str:
    """Fetch raw satellite and rainfall files for a WGS84 bbox and inclusive YYYY-MM-DD dates.

    Sources: sentinel-2, modis (Terra), chirps. Includes quality layers. Requires a
    resolved region and dates: never guess them. Default bounds: 1 scene/product,
    3 rainfall days, 2 GiB total, 512 MiB/file. Discovery alone downloads no data.
    """
    from fetch.connectors.environment import fetch_environment as retrieve
    try:
        result = retrieve(bbox, start, end, sources=sources, max_items=max_items,
                          max_days=max_days, discover_only=discover_only)
        # Keep large provider metadata out of LLM context; archive retains it.
        result["outcomes"] = [{k: v for k, v in item.items() if k in ("dataset_id", "status", "code")}
                              for item in result["outcomes"]]
        result["raw_artifacts"] = [{"artifact_id": a["artifact_id"], "storage": a["storage"], "coverage": a["coverage"]}
                                   for a in result["raw_artifacts"]]
        return json.dumps(result)
    except ValueError as exc:
        from fetch.session import record
        error = {"status": "error", "message": str(exc)}
        record(error)
        return json.dumps(error)


FETCH_AGENT_TOOLS = [
    list_downloaded_files,
    search_catalog,
    inspect_source,
    check_access,
    download_dataset,
    fetch_environment,
]

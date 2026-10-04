"""Agent-facing tools — thin wrappers over habitat.fetch.service."""

import json

from anthropic import beta_tool

from habitat.fetch import service, session


@beta_tool
def list_downloaded_files() -> str:
    """List files currently stored in the local raw data archive."""
    return json.dumps(service.list_downloaded_files())


@beta_tool
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


@beta_tool
def inspect_source(dataset_id: str) -> str:
    """Return metadata, coverage, and rights for a catalog dataset id."""
    return json.dumps(service.inspect_source(dataset_id), indent=2)


@beta_tool
def check_access(dataset_id: str) -> str:
    """Check whether a dataset can be downloaded under its license and access rules."""
    return json.dumps(service.check_access(dataset_id), indent=2)


@beta_tool
def download_dataset(dataset_id: str) -> str:
    """
    Download a dataset by id into the raw archive and return its RawManifest JSON.
    On failure returns a JSON error object instead of raising.
    """
    result = service.download_dataset(dataset_id)
    if hasattr(result, "model_dump"):
        return json.dumps(result.model_dump(mode="json"), indent=2, default=str)
    return json.dumps(result, indent=2)


@beta_tool
def fetch_environment(
    bbox: list[float],
    start: str,
    end: str,
    sources: list[str] | None = None,
    max_items: int = 1,
    max_days: int = 3,
    discover_only: bool = False,
) -> str:
    """Fetch raw satellite and rainfall files for a WGS84 bbox and inclusive YYYY-MM-DD dates.

    Sources: sentinel2, modis_mod13q1 (Terra), chirps. Includes quality layers. Requires a
    resolved region and dates: never guess them. Default bounds: 1 scene/product,
    3 rainfall days, 2 GiB total, 512 MiB/file. Rasters are clipped to the bbox.
    Discovery alone downloads no data.
    Habitat degradation sources, only when named: landsat_c2_l2 (30 m surface reflectance and
    vegetation, 1982 to now), esa_cci_lc (land_cover, 1992-2020), io_lulc_annual (land_cover,
    2017-2023), modis_mcd64a1 (fire_observations, monthly burned area). One land cover item is
    one year and tile, so ask for max_items per year. Landsat gives about 2 scenes per month.
    """
    try:
        result = service.fetch_environment(
            bbox, start, end, sources=sources, max_items=max_items, max_days=max_days, discover_only=discover_only
        )
        return json.dumps(result)
    except ValueError as error:
        failure = {"status": "error", "message": str(error)}
        session.record(failure)
        return json.dumps(failure)


FETCH_AGENT_TOOLS = [
    list_downloaded_files,
    search_catalog,
    inspect_source,
    check_access,
    download_dataset,
    fetch_environment,
]

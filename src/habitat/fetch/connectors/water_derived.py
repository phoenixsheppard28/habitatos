from habitat.archive import Archive
from habitat.archive.index import AlreadyIngested, never_ingested
from habitat.fetch.connectors import ConnectorRequest, ConnectorResult

PRODUCT = "water-derived"
STORAGE_FORMAT = "json"


def fetch_water_derived(
    request: ConnectorRequest, archive: Archive, already_ingested: AlreadyIngested = never_ingested
) -> ConnectorResult:
    """Nothing to download: the pipeline derives these values after it ingests the water sources."""
    return ConnectorResult().fail(
        "derived_source",
        "water_derived is computed from site_features and JRC monthly water; fetch osm_overpass, wpdx or "
        "jrc_gsw_monthly, or run python -m habitat.derive.water",
    )

from habitat.archive import Archive
from habitat.archive.index import AlreadyIngested, never_ingested
from habitat.fetch.connectors import ConnectorRequest, ConnectorResult

ANNUAL_SOURCE_ID = "vegetation_annual_derived"
ANNUAL_PRODUCT = "vegetation-annual"
ANNUAL_MAPPING_VERSION = "vegetation-annual-v1"

TREND_SOURCE_ID = "vegetation_trend_derived"
TREND_PRODUCT = "vegetation-trend"
TREND_MAPPING_VERSION = "vegetation-trend-v1"

DERIVED_FORMAT = "json"


def fetch_derived(
    request: ConnectorRequest, archive: Archive, already_ingested: AlreadyIngested = never_ingested
) -> ConnectorResult:
    """A derived source downloads nothing. `habitat.derive.vegetation` computes it from stored series."""
    return ConnectorResult().fail(
        "derived_source", "this source is computed from stored series; use derive_habitat_indicators"
    )

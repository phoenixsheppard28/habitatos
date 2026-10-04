from datetime import datetime

import pystac

from habitat.archive import Archive
from habitat.archive.index import AlreadyIngested, never_ingested
from habitat.contracts import BBox, Rights, TimePrecision
from habitat.fetch.connectors import ConnectorRequest, ConnectorResult, stac
from habitat.fetch.connectors.stac import fetch_scenes, modis_processing_version, modis_production_time, published_at

SOURCE_ID = "modis_mcd64a1"
COLLECTION = "modis-64A1-061"
PRODUCT = "mcd64a1-061"
ITEM_PREFIX = "MCD64A1."
ASSETS = {"burn_date": "Burn_Date"}

RIGHTS = Rights(
    license="NASA EOSDIS open data",
    retention_allowed=True,
    reuse_allowed=True,
    attribution="Giglio, L. et al. MCD64A1 MODIS/Terra+Aqua Burned Area Monthly L3 Global 500m SIN Grid V061, "
                "NASA LP DAAC",
)


def search_mcd64a1(bbox: BBox, start: datetime, end: datetime) -> list[pystac.Item]:
    return [item for item in stac.search_items(COLLECTION, bbox, start, end) if item.id.startswith(ITEM_PREFIX)]


def describe_mcd64a1(item: pystac.Item) -> dict:
    return {
        "source_id": SOURCE_ID,
        "product": PRODUCT,
        "source_name": "MODIS MCD64A1 v061 burned area (Planetary Computer)",
        "rights": RIGHTS,
        "time_start": datetime.fromisoformat(item.properties["start_datetime"]),
        "time_end": datetime.fromisoformat(item.properties["end_datetime"]),
        "precision": TimePrecision.COMPOSITE,
        "available_at": published_at(item) or modis_production_time(item),
        "processing_version": modis_processing_version(item),
        "properties": {"platform": item.properties.get("platform")},
    }


def fetch_mcd64a1(
    request: ConnectorRequest, archive: Archive, already_ingested: AlreadyIngested = never_ingested
) -> ConnectorResult:
    return fetch_scenes(
        request, archive, already_ingested, source_id=SOURCE_ID,
        search=search_mcd64a1, describe=describe_mcd64a1, asset_names=ASSETS,
    )

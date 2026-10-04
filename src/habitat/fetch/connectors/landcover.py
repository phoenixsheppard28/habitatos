import math
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
import pystac

from habitat.archive import Archive
from habitat.archive.index import AlreadyIngested, never_ingested
from habitat.contracts import BBox, Rights, TimePrecision
from habitat.fetch import http
from habitat.fetch.connectors import ConnectorRequest, ConnectorResult, stac
from habitat.fetch.connectors.stac import fetch_scenes, published_at

ESA_CCI_SOURCE_ID = "esa_cci_lc"
ESA_CCI_COLLECTION = "esa-cci-lc"
ESA_CCI_PRODUCT = "esa-cci-lc"
ESA_CCI_ASSETS = {"lccs_class": "lccs_class", "processed_flag": "processed_flag"}

IO_LULC_SOURCE_ID = "io_lulc_annual"
IO_LULC_COLLECTION = "io-lulc-annual-v02"
IO_LULC_PRODUCT = "io-lulc-annual-v02"
IO_LULC_ASSETS = {"data": "data"}
IO_LULC_VERSION = "v02"

ESA_CCI_RIGHTS = Rights(
    license="ESA CCI Land Cover licence (Planetary Computer: proprietary); confirm reuse before publication",
    retention_allowed=True,
    reuse_allowed=True,
    attribution="ESA Climate Change Initiative - Land Cover led by UCLouvain; Copernicus Climate Change Service",
)
IO_LULC_RIGHTS = Rights(
    license="CC-BY-4.0",
    retention_allowed=True,
    reuse_allowed=True,
    attribution="Impact Observatory, Esri and Microsoft, 10m Annual Land Use Land Cover (9-class) V2",
)


def interval(item: pystac.Item) -> tuple[datetime, datetime]:
    return (
        datetime.fromisoformat(item.properties["start_datetime"]),
        datetime.fromisoformat(item.properties["end_datetime"]),
    )


def describe_esa_cci_lc(item: pystac.Item) -> dict:
    start, end = interval(item)
    return {
        "source_id": ESA_CCI_SOURCE_ID,
        "product": ESA_CCI_PRODUCT,
        "source_name": "ESA CCI Land Cover (Planetary Computer)",
        "rights": ESA_CCI_RIGHTS,
        "time_start": start,
        "time_end": end,
        "precision": TimePrecision.COMPOSITE,
        "available_at": published_at(item),
        "processing_version": item.properties.get("esa_cci_lc:version"),
        "properties": {"tile": item.properties.get("esa_cci_lc:tile")},
    }


def blob_created_at(href: str) -> datetime | None:
    """When the provider wrote the file to Planetary Computer storage. The IO LULC items have no `created` date."""
    stac.check_asset_href(href)
    try:
        with http.client() as client:
            response = client.head(href)
            response.raise_for_status()
    except httpx.HTTPError:
        return None

    headers = response.headers

    value = headers.get("x-ms-creation-time") or headers.get("last-modified")
    return parsedate_to_datetime(value).astimezone(UTC) if value else None


def describe_io_lulc(item: pystac.Item, created_at: Callable[[str], datetime | None] = blob_created_at) -> dict:
    start, end = interval(item)
    return {
        "source_id": IO_LULC_SOURCE_ID,
        "product": IO_LULC_PRODUCT,
        "source_name": "Impact Observatory annual LULC V2 (Planetary Computer)",
        "rights": IO_LULC_RIGHTS,
        "time_start": start,
        "time_end": end,
        "precision": TimePrecision.COMPOSITE,
        "available_at": created_at(item.assets["data"].href) if "data" in item.assets else None,
        "processing_version": IO_LULC_VERSION,
        "properties": {"tile": tile_id(item)},
    }


def tile_id(item: pystac.Item) -> str:
    return item.id.split("-")[0]


def utm_zones(bbox: BBox) -> set[int]:
    west, _, east, _ = bbox
    first, last = (math.floor((longitude + 180) / 6) + 1 for longitude in (west, min(east, 179.999999)))
    return set(range(first, last + 1))


def search_esa_cci_lc(bbox: BBox, start: datetime, end: datetime) -> list[pystac.Item]:
    return stac.search_items(ESA_CCI_COLLECTION, bbox, start, end)


def search_io_lulc(bbox: BBox, start: datetime, end: datetime) -> list[pystac.Item]:
    # Tiles of UTM zones 1 and 60 have a footprint of -180 to 180 degrees, so a search anywhere returns them.
    zones = utm_zones(bbox)
    return [
        item for item in stac.search_items(IO_LULC_COLLECTION, bbox, start, end)
        if int(tile_id(item)[:-1]) in zones
    ]


def fetch_esa_cci_lc(
    request: ConnectorRequest, archive: Archive, already_ingested: AlreadyIngested = never_ingested
) -> ConnectorResult:
    return fetch_scenes(
        request, archive, already_ingested, source_id=ESA_CCI_SOURCE_ID,
        search=search_esa_cci_lc, describe=describe_esa_cci_lc, asset_names=ESA_CCI_ASSETS,
    )


def fetch_io_lulc(
    request: ConnectorRequest, archive: Archive, already_ingested: AlreadyIngested = never_ingested
) -> ConnectorResult:
    descriptions = {}

    def describe(item: pystac.Item) -> dict:
        if item.id not in descriptions:
            descriptions[item.id] = describe_io_lulc(item, blob_created_at)
        return descriptions[item.id]

    return fetch_scenes(
        request, archive, already_ingested, source_id=IO_LULC_SOURCE_ID,
        search=search_io_lulc, describe=describe, asset_names=IO_LULC_ASSETS,
    )

from datetime import datetime

import pystac

from habitat.archive import Archive
from habitat.archive.index import AlreadyIngested, never_ingested
from habitat.contracts import BBox, Rights, TimePrecision
from habitat.fetch.connectors import ConnectorRequest, ConnectorResult, stac
from habitat.fetch.connectors.stac import fetch_scenes, published_at

SOURCE_ID = "landsat_c2_l2"
COLLECTION = "landsat-c2-l2"
PRODUCT = "landsat-c2-l2"
ASSETS = {"blue": "blue", "red": "red", "nir08": "nir08", "swir16": "swir16", "qa_pixel": "qa_pixel"}

RIGHTS = Rights(
    license="USGS Landsat data policy (public domain)",
    retention_allowed=True,
    reuse_allowed=True,
    attribution="Landsat Level-2 Surface Reflectance courtesy of the U.S. Geological Survey",
)


def search_landsat(bbox: BBox, start: datetime, end: datetime, max_cloud: float = 80.0) -> list[pystac.Item]:
    return stac.search_items(COLLECTION, bbox, start, end, max_cloud)


def reflectance_scale(item: pystac.Item) -> tuple[float | None, float | None]:
    """Scale and offset of the red band from the STAC `raster:bands`. The normalizer checks them."""
    asset = item.assets.get("red")
    bands = asset.extra_fields.get("raster:bands", []) if asset else []
    if not bands:
        return None, None

    return bands[0].get("scale"), bands[0].get("offset")


def describe_landsat(item: pystac.Item) -> dict:
    properties = item.properties
    collection_number = properties.get("landsat:collection_number")
    collection_category = properties.get("landsat:collection_category")
    scale, offset = reflectance_scale(item)
    return {
        "source_id": SOURCE_ID,
        "product": PRODUCT,
        "source_name": "Landsat Collection 2 Level-2 (Planetary Computer)",
        "rights": RIGHTS,
        "time_start": item.datetime,
        "time_end": item.datetime,
        "precision": TimePrecision.INSTANT,
        "available_at": published_at(item),
        "processing_version": f"{collection_number}.{collection_category}",
        "properties": {
            "platform": properties.get("platform"),
            "wrs_path": properties.get("landsat:wrs_path"),
            "wrs_row": properties.get("landsat:wrs_row"),
            "cloud_cover_land": properties.get("landsat:cloud_cover_land"),
            "collection_number": collection_number,
            "collection_category": collection_category,
            "reflectance_scale": scale,
            "reflectance_offset": offset,
        },
    }


def fetch_landsat(
    request: ConnectorRequest, archive: Archive, already_ingested: AlreadyIngested = never_ingested
) -> ConnectorResult:
    return fetch_scenes(
        request, archive, already_ingested, source_id=SOURCE_ID,
        search=lambda bbox, start, end: search_landsat(bbox, start, end, request.cloud_cover),
        describe=describe_landsat, asset_names=ASSETS,
    )

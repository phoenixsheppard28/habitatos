"""Copernicus Global Land Service lake water quality (300 m), from the Digital Earth Africa STAC catalog.

The asset files are public COGs in the S3 bucket `deafrica-input-datasets` (af-south-1). An unsigned HTTPS read works.
"""

from datetime import datetime

import pystac
from pystac_client import Client

from habitat.archive import Archive
from habitat.archive.index import AlreadyIngested, never_ingested
from habitat.contracts import BBox, Rights, TimePrecision
from habitat.fetch.connectors import ConnectorRequest, ConnectorResult
from habitat.fetch.connectors.stac import fetch_scenes, published_at

DEAFRICA_STAC = "https://explorer.digitalearth.africa/stac"
COLLECTIONS = ("cgls_lwq300_2002_2012", "cgls_lwq300_2016_2024")
PRODUCT = "cgls-lwq300"
S3_BUCKET = "deafrica-input-datasets"
S3_HTTPS_ROOT = f"https://{S3_BUCKET}.s3.af-south-1.amazonaws.com/"
ASSETS = {"turbidity": "turbidity_mean", "trophic_state_index": "trophic_state_index"}
RIGHTS = Rights(
    license="CC-BY-4.0",
    retention_allowed=True,
    reuse_allowed=True,
    attribution="Copernicus Global Land Service lake water quality, Plymouth Marine Laboratory and Brockmann "
                "Consult, via Digital Earth Africa",
)


def https_href(href: str) -> str:
    prefix = f"s3://{S3_BUCKET}/"
    if href.startswith(S3_HTTPS_ROOT):
        return href
    if not href.startswith(prefix):
        raise ValueError(f"asset {href!r} is not in the bucket {S3_BUCKET}")
    return S3_HTTPS_ROOT + href.removeprefix(prefix)


def search_cgls_lwq(bbox: BBox, start: datetime, end: datetime) -> list[pystac.Item]:
    catalog = Client.open(DEAFRICA_STAC)
    search = catalog.search(collections=list(COLLECTIONS), bbox=bbox, datetime=f"{start.isoformat()}/{end.isoformat()}")
    items = list(search.items())
    for item in items:
        for name in ASSETS.values():
            if name in item.assets:
                item.assets[name].href = https_href(item.assets[name].href)
    return items


def describe_cgls_lwq(item: pystac.Item) -> dict:
    return {
        "source_id": "cgls_lwq",
        "product": PRODUCT,
        "source_name": "CGLS lake water quality 300 m (Digital Earth Africa)",
        "rights": RIGHTS,
        "time_start": datetime.fromisoformat(item.properties["start_datetime"].replace("Z", "+00:00")),
        "time_end": datetime.fromisoformat(item.properties["end_datetime"].replace("Z", "+00:00")),
        "precision": TimePrecision.COMPOSITE,
        "available_at": published_at(item),
        "processing_version": item.properties["odc:dataset_version"],
        "properties": {
            "platform": item.properties.get("platform"), "region_code": item.properties.get("odc:region_code"),
        },
    }


def fetch_cgls_lwq(
    request: ConnectorRequest, archive: Archive, already_ingested: AlreadyIngested = never_ingested
) -> ConnectorResult:
    return fetch_scenes(
        request, archive, already_ingested, source_id="cgls_lwq",
        search=lambda bbox, start, end: search_cgls_lwq(bbox, start, end),
        describe=describe_cgls_lwq, asset_names=ASSETS,
    )

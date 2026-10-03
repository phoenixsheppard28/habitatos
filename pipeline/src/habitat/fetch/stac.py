from datetime import UTC, datetime

import planetary_computer
import pystac
from pystac_client import Client

from habitat.contracts import (
    BBox,
    Coverage,
    SourceItem,
    RawManifest,
    Rights,
    SourceRef,
    StorageRef,
    TimePrecision,
)
from habitat.fetch.archive import RawArchive

PLANETARY_COMPUTER_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"

SENTINEL2_PRODUCT = "sentinel-2-l2a"
MODIS_PRODUCT = "mod13q1-061"

SENTINEL2_ASSETS = {"green": "B03", "red": "B04", "nir": "B08", "swir16": "B11", "scl": "SCL"}
MODIS_ASSETS = {
    "ndvi": "250m_16_days_NDVI",
    "evi": "250m_16_days_EVI",
    "pixel_reliability": "250m_16_days_pixel_reliability",
}

SENTINEL2_RIGHTS = Rights(
    license="Copernicus Sentinel data terms",
    retention_allowed=True,
    reuse_allowed=True,
    attribution="Contains modified Copernicus Sentinel data",
)
MODIS_RIGHTS = Rights(
    license="NASA EOSDIS open data",
    retention_allowed=True,
    reuse_allowed=True,
    attribution="Didan, K. MOD13Q1 MODIS/Terra Vegetation Indices 16-Day L3 Global 250m SIN Grid V061, NASA LP DAAC",
)

# From processing baseline 04.00 (25 January 2022) L2A digital numbers carry an offset of -1000.
# https://sentinels.copernicus.eu/web/sentinel/-/copernicus-sentinel-2-major-products-upgrade-upcoming
SENTINEL2_OFFSET_BASELINE = (4, 0)
SENTINEL2_BOA_ADD_OFFSET = -1000.0


def search_items(collection: str, bbox: BBox, start: datetime, end: datetime, max_cloud: float | None = None):
    catalog = Client.open(PLANETARY_COMPUTER_STAC, modifier=planetary_computer.sign_inplace)
    query = {"eo:cloud_cover": {"lt": max_cloud}} if max_cloud is not None else None
    search = catalog.search(
        collections=[collection], bbox=bbox, datetime=f"{start.isoformat()}/{end.isoformat()}", query=query
    )
    return list(search.items())


def search_sentinel2(bbox: BBox, start: datetime, end: datetime, max_cloud: float = 80.0) -> list[pystac.Item]:
    return search_items("sentinel-2-l2a", bbox, start, end, max_cloud)


def search_modis_terra(bbox: BBox, start: datetime, end: datetime) -> list[pystac.Item]:
    # The collection mixes Terra (MOD13Q1) and Aqua (MYD13Q1); their 16-day windows are offset by 8 days.
    return [item for item in search_items("modis-13Q1-061", bbox, start, end) if item.id.startswith("MOD13Q1.")]


def boa_add_offset(processing_baseline: str) -> float:
    major, minor = (int(part) for part in processing_baseline.split("."))
    return SENTINEL2_BOA_ADD_OFFSET if (major, minor) >= SENTINEL2_OFFSET_BASELINE else 0.0


def archive_assets(item: pystac.Item, asset_names: dict[str, str], archive: RawArchive) -> tuple[dict[str, str], str]:
    local = {}
    checksums = []
    for canonical, remote in asset_names.items():
        archived = archive.download(item.assets[remote].href, item.id, f"{remote}.tif")
        local[canonical] = str(archived.path)
        checksums.append(archived.checksum)
    return local, ",".join(checksums)


def sentinel2_manifest(item: pystac.Item, archive: RawArchive, access_scope: str = "public") -> RawManifest:
    assets, checksum = archive_assets(item, SENTINEL2_ASSETS, archive)
    retrieved_at = datetime.now(UTC)
    baseline = item.properties["s2:processing_baseline"]

    return RawManifest(
        artifact_id=item.id,
        version="1",
        created_at=retrieved_at,
        access_scope=access_scope,
        source=SourceRef(name="Sentinel-2 L2A (Planetary Computer)", url=item.get_self_href()),
        storage=StorageRef(uri=str(archive.root / item.id), format="cog"),
        checksum=checksum,
        retrieved_at=retrieved_at,
        coverage=Coverage(bbox=tuple(item.bbox), start=item.datetime, end=item.datetime),
        rights=SENTINEL2_RIGHTS,
        extensions=SourceItem(
            source_id="sentinel2",
            product=SENTINEL2_PRODUCT,
            source_item_id=item.id,
            time_start=item.datetime,
            time_end=item.datetime,
            time_precision=TimePrecision.INSTANT,
            available_at=published_at(item, retrieved_at),
            processing_version=baseline,
            assets=assets,
            properties={
                "boa_add_offset": boa_add_offset(baseline),
                "mgrs_tile": item.properties.get("s2:mgrs_tile"),
                "cloud_cover": item.properties.get("eo:cloud_cover"),
            },
        ),
    )


def modis_manifest(item: pystac.Item, archive: RawArchive, access_scope: str = "public") -> RawManifest:
    assets, checksum = archive_assets(item, MODIS_ASSETS, archive)
    retrieved_at = datetime.now(UTC)
    start = datetime.fromisoformat(item.properties["start_datetime"])
    end = datetime.fromisoformat(item.properties["end_datetime"])

    return RawManifest(
        artifact_id=item.id,
        version="1",
        created_at=retrieved_at,
        access_scope=access_scope,
        source=SourceRef(name="MODIS MOD13Q1 v061 (Planetary Computer)", url=item.get_self_href()),
        storage=StorageRef(uri=str(archive.root / item.id), format="cog"),
        checksum=checksum,
        retrieved_at=retrieved_at,
        coverage=Coverage(bbox=tuple(item.bbox), start=start, end=end),
        rights=MODIS_RIGHTS,
        extensions=SourceItem(
            source_id="modis_mod13q1",
            product=MODIS_PRODUCT,
            source_item_id=item.id,
            time_start=start,
            time_end=end,
            time_precision=TimePrecision.COMPOSITE,
            available_at=published_at(item, retrieved_at),
            processing_version=modis_processing_version(item),
            assets=assets,
        ),
    )


def modis_processing_version(item: pystac.Item) -> str:
    # Item id: MOD13Q1.A<year><doy>.<tile>.<collection>.<production timestamp>
    _, _, _, collection, production = item.id.split(".")
    return f"{collection}.{production}"


def published_at(item: pystac.Item, fallback: datetime) -> datetime:
    """When the value became public. The retrieval time is a safe upper bound when the catalog does not say."""
    for key in ("created", "s2:generation_time"):
        if value := item.properties.get(key):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return fallback

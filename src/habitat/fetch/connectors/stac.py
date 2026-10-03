import math
from collections.abc import Callable
from datetime import UTC, datetime, time
from pathlib import Path
from urllib.parse import urlsplit

import planetary_computer
import pystac
import rasterio
from pystac_client import Client
from rasterio.warp import transform_bounds
from rasterio.windows import Window, from_bounds

from habitat.archive import Archive, StoredArtifact
from habitat.archive.index import AlreadyIngested, never_ingested
from habitat.contracts import (
    BBox,
    Coverage,
    RawManifest,
    Rights,
    SourceItem,
    SourceRef,
    TimePrecision,
)
from habitat.fetch.connectors import ConnectorRequest, ConnectorResult, validate_area_and_dates

PLANETARY_COMPUTER_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
STORAGE_FORMAT = "geotiff"

SENTINEL2_COLLECTION = "sentinel-2-l2a"
SENTINEL2_PRODUCT = "sentinel-2-l2a"
MODIS_COLLECTION = "modis-13Q1-061"
MODIS_PRODUCT = "mod13q1-061"

SENTINEL2_ASSETS = {"green": "B03", "red": "B04", "nir": "B08", "swir16": "B11", "scl": "SCL"}
MODIS_ASSETS = {
    "ndvi": "250m_16_days_NDVI",
    "evi": "250m_16_days_EVI",
    "pixel_reliability": "250m_16_days_pixel_reliability",
}

# Asset hosts of the provider-owned STAC collections. A connector never reads an arbitrary URL.
ASSET_HOSTS = {
    "sentinel2l2a01.blob.core.windows.net",
    "sentinel2l2a02.blob.core.windows.net",
    "sentinel2l2a03.blob.core.windows.net",
    "modiseuwest.blob.core.windows.net",
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

GDAL_REMOTE_OPTIONS = {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "GDAL_HTTP_MAX_RETRY": "3", "GDAL_HTTP_RETRY_DELAY": "2"}
CLIPPED_PROFILE = {"driver": "GTiff", "compress": "deflate", "tiled": False}


class NoOverlap(ValueError):
    pass


def search_items(collection: str, bbox: BBox, start: datetime, end: datetime, max_cloud: float | None = None):
    catalog = Client.open(PLANETARY_COMPUTER_STAC, modifier=planetary_computer.sign_inplace)
    query = {"eo:cloud_cover": {"lte": max_cloud}} if max_cloud is not None else None
    search = catalog.search(
        collections=[collection], bbox=bbox, datetime=f"{start.isoformat()}/{end.isoformat()}", query=query
    )
    return list(search.items())


def search_sentinel2(bbox: BBox, start: datetime, end: datetime, max_cloud: float = 80.0) -> list[pystac.Item]:
    return search_items(SENTINEL2_COLLECTION, bbox, start, end, max_cloud)


def search_modis_terra(bbox: BBox, start: datetime, end: datetime) -> list[pystac.Item]:
    # The collection mixes Terra (MOD13Q1) and Aqua (MYD13Q1); their 16-day windows are offset by 8 days.
    # Older records have an empty platform property, so the product id is the filter, not a server-side query.
    return [item for item in search_items(MODIS_COLLECTION, bbox, start, end) if item.id.startswith("MOD13Q1.")]


def boa_add_offset(processing_baseline: str) -> float:
    major, minor = (int(part) for part in processing_baseline.split("."))
    return SENTINEL2_BOA_ADD_OFFSET if (major, minor) >= SENTINEL2_OFFSET_BASELINE else 0.0


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


def check_asset_href(href: str) -> None:
    parts = urlsplit(href)
    if parts.scheme != "https" or parts.hostname not in ASSET_HOSTS:
        raise ValueError(f"asset host {parts.hostname!r} is not an allowed Planetary Computer host")


def aoi_window(source: rasterio.io.DatasetReader, bbox: BBox) -> Window:
    """The pixels that cover the bbox, rounded outward to whole pixels and limited to the raster."""
    bounds = transform_bounds("EPSG:4326", source.crs, *bbox, densify_pts=21)
    window = from_bounds(*bounds, transform=source.transform)
    col_start, row_start = math.floor(window.col_off), math.floor(window.row_off)
    col_end = math.ceil(window.col_off + window.width)
    row_end = math.ceil(window.row_off + window.height)

    col_start, row_start = max(col_start, 0), max(row_start, 0)
    col_end, row_end = min(col_end, source.width), min(row_end, source.height)
    if col_end <= col_start or row_end <= row_start:
        raise NoOverlap("the raster does not overlap the bbox")

    return Window(col_start, row_start, col_end - col_start, row_end - row_start)


def clip_to_bbox(href: str, bbox: BBox, target: Path) -> int:
    """Read only the AOI window of a (remote) COG and write it as a GeoTIFF. Returns the file size."""
    with rasterio.Env(**GDAL_REMOTE_OPTIONS), rasterio.open(href) as source:
        window = aoi_window(source, bbox)
        data = source.read(window=window)
        profile = {
            key: value for key, value in source.profile.items()
            if key not in ("blockxsize", "blockysize", "interleave", "compress", "tiled")
        }
        profile.update(
            CLIPPED_PROFILE, width=int(window.width), height=int(window.height),
            transform=source.window_transform(window),
        )

    with rasterio.open(target, "w", **profile) as clipped:
        clipped.write(data)
    return target.stat().st_size


def intersection(a: BBox, b: BBox | None) -> BBox:
    if b is None:
        return a
    return max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])


def bbox_key(bbox: BBox) -> str:
    return ",".join(f"{value:.5f}" for value in bbox)


def archive_scene(
    item: pystac.Item,
    asset_names: dict[str, str],
    bbox: BBox,
    archive: Archive,
    max_file_bytes: int,
) -> tuple[dict[str, str], str, StoredArtifact, int]:
    with archive.store.staging() as staging:
        files = {}
        size = 0
        for remote in asset_names.values():
            href = item.assets[remote].href
            check_asset_href(href)
            target = staging / f"{remote}.tif"
            size += clip_to_bbox(href, bbox, target)
            if target.stat().st_size > max_file_bytes:
                raise ValueError(f"{item.id}/{remote}: clipped file exceeds {max_file_bytes} bytes")
            files[target.name] = target

        version, stored = archive.put(item.id, files, STORAGE_FORMAT)

    assets = {canonical: f"{remote}.tif" for canonical, remote in asset_names.items()}
    return assets, version, stored, size


def scene_manifest(
    item: pystac.Item,
    *,
    source_id: str,
    product: str,
    source_name: str,
    rights: Rights,
    assets: dict[str, str],
    version: str,
    stored: StoredArtifact,
    source_key: str,
    bbox: BBox,
    time_start: datetime,
    time_end: datetime,
    precision: TimePrecision,
    processing_version: str,
    properties: dict,
    access_scope: str,
    retrieved_at: datetime,
) -> RawManifest:
    return RawManifest(
        artifact_id=item.id,
        version=version,
        created_at=retrieved_at,
        access_scope=access_scope,
        source=SourceRef(name=source_name, url=item.get_self_href()),
        storage=stored.storage,
        checksum=stored.checksum,
        retrieved_at=retrieved_at,
        coverage=Coverage(bbox=intersection(tuple(item.bbox), bbox), start=time_start, end=time_end),
        rights=rights,
        extensions=SourceItem(
            source_id=source_id,
            product=product,
            source_item_id=item.id,
            source_key=source_key,
            time_start=time_start,
            time_end=time_end,
            time_precision=precision,
            available_at=published_at(item, retrieved_at),
            processing_version=processing_version,
            assets=assets,
            properties={**properties, "requested_bbox": list(bbox), "collection": item.collection_id},
        ),
    )


def fetch_scenes(
    request: ConnectorRequest,
    archive: Archive,
    already_ingested: AlreadyIngested,
    *,
    source_id: str,
    search: Callable[[BBox, datetime, datetime], list[pystac.Item]],
    describe: Callable[[pystac.Item], dict],
    asset_names: dict[str, str],
) -> ConnectorResult:
    result = ConnectorResult()
    try:
        first, last = validate_area_and_dates(request)
    except ValueError as error:
        return result.fail("invalid_request", str(error))

    bbox = tuple(request.bbox)
    start = datetime.combine(first, time.min, tzinfo=UTC)
    end = datetime.combine(last, time.max, tzinfo=UTC)
    try:
        items = sorted(search(bbox, start, end), key=lambda item: item.datetime or datetime.min.replace(tzinfo=UTC))
    except Exception as error:
        # Exception text can hold signed URLs; keep only the type.
        return result.fail("search_failed", f"{source_id}: catalog search failed ({type(error).__name__})", True)

    new = [item for item in items if not already_ingested(item.id, describe(item)["processing_version"], "final")]
    if len(new) > request.max_items:
        result.warnings.append(
            f"{source_id}: {len(new)} scenes match; fetched the first {request.max_items}. This is a bounded sample."
        )

    used = 0
    for item in new[: request.max_items]:
        source_key = f"{source_id}:{item.id}:{bbox_key(bbox)}"
        if (cached := archive.cached(source_key)) is not None:
            result.manifests.append(cached)
            continue

        if used >= request.max_bytes:
            result.warnings.append(f"{source_id}: byte budget exhausted before {item.id}")
            break

        missing = [remote for remote in asset_names.values() if remote not in item.assets]
        if missing:
            result.warnings.append(f"{item.id}: missing assets {missing}; scene skipped")
            continue

        try:
            retrieved_at = datetime.now(UTC)
            assets, version, stored, size = archive_scene(item, asset_names, bbox, archive, request.max_file_bytes)
        except NoOverlap:
            result.warnings.append(f"{item.id}: scene does not overlap the bbox")
            continue
        except Exception as error:
            detail = str(error) if isinstance(error, ValueError) else type(error).__name__
            result.warnings.append(f"{item.id}: download failed ({detail})")
            continue

        used += size
        description = describe(item)
        manifest = scene_manifest(
            item, assets=assets, version=version, stored=stored, source_key=source_key, bbox=bbox,
            access_scope=request.access_scope, retrieved_at=retrieved_at, **description,
        )
        result.manifests.append(archive.record(manifest))

    if not result.manifests and not result.errors:
        result.warnings.append(f"{source_id}: no new scenes for the requested region and dates")
    return result


def describe_sentinel2(item: pystac.Item) -> dict:
    baseline = item.properties["s2:processing_baseline"]
    return {
        "source_id": "sentinel2",
        "product": SENTINEL2_PRODUCT,
        "source_name": "Sentinel-2 L2A (Planetary Computer)",
        "rights": SENTINEL2_RIGHTS,
        "time_start": item.datetime,
        "time_end": item.datetime,
        "precision": TimePrecision.INSTANT,
        "processing_version": baseline,
        "properties": {
            "boa_add_offset": boa_add_offset(baseline),
            "mgrs_tile": item.properties.get("s2:mgrs_tile"),
            "cloud_cover": item.properties.get("eo:cloud_cover"),
        },
    }


def describe_modis(item: pystac.Item) -> dict:
    return {
        "source_id": "modis_mod13q1",
        "product": MODIS_PRODUCT,
        "source_name": "MODIS MOD13Q1 v061 (Planetary Computer)",
        "rights": MODIS_RIGHTS,
        "time_start": datetime.fromisoformat(item.properties["start_datetime"]),
        "time_end": datetime.fromisoformat(item.properties["end_datetime"]),
        "precision": TimePrecision.COMPOSITE,
        "processing_version": modis_processing_version(item),
        "properties": {"platform": item.properties.get("platform") or "terra"},
    }


def fetch_sentinel2(
    request: ConnectorRequest, archive: Archive, already_ingested: AlreadyIngested = never_ingested
) -> ConnectorResult:
    return fetch_scenes(
        request, archive, already_ingested, source_id="sentinel2",
        search=lambda bbox, start, end: search_sentinel2(bbox, start, end, request.cloud_cover),
        describe=describe_sentinel2, asset_names=SENTINEL2_ASSETS,
    )


def fetch_modis(
    request: ConnectorRequest, archive: Archive, already_ingested: AlreadyIngested = never_ingested
) -> ConnectorResult:
    return fetch_scenes(
        request, archive, already_ingested, source_id="modis_mod13q1",
        search=search_modis_terra, describe=describe_modis, asset_names=MODIS_ASSETS,
    )

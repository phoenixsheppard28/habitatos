import math
from datetime import UTC, date, datetime, time
from email.utils import parsedate_to_datetime

import httpx

from habitat.archive import Archive
from habitat.archive.index import AlreadyIngested, never_ingested
from habitat.contracts import BBox, Coverage, RawManifest, Rights, SourceItem, SourceRef, TimePrecision
from habitat.fetch import http
from habitat.fetch.connectors import ConnectorRequest, ConnectorResult, validate_area_and_dates
from habitat.fetch.connectors.stac import NoOverlap, bbox_key, clip_to_bbox, intersection

BASE_URL = "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GSWE/MonthlyHistory/LATEST/tiles"
ALLOWED_HOSTS = {"jeodpp.jrc.ec.europa.eu"}
PRODUCT = "gsw-monthly-history-v1.4"
DATA_VERSION = "1.4"
STORAGE_FORMAT = "geotiff"
PIXEL_DEGREES = 0.00025
TILE_DEGREES = 10
GRID_NORTH, GRID_SOUTH = 80, -60
FIRST_MONTH, LAST_MONTH = date(1984, 3, 1), date(2021, 12, 1)
RIGHTS = Rights(
    license="CC-BY-4.0",
    retention_allowed=True,
    reuse_allowed=True,
    attribution="Pekel et al. 2016, Nature 540, 418-422, © European Union",
)


def tile_offsets(bbox: BBox) -> list[tuple[int, int]]:
    """Row and column pixel offsets of the 10° tiles that the bbox touches. The tile grid starts at 180°W, 80°N."""
    west, south, east, north = bbox
    norths = range(
        min(GRID_NORTH, math.ceil(north / TILE_DEGREES) * TILE_DEGREES),
        max(GRID_SOUTH, math.floor(south / TILE_DEGREES) * TILE_DEGREES),
        -TILE_DEGREES,
    )
    wests = range(math.floor(west / TILE_DEGREES) * TILE_DEGREES, math.ceil(east / TILE_DEGREES) * TILE_DEGREES,
                  TILE_DEGREES)
    return [
        (round((GRID_NORTH - tile_north) / PIXEL_DEGREES), round((tile_west + 180) / PIXEL_DEGREES))
        for tile_north in norths
        for tile_west in wests
    ]


def item_id(month: date, offsets: tuple[int, int]) -> str:
    row, col = offsets
    return f"{month:%Y_%m}-{row:010d}-{col:010d}"


def tile_url(month: date, offsets: tuple[int, int]) -> str:
    return f"{BASE_URL}/{month:%Y}/{month:%Y_%m}/{item_id(month, offsets)}.tif"


def tile_bbox(offsets: tuple[int, int]) -> BBox:
    row, col = offsets
    west, north = col * PIXEL_DEGREES - 180, GRID_NORTH - row * PIXEL_DEGREES
    return west, north - TILE_DEGREES, west + TILE_DEGREES, north


def months_between(first: date, last: date) -> list[date]:
    months, month = [], first.replace(day=1)
    while month <= last:
        months.append(month)
        month = next_month(month)
    return months


def next_month(month: date) -> date:
    return date(month.year + month.month // 12, month.month % 12 + 1, 1)


def fetch_jrc_gsw_monthly(
    request: ConnectorRequest, archive: Archive, already_ingested: AlreadyIngested = never_ingested
) -> ConnectorResult:
    result = ConnectorResult()
    try:
        first, last = validate_area_and_dates(request)
    except ValueError as error:
        return result.fail("invalid_request", str(error))

    bbox = tuple(request.bbox)
    requested = months_between(first, last)
    months = [month for month in requested if FIRST_MONTH <= month <= LAST_MONTH]
    if len(months) < len(requested):
        result.warnings.append("jrc_gsw_monthly: JRC monthly history v1.4 covers 1984-03 to 2021-12 only")

    tiles = tile_offsets(bbox)
    if not tiles:
        result.warnings.append("jrc_gsw_monthly: the tile grid covers 80°N to 60°S only")
        return result

    items = [(month, offsets) for month in months for offsets in tiles]
    if len(items) > request.max_items:
        result.warnings.append(
            f"jrc_gsw_monthly: item limit reached: fetched the first {request.max_items} of {len(items)} month tiles"
        )

    used = 0
    with http.client() as client:
        for month, offsets in items[: request.max_items]:
            name = item_id(month, offsets)
            try:
                manifest = fetch_tile(month, offsets, bbox, client, archive, already_ingested, request,
                                      request.max_bytes - used)
            except Exception as error:
                detail = str(error) if isinstance(error, ValueError) else type(error).__name__
                result.warnings.append(f"{name}: download failed ({detail})")
                continue

            if manifest is None:
                continue
            used += sum(path.stat().st_size for path in archive.resolve(manifest).values())
            result.manifests.append(manifest)
    return result


def fetch_tile(
    month: date,
    offsets: tuple[int, int],
    bbox: BBox,
    client: httpx.Client,
    archive: Archive,
    already_ingested: AlreadyIngested,
    request: ConnectorRequest,
    budget: int,
) -> RawManifest | None:
    name, url = item_id(month, offsets), tile_url(month, offsets)
    http.check_host(url, ALLOWED_HOSTS)
    head = client.head(url)
    if head.status_code == 404:
        raise ValueError("JRC has no file for this month and tile")
    head.raise_for_status()

    modified = head.headers.get("last-modified")
    if not modified:
        raise ValueError("the server gives no Last-Modified date, so the publication date is unknown")
    available_at = parsedate_to_datetime(modified).astimezone(UTC)
    processing_version = f"{DATA_VERSION}@{available_at:%Y-%m-%dT%H:%M:%SZ}"
    if already_ingested(name, processing_version, "final"):
        return None

    source_key = f"jrc_gsw_monthly:{name}:{bbox_key(bbox)}"
    cached = archive.cached(source_key)
    if cached is not None and cached.extensions.processing_version == processing_version:
        return cached

    if budget <= 0:
        raise ValueError("byte budget exhausted")

    retrieved_at = datetime.now(UTC)
    try:
        with archive.store.staging() as staging:
            target = staging / f"{name}.tif"
            clip_to_bbox(url, bbox, target)
            if target.stat().st_size > request.max_file_bytes:
                raise ValueError(f"clipped file exceeds {request.max_file_bytes} bytes")
            version, stored = archive.put(f"jrc-gsw-monthly-{name}", {target.name: target}, STORAGE_FORMAT)
    except NoOverlap:
        return None

    start = datetime.combine(month, time.min, tzinfo=UTC)
    end = datetime.combine(next_month(month), time.min, tzinfo=UTC)
    manifest = RawManifest(
        artifact_id=f"jrc-gsw-monthly-{name}",
        version=version,
        created_at=retrieved_at,
        access_scope=request.access_scope,
        source=SourceRef(name="JRC Global Surface Water, monthly history v1.4", url=url),
        storage=stored.storage,
        checksum=stored.checksum,
        retrieved_at=retrieved_at,
        coverage=Coverage(bbox=intersection(tile_bbox(offsets), bbox), start=start, end=end),
        rights=RIGHTS,
        extensions=SourceItem(
            source_id="jrc_gsw_monthly",
            product=PRODUCT,
            source_item_id=name,
            source_key=source_key,
            time_start=start,
            time_end=end,
            time_precision=TimePrecision.COMPOSITE,
            available_at=available_at,
            processing_version=processing_version,
            assets={"water": target.name},
            properties={
                "requested_bbox": list(bbox),
                "pixel_values": {"0": "no data", "1": "not water", "2": "water"},
                "resolution_degrees": PIXEL_DEGREES,
            },
        ),
    )
    return archive.record(manifest)

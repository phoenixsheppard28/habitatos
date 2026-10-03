from datetime import UTC, date, datetime, time, timedelta
from email.utils import parsedate_to_datetime

import httpx

from habitat.archive import Archive
from habitat.archive.index import AlreadyIngested, never_ingested
from habitat.contracts import (
    Coverage,
    ProductStatus,
    RawManifest,
    Rights,
    SourceItem,
    SourceRef,
    TimePrecision,
)
from habitat.fetch import http
from habitat.fetch.connectors import ConnectorRequest, ConnectorResult, validate_area_and_dates

FINAL_URL = "https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_daily/tifs/p05/{d:%Y}/chirps-v2.0.{d:%Y.%m.%d}.tif.gz"
PRELIM_URL = "https://data.chc.ucsb.edu/products/CHIRPS-2.0/prelim/global_daily/tifs/p05/{d:%Y}/chirps-v2.0.{d:%Y.%m.%d}.tif.gz"
ALLOWED_HOSTS = {"data.chc.ucsb.edu"}
PRODUCT = "chirps-v2.0-daily-p05"
PROCESSING_VERSION = "2.0"
STORAGE_FORMAT = "tif.gz"
GZIP_MAGIC = b"\x1f\x8b"
COVERAGE_BBOX = (-180.0, -50.0, 180.0, 50.0)
RIGHTS = Rights(
    license="CC-BY-4.0",
    retention_allowed=True,
    reuse_allowed=True,
    attribution="Funk et al. 2015, The climate hazards infrared precipitation with stations, Scientific Data 2, 150066",
)


def item_id(day: date) -> str:
    return f"chirps-v2.0.{day:%Y.%m.%d}"


def available_status(day: date, client: httpx.Client) -> ProductStatus | None:
    """The final file wins over the preliminary file. None when neither exists yet."""
    for status, template in ((ProductStatus.FINAL, FINAL_URL), (ProductStatus.PRELIMINARY, PRELIM_URL)):
        if client.head(template.format(d=day)).status_code == 200:
            return status
    return None


def last_modified(client: httpx.Client, url: str) -> datetime | None:
    value = client.head(url).headers.get("last-modified")
    return parsedate_to_datetime(value).astimezone(UTC) if value else None


def fetch_chirps(
    request: ConnectorRequest, archive: Archive, already_ingested: AlreadyIngested = never_ingested
) -> ConnectorResult:
    result = ConnectorResult()
    try:
        first, last = validate_area_and_dates(request)
    except ValueError as error:
        return result.fail("invalid_request", str(error))

    west, south, east, north = request.bbox
    if north <= COVERAGE_BBOX[1] or south >= COVERAGE_BBOX[3]:
        result.warnings.append("CHIRPS v2 has no coverage outside 50°S–50°N")
        return result
    if south < COVERAGE_BBOX[1] or north > COVERAGE_BBOX[3]:
        result.warnings.append("CHIRPS v2 covers only the part of this region within 50°S–50°N")

    days = (last - first).days + 1
    if days > request.max_days:
        result.warnings.append(f"CHIRPS day limit reached: fetched the first {request.max_days} of {days} days")

    used = 0
    with http.client() as client:
        for offset in range(min(days, request.max_days)):
            day = first + timedelta(days=offset)
            try:
                manifest = fetch_day(day, client, archive, already_ingested, request, request.max_bytes - used)
            except Exception as error:
                detail = str(error) if isinstance(error, ValueError) else type(error).__name__
                result.warnings.append(f"{item_id(day)}: download failed ({detail})")
                continue

            if manifest is None:
                continue
            used += sum(path.stat().st_size for path in archive.resolve(manifest).values())
            result.manifests.append(manifest)
    return result


def fetch_day(
    day: date,
    client: httpx.Client,
    archive: Archive,
    already_ingested: AlreadyIngested,
    request: ConnectorRequest,
    budget: int,
) -> RawManifest | None:
    status = available_status(day, client)
    if status is None or already_ingested(item_id(day), PROCESSING_VERSION, status.value):
        return None

    source_key = f"chirps:{item_id(day)}:{status.value}"
    if (cached := archive.cached(source_key)) is not None:
        return cached

    if budget <= 0:
        raise ValueError("byte budget exhausted")

    url = (FINAL_URL if status is ProductStatus.FINAL else PRELIM_URL).format(d=day)
    filename = url.rsplit("/", 1)[-1]
    artifact_id = f"chirps-{status.value}-{day:%Y%m%d}"
    with archive.store.staging() as staging:
        target = staging / filename
        http.download(url, target, max_bytes=min(budget, request.max_file_bytes), allowed_hosts=ALLOWED_HOSTS)
        if target.read_bytes()[:2] != GZIP_MAGIC:
            raise ValueError("unexpected payload; expected a gzip file")
        retrieved_at = datetime.now(UTC)
        version, stored = archive.put(artifact_id, {filename: target}, STORAGE_FORMAT)

    start = datetime.combine(day, time.min, tzinfo=UTC)
    manifest = RawManifest(
        artifact_id=artifact_id,
        version=version,
        created_at=retrieved_at,
        access_scope=request.access_scope,
        source=SourceRef(name="CHIRPS", url=url),
        storage=stored.storage,
        checksum=stored.checksum,
        retrieved_at=retrieved_at,
        coverage=Coverage(bbox=COVERAGE_BBOX, start=start, end=start + timedelta(days=1)),
        rights=RIGHTS,
        extensions=SourceItem(
            source_id="chirps",
            product=PRODUCT,
            source_item_id=item_id(day),
            source_key=source_key,
            time_start=start,
            time_end=start + timedelta(days=1),
            time_precision=TimePrecision.DAY,
            available_at=last_modified(client, url) or retrieved_at,
            processing_version=PROCESSING_VERSION,
            product_status=status,
            assets={"precipitation": filename},
            properties={
                "units": "mm", "resolution_degrees": 0.05, "compression": "gzip",
                "requested_bbox": list(request.bbox),
            },
        ),
    )
    return archive.record(manifest)

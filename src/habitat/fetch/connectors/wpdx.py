import hashlib
import json
from datetime import UTC, datetime, time

import httpx

from habitat.archive import Archive
from habitat.archive.index import AlreadyIngested, never_ingested
from habitat.contracts import BBox, Coverage, RawManifest, Rights, SourceItem, SourceRef, TimePrecision
from habitat.fetch import http
from habitat.fetch.connectors import ConnectorRequest, ConnectorResult, validate_area_and_dates
from habitat.fetch.connectors.stac import bbox_key
from habitat.normalize.sources.wpdx import floating_utc

DATASET = "eqje-vguj"
ENDPOINT = f"https://data.waterpointdata.org/resource/{DATASET}.json"
PRODUCT = "wpdx-plus"
STORAGE_FORMAT = "json"
PAGE_ROWS = 50_000
RIGHTS = Rights(
    license="CC-BY-4.0",
    retention_allowed=True,
    reuse_allowed=True,
    attribution="Water Point Data Exchange (WPdx+), https://www.waterpointdata.org",
)


class WpdxError(ValueError):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


def bbox_filter(bbox: BBox) -> str:
    west, south, east, north = bbox
    return f"lat_deg between {south} and {north} AND lon_deg between {west} and {east} AND lat_deg IS NOT NULL"


def fetch_wpdx(
    request: ConnectorRequest, archive: Archive, already_ingested: AlreadyIngested = never_ingested
) -> ConnectorResult:
    """WPdx has no version id, so the latest `updated` of the rows is the version. The rows are always fetched."""
    result = ConnectorResult()
    try:
        first, last = validate_area_and_dates(request)
    except ValueError as error:
        return result.fail("invalid_request", str(error))

    bbox = tuple(request.bbox)
    try:
        rows, body = fetch_rows(bbox, min(request.max_bytes, request.max_file_bytes))
    except WpdxError as error:
        return result.fail("download_failed", f"wpdx: {error}", error.retryable)

    if not rows:
        result.warnings.append("wpdx: no water points in the requested bbox")
        return result

    latest_update = max(row["updated"] for row in rows if row.get("updated"))
    item_id = f"{DATASET}:{bbox_key(bbox)}"
    key = f"wpdx:{item_id}:{latest_update}"
    if already_ingested(item_id, latest_update, "final"):
        return result
    if (cached := archive.cached(key)) is not None:
        result.manifests.append(cached)
        return result

    retrieved_at = datetime.now(UTC)
    available_at = floating_utc(latest_update)
    artifact_id = f"wpdx-{hashlib.sha256(key.encode()).hexdigest()[:16]}"
    version, stored = archive.put(artifact_id, {"wpdx.json": body}, STORAGE_FORMAT)
    manifest = RawManifest(
        artifact_id=artifact_id,
        version=version,
        created_at=retrieved_at,
        access_scope=request.access_scope,
        source=SourceRef(name="Water Point Data Exchange (WPdx+)", url=ENDPOINT),
        storage=stored.storage,
        checksum=stored.checksum,
        retrieved_at=retrieved_at,
        coverage=Coverage(
            bbox=bbox,
            start=datetime.combine(first, time.min, tzinfo=UTC),
            end=datetime.combine(last, time.max, tzinfo=UTC),
        ),
        rights=RIGHTS,
        extensions=SourceItem(
            source_id="wpdx",
            product=PRODUCT,
            source_item_id=item_id,
            source_key=key,
            kind="vector",
            time_start=available_at,
            time_end=available_at,
            time_precision=TimePrecision.STATIC,
            available_at=available_at,
            processing_version=latest_update,
            assets={"water_points": "wpdx.json"},
            properties={"requested_bbox": list(bbox), "dataset": DATASET, "row_count": len(rows)},
        ),
    )
    result.manifests.append(archive.record(manifest))
    return result


def fetch_rows(bbox: BBox, max_bytes: int) -> tuple[list[dict], bytes]:
    """All rows in the bbox, page by page, as one JSON array."""
    rows, size = [], 0
    try:
        with http.client() as client:
            while True:
                response = client.get(ENDPOINT, params={
                    "$where": bbox_filter(bbox), "$order": "row_id", "$limit": PAGE_ROWS, "$offset": len(rows),
                })
                if response.status_code in http.RETRY_STATUS:
                    raise WpdxError(f"the server answered HTTP {response.status_code}; retry later", retryable=True)
                response.raise_for_status()

                size += len(response.content)
                if size > max_bytes:
                    raise WpdxError(f"the rows exceed the size budget of {max_bytes} bytes")

                page = response.json()
                rows.extend(page)
                if len(page) < PAGE_ROWS:
                    return rows, json.dumps(rows).encode()
    except httpx.HTTPError as error:
        raise WpdxError(f"request failed ({type(error).__name__})", retryable=True) from error

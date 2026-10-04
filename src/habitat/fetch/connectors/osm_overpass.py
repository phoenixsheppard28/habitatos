import hashlib
import json
import re
from datetime import UTC, date, datetime, time

import httpx

from habitat.archive import Archive
from habitat.archive.index import AlreadyIngested, never_ingested
from habitat.contracts import BBox, Coverage, RawManifest, Rights, SourceItem, SourceRef, TimePrecision
from habitat.fetch import http
from habitat.fetch.connectors import ConnectorRequest, ConnectorResult, validate_area_and_dates
from habitat.fetch.connectors.stac import bbox_key

ENDPOINT = "https://overpass-api.de/api/interpreter"
PRODUCT = "osm-water"
STORAGE_FORMAT = "json"
TIMEOUT_S = 180
# The Overpass wiki gives this as the oldest state that an attic query can return.
OLDEST_SNAPSHOT = datetime(2012, 9, 12, 6, 55, tzinfo=UTC)
WATER_FILTERS = (
    'nwr["natural"~"^(water|spring|wetland)$"]',
    'nwr["waterway"~"^(river|stream|canal|dam|weir)$"]',
    'nwr["landuse"="reservoir"]',
    'nwr["man_made"~"^(water_well|water_tap|reservoir_covered|dam)$"]',
    'nwr["amenity"~"^(drinking_water|water_point)$"]',
)
QUERY_HASH = hashlib.sha256("\n".join(WATER_FILTERS).encode()).hexdigest()[:8]
RIGHTS = Rights(
    license="ODbL-1.0", retention_allowed=True, reuse_allowed=True, attribution="© OpenStreetMap contributors"
)


class OverpassError(ValueError):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


def today() -> date:
    return datetime.now(UTC).date()


def snapshot_time(end: date) -> datetime | None:
    """The state at the end of the last requested day. None asks for the current state."""
    if end >= today():
        return None

    return max(datetime.combine(end, time(23, 59, 59), tzinfo=UTC), OLDEST_SNAPSHOT)


def iso_utc(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def overpass_query(bbox: BBox, snapshot: datetime | None) -> str:
    west, south, east, north = bbox
    area = f"({south},{west},{north},{east})"
    settings = f'[out:json][timeout:{TIMEOUT_S}]' + (f'[date:"{iso_utc(snapshot)}"]' if snapshot else "")
    filters = "\n".join(f"  {water_filter}{area};" for water_filter in WATER_FILTERS)
    return f"{settings};\n(\n{filters}\n);\nout meta geom;"


def fetch_osm_overpass(
    request: ConnectorRequest, archive: Archive, already_ingested: AlreadyIngested = never_ingested
) -> ConnectorResult:
    result = ConnectorResult()
    try:
        first, last = validate_area_and_dates(request)
    except ValueError as error:
        return result.fail("invalid_request", str(error))

    bbox = tuple(request.bbox)
    snapshot = snapshot_time(last)
    if snapshot == OLDEST_SNAPSHOT:
        result.warnings.append(
            "osm_overpass: OSM history starts 2012-09-12; used the oldest snapshot, which can hold features mapped "
            "after the requested dates"
        )

    if snapshot is not None:
        item_id = f"{bbox_key(bbox)}@{snapshot:%Y-%m-%d}"
        if already_ingested(item_id, iso_utc(snapshot), "final"):
            return result
        if (cached := archive.cached(source_key(bbox, snapshot))) is not None:
            result.manifests.append(cached)
            return result

    try:
        body = post_query(overpass_query(bbox, snapshot), min(request.max_bytes, request.max_file_bytes))
        document = parse_response(body)
    except OverpassError as error:
        return result.fail("download_failed", f"osm_overpass: {error}", error.retryable)

    database_time = parse_time(document["osm3s"]["timestamp_osm_base"])
    state = snapshot or database_time
    item_id = f"{bbox_key(bbox)}@{state:%Y-%m-%d}"
    processing_version = iso_utc(snapshot) if snapshot else document["osm3s"]["timestamp_osm_base"]
    if snapshot is None and already_ingested(item_id, processing_version, "final"):
        return result

    retrieved_at = datetime.now(UTC)
    artifact_id = f"osm-overpass-{hashlib.sha256(source_key(bbox, state).encode()).hexdigest()[:16]}"
    version, stored = archive.put(artifact_id, {"overpass.json": body}, STORAGE_FORMAT)
    manifest = RawManifest(
        artifact_id=artifact_id,
        version=version,
        created_at=retrieved_at,
        access_scope=request.access_scope,
        source=SourceRef(name="OpenStreetMap (Overpass API)", url=ENDPOINT),
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
            source_id="osm_overpass",
            product=PRODUCT,
            source_item_id=item_id,
            source_key=source_key(bbox, state),
            kind="vector",
            time_start=state,
            time_end=state,
            time_precision=TimePrecision.STATIC,
            available_at=database_time,
            processing_version=processing_version,
            assets={"features": "overpass.json"},
            properties={
                "requested_bbox": list(bbox),
                "snapshot": iso_utc(snapshot) if snapshot else None,
                "query_hash": QUERY_HASH,
                "element_count": len(document["elements"]),
            },
        ),
    )
    result.manifests.append(archive.record(manifest))
    return result


def source_key(bbox: BBox, state: datetime) -> str:
    return f"osm_overpass:{QUERY_HASH}:{bbox_key(bbox)}:{state:%Y-%m-%d}"


def post_query(query: str, max_bytes: int) -> bytes:
    try:
        with http.client(timeout=TIMEOUT_S + 30) as client, client.stream(
            "POST", ENDPOINT, data={"data": query}
        ) as response:
            if response.status_code in http.RETRY_STATUS:
                raise OverpassError(f"the server answered HTTP {response.status_code}; retry later", retryable=True)
            response.raise_for_status()

            body = bytearray()
            for chunk in response.iter_bytes(http.CHUNK_BYTES):
                body += chunk
                if len(body) > max_bytes:
                    raise OverpassError(f"the response exceeds the size budget of {max_bytes} bytes")
            return bytes(body)
    except httpx.HTTPError as error:
        raise OverpassError(f"request failed ({type(error).__name__})", retryable=True) from error


def parse_response(body: bytes) -> dict:
    """Overpass reports an overload as an HTML page and a query timeout as a `remark`; neither is an empty result."""
    try:
        document = json.loads(body)
    except ValueError:
        text = re.sub(r"<[^>]+>", " ", body.decode(errors="replace"))
        busy = "too busy" in text or "timeout" in text
        detail = "the server is probably too busy; retry later" if busy else "the server returned no JSON"
        raise OverpassError(detail, retryable=busy) from None

    remark = document.get("remark") or ""
    if "error" in remark:
        raise OverpassError(f"the query failed: {remark}", retryable=True)

    if not (document.get("osm3s") or {}).get("timestamp_osm_base") or "elements" not in document:
        raise OverpassError("the response has no osm3s.timestamp_osm_base or no elements")

    return document


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

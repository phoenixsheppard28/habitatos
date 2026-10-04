"""GBIF occurrence search. Download mode is not implemented: a larger request gives a bounded sample and a warning."""

import hashlib
import json
from datetime import UTC, datetime, time, timedelta

import httpx

from habitat.archive import Archive
from habitat.archive.index import AlreadyIngested, never_ingested
from habitat.contracts import BBox, Coverage, RawManifest, Rights, SourceItem, SourceRef, TimePrecision
from habitat.fetch import http
from habitat.fetch.connectors import ConnectorRequest, ConnectorResult, validate_area_and_dates
from habitat.fetch.connectors.movebank_repository import UUID_PATTERN
from habitat.normalize.rows import QuarantineError
from habitat.normalize.sources.gbif_occurrence import (
    DATASETS_ASSET,
    PAGE_ASSET_PREFIX,
    event_time,
    most_restrictive,
    record_available_at,
    spdx_license,
)

GBIF_API = "https://api.gbif.org/v1"
SEARCH_PATH = "/occurrence/search"
PRODUCT = "gbif-occurrence"
STORAGE_FORMAT = "json"
# The search service rejects a larger limit.
PAGE_LIMIT = 300
DATASET_FIELDS = ("key", "title", "pubDate", "modified", "license", "doi")


def bbox_wkt(bbox: BBox) -> str:
    """A counter-clockwise polygon, as GBIF requires."""
    west, south, east, north = bbox
    return f"POLYGON(({west} {south},{east} {south},{east} {north},{west} {north},{west} {south}))"


def search_parameters(request: ConnectorRequest) -> dict:
    parameters = {
        "geometry": bbox_wkt(request.bbox),
        "eventDate": f"{request.start.isoformat()},{request.end.isoformat()}",
        "hasCoordinate": "true",
    }
    if request.taxon_keys:
        parameters["taxonKey"] = [str(key) for key in sorted(request.taxon_keys)]
    if request.item:
        parameters["datasetKey"] = request.item
    return parameters


def fetch_gbif_occurrence(
    request: ConnectorRequest, archive: Archive, already_ingested: AlreadyIngested = never_ingested
) -> ConnectorResult:
    """One manifest per search: the JSON pages as downloaded and the metadata of their datasets.

    `item` is an optional GBIF dataset key, for example a Global Roadkill Data dataset.
    """
    result = ConnectorResult()
    try:
        validate_area_and_dates(request)
    except ValueError as error:
        return result.fail("invalid_request", str(error))

    if request.item is not None and not UUID_PATTERN.match(request.item):
        return result.fail("invalid_request", "a GBIF dataset key must be a UUID")

    parameters = search_parameters(request)
    query = json.dumps({**parameters, "max_records": request.max_records}, sort_keys=True)
    source_item_id = f"gbif-search:{hashlib.sha256(query.encode()).hexdigest()}"
    try:
        with http.client(base_url=GBIF_API) as client:
            pages, count = search_pages(client, parameters, request.max_records)
            records = [record for page in pages for record in json.loads(page)["results"]]
            datasets = dataset_metadata(client, {record["datasetKey"] for record in records if "datasetKey" in record})
    except (httpx.HTTPError, ValueError) as error:
        return result.fail("download_failed", f"GBIF search failed ({type(error).__name__})", retryable=True)

    if not records:
        result.warnings.append("no GBIF records match the bbox, dates and taxa")
        return result
    if count > request.max_records:
        result.warnings.append(
            f"GBIF record limit reached: fetched the first {request.max_records} of {count} records; "
            "download mode for larger requests is not implemented"
        )

    processing_version = f"sha256:{hashlib.sha256(b''.join(pages)).hexdigest()}"
    source_key = f"gbif_occurrence:{source_item_id}:{processing_version}"
    if (cached := archive.cached(source_key)) is not None:
        result.manifests.append(cached)
        return result
    if already_ingested(source_item_id, processing_version, "final"):
        return result

    published = latest_publication(records, datasets)
    if published is None:
        result.warnings.append("no GBIF record has a publication date; the search was not archived")
        return result

    files = {f"{PAGE_ASSET_PREFIX}{index:03d}.json": page for index, page in enumerate(pages)}
    files[f"{DATASETS_ASSET}.json"] = json.dumps(datasets, indent=1, sort_keys=True).encode()
    retrieved_at = datetime.now(UTC)
    artifact_id = f"gbif-search-{source_item_id.removeprefix('gbif-search:')[:16]}"
    version, stored = archive.put(artifact_id, files, STORAGE_FORMAT)

    start = datetime.combine(request.start, time.min, tzinfo=UTC)
    end = datetime.combine(request.end, time.min, tzinfo=UTC) + timedelta(days=1)
    manifest = RawManifest(
        artifact_id=artifact_id,
        version=version,
        created_at=retrieved_at,
        access_scope=request.access_scope,
        source=SourceRef(name="GBIF occurrence search", url=f"{GBIF_API}{SEARCH_PATH}"),
        storage=stored.storage,
        checksum=stored.checksum,
        retrieved_at=retrieved_at,
        coverage=Coverage(bbox=tuple(request.bbox), start=start, end=end),
        rights=rights(records, retrieved_at),
        extensions=SourceItem(
            source_id="gbif_occurrence",
            product=PRODUCT,
            source_item_id=source_item_id,
            source_key=source_key,
            kind="tabular",
            time_start=start,
            time_end=end,
            time_precision=TimePrecision.COMPOSITE,
            available_at=published,
            processing_version=processing_version,
            assets={name.removesuffix(".json"): name for name in files},
            properties={
                "requested_bbox": list(request.bbox),
                "search_parameters": parameters,
                "record_count": len(records),
                "matching_record_count": count,
            },
        ),
    )
    result.manifests.append(archive.record(manifest))
    return result


def search_pages(client: httpx.Client, parameters: dict, max_records: int) -> tuple[list[bytes], int]:
    """The raw pages, at most `max_records` records in pages of 300, and the count of all matching records."""
    count = get(client, SEARCH_PATH, {**parameters, "limit": 0}).json()["count"]
    wanted = min(count, max_records)

    pages = []
    for offset in range(0, wanted, PAGE_LIMIT):
        response = get(client, SEARCH_PATH, {**parameters, "limit": min(PAGE_LIMIT, wanted - offset), "offset": offset})
        pages.append(response.content)
        if response.json()["endOfRecords"]:
            break
    return pages, count


def dataset_metadata(client: httpx.Client, dataset_keys: set[str]) -> dict[str, dict]:
    """Publication date, title, license and DOI of each dataset. A dataset without metadata is left out."""
    datasets = {}
    for key in sorted(dataset_keys):
        response = client.get(f"/dataset/{key}")
        if response.status_code == 200:
            datasets[key] = {field: response.json().get(field) for field in DATASET_FIELDS}
    return datasets


def get(client: httpx.Client, path: str, parameters: dict) -> httpx.Response:
    response = client.get(path, params=parameters)
    response.raise_for_status()
    return response


def latest_publication(records: list[dict], datasets: dict[str, dict]) -> datetime | None:
    dates = []
    for record in records:
        try:
            _, end, _, _ = event_time(record)
            dates.append(record_available_at(record, datasets, end)[0])
        except (QuarantineError, ValueError):
            continue
    return max(dates, default=None)


def rights(records: list[dict], retrieved_at: datetime) -> Rights:
    licenses = {record.get("license") for record in records}
    unmapped = sorted(str(license) for license in licenses if spdx_license(license) is None)
    return Rights(
        license=", ".join(unmapped) if unmapped else most_restrictive(spdx_license(license) for license in licenses),
        retention_allowed=True,
        reuse_allowed=not unmapped,
        attribution=f"GBIF.org ({retrieved_at:%d %B %Y}) GBIF Occurrence Search {GBIF_API}{SEARCH_PATH}. "
                    "Cite the datasets in datasets.json. A derived dataset DOI is not registered.",
    )

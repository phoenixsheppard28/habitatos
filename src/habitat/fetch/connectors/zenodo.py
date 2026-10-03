import re
from datetime import UTC, datetime
from typing import Any

from habitat.archive import Archive
from habitat.archive.index import AlreadyIngested, never_ingested
from habitat.archive.paths import safe_name
from habitat.contracts import Coverage, RawManifest, Rights, SourceItem, SourceRef, TimePrecision
from habitat.fetch import http
from habitat.fetch.connectors import ConnectorRequest, ConnectorResult

ZENODO_RECORDS_API = "https://zenodo.org/api/records"
ALLOWED_HOSTS = {"zenodo.org"}
PRODUCT = "zenodo-record-file"
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024
PREFERRED_SUFFIXES = (".csv", ".json", ".txt", ".zip")


def parse_record_id(value: str | None) -> str | None:
    match = re.fullmatch(r"(?:zenodo:)?(\d+)", (value or "").strip())
    return match.group(1) if match else None


def search_zenodo(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    try:
        data = http.get_json(ZENODO_RECORDS_API, params={"q": query, "size": max_results})
    except Exception as error:
        return [{"error": True, "message": f"Zenodo search failed ({type(error).__name__})", "source_id": "zenodo"}]

    hits = []
    for hit in data.get("hits", {}).get("hits", []):
        meta = hit.get("metadata") or {}
        description = meta.get("description") or ""
        if isinstance(description, str) and len(description) > 400:
            description = description[:400] + "…"
        hits.append({
            "dataset_id": f"zenodo:{hit['id']}",
            "source_id": "zenodo",
            "title": meta.get("title", ""),
            "description": description,
            "species": [],
            "data_kind": "external_research_data",
            "landing_page": hit.get("links", {}).get("html"),
        })
    return hits


def inspect_zenodo(dataset_id: str) -> dict[str, Any]:
    record_id = parse_record_id(dataset_id)
    if not record_id:
        return {"found": False, "dataset_id": dataset_id}

    try:
        hit = http.get_json(f"{ZENODO_RECORDS_API}/{record_id}")
    except Exception as error:
        return {"found": False, "dataset_id": dataset_id, "error": type(error).__name__}

    meta = hit.get("metadata") or {}
    license_info = meta.get("license") or {}
    files = [
        {"filename": f.get("key"), "size": f.get("size"), "download_url": f.get("links", {}).get("self")}
        for f in hit.get("files") or []
    ]
    return {
        "found": True,
        "dataset_id": f"zenodo:{record_id}",
        "source_id": "zenodo",
        "title": meta.get("title"),
        "description": meta.get("description"),
        "publication_date": meta.get("publication_date"),
        "revision": hit.get("revision"),
        "files": files,
        "coverage": {"species": [], "bbox": None, "start": None, "end": None},
        "source": {
            "name": "zenodo",
            "url": hit.get("links", {}).get("html") or f"https://zenodo.org/records/{record_id}",
            "study_id": record_id,
        },
        "rights": {
            "license": license_info.get("id") if isinstance(license_info, dict) else license_info,
            "retention_allowed": True,
            "reuse_allowed": True,
            "attribution": meta.get("title"),
        },
    }


def check_zenodo_access(dataset_id: str) -> dict[str, Any]:
    info = inspect_zenodo(dataset_id)
    if not info.get("found"):
        return {"dataset_id": dataset_id, "status": "not_found", "message": info.get("error", "Unknown Zenodo record.")}

    if not info["files"]:
        return {"dataset_id": dataset_id, "status": "unavailable", "message": "Record has no downloadable files."}

    return {
        "dataset_id": dataset_id,
        "status": "available",
        "file_count": len(info["files"]),
        "license": info["rights"]["license"],
    }


def choose_file(files: list[dict], max_bytes: int) -> dict | None:
    small = [f for f in files if f.get("download_url") and (f.get("size") or 0) <= max_bytes]
    preferred = [f for f in small if str(f.get("filename", "")).lower().endswith(PREFERRED_SUFFIXES)]
    candidates = preferred or small
    return min(candidates, key=lambda f: f.get("size") or 0) if candidates else None


def fetch_zenodo(
    request: ConnectorRequest, archive: Archive, already_ingested: AlreadyIngested = never_ingested
) -> ConnectorResult:
    result = ConnectorResult()
    record_id = parse_record_id(request.item)
    if not record_id:
        return result.fail("invalid_request", "zenodo needs a record id, such as zenodo:123")

    info = inspect_zenodo(record_id)
    if not info.get("found"):
        return result.fail("not_found", "Zenodo record not found.")

    chosen = choose_file(info["files"], min(MAX_DOWNLOAD_BYTES, request.max_file_bytes))
    if chosen is None:
        return result.fail("no_suitable_file", "No file under the size limit; try a record with a small CSV.")

    filename = safe_name(chosen.get("filename") or "download.bin")
    source_item_id = f"zenodo:{record_id}/{filename}"
    processing_version = f"revision:{info.get('revision')}"
    if already_ingested(source_item_id, processing_version, "final"):
        return result

    source_key = f"zenodo:{record_id}:{filename}:{chosen.get('size')}"
    if (cached := archive.cached(source_key)) is not None:
        result.manifests.append(cached)
        return result

    url = chosen["download_url"]
    try:
        with archive.store.staging() as staging:
            target = staging / filename
            http.download(url, target, max_bytes=MAX_DOWNLOAD_BYTES, allowed_hosts=ALLOWED_HOSTS)
            retrieved_at = datetime.now(UTC)
            storage_format = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
            version, stored = archive.put(f"zenodo-{record_id}", {filename: target}, storage_format)
    except Exception as error:
        detail = str(error) if isinstance(error, ValueError) else type(error).__name__
        return result.fail("download_failed", detail, retryable=True)

    published = parse_date(info.get("publication_date")) or retrieved_at
    rights = info["rights"]
    manifest = RawManifest(
        artifact_id=f"zenodo-{record_id}",
        version=version,
        created_at=retrieved_at,
        access_scope=request.access_scope,
        source=SourceRef(name="zenodo", url=info["source"]["url"], study_id=record_id),
        storage=stored.storage,
        checksum=stored.checksum,
        retrieved_at=retrieved_at,
        coverage=Coverage(),
        rights=Rights(**rights),
        extensions=SourceItem(
            source_id="zenodo",
            product=PRODUCT,
            source_item_id=source_item_id,
            source_key=source_key,
            kind="tabular",
            time_start=published,
            time_end=published,
            time_precision=TimePrecision.STATIC,
            available_at=published,
            processing_version=processing_version,
            assets={"data": filename},
            properties={"title": info.get("title"), "data_kind": "external_research_data"},
        ),
    )
    result.manifests.append(archive.record(manifest))
    return result


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value).replace(tzinfo=UTC)

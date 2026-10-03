"""Zenodo public API — search and download open research datasets."""

from __future__ import annotations

import json
import re
import urllib.error
from typing import Any
from urllib.parse import quote

from fetch.archive import register_raw_artifact
from fetch.http_util import fetch_bytes, fetch_json
from fetch.models import Coverage, RawManifest, Rights, SourceRef

ZENODO_RECORDS_API = "https://zenodo.org/api/records"
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024  # 10 MiB safety cap


def _parse_zenodo_id(dataset_id: str) -> str | None:
    m = re.fullmatch(r"zenodo:(\d+)", dataset_id.strip())
    return m.group(1) if m else None


def search_zenodo(query: str, *, max_results: int = 5) -> list[dict[str, Any]]:
    url = f"{ZENODO_RECORDS_API}?q={quote(query)}&size={max_results}"
    try:
        data = fetch_json(url)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return [{"error": True, "message": str(exc), "source": "zenodo"}]

    hits: list[dict[str, Any]] = []
    for hit in data.get("hits", {}).get("hits", []):
        meta = hit.get("metadata") or {}
        desc = meta.get("description") or ""
        if isinstance(desc, str) and len(desc) > 400:
            desc = desc[:400] + "…"
        hits.append(
            {
                "dataset_id": f"zenodo:{hit['id']}",
                "title": meta.get("title", ""),
                "description": desc,
                "species": [],
                "data_kind": "external_research_data",
                "source_name": "zenodo",
                "landing_page": hit.get("links", {}).get("html"),
            }
        )
    return hits


def inspect_zenodo(dataset_id: str) -> dict[str, Any]:
    rec_id = _parse_zenodo_id(dataset_id)
    if not rec_id:
        return {"found": False, "dataset_id": dataset_id}
    try:
        hit = fetch_json(f"{ZENODO_RECORDS_API}/{rec_id}")
    except Exception as exc:  # noqa: BLE001
        return {"found": False, "dataset_id": dataset_id, "error": str(exc)}

    meta = hit.get("metadata") or {}
    files = [
        {
            "filename": f.get("key"),
            "size": f.get("size"),
            "download_url": f.get("links", {}).get("self"),
        }
        for f in hit.get("files") or []
    ]
    license_info = meta.get("license") or {}
    return {
        "found": True,
        "dataset_id": dataset_id,
        "title": meta.get("title"),
        "description": meta.get("description"),
        "files": files,
        "coverage": {"species": [], "bbox": None, "start": None, "end": None},
        "source": {
            "name": "zenodo",
            "url": hit.get("links", {}).get("html") or f"https://zenodo.org/record/{rec_id}",
            "study_id": rec_id,
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
        return {
            "dataset_id": dataset_id,
            "status": "not_found",
            "message": info.get("error", "Unknown Zenodo record."),
        }
    files = info.get("files") or []
    if not files:
        return {
            "dataset_id": dataset_id,
            "status": "unavailable",
            "message": "Record has no downloadable files.",
        }
    return {
        "dataset_id": dataset_id,
        "status": "available",
        "file_count": len(files),
        "license": (info.get("rights") or {}).get("license"),
    }


def download_zenodo(dataset_id: str, *, prefer_filename: str | None = None) -> RawManifest | dict[str, Any]:
    rec_id = _parse_zenodo_id(dataset_id)
    if not rec_id:
        return {"status": "error", "code": "invalid_id", "message": "Expected zenodo:<record_id>"}

    info = inspect_zenodo(dataset_id)
    if not info.get("found"):
        return {"status": "error", "code": "not_found", "message": "Zenodo record not found."}

    files = info.get("files") or []
    chosen = None
    if prefer_filename:
        for f in files:
            if f.get("filename") == prefer_filename:
                chosen = f
                break
    if chosen is None:
        # Prefer smallest CSV/JSON under size cap
        candidates = [
            f
            for f in files
            if f.get("download_url")
            and (f.get("size") or 0) <= MAX_DOWNLOAD_BYTES
            and str(f.get("filename", "")).lower().endswith((".csv", ".json", ".txt", ".zip"))
        ]
        if not candidates:
            candidates = [f for f in files if f.get("download_url") and (f.get("size") or 0) <= MAX_DOWNLOAD_BYTES]
        if not candidates:
            return {
                "status": "error",
                "code": "no_suitable_file",
                "message": "No file under size limit; try a specific record with a small CSV.",
            }
        chosen = min(candidates, key=lambda f: f.get("size") or 0)

    url = chosen["download_url"]
    filename = chosen.get("filename") or "download.bin"
    try:
        content = fetch_bytes(url, max_bytes=MAX_DOWNLOAD_BYTES)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "code": "download_failed", "message": str(exc)}

    source = SourceRef(
        name="zenodo",
        url=info["source"]["url"],
        study_id=rec_id,
    )
    rights_raw = info.get("rights") or {}
    rights = Rights(
        license=rights_raw.get("license"),
        retention_allowed=rights_raw.get("retention_allowed"),
        reuse_allowed=rights_raw.get("reuse_allowed"),
        attribution=rights_raw.get("attribution"),
    )
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    source_key = f"zenodo:{rec_id}:{filename}:{chosen.get('size')}"

    return register_raw_artifact(
        content=content,
        filename=filename,
        source=source,
        coverage=Coverage(),
        rights=rights,
        source_key=source_key,
        file_format=ext,
    )

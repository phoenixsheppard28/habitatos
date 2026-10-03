"""Movebank connector — search, inspect, download (public preview or authenticated CSV)."""

from __future__ import annotations

import csv
import hashlib
from datetime import datetime, timezone
import io
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from fetch.archive import load_cached_by_source_key, register_raw_artifact, save_manifest
from fetch.http_util import DEFAULT_TIMEOUT_S, USER_AGENT, fetch_json
from fetch.models import Coverage, RawManifest, Rights, SourceRef

MOVEBANK_DIRECT_READ = "https://www.movebank.org/movebank/service/direct-read"
MOVEBANK_PUBLIC_JSON = "https://www.movebank.org/movebank/service/public/json"
GPS_SENSOR_TYPE_ID = "653"
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024
PUBLIC_PREVIEW_MAX_EVENTS_PER_INDIVIDUAL = 1
MOVEBANK_JSON_TIMEOUT_S = 45


@dataclass(frozen=True)
class PublicStudy:
    study_id: int
    name: str
    species: list[str]
    notes: str


# Known fully public studies (documented in Movebank API examples).
PUBLIC_STUDIES: list[PublicStudy] = [
    PublicStudy(
        2911040,
        "Galapagos Albatrosses",
        ["Phoebastria irrorata", "waved albatross"],
        "Public demo study in Movebank API documentation.",
    ),
    PublicStudy(
        16615296,
        "Movebank API example study",
        [],
        "Used in official API license/download examples.",
    ),
]


def _parse_movebank_id(dataset_id: str) -> int | None:
    m = re.fullmatch(r"movebank:(\d+)", dataset_id.strip())
    return int(m.group(1)) if m else None


def _credentials() -> tuple[str, str] | None:
    user = os.environ.get("MOVEBANK_USERNAME") or os.environ.get("MOVEBANK_USER")
    password = os.environ.get("MOVEBANK_PASSWORD") or os.environ.get("MOVEBANK_PASS")
    if user and password:
        return user, password
    return None


def _direct_read(params: dict[str, str], *, auth: tuple[str, str] | None) -> bytes:
    url = f"{MOVEBANK_DIRECT_READ}?{urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    if auth:
        user, password = auth
        credentials = f"{user}:{password}".encode()
        token = __import__("base64").b64encode(credentials).decode("ascii")
        req.add_header("Authorization", f"Basic {token}")

    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT_S) as resp:
            body = resp.read(MAX_DOWNLOAD_BYTES + 1)
    except urllib.error.HTTPError as exc:
        body = exc.read()
        if exc.code not in (403,):
            raise
        if b"License Terms:" not in body[:2000]:
            raise PermissionError("Movebank denied download access.") from exc

    if len(body) > MAX_DOWNLOAD_BYTES:
        raise ValueError(f"Movebank response exceeds {MAX_DOWNLOAD_BYTES} bytes")

    if b"License Terms:" in body[:2000]:
        raise PermissionError("Study license must be reviewed and accepted on Movebank before downloading.")
    if body.lstrip().lower().startswith((b"<!doctype", b"<html")):
        raise PermissionError("Movebank returned a login or access page instead of data.")

    return body


def _parse_csv(text: str) -> list[dict[str, str]]:
    if not text.strip():
        return []
    return list(csv.DictReader(io.StringIO(text)))


def _search_public_catalog(query: str) -> list[dict[str, Any]]:
    tokens = [t.lower() for t in query.split() if t]
    hits: list[dict[str, Any]] = []
    for study in PUBLIC_STUDIES:
        haystack = " ".join([study.name, study.notes, *study.species]).lower()
        if tokens and not all(tok in haystack for tok in tokens):
            continue
        hits.append(
            {
                "dataset_id": f"movebank:{study.study_id}",
                "title": study.name,
                "description": study.notes,
                "species": study.species,
                "data_kind": "animal_locations",
                "source_name": "movebank",
                "study_id": study.study_id,
            }
        )
    return hits


def _search_authenticated_catalog(query: str, auth: tuple[str, str]) -> list[dict[str, Any]]:
    raw = _direct_read(
        {
            "entity_type": "study",
            "i_have_download_access": "true",
            "attributes": "id,name,number_of_deployed_locations,taxon_ids,sensor_type_ids",
        },
        auth=auth,
    ).decode("utf-8", errors="replace")
    rows = _parse_csv(raw)
    tokens = [t.lower() for t in query.split() if t]
    hits: list[dict[str, Any]] = []
    for row in rows:
        name = row.get("name") or ""
        haystack = name.lower()
        if tokens and not all(tok in haystack for tok in tokens):
            continue
        study_id = row.get("id")
        if not study_id:
            continue
        hits.append(
            {
                "dataset_id": f"movebank:{study_id}",
                "title": name,
                "description": f"Movebank study with download access ({row.get('number_of_deployed_locations', '?')} locations).",
                "species": [],
                "data_kind": "animal_locations",
                "source_name": "movebank",
                "study_id": int(study_id),
            }
        )
    return hits


def search_movebank(query: str, *, max_results: int = 8) -> list[dict[str, Any]]:
    if not query.strip():
        return []
    hits = _search_public_catalog(query)
    auth = _credentials()
    if auth:
        try:
            hits.extend(_search_authenticated_catalog(query, auth))
        except Exception as exc:  # noqa: BLE001
            hits.append({"error": True, "source": "movebank", "message": str(exc)})
    # De-dupe by dataset_id, preserve order
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for hit in hits:
        if hit.get("error"):
            unique.append(hit)
            continue
        ds_id = hit.get("dataset_id")
        if not ds_id or ds_id in seen:
            continue
        seen.add(ds_id)
        unique.append(hit)
        if len(unique) >= max_results:
            break
    return unique


def inspect_movebank(dataset_id: str) -> dict[str, Any]:
    study_id = _parse_movebank_id(dataset_id)
    if study_id is None:
        return {"found": False, "dataset_id": dataset_id}

    public = next((s for s in PUBLIC_STUDIES if s.study_id == study_id), None)
    landing = f"https://www.movebank.org/study/{study_id}"

    # Known public studies: skip live API here (Movebank JSON can take 30–60s).
    species: list[str] = list(public.species) if public else []
    preview_individuals = 0
    if public is None:
        try:
            preview = fetch_json(
                f"{MOVEBANK_PUBLIC_JSON}?study_id={study_id}&sensor_type=gps"
                f"&max_events_per_individual=1",
                timeout=MOVEBANK_JSON_TIMEOUT_S,
            )
            individuals = preview.get("individuals") or []
            preview_individuals = len(individuals)
            for ind in individuals:
                taxon = ind.get("individual_taxon_canonical_name")
                if taxon and taxon not in species:
                    species.append(taxon)
        except Exception:  # noqa: BLE001
            pass

    return {
        "found": True,
        "dataset_id": dataset_id,
        "title": public.name if public else f"Movebank study {study_id}",
        "description": public.notes if public else "Movebank tracking study.",
        "species": species,
        "coverage": {"species": species, "bbox": None, "start": None, "end": None},
        "source": {"name": "movebank", "url": landing, "study_id": str(study_id)},
        "rights": {
            "license": "movebank_terms",
            "retention_allowed": None,
            "reuse_allowed": None,
            "attribution": public.name if public else f"Movebank study {study_id}",
        },
        "has_credentials": _credentials() is not None,
        "preview_individuals": preview_individuals,
    }


def check_movebank_access(dataset_id: str) -> dict[str, Any]:
    study_id = _parse_movebank_id(dataset_id)
    if study_id is None:
        return {"dataset_id": dataset_id, "status": "not_found", "message": "Invalid id."}

    if _credentials():
        return {
            "dataset_id": dataset_id,
            "status": "unknown",
            "mode": "authenticated_csv",
            "message": "Credentials configured; download must still verify study permissions and license acceptance.",
        }

    is_public = any(s.study_id == study_id for s in PUBLIC_STUDIES)
    if is_public:
        return {
            "dataset_id": dataset_id,
            "status": "available",
            "mode": "public_preview",
            "message": "No Movebank login — downloads a small public JSON sample (not full tracks).",
        }
    return {
        "dataset_id": dataset_id,
        "status": "restricted",
        "message": "Set MOVEBANK_USERNAME and MOVEBANK_PASSWORD in .env for download.",
    }


def _public_json_to_csv(study_id: int, *, max_events_per_individual: int) -> bytes:
    url = (
        f"{MOVEBANK_PUBLIC_JSON}?study_id={study_id}&sensor_type=gps"
        f"&max_events_per_individual={max_events_per_individual}"
    )
    payload = fetch_json(url, timeout=MOVEBANK_JSON_TIMEOUT_S)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "study_id",
            "individual_local_identifier",
            "individual_taxon_canonical_name",
            "timestamp",
            "location_long",
            "location_lat",
        ]
    )
    for ind in payload.get("individuals") or []:
        for loc in ind.get("locations") or []:
            writer.writerow(
                [
                    study_id,
                    ind.get("individual_local_identifier"),
                    ind.get("individual_taxon_canonical_name"),
                    loc.get("timestamp"),
                    loc.get("location_long"),
                    loc.get("location_lat"),
                ]
            )
    return buf.getvalue().encode("utf-8")


def _download_authenticated_gps_csv(study_id: int, auth: tuple[str, str]) -> bytes:
    body = _direct_read(
        {
            "entity_type": "event",
            "study_id": str(study_id),
            "sensor_type_id": GPS_SENSOR_TYPE_ID,
            "attributes": "individual_local_identifier,timestamp,location_long,location_lat,individual_taxon_canonical_name",
        },
        auth=auth,
    )
    return body


def download_movebank(dataset_id: str) -> RawManifest | dict[str, Any]:
    study_id = _parse_movebank_id(dataset_id)
    if study_id is None:
        return {"status": "error", "code": "invalid_id", "message": "Expected movebank:<study_id>"}

    access = check_movebank_access(dataset_id)
    if access.get("status") == "not_found":
        return {"status": "error", "code": "not_found", "message": access.get("message")}
    if access.get("status") == "restricted":
        return {"status": "error", "code": "restricted", "message": access.get("message")}

    auth = _credentials()
    mode = access.get("mode")
    if auth and mode == "authenticated_csv":
        note = "full_csv"
        source_key = f"movebank:{study_id}:full_csv:{hashlib.sha256(auth[0].encode()).hexdigest()[:16]}"
    else:
        note = "public_preview"
        n = PUBLIC_PREVIEW_MAX_EVENTS_PER_INDIVIDUAL
        source_key = f"movebank:{study_id}:public_preview_json:max{n}"

    cached_manifest = load_cached_by_source_key(source_key)
    if cached_manifest is not None:
        cached_manifest.extensions.setdefault("movebank_download_mode", note)
        cached_manifest.extensions["cache_hit"] = True
        save_manifest(cached_manifest)
        return cached_manifest

    try:
        if auth and mode == "authenticated_csv":
            content = _download_authenticated_gps_csv(study_id, auth)
            filename = f"movebank-{study_id}-gps.csv"
        else:
            payload = fetch_json(
                f"{MOVEBANK_PUBLIC_JSON}?study_id={study_id}&sensor_type=gps"
                f"&max_events_per_individual={PUBLIC_PREVIEW_MAX_EVENTS_PER_INDIVIDUAL}",
                timeout=MOVEBANK_JSON_TIMEOUT_S,
            )
            content = json.dumps(payload).encode("utf-8")
            filename = f"movebank-{study_id}-preview.json"
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "code": "download_failed", "message": str(exc)}

    try:
        if note == "public_preview":
            rows = [loc for ind in payload.get("individuals", []) for loc in ind.get("locations", [])]
        else:
            rows = _parse_csv(content.decode("utf-8-sig"))
        if not rows:
            return {"status": "error", "code": "empty", "message": "No GPS events returned."}
        if not all(all(k in row for k in ("timestamp", "location_long", "location_lat")) for row in rows):
            return {"status": "error", "code": "invalid_payload", "message": "Response is not a GPS event dataset."}
    except (ValueError, TypeError, AttributeError):
        return {"status": "error", "code": "invalid_payload", "message": "Invalid Movebank event response."}

    info = inspect_movebank(dataset_id)
    source = SourceRef(
        name="movebank",
        url=info["source"]["url"],
        study_id=str(study_id),
    )
    rights = Rights(
        license="movebank_terms",
        retention_allowed=None,
        reuse_allowed=None,
        attribution=info.get("title"),
    )
    coverage = Coverage(species=list(info.get("species") or []))
    try:
        points = [(float(r["location_long"]), float(r["location_lat"])) for r in rows]
        if all(-180 <= x <= 180 and -90 <= y <= 90 for x, y in points):
            coverage.bbox = [min(x for x, y in points), min(y for x, y in points),
                             max(x for x, y in points), max(y for x, y in points)]
        # Public JSON timestamps are Unix milliseconds; CSV timestamps are UTC text.
        times = [datetime.fromtimestamp(float(r["timestamp"]) / 1000, timezone.utc)
                 if isinstance(r["timestamp"], (int, float)) else
                 datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")).replace(tzinfo=timezone.utc)
                 for r in rows]
        coverage.start = min(times).isoformat()
        coverage.end = max(times).isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        pass  # Unknown coverage remains unknown; never substitute the request bounds.


    manifest = register_raw_artifact(
        content=content,
        filename=filename,
        source=source,
        coverage=coverage,
        rights=rights,
        source_key=source_key,
        file_format="json" if note == "public_preview" else "csv",
        access_scope="public" if note == "public_preview" else "movebank-account",
    )
    manifest.extensions["data_kind"] = "animal_locations"
    manifest.extensions["event_count"] = len(rows)
    manifest.extensions["coverage_basis"] = "returned_events"
    manifest.extensions["movebank_download_mode"] = note
    save_manifest(manifest)
    return manifest

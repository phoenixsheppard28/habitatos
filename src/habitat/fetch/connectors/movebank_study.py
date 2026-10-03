import csv
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import pandas as pd

from habitat.archive import Archive
from habitat.archive.index import AlreadyIngested, never_ingested
from habitat.config import settings
from habitat.contracts import Coverage, RawManifest, Rights, SourceItem, SourceRef, TimePrecision
from habitat.fetch import http
from habitat.fetch.connectors import ConnectorRequest, ConnectorResult

MOVEBANK_DIRECT_READ = "https://www.movebank.org/movebank/service/direct-read"
MOVEBANK_PUBLIC_JSON = "https://www.movebank.org/movebank/service/public/json"
PRODUCT = "movebank-direct-read"
STORAGE_FORMAT = "csv"
GPS_SENSOR_TYPE_ID = "653"
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024
PUBLIC_PREVIEW_MAX_EVENTS_PER_INDIVIDUAL = 1
MOVEBANK_JSON_TIMEOUT_S = 45
EVENT_ATTRIBUTES = [
    "event_id",
    "individual_local_identifier",
    "individual_taxon_canonical_name",
    "timestamp",
    "location_long",
    "location_lat",
]
ACCOUNT_SCOPE = "movebank-account"
PUBLIC_PREVIEW = "public_preview"
FULL_CSV = "full_csv"


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
    PublicStudy(16615296, "Movebank API example study", [], "Used in official API license/download examples."),
]


def parse_study_id(value: str | None) -> int | None:
    match = re.fullmatch(r"(?:movebank:)?(\d+)", (value or "").strip())
    return int(match.group(1)) if match else None


def direct_read(params: dict[str, str], auth: tuple[str, str] | None) -> bytes:
    with http.client(auth=auth) as client:
        response = client.get(MOVEBANK_DIRECT_READ, params=params)
    body = response.content[: MAX_DOWNLOAD_BYTES + 1]

    if response.status_code == 403 and b"License Terms:" not in body[:2000]:
        raise PermissionError("Movebank denied download access.")
    if response.status_code not in (200, 403):
        response.raise_for_status()
    if len(body) > MAX_DOWNLOAD_BYTES:
        raise ValueError(f"Movebank response exceeds {MAX_DOWNLOAD_BYTES} bytes")
    if b"License Terms:" in body[:2000]:
        raise PermissionError("Study license must be reviewed and accepted on Movebank before downloading.")
    if body.lstrip().lower().startswith((b"<!doctype", b"<html")):
        raise PermissionError("Movebank returned a login or access page instead of data.")
    return body


def parse_csv(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text))) if text.strip() else []


def search_public_catalog(query: str) -> list[dict[str, Any]]:
    tokens = [t.lower() for t in query.split() if t]
    hits = []
    for study in PUBLIC_STUDIES:
        haystack = " ".join([study.name, study.notes, *study.species]).lower()
        if tokens and not all(token in haystack for token in tokens):
            continue
        hits.append(catalog_hit(study.study_id, study.name, study.notes, study.species))
    return hits


def search_authenticated_catalog(query: str, auth: tuple[str, str]) -> list[dict[str, Any]]:
    raw = direct_read(
        {
            "entity_type": "study",
            "i_have_download_access": "true",
            "attributes": "id,name,number_of_deployed_locations,taxon_ids,sensor_type_ids",
        },
        auth,
    ).decode("utf-8", errors="replace")
    tokens = [t.lower() for t in query.split() if t]
    hits = []
    for row in parse_csv(raw):
        name = row.get("name") or ""
        if (tokens and not all(token in name.lower() for token in tokens)) or not row.get("id"):
            continue
        locations = row.get("number_of_deployed_locations", "?")
        hits.append(catalog_hit(int(row["id"]), name, f"Movebank study with download access ({locations} locations).", []))
    return hits


def catalog_hit(study_id: int, title: str, description: str, species: list[str]) -> dict[str, Any]:
    return {
        "dataset_id": f"movebank:{study_id}",
        "source_id": "movebank_study",
        "title": title,
        "description": description,
        "species": species,
        "data_kind": "animal_locations",
        "study_id": study_id,
    }


def search_movebank(query: str, max_results: int = 8) -> list[dict[str, Any]]:
    if not query.strip():
        return []

    hits = search_public_catalog(query)
    if auth := settings().movebank_credentials:
        try:
            hits.extend(search_authenticated_catalog(query, auth))
        except Exception as error:
            hits.append({"error": True, "source_id": "movebank_study", "message": str(error)})

    seen: set[str] = set()
    unique = []
    for hit in hits:
        if hit.get("error"):
            unique.append(hit)
            continue
        if hit["dataset_id"] in seen:
            continue
        seen.add(hit["dataset_id"])
        unique.append(hit)
        if len(unique) >= max_results:
            break
    return unique


def inspect_movebank(dataset_id: str) -> dict[str, Any]:
    study_id = parse_study_id(dataset_id)
    if study_id is None:
        return {"found": False, "dataset_id": dataset_id}

    public = next((s for s in PUBLIC_STUDIES if s.study_id == study_id), None)
    species = list(public.species) if public else []
    preview_individuals = 0
    # Known public studies skip the live API here: the Movebank JSON service can take 30–60 s.
    if public is None:
        try:
            preview = http.get_json(
                MOVEBANK_PUBLIC_JSON,
                params={"study_id": study_id, "sensor_type": "gps", "max_events_per_individual": 1},
                timeout=MOVEBANK_JSON_TIMEOUT_S,
            )
            individuals = preview.get("individuals") or []
            preview_individuals = len(individuals)
            for individual in individuals:
                taxon = individual.get("individual_taxon_canonical_name")
                if taxon and taxon not in species:
                    species.append(taxon)
        except Exception:
            pass

    title = public.name if public else f"Movebank study {study_id}"
    return {
        "found": True,
        "dataset_id": f"movebank:{study_id}",
        "source_id": "movebank_study",
        "title": title,
        "description": public.notes if public else "Movebank tracking study.",
        "species": species,
        "coverage": {"species": species, "bbox": None, "start": None, "end": None},
        "source": {"name": "movebank", "url": f"https://www.movebank.org/study/{study_id}", "study_id": str(study_id)},
        "rights": {"license": "movebank_terms", "retention_allowed": None, "reuse_allowed": None, "attribution": title},
        "has_credentials": settings().movebank_credentials is not None,
        "preview_individuals": preview_individuals,
    }


def check_movebank_access(dataset_id: str) -> dict[str, Any]:
    study_id = parse_study_id(dataset_id)
    if study_id is None:
        return {"dataset_id": dataset_id, "status": "not_found", "message": "Invalid id."}

    if settings().movebank_credentials:
        return {
            "dataset_id": dataset_id,
            "status": "unknown",
            "mode": "authenticated_csv",
            "message": "Credentials configured; download must still verify study permissions and license acceptance.",
        }

    if any(s.study_id == study_id for s in PUBLIC_STUDIES):
        return {
            "dataset_id": dataset_id,
            "status": "available",
            "mode": PUBLIC_PREVIEW,
            "message": "No Movebank login — downloads a small public sample (not full tracks).",
        }
    return {
        "dataset_id": dataset_id,
        "status": "restricted",
        "message": "Set MOVEBANK_USERNAME and MOVEBANK_PASSWORD in .env for download.",
    }


def public_preview_csv(study_id: int) -> bytes:
    payload = http.get_json(
        MOVEBANK_PUBLIC_JSON,
        params={
            "study_id": study_id,
            "sensor_type": "gps",
            "max_events_per_individual": PUBLIC_PREVIEW_MAX_EVENTS_PER_INDIVIDUAL,
        },
        timeout=MOVEBANK_JSON_TIMEOUT_S,
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(EVENT_ATTRIBUTES)
    for individual in payload.get("individuals") or []:
        for location in individual.get("locations") or []:
            # Public JSON timestamps are Unix milliseconds; the direct-read CSV has UTC text.
            observed = datetime.fromtimestamp(float(location["timestamp"]) / 1000, UTC)
            writer.writerow([
                "",
                individual.get("individual_local_identifier"),
                individual.get("individual_taxon_canonical_name"),
                observed.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                location.get("location_long"),
                location.get("location_lat"),
            ])
    return buffer.getvalue().encode()


def authenticated_csv(study_id: int, auth: tuple[str, str]) -> bytes:
    return direct_read(
        {
            "entity_type": "event",
            "study_id": str(study_id),
            "sensor_type_id": GPS_SENSOR_TYPE_ID,
            "attributes": ",".join(EVENT_ATTRIBUTES),
        },
        auth,
    )


def fetch_movebank_study(
    request: ConnectorRequest, archive: Archive, already_ingested: AlreadyIngested = never_ingested
) -> ConnectorResult:
    result = ConnectorResult()
    study_id = parse_study_id(request.item)
    if study_id is None:
        return result.fail("invalid_request", "movebank_study needs a study id, such as movebank:2911040")

    access = check_movebank_access(f"movebank:{study_id}")
    if access["status"] == "restricted":
        return result.fail("restricted", access["message"])

    auth = settings().movebank_credentials
    if auth:
        mode = FULL_CSV
        account = hashlib.sha256(auth[0].encode()).hexdigest()[:16]
        source_key = f"movebank_study:{study_id}:{FULL_CSV}:{account}"
    else:
        mode = PUBLIC_PREVIEW
        source_key = f"movebank_study:{study_id}:{PUBLIC_PREVIEW}:max{PUBLIC_PREVIEW_MAX_EVENTS_PER_INDIVIDUAL}"

    if (cached := archive.cached(source_key)) is not None:
        result.manifests.append(cached)
        return result

    try:
        content = authenticated_csv(study_id, auth) if auth else public_preview_csv(study_id)
    except PermissionError as error:
        return result.fail("restricted", str(error))
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
        return result.fail("download_failed", f"Movebank study {study_id}: {type(error).__name__}", retryable=True)

    try:
        rows = parse_csv(content.decode("utf-8-sig"))
    except UnicodeDecodeError:
        return result.fail("invalid_payload", "Movebank response is not text")
    if not rows:
        return result.fail("empty", "No GPS events returned.")
    if not all(row.get("timestamp") and row.get("location_long") and row.get("location_lat") for row in rows):
        return result.fail("invalid_payload", "Response is not a GPS event dataset.")

    access_scope = ACCOUNT_SCOPE if mode == FULL_CSV else request.access_scope
    manifest = study_manifest(study_id, mode, source_key, content, rows, access_scope, archive)
    if not already_ingested(manifest.extensions.source_item_id, manifest.extensions.processing_version, "final"):
        result.manifests.append(manifest)
    return result


def study_manifest(
    study_id: int, mode: str, source_key: str, content: bytes, rows: list[dict], access_scope: str, archive: Archive
) -> RawManifest:
    info = inspect_movebank(f"movebank:{study_id}")
    filename = f"movebank-{study_id}-{'gps' if mode == FULL_CSV else 'preview'}.csv"
    retrieved_at = datetime.now(UTC)
    version, stored = archive.put(f"movebank-study-{study_id}-{mode}", {filename: content}, STORAGE_FORMAT)

    times = pd.to_datetime([row["timestamp"] for row in rows], utc=True)
    points = [(float(row["location_long"]), float(row["location_lat"])) for row in rows]
    bbox = None
    if all(-180 <= x <= 180 and -90 <= y <= 90 for x, y in points):
        xs, ys = [x for x, _ in points], [y for _, y in points]
        bbox = (min(xs), min(ys), max(xs), max(ys))

    manifest = RawManifest(
        artifact_id=f"movebank-study-{study_id}-{mode}",
        version=version,
        created_at=retrieved_at,
        access_scope=access_scope,
        source=SourceRef(name="movebank", url=info["source"]["url"], study_id=str(study_id)),
        storage=stored.storage,
        checksum=stored.checksum,
        retrieved_at=retrieved_at,
        coverage=Coverage(species=list(info["species"]), bbox=bbox, start=times.min(), end=times.max()),
        rights=Rights(license="movebank_terms", attribution=info["title"]),
        extensions=SourceItem(
            source_id="movebank_study",
            product=PRODUCT,
            source_item_id=f"movebank:{study_id}:{mode}",
            source_key=source_key,
            kind="tabular",
            time_start=times.min().to_pydatetime(),
            time_end=times.max().to_pydatetime(),
            time_precision=TimePrecision.INSTANT,
            available_at=retrieved_at,
            processing_version=stored.checksum,
            assets={"locations": filename},
            properties={
                "study_id": str(study_id),
                "movebank_download_mode": mode,
                "event_count": len(rows),
                "coverage_basis": "returned_events",
                "data_kind": "animal_locations",
            },
        ),
    )
    return archive.record(manifest)

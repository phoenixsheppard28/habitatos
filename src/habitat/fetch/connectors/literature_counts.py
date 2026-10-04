"""Reviewed literature files in the repository. The connector copies one file into the archive; it uses no network."""

import hashlib
import re
from datetime import UTC, datetime, time
from typing import Any

from pydantic import ValidationError

from habitat.archive import Archive
from habitat.archive.index import AlreadyIngested, never_ingested
from habitat.config import PROJECT_ROOT
from habitat.contracts import Coverage, RawManifest, Rights, SourceItem, SourceRef, TimePrecision
from habitat.fetch.connectors import ConnectorRequest, ConnectorResult
from habitat.normalize.sources.literature_counts import (
    DATE_FORMAT,
    SOURCE_ID,
    LiteratureMetadata,
    read_literature_file,
    read_metadata,
)

LITERATURE_DIR = PROJECT_ROOT / "reference" / "literature_counts"
PRODUCT = "literature-counts"
STORAGE_FORMAT = "csv"
DATASET_PREFIX = f"{SOURCE_ID}:"
CITATION_KEY_PATTERN = re.compile(r"^[a-z0-9_]+$")
DESCRIPTION = (
    "Animal counts and population estimates typed from papers and census reports, one reviewed file per publication"
)


def git_blob_sha(content: bytes) -> str:
    """The id that git gives the file content, so a processing version names one committed revision."""
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


def citation_keys() -> list[str]:
    return sorted(path.stem for path in LITERATURE_DIR.glob("*.csv") if path.with_suffix(".json").is_file())


def catalog_entries() -> list[dict[str, Any]]:
    return [catalog_entry(key) for key in citation_keys()]


def catalog_entry(citation_key: str) -> dict[str, Any]:
    metadata = read_metadata(LITERATURE_DIR / f"{citation_key}.json")
    rows = read_literature_file(LITERATURE_DIR / f"{citation_key}.csv")
    return {
        "dataset_id": f"{DATASET_PREFIX}{citation_key}",
        "source_id": SOURCE_ID,
        "title": metadata.citation,
        "description": f"{len(rows)} values typed from {citation_key}. Access scope {metadata.access_scope}.",
        "species": sorted(set(rows.get("taxon_name", []))),
        "areas": sorted(set(rows.get("area_name", []))),
        "bbox": located_bbox(rows),
        "method": ", ".join(sorted(set(rows.get("method", [])))),
        "area_type": ", ".join(sorted(set(rows.get("area_type", [])))),
        "data_kind": "population_counts",
        "license": metadata.license,
        "access_scope": metadata.access_scope,
    }


def located_bbox(rows) -> list[float] | None:
    if not {"longitude", "latitude"} <= set(rows.columns):
        return None

    located = rows[(rows["longitude"] != "") & (rows["latitude"] != "")]
    if located.empty:
        return None

    longitudes, latitudes = located["longitude"].astype(float), located["latitude"].astype(float)
    return [float(longitudes.min()), float(latitudes.min()), float(longitudes.max()), float(latitudes.max())]


def inspect_literature(dataset_id: str) -> dict[str, Any]:
    citation_key = dataset_id.removeprefix(DATASET_PREFIX)
    if citation_key not in citation_keys():
        return {"found": False, "dataset_id": dataset_id}

    metadata = read_metadata(LITERATURE_DIR / f"{citation_key}.json")
    return {
        "found": True,
        **catalog_entry(citation_key),
        "publication_date": metadata.published.isoformat(),
        "source": {
            "name": "Reviewed literature values", "url": f"https://doi.org/{metadata.doi}", "study_id": citation_key
        },
        "rights": rights(metadata).model_dump(),
        "checked_by": metadata.checked_by,
    }


def check_literature_access(dataset_id: str) -> dict[str, Any]:
    citation_key = dataset_id.removeprefix(DATASET_PREFIX)
    if citation_key not in citation_keys():
        return {"dataset_id": dataset_id, "status": "not_found", "message": "Unknown literature file."}

    metadata = read_metadata(LITERATURE_DIR / f"{citation_key}.json")
    access = {"dataset_id": dataset_id, "license": metadata.license, "access_scope": metadata.access_scope}
    if metadata.access_scope != "public":
        return access | {
            "status": "restricted",
            "message": f"Access scope {metadata.access_scope}: a person must check the license and the values first.",
        }
    return access | {"status": "available"}


def fetch_literature_counts(
    request: ConnectorRequest, archive: Archive, already_ingested: AlreadyIngested = never_ingested
) -> ConnectorResult:
    """The access scope comes from the metadata file. A file with an unchecked license stays out of the public scope."""
    result = ConnectorResult()
    citation_key = (request.item or "").removeprefix(DATASET_PREFIX)
    if not CITATION_KEY_PATTERN.match(citation_key):
        return result.fail("invalid_request", f"{SOURCE_ID} needs a citation key of lowercase letters, digits and _")

    data_path, metadata_path = LITERATURE_DIR / f"{citation_key}.csv", LITERATURE_DIR / f"{citation_key}.json"
    if not (data_path.is_file() and metadata_path.is_file()):
        return result.fail("not_found", f"no literature file {citation_key}; known: {citation_keys()}")

    try:
        metadata = read_metadata(metadata_path)
    except ValidationError as error:
        return result.fail("invalid_request", f"{metadata_path.name}: {error.error_count()} metadata problem(s)")

    data, metadata_bytes = data_path.read_bytes(), metadata_path.read_bytes()
    processing_version = f"git:{git_blob_sha(data)}"
    if already_ingested(citation_key, processing_version, "final"):
        return result

    source_key = f"{SOURCE_ID}:{citation_key}:{git_blob_sha(data)}:{git_blob_sha(metadata_bytes)}"
    if (cached := archive.cached(source_key)) is not None:
        result.manifests.append(cached)
        return result

    period = file_period(data_path)
    if period is None:
        return result.fail("invalid_payload", f"{data_path.name} has no valid dates")

    retrieved_at = datetime.now(UTC)
    artifact_id = f"{SOURCE_ID}-{citation_key}"
    version, stored = archive.put(
        artifact_id, {data_path.name: data, metadata_path.name: metadata_bytes}, STORAGE_FORMAT
    )
    manifest = RawManifest(
        artifact_id=artifact_id,
        version=version,
        created_at=retrieved_at,
        access_scope=metadata.access_scope,
        source=SourceRef(
            name="Reviewed literature values", url=f"https://doi.org/{metadata.doi}" if metadata.doi else None,
            study_id=citation_key,
        ),
        storage=stored.storage,
        checksum=stored.checksum,
        retrieved_at=retrieved_at,
        coverage=Coverage(start=period[0], end=period[1]),
        rights=rights(metadata),
        extensions=SourceItem(
            source_id=SOURCE_ID,
            product=PRODUCT,
            source_item_id=citation_key,
            source_key=source_key,
            kind="tabular",
            time_start=period[0],
            time_end=period[1],
            time_precision=TimePrecision.COMPOSITE,
            available_at=datetime.combine(metadata.published, time.min, tzinfo=UTC),
            processing_version=processing_version,
            assets={"data": data_path.name, "metadata": metadata_path.name},
            properties={
                "doi": metadata.doi,
                "data_kind": "population_counts",
                "entered_by": metadata.entered_by,
                "checked_by": metadata.checked_by,
            },
        ),
    )
    result.manifests.append(archive.record(manifest))
    return result


def rights(metadata: LiteratureMetadata) -> Rights:
    return Rights(
        license=metadata.license, retention_allowed=True, reuse_allowed=metadata.reuse_allowed,
        attribution=metadata.citation,
    )


def file_period(path) -> tuple[datetime, datetime] | None:
    rows = read_literature_file(path)
    days = []
    for column in ("time_start", "time_end"):
        for text in rows.get(column, []):
            try:
                days.append(datetime.strptime(text, DATE_FORMAT).replace(tzinfo=UTC))
            except ValueError:
                continue
    return (min(days), max(days)) if days else None

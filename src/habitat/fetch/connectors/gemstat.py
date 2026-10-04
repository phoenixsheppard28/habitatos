"""GEMStat open archive on Zenodo (UNEP GEMS/Water, BfG). One ZIP of about 201 MB holds all open data.

The connector downloads the ZIP once and keeps it in the archive. For each request it also keeps an extract with the
stations in the bbox and the samples in the dates, in the same layout. The extract holds unchanged source rows.
"""

import csv
import hashlib
import io
import re
import zipfile
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from habitat.archive import Archive
from habitat.archive.index import AlreadyIngested, never_ingested
from habitat.contracts import BBox, Coverage, RawManifest, Rights, SourceItem, SourceRef, TimePrecision
from habitat.fetch import http
from habitat.fetch.connectors import ConnectorRequest, ConnectorResult, validate_area_and_dates
from habitat.fetch.connectors.zenodo import ZENODO_RECORDS_API, parse_date
from habitat.normalize.sources.gemstat import DATA_FILES, ENCODING, STATION_FILE, monitoring_sites, read_csv
from habitat.normalize.water_quality import in_area

RECORD_ID = "18459694"
CONCEPT_DOI = "10.5281/zenodo.13881899"
ARCHIVE_FILE = "GFQA_v3.zip"
EXTRACT_FILE = "gemstat_extract.zip"
ALLOWED_HOSTS = {"zenodo.org"}
PRODUCT = "gemstat-gfqa"
STORAGE_FORMAT = "zip"
RIGHTS = Rights(
    license="CC-BY-4.0",
    retention_allowed=True,
    reuse_allowed=True,
    attribution=f"UNEP GEMS/Water Global Freshwater Quality Archive, GEMS/Water Data Centre (BfG), doi:{CONCEPT_DOI}",
)


def record_version(record: dict, filename: str) -> str:
    """The Zenodo version label. The v3 record has none, so the label comes from the file name (GFQA_v3.zip)."""
    if version := (record.get("metadata") or {}).get("version"):
        return str(version)

    match = re.search(r"_(v\d+)\.zip$", filename)
    return match.group(1) if match else f"revision:{record.get('revision')}"


def fetch_gemstat(
    request: ConnectorRequest, archive: Archive, already_ingested: AlreadyIngested = never_ingested
) -> ConnectorResult:
    result = ConnectorResult()
    try:
        first, last = validate_area_and_dates(request)
    except ValueError as error:
        return result.fail("invalid_request", str(error))

    try:
        record = http.get_json(f"{ZENODO_RECORDS_API}/{RECORD_ID}")
        archive_file = next(f for f in record["files"] if f["key"] == ARCHIVE_FILE)
    except Exception as error:
        return result.fail("not_found", f"gemstat: Zenodo record {RECORD_ID} ({type(error).__name__})", True)

    published = parse_date((record.get("metadata") or {}).get("publication_date"))
    if published is None:
        return result.fail("no_publication_date", "gemstat: the Zenodo record gives no publication date")

    version_label = record_version(record, ARCHIVE_FILE)
    bbox_key = ",".join(f"{value:.5f}" for value in request.bbox)
    source_item_id = f"gemstat:{RECORD_ID}:{bbox_key}:{first}:{last}"
    if already_ingested(source_item_id, version_label, "final"):
        return result

    base_key = f"gemstat:{RECORD_ID}:{archive_file['checksum']}"
    source_key = f"{base_key}:{bbox_key}:{first}:{last}"
    if (cached := archive.cached(source_key)) is not None:
        result.manifests.append(cached)
        return result

    try:
        full = archive.cached(base_key) or archive_full_file(
            request, archive, record, archive_file, base_key, published, version_label
        )
        manifest = archive_extract(
            request, archive, full, source_key, source_item_id, published, version_label, first, last, result
        )
    except Exception as error:
        detail = str(error) if isinstance(error, ValueError) else type(error).__name__
        return result.fail("download_failed", f"gemstat: {detail}", retryable=True)

    if manifest is not None:
        result.manifests.append(manifest)
    return result


def archive_full_file(
    request: ConnectorRequest,
    archive: Archive,
    record: dict,
    archive_file: dict,
    base_key: str,
    published: datetime,
    version_label: str,
) -> RawManifest:
    artifact_id = f"gemstat-{RECORD_ID}"
    with archive.store.staging() as staging:
        target = staging / ARCHIVE_FILE
        http.download(
            archive_file["links"]["self"], target, max_bytes=request.max_file_bytes, allowed_hosts=ALLOWED_HOSTS
        )
        expected = archive_file["checksum"].removeprefix("md5:")
        if md5(target) != expected:
            raise ValueError("the downloaded archive does not match the Zenodo checksum")

        retrieved_at = datetime.now(UTC)
        version, stored = archive.put(artifact_id, {ARCHIVE_FILE: target}, STORAGE_FORMAT)

    manifest = manifest_for(
        artifact_id, version, stored, request, published, version_label, retrieved_at,
        source_item_id=f"gemstat:{RECORD_ID}", source_key=base_key, filename=ARCHIVE_FILE,
        start=published, end=published, properties={"title": (record.get("metadata") or {}).get("title")},
    )
    return archive.record(manifest)


def archive_extract(
    request: ConnectorRequest,
    archive: Archive,
    full: RawManifest,
    source_key: str,
    source_item_id: str,
    published: datetime,
    version_label: str,
    first: date,
    last: date,
    result: ConnectorResult,
) -> RawManifest | None:
    artifact_id = f"gemstat-{RECORD_ID}-{hashlib.sha256(source_key.encode()).hexdigest()[:16]}"
    with archive.store.staging() as staging:
        target = staging / EXTRACT_FILE
        sample_count = write_extract(archive.store.open(full, "archive"), target, tuple(request.bbox), first, last)
        if sample_count == 0:
            result.warnings.append(
                "gemstat: no open station with samples in this region and these dates (a coverage gap, not an error); "
                "the open archive has no station in Africa"
            )
            return None

        retrieved_at = datetime.now(UTC)
        version, stored = archive.put(artifact_id, {EXTRACT_FILE: target}, STORAGE_FORMAT)

    start = datetime.combine(first, time.min, tzinfo=UTC)
    end = datetime.combine(last, time.min, tzinfo=UTC) + timedelta(days=1)
    manifest = manifest_for(
        artifact_id, version, stored, request, published, version_label, retrieved_at,
        source_item_id=source_item_id, source_key=source_key, filename=EXTRACT_FILE, start=start, end=end,
        properties={"requested_bbox": list(request.bbox), "extract_of": full.storage.uri},
    )
    return archive.record(manifest)


def write_extract(source: Path, target: Path, bbox: BBox, first: date, last: date) -> int:
    """Copy the station rows in the bbox and their sample rows in the dates. Returns the number of sample rows."""
    sample_count = 0
    with zipfile.ZipFile(source) as full, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as extract:
        stations = read_csv(full, STATION_FILE)
        sites = monitoring_sites(stations)
        wanted = set(sites.loc[in_area(sites, bbox), "local_site_id"])
        if not wanted:
            return sample_count

        extract.writestr(STATION_FILE, to_csv(stations[stations["GEMS Station Number"].isin(wanted)]))
        names = set(full.namelist())
        for name in DATA_FILES:
            if name not in names:
                continue

            samples = read_csv(full, name)
            samples = samples[samples["GEMS Station Number"].isin(wanted)]
            day = samples["Sample Date"].map(date.fromisoformat)
            kept = samples[(day >= first) & (day <= last)]
            if not kept.empty:
                extract.writestr(name, to_csv(kept))
                sample_count += len(kept)

    return sample_count


def to_csv(frame) -> bytes:
    text = io.StringIO()
    frame.to_csv(text, index=False, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    return text.getvalue().encode(ENCODING)


def md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def manifest_for(
    artifact_id: str,
    version: str,
    stored,
    request: ConnectorRequest,
    published: datetime,
    version_label: str,
    retrieved_at: datetime,
    *,
    source_item_id: str,
    source_key: str,
    filename: str,
    start: datetime,
    end: datetime,
    properties: dict,
) -> RawManifest:
    return RawManifest(
        artifact_id=artifact_id,
        version=version,
        created_at=retrieved_at,
        access_scope=request.access_scope,
        source=SourceRef(name="GEMStat (Zenodo)", url=f"https://zenodo.org/records/{RECORD_ID}", study_id=RECORD_ID),
        storage=stored.storage,
        checksum=stored.checksum,
        retrieved_at=retrieved_at,
        coverage=Coverage(bbox=tuple(request.bbox), start=start, end=end),
        rights=RIGHTS,
        extensions=SourceItem(
            source_id="gemstat",
            product=PRODUCT,
            source_item_id=source_item_id,
            source_key=source_key,
            kind="tabular",
            time_start=start,
            time_end=end,
            time_precision=TimePrecision.COMPOSITE,
            available_at=published,
            processing_version=version_label,
            assets={"archive": filename},
            properties=properties,
        ),
    )

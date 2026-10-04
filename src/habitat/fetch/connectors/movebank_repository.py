import re
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from habitat.archive import Archive
from habitat.archive.index import AlreadyIngested, never_ingested
from habitat.archive.paths import safe_name
from habitat.contracts import Coverage, RawManifest, Rights, SourceItem, SourceRef, TimePrecision
from habitat.fetch import http
from habitat.fetch.connectors import ConnectorRequest, ConnectorResult

DATA_REPOSITORY_API = "https://datarepository.movebank.org/server/api/core"
SEARCH_API = "https://datarepository.movebank.org/server/api/discover/search/objects"
ALLOWED_HOSTS = {"datarepository.movebank.org"}
PRODUCT = "movebank-data-repository"
STORAGE_FORMAT = "csv"
REFERENCE_FILE_SUFFIX = "-reference-data.csv"
# Accessory files hold sensor data such as acceleration, not locations.
NOT_LOCATION_SUFFIXES = (REFERENCE_FILE_SUFFIX, "-accessory.csv")
LOCATION_COLUMNS = {"timestamp", "location-long", "location-lat"}
UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def metadata_value(item: dict, *keys: str) -> str | None:
    """The first value of the first key that the package has. Older and newer packages use different keys."""
    for key in keys:
        if values := item["metadata"].get(key):
            return values[0]["value"]
    return None


def published_at(item: dict) -> datetime:
    value = metadata_value(item, "dc.date.available", "dc.date.issued", "dc.date.accessioned")
    if value is None:
        raise ValueError("data package has no publication date")

    published = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return published if published.tzinfo else published.replace(tzinfo=UTC)


def package_files(item: dict) -> list[dict]:
    bundles = item["_embedded"]["bundles"]["_embedded"]["bundles"]
    original = next(bundle for bundle in bundles if bundle["name"] == "ORIGINAL")
    return original["_embedded"]["bitstreams"]["_embedded"]["bitstreams"]


def artifact_id_for(handle: str, location_name: str) -> str:
    return safe_name(f"{handle.replace('/', '_')}_{Path(location_name).stem}")


def fetch_movebank_repository(
    request: ConnectorRequest, archive: Archive, already_ingested: AlreadyIngested = never_ingested
) -> ConnectorResult:
    """One manifest per location file of a published data package. The reference file goes with each."""
    result = ConnectorResult()
    if request.item is None or not UUID_PATTERN.match(request.item):
        return result.fail("invalid_request", "movebank_repository needs a data package UUID")

    try:
        item = http.get_json(f"{DATA_REPOSITORY_API}/items/{request.item}", params={"embed": "bundles/bitstreams"})
        files = package_files(item)
    except Exception as error:
        return result.fail("not_found", f"data package {request.item}: {type(error).__name__}")

    reference = next((f for f in files if f["name"].endswith(REFERENCE_FILE_SUFFIX)), None)
    # File names vary between packages, so a CSV counts as a location file when its header has the location columns.
    candidates = [f for f in files if f["name"].endswith(".csv") and not f["name"].endswith(NOT_LOCATION_SUFFIXES)]
    handle = item["handle"]
    study_id = metadata_value(item, "mdr.study.id") or handle

    for location_file in candidates:
        name = safe_name(location_file["name"])
        source_item_id = f"{handle}/{name}"
        processing_version = f"md5:{location_file['checkSum']['value']}"
        if already_ingested(source_item_id, processing_version, "final"):
            continue

        source_key = f"movebank_repository:{source_item_id}:{processing_version}"
        if (cached := archive.cached(source_key)) is not None:
            result.manifests.append(cached)
            continue

        try:
            manifest = archive_location_file(
                item, location_file, reference, source_item_id, source_key, processing_version, study_id,
                request, archive,
            )
        except Exception as error:
            detail = str(error) if isinstance(error, ValueError) else type(error).__name__
            result.warnings.append(f"{source_item_id}: download failed ({detail})")
            continue

        if manifest is not None:
            result.manifests.append(manifest)
    return result


def archive_location_file(
    item: dict,
    location_file: dict,
    reference: dict | None,
    source_item_id: str,
    source_key: str,
    processing_version: str,
    study_id: str,
    request: ConnectorRequest,
    archive: Archive,
) -> RawManifest | None:
    with archive.store.staging() as staging:
        locations = download(location_file, staging, request.max_file_bytes)
        if not LOCATION_COLUMNS <= set(pd.read_csv(locations, nrows=0).columns):
            return None

        files = {locations.name: locations}
        assets = {"locations": locations.name}
        if reference is not None:
            reference_path = download(reference, staging, request.max_file_bytes)
            files[reference_path.name] = reference_path
            assets["reference"] = reference_path.name

        timestamps = pd.to_datetime(pd.read_csv(locations, usecols=["timestamp"])["timestamp"], utc=True)
        retrieved_at = datetime.now(UTC)
        version, stored = archive.put(artifact_id_for(item["handle"], locations.name), files, STORAGE_FORMAT)

    start, end = timestamps.min().to_pydatetime(), timestamps.max().to_pydatetime()
    published = published_at(item)
    taxon = metadata_value(item, "dwc.ScientificName")
    manifest = RawManifest(
        artifact_id=artifact_id_for(item["handle"], locations.name),
        version=version,
        created_at=retrieved_at,
        access_scope=request.access_scope,
        source=SourceRef(
            name="Movebank Data Repository", url=metadata_value(item, "dc.identifier.uri"), study_id=study_id
        ),
        storage=stored.storage,
        checksum=stored.checksum,
        retrieved_at=retrieved_at,
        coverage=Coverage(species=[taxon] if taxon else [], start=start, end=end),
        rights=Rights(
            license=metadata_value(item, "dc.rights", "dc.rights.uri"),
            retention_allowed=True,
            reuse_allowed=True,
            attribution=metadata_value(item, "dc.identifier.citation", "mdr.citation.CSE"),
        ),
        extensions=SourceItem(
            source_id="movebank_repository",
            product=PRODUCT,
            source_item_id=source_item_id,
            source_key=source_key,
            kind="tabular",
            time_start=start,
            time_end=end,
            time_precision=TimePrecision.INSTANT,
            available_at=published,
            processing_version=processing_version,
            assets=assets,
            properties={
                "study_id": study_id,
                "doi": metadata_value(item, "dc.identifier.doi"),
                "title": metadata_value(item, "dc.title"),
            },
        ),
    )
    return archive.record(manifest)


def download(bitstream: dict, directory: Path, max_bytes: int) -> Path:
    target = directory / safe_name(bitstream["name"])
    http.download(bitstream["_links"]["content"]["href"], target, max_bytes=max_bytes, allowed_hosts=ALLOWED_HOSTS)
    return target


def search_data_packages(query: str, size: int = 20) -> list[dict]:
    """Published data packages that match a free-text query, such as a species name."""
    response = http.get_json(SEARCH_API, params={"query": query, "dsoType": "ITEM", "size": size})
    objects = response["_embedded"]["searchResult"]["_embedded"]["objects"]
    packages = [entry["_embedded"]["indexableObject"] for entry in objects]
    return [
        {
            "uuid": package["uuid"],
            "title": metadata_value(package, "dc.title"),
            "taxon": metadata_value(package, "dwc.ScientificName"),
            "study_id": metadata_value(package, "mdr.study.id"),
        }
        for package in packages
        if metadata_value(package, "dspace.entity.type") == "Datapackage"
    ]

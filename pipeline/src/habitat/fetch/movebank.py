from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import pandas as pd

from habitat.contracts import (
    Coverage,
    RawManifest,
    Rights,
    SourceItem,
    SourceRef,
    StorageRef,
    TimePrecision,
)
from habitat.fetch.archive import RawArchive

DATA_REPOSITORY_API = "https://datarepository.movebank.org/server/api/core"
SEARCH_API = "https://datarepository.movebank.org/server/api/discover/search/objects"
PRODUCT = "movebank-data-repository"
REFERENCE_FILE_SUFFIX = "-reference-data.csv"
# Accessory files hold sensor data such as acceleration, not locations.
NOT_LOCATION_SUFFIXES = (REFERENCE_FILE_SUFFIX, "-accessory.csv")
LOCATION_COLUMNS = {"timestamp", "location-long", "location-lat"}


def metadata_value(item: dict, key: str) -> str | None:
    values = item.get("metadata", {}).get(key)
    return values[0]["value"] if values else None


def package_files(item: dict) -> list[dict]:
    bundles = item.get("_embedded", {}).get("bundles", {}).get("_embedded", {}).get("bundles", [])
    original = next((bundle for bundle in bundles if bundle.get("name") == "ORIGINAL"), None)
    if original is None:
        return []
    return original.get("_embedded", {}).get("bitstreams", {}).get("_embedded", {}).get("bitstreams", [])


def fetch_data_package(
    item_uuid: str,
    archive: RawArchive,
    already_ingested: Callable[[str, str, str], bool] = lambda *_: False,
    access_scope: str = "public",
) -> list[RawManifest]:
    """One manifest per location file of a published Movebank data package. The reference file goes with each."""
    client = archive.client
    item = client.get(f"{DATA_REPOSITORY_API}/items/{item_uuid}", params={"embed": "bundles/bitstreams"})
    item.raise_for_status()
    item = item.json()

    files = package_files(item)
    reference = next((f for f in files if f["name"].endswith(REFERENCE_FILE_SUFFIX)), None)
    # File names vary between packages, so a CSV counts as a location file when its header has the location columns.
    candidates = [f for f in files if f["name"].endswith(".csv") and not f["name"].endswith(NOT_LOCATION_SUFFIXES)]
    handle = item["handle"]
    study_id = metadata_value(item, "mdr.study.id") or handle

    manifests = []
    for location_file in candidates:
        source_item_id = f"{handle}/{location_file['name']}"
        processing_version = f"md5:{location_file['checkSum']['value']}"
        if already_ingested(source_item_id, processing_version, "final"):
            continue

        locations_path = download(archive, handle, location_file)
        if not LOCATION_COLUMNS <= set(pd.read_csv(locations_path, nrows=0).columns):
            continue

        assets = {"locations": locations_path}
        if reference is not None:
            assets["reference"] = download(archive, handle, reference)
        manifests.append(
            package_manifest(item, source_item_id, processing_version, study_id, assets, access_scope)
        )
    return manifests


def download(archive: RawArchive, handle: str, bitstream: dict) -> str:
    url = bitstream["_links"]["content"]["href"]
    return str(archive.download(url, handle.replace("/", "_"), bitstream["name"]).path)


def package_manifest(
    item: dict, source_item_id: str, processing_version: str, study_id: str, assets: dict[str, str], access_scope: str
) -> RawManifest:
    retrieved_at = datetime.now(UTC)
    timestamps = pd.to_datetime(pd.read_csv(assets["locations"], usecols=["timestamp"])["timestamp"], utc=True)
    start, end = timestamps.min().to_pydatetime(), timestamps.max().to_pydatetime()
    published = datetime.fromisoformat(metadata_value(item, "dc.date.available").replace("Z", "+00:00"))
    taxon = metadata_value(item, "dwc.ScientificName")

    return RawManifest(
        artifact_id=source_item_id,
        version="1",
        created_at=retrieved_at,
        access_scope=access_scope,
        source=SourceRef(
            name="Movebank Data Repository", url=metadata_value(item, "dc.identifier.uri"), study_id=study_id
        ),
        storage=StorageRef(uri=assets["locations"], format="csv"),
        checksum=processing_version,
        retrieved_at=retrieved_at,
        coverage=Coverage(species=[taxon] if taxon else [], start=start, end=end),
        rights=Rights(
            license=metadata_value(item, "dc.rights"),
            retention_allowed=True,
            reuse_allowed=True,
            attribution=metadata_value(item, "dc.identifier.citation"),
        ),
        extensions=SourceItem(
            source_id="movebank",
            product=PRODUCT,
            source_item_id=source_item_id,
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


def search_data_packages(query: str, client: httpx.Client | None = None, size: int = 20) -> list[dict]:
    """Published data packages that match a free-text query, such as a species name."""
    if client is None:
        with httpx.Client(timeout=60) as owned_client:
            return search_data_packages(query, owned_client, size)
    response = client.get(
        SEARCH_API,
        params={"query": query, "dsoType": "ITEM", "size": size},
    )
    response.raise_for_status()
    objects = response.json()["_embedded"]["searchResult"]["_embedded"]["objects"]
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

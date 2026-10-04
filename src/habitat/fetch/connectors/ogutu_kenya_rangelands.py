"""Ogutu et al. 2016, PLOS ONE, S4 Data, with the Kenya county boundaries of geoBoundaries."""

from datetime import UTC, datetime
from typing import Any

from habitat.archive import Archive
from habitat.archive.index import AlreadyIngested, never_ingested
from habitat.archive.store import sha256_file
from habitat.contracts import Coverage, RawManifest, Rights, SourceItem, SourceRef, TimePrecision
from habitat.fetch import http
from habitat.fetch.connectors import ConnectorRequest, ConnectorResult
from habitat.normalize.rows import QuarantineError
from habitat.normalize.sources.ogutu_kenya_rangelands import SOURCE_ID, SPECIES_TAXA, survey_period

PRODUCT = "ogutu-2016-s4"
STORAGE_FORMAT = "xlsx"
DOI = "10.1371/journal.pone.0163249"
ITEM_ID = f"{DOI}.s004"
DATASET_ID = f"{SOURCE_ID}:{ITEM_ID}"
S4_URL = f"https://journals.plos.org/plosone/article/file?type=supplementary&id={ITEM_ID}"
S4_FILENAME = "pone.0163249.s004.xlsx"
# Crossref gives 2016-09-27 as the online publication date of the article and of its supplement.
PUBLISHED = datetime(2016, 9, 27, tzinfo=UTC)
CITATION = (
    "Ogutu JO, Piepho H-P, Said MY, Ojwang GO, Njino LW, Kifugo SC, Wargute PW (2016) Extreme Wildlife Declines "
    "and Concurrent Increase in Livestock Numbers in Kenya: What Are the Causes? PLoS ONE 11(9): e0163249. "
    f"https://doi.org/{DOI}"
)

# A fixed geoBoundaries commit, so the county geometry of a batch does not change under it.
# The geoBoundaries API gives the license of KEN ADM1 as Public Domain, from the RCMRD GeoPortal.
BOUNDARY_COMMIT = "9469f09"
BOUNDARY_VERSION = f"geoboundaries-{BOUNDARY_COMMIT}"
BOUNDARY_FILENAME = "geoBoundaries-KEN-ADM1_simplified.geojson"
BOUNDARY_URL = (
    f"https://github.com/wmgeolab/geoBoundaries/raw/{BOUNDARY_COMMIT}/releaseData/gbOpen/KEN/ADM1/{BOUNDARY_FILENAME}"
)
BOUNDARY_SOURCE = f"geoBoundaries gbOpen KEN ADM1 (2020 counties), commit {BOUNDARY_COMMIT}, Public Domain"

ALLOWED_HOSTS = {
    "journals.plos.org", "storage.googleapis.com", "github.com", "raw.githubusercontent.com",
    "media.githubusercontent.com",
}
MAX_DOWNLOAD_BYTES = 20 * 1024**2
RANGELAND_COUNTIES_BBOX = (33.99, -4.7, 41.91, 5.43)
DESCRIPTION = (
    "DRSRS aerial sample survey estimates with standard errors, and model estimates with 95% prediction limits, "
    "of 18 wildlife species and 4 livestock groups in 20 Kenya rangeland counties, 1977 to 2016"
)


def catalog_entry() -> dict[str, Any]:
    return {
        "dataset_id": DATASET_ID,
        "source_id": SOURCE_ID,
        "title": "Kenya rangeland wildlife and livestock counts per county (Ogutu et al. 2016, S4 Data)",
        "description": DESCRIPTION,
        "species": sorted(name for name, key in SPECIES_TAXA.values() if key is not None),
        "common_names": sorted(SPECIES_TAXA),
        "region": "Kenya",
        "bbox": list(RANGELAND_COUNTIES_BBOX),
        "method": "aerial_sample and model",
        "area_type": "admin_unit",
        "data_kind": "population_counts",
        "license": "CC-BY-4.0",
    }


def inspect_ogutu(dataset_id: str) -> dict[str, Any]:
    if dataset_id not in (DATASET_ID, ITEM_ID):
        return {"found": False, "dataset_id": dataset_id}

    return {
        "found": True,
        **catalog_entry(),
        "publication_date": PUBLISHED.date().isoformat(),
        "files": [
            {"filename": S4_FILENAME, "download_url": S4_URL},
            {"filename": BOUNDARY_FILENAME, "download_url": BOUNDARY_URL},
        ],
        "coverage": {"bbox": list(RANGELAND_COUNTIES_BBOX), "start": "1977", "end": "2016"},
        "source": {"name": "PLOS ONE supporting information", "url": f"https://doi.org/{DOI}", "study_id": DOI},
        "rights": Rights(
            license="CC-BY-4.0", retention_allowed=True, reuse_allowed=True, attribution=CITATION
        ).model_dump(),
        "limits": "County totals: never assign a county value to one park. "
                  "The file gives only the end day of a survey.",
    }


def check_ogutu_access(dataset_id: str) -> dict[str, Any]:
    if dataset_id not in (DATASET_ID, ITEM_ID):
        return {"dataset_id": dataset_id, "status": "not_found", "message": f"{SOURCE_ID} has one item: {DATASET_ID}"}

    return {"dataset_id": dataset_id, "status": "available", "license": "CC-BY-4.0", "access_scope": "public"}


def fetch_ogutu_kenya_rangelands(
    request: ConnectorRequest, archive: Archive, already_ingested: AlreadyIngested = never_ingested
) -> ConnectorResult:
    """The one S4 file of the article. PLOS gives no checksum, so the processing version is the SHA-256 of the file."""
    result = ConnectorResult()
    if request.item not in (None, ITEM_ID, DATASET_ID):
        return result.fail("invalid_request", f"{SOURCE_ID} has one item: {ITEM_ID}")

    source_key = f"{SOURCE_ID}:{ITEM_ID}:{BOUNDARY_VERSION}"
    if (cached := archive.cached(source_key)) is not None:
        if not already_ingested(ITEM_ID, cached.extensions.processing_version, "final"):
            result.manifests.append(cached)
        return result

    try:
        with archive.store.staging() as staging:
            s4, boundaries = staging / S4_FILENAME, staging / BOUNDARY_FILENAME
            http.download(S4_URL, s4, max_bytes=MAX_DOWNLOAD_BYTES, allowed_hosts=ALLOWED_HOSTS)
            http.download(BOUNDARY_URL, boundaries, max_bytes=MAX_DOWNLOAD_BYTES, allowed_hosts=ALLOWED_HOSTS)
            processing_version = sha256_file(s4)
            start, end = survey_period(s4)
            retrieved_at = datetime.now(UTC)
            version, stored = archive.put(
                SOURCE_ID, {s4.name: s4, boundaries.name: boundaries}, STORAGE_FORMAT
            )
    except QuarantineError as error:
        return result.fail("invalid_payload", f"S4 file: {error}")
    except Exception as error:
        detail = str(error) if isinstance(error, ValueError) else type(error).__name__
        return result.fail("download_failed", detail, retryable=True)

    manifest = RawManifest(
        artifact_id=SOURCE_ID,
        version=version,
        created_at=retrieved_at,
        access_scope="public",
        source=SourceRef(name="PLOS ONE supporting information", url=f"https://doi.org/{DOI}", study_id=DOI),
        storage=stored.storage,
        checksum=stored.checksum,
        retrieved_at=retrieved_at,
        coverage=Coverage(
            species=catalog_entry()["species"], bbox=RANGELAND_COUNTIES_BBOX, start=start, end=end
        ),
        rights=Rights(license="CC-BY-4.0", retention_allowed=True, reuse_allowed=True, attribution=CITATION),
        extensions=SourceItem(
            source_id=SOURCE_ID,
            product=PRODUCT,
            source_item_id=ITEM_ID,
            source_key=source_key,
            kind="tabular",
            time_start=start,
            time_end=end,
            time_precision=TimePrecision.COMPOSITE,
            available_at=PUBLISHED,
            processing_version=processing_version,
            assets={"data": S4_FILENAME, "boundaries": BOUNDARY_FILENAME},
            properties={
                "doi": DOI,
                "data_kind": "population_counts",
                "boundary_source": BOUNDARY_SOURCE,
                "boundary_version": BOUNDARY_VERSION,
                "boundary_url": BOUNDARY_URL,
            },
        ),
    )
    archive.record(manifest)
    if not already_ingested(ITEM_ID, processing_version, "final"):
        result.manifests.append(manifest)
    return result

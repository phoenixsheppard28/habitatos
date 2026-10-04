import json
import re
from datetime import UTC, date, datetime, timedelta

import pandas as pd

from habitat.archive.store import ArtifactStore
from habitat.contracts import BBox, RawManifest, TimePrecision
from habitat.grid import Grid
from habitat.normalize.events import quality_flags, to_point_events, within_bbox
from habitat.normalize.rows import NormalizedBatch, QuarantineError

MAPPING_VERSION = "gbif-occurrence-search-v1"
PAGE_ASSET_PREFIX = "page-"
DATASETS_ASSET = "datasets"

LICENSES = {
    "creativecommons.org/publicdomain/zero/1.0": "CC0-1.0",
    "creativecommons.org/licenses/by/4.0": "CC-BY-4.0",
    "creativecommons.org/licenses/by-nc/4.0": "CC-BY-NC-4.0",
    "cc0_1_0": "CC0-1.0",
    "cc_by_4_0": "CC-BY-4.0",
    "cc_by_nc_4_0": "CC-BY-NC-4.0",
}
LICENSES_BY_RESTRICTION = ["CC0-1.0", "CC-BY-4.0", "CC-BY-NC-4.0"]

# Global Roadkill Data, opportunistic and systematic records.
MORTALITY_DATASET_KEYS = frozenset({"65908f95-5ab6-48d9-bf6d-6da274ed730e", "d3b6cb30-0a64-4f82-91ea-0bb14637ee17"})

GEOSPATIAL_ISSUES = frozenset({
    "ZERO_COORDINATE", "COORDINATE_INVALID", "COORDINATE_OUT_OF_RANGE", "COUNTRY_COORDINATE_MISMATCH",
    "PRESUMED_SWAPPED_COORDINATE", "COORDINATE_REPROJECTION_FAILED", "COORDINATE_REPROJECTION_SUSPICIOUS",
    "GEODETIC_DATUM_INVALID",
})
DATE_ISSUES = frozenset({"RECORDED_DATE_INVALID", "RECORDED_DATE_UNLIKELY", "RECORDED_DATE_MISMATCH"})
TAXON_LEVELS = [
    ("species", "speciesKey"), ("genus", "genusKey"), ("family", "familyKey"), ("order", "orderKey"),
    ("class", "classKey"), ("phylum", "phylumKey"), ("kingdom", "kingdomKey"),
]
INATURALIST_URL = re.compile(r"^https?://(?:www\.)?inaturalist\.org/observations/(\d+)/?$")
OBSERVATION_ORG_URL = re.compile(r"^https?://(?:[a-z]+\.)?observation\.org/observation/(\d+)")
UTC_OFFSET = re.compile(r"(Z|[+-]\d{2}(:?\d{2})?)$")
ROW_COLUMNS = [
    "source_record_id", "event_type", "occurrence_status", "sampling_design", "taxon_name", "gbif_taxon_key",
    "time_start", "time_end", "time_precision", "available_at", "available_at_source", "longitude", "latitude",
    "coordinate_uncertainty_m", "individual_count", "basis", "method", "origin_record_id", "license", "issues",
    "attributes",
]


def spdx_license(value: str | None) -> str | None:
    if not value:
        return None

    key = value.strip().lower().removeprefix("https://").removeprefix("http://").removesuffix("/legalcode")
    return LICENSES.get(key.rstrip("/"))


def most_restrictive(licenses) -> str | None:
    known = [LICENSES_BY_RESTRICTION.index(license) for license in licenses if license in LICENSES_BY_RESTRICTION]
    return LICENSES_BY_RESTRICTION[max(known)] if known else None


def normalize_gbif_occurrence(
    manifest: RawManifest, store: ArtifactStore, grid: Grid, aoi: BBox | None = None
) -> NormalizedBatch:
    """GBIF occurrence search pages to point_events. See docs/ingestion/EVENTS.md section 4."""
    assets = manifest.extensions.assets
    pages = [
        json.loads(store.open(manifest, name).read_text()) for name in sorted(assets) if name.startswith(PAGE_ASSET_PREFIX)
    ]
    datasets = json.loads(store.open(manifest, DATASETS_ASSET).read_text()) if DATASETS_ASSET in assets else {}
    records = [record for page in pages for record in page["results"]]

    rows = pd.DataFrame([record_row(record, datasets) for record in records], columns=ROW_COLUMNS)
    requested = manifest.extensions.properties.get("requested_bbox")
    rows = within_bbox(rows, tuple(requested) if requested else aoi)

    issues = rows.pop("issues")
    basis = rows["basis"]
    available_at_source = rows.pop("available_at_source")
    rows["quality_flag"] = quality_flags(
        rows,
        {
            "geospatial_issue": issues.map(lambda found: bool(GEOSPATIAL_ISSUES & set(found))),
            "date_issue": issues.map(lambda found: bool(DATE_ISSUES & set(found))),
            "captive_record": basis == "LIVING_SPECIMEN",
            "not_live_observation": basis == "FOSSIL_SPECIMEN",
            "taxon_unresolved": rows["gbif_taxon_key"].isna(),
            "available_at_from_dataset": available_at_source != "record_modified",
        },
    )

    return to_point_events(rows, manifest, grid, MAPPING_VERSION)


def record_row(record: dict, datasets: dict[str, dict]) -> dict:
    record_id = record.get("gbifID") or record.get("key")
    if record_id is None:
        raise QuarantineError("a GBIF record has no gbifID")

    if record.get("decimalLongitude") is None or record.get("decimalLatitude") is None:
        raise QuarantineError(f"GBIF record {record_id} has no coordinates")

    for field in ("datasetKey", "basisOfRecord", "occurrenceStatus"):
        if not record.get(field):
            raise QuarantineError(f"GBIF record {record_id} has no {field}")

    license = spdx_license(record.get("license"))
    if license is None:
        raise QuarantineError(f"GBIF record {record_id} has the license {record.get('license')!r}; "
                              "only CC0 1.0, CC-BY 4.0 and CC-BY-NC 4.0 are mapped")

    start, end, precision, local_time = event_time(record)
    available_at, available_at_source = record_available_at(record, datasets, end)
    taxon_name, taxon_key = taxon(record)
    attributes = {
        "datasetKey": record["datasetKey"],
        "issues": record.get("issues", []),
        "isInCluster": record.get("isInCluster"),
        "recordedBy": record.get("recordedBy"),
        "eventDate": record.get("eventDate"),
        "verbatimEventDate": record.get("verbatimEventDate"),
        "local_time": local_time,
        "acceptedScientificName": record.get("acceptedScientificName"),
        "taxonRank": record.get("taxonRank"),
        "available_at_source": available_at_source,
    }

    return {
        "source_record_id": str(record_id),
        "event_type": "wildlife_mortality" if record["datasetKey"] in MORTALITY_DATASET_KEYS else "species_occurrence",
        "occurrence_status": record["occurrenceStatus"].lower(),
        "sampling_design": "presence_only",
        "taxon_name": taxon_name,
        "gbif_taxon_key": taxon_key,
        "time_start": start,
        "time_end": end,
        "time_precision": precision.value,
        "available_at": available_at,
        "available_at_source": available_at_source,
        "longitude": float(record["decimalLongitude"]),
        "latitude": float(record["decimalLatitude"]),
        "coordinate_uncertainty_m": record.get("coordinateUncertaintyInMeters"),
        "individual_count": individual_count(record.get("individualCount")),
        "basis": record["basisOfRecord"],
        "method": sampling_protocol(record.get("samplingProtocol")),
        "origin_record_id": origin_record_id(record),
        "license": license,
        "issues": record.get("issues", []),
        "attributes": json.dumps({key: value for key, value in attributes.items() if value is not None}),
    }


def event_time(record: dict) -> tuple[datetime, datetime, TimePrecision, str | None]:
    """Half-open UTC intervals. A time without an offset is local time: keep the local date, not a guessed zone."""
    text = record.get("eventDate") or date_from_parts(record)
    if not text:
        raise QuarantineError(f"GBIF record {record.get('gbifID')} has no event time")

    if "/" in text:
        first, last = text.split("/", 1)
        start, _, _, _ = time_part(first)
        _, end, _, _ = time_part(last)
        return start, end, TimePrecision.COMPOSITE, None

    return time_part(text)


def date_from_parts(record: dict) -> str | None:
    if record.get("year") is None:
        return None

    text = f"{record['year']:04d}"
    if record.get("month") is not None:
        text += f"-{record['month']:02d}"
        if record.get("day") is not None:
            text += f"-{record['day']:02d}"
    return text


def time_part(text: str) -> tuple[datetime, datetime, TimePrecision, str | None]:
    if "T" in text:
        day_text, clock = text.split("T", 1)
        if UTC_OFFSET.search(clock):
            instant = pd.Timestamp(text).tz_convert(UTC).to_pydatetime()
            return instant, instant, TimePrecision.INSTANT, None

        start = utc_midnight(date.fromisoformat(day_text))
        return start, start + timedelta(days=1), TimePrecision.DAY, clock

    parts = [int(part) for part in text.split("-")]
    if len(parts) == 3:
        start = utc_midnight(date(*parts))
        return start, start + timedelta(days=1), TimePrecision.DAY, None

    if len(parts) == 2:
        year, month = parts
        following = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        return utc_midnight(date(year, month, 1)), utc_midnight(following), TimePrecision.COMPOSITE, None

    (year,) = parts
    return utc_midnight(date(year, 1, 1)), utc_midnight(date(year + 1, 1, 1)), TimePrecision.COMPOSITE, None


def utc_midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def record_available_at(record: dict, datasets: dict[str, dict], event_end: datetime) -> tuple[datetime, str]:
    """The first date that is not before the end of the event. `lastInterpreted` changes often, so it is never used.

    The crawl time is late but never early, so it is the last fallback.
    """
    candidates = [
        ("record_modified", record.get("modified")),
        ("dataset_pub_date", datasets.get(record.get("datasetKey"), {}).get("pubDate")),
        ("last_crawled", record.get("lastCrawled")),
    ]
    for source, value in candidates:
        if value and (published := parsed_utc(value)) >= event_end:
            return published, source

    raise QuarantineError(f"GBIF record {record.get('gbifID')} has no publication date after the event")


def parsed_utc(value: str) -> datetime:
    published = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return published if published.tzinfo else published.replace(tzinfo=UTC)


def taxon(record: dict) -> tuple[str | None, int | None]:
    """The canonical name and key of the most specific rank, so a species joins on its plain binomial."""
    for name_field, key_field in TAXON_LEVELS:
        if record.get(name_field) and record.get(key_field) is not None:
            return record[name_field], int(record[key_field])
    return None, None


def individual_count(value) -> int | None:
    return int(value) if value is not None and int(value) >= 0 else None


def sampling_protocol(value) -> str | None:
    if isinstance(value, list):
        return "; ".join(value) or None
    return value or None


def origin_record_id(record: dict) -> str | None:
    occurrence_id = record.get("occurrenceID")
    if not occurrence_id:
        return None

    if match := INATURALIST_URL.match(occurrence_id):
        return f"inaturalist:{match.group(1)}"
    if match := OBSERVATION_ORG_URL.match(occurrence_id):
        return f"observation_org:{match.group(1)}"
    return f"gbif:{record['datasetKey']}:{occurrence_id}"

"""NASA FIRMS active fire detections, standard product, from the yearly country files. No MAP_KEY is needed."""

import csv
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from functools import cache
from pathlib import Path

import httpx

from habitat.archive import Archive
from habitat.archive.index import AlreadyIngested, never_ingested
from habitat.contracts import BBox, Coverage, RawManifest, Rights, SourceItem, SourceRef, TimePrecision
from habitat.fetch import http
from habitat.fetch.connectors import ConnectorRequest, ConnectorResult, validate_area_and_dates
from habitat.fetch.connectors.stac import bbox_key

COUNTRY_FILE_URL = "https://firms.modaps.eosdis.nasa.gov/data/country/{folder}/{year}/{prefix}{year}_{country}.csv"
ALLOWED_HOSTS = {"firms.modaps.eosdis.nasa.gov"}
STORAGE_FORMAT = "csv"
CSV_HEADER_COLUMNS = {"latitude", "longitude", "version"}
# Bounding boxes of the land parts of each FIRMS country, from Natural Earth 1:50m admin-0, with a 0.05° margin.
COUNTRIES_FILE = Path(__file__).with_name("firms_countries.json")
RIGHTS = Rights(
    license="NASA-open-data",
    retention_allowed=True,
    reuse_allowed=True,
    attribution="We acknowledge the use of data from NASA's Fire Information for Resource Management System "
                "(FIRMS) (https://www.earthdata.nasa.gov/firms), part of NASA's Earth Science Data and "
                "Information System (ESDIS).",
)


@dataclass(frozen=True)
class Instrument:
    source_id: str
    file_prefix: str
    product: str
    label: str
    first_day: date

    @property
    def folder(self) -> str:
        return self.file_prefix.removesuffix("_")


MODIS = Instrument("firms_modis", "modis_", "firms-modis-sp", "MODIS Terra and Aqua", date(2000, 11, 1))
VIIRS_SNPP = Instrument("firms_viirs", "viirs-snpp_", "firms-viirs-snpp-sp", "VIIRS S-NPP", date(2012, 1, 20))


@cache
def country_boxes() -> dict[str, list[BBox]]:
    return {name: [tuple(box) for box in boxes] for name, boxes in json.loads(COUNTRIES_FILE.read_text()).items()}


def countries_for(bbox: BBox) -> list[str]:
    west, south, east, north = bbox
    return sorted(
        name for name, boxes in country_boxes().items()
        if any(w <= east and west <= e and s <= north and south <= n for w, s, e, n in boxes)
    )


def fetch_firms_modis(
    request: ConnectorRequest, archive: Archive, already_ingested: AlreadyIngested = never_ingested
) -> ConnectorResult:
    return fetch_firms(MODIS, request, archive, already_ingested)


def fetch_firms_viirs(
    request: ConnectorRequest, archive: Archive, already_ingested: AlreadyIngested = never_ingested
) -> ConnectorResult:
    return fetch_firms(VIIRS_SNPP, request, archive, already_ingested)


def fetch_firms(
    instrument: Instrument, request: ConnectorRequest, archive: Archive, already_ingested: AlreadyIngested
) -> ConnectorResult:
    """One manifest per country and year. The normalizer keeps only the detections in the requested bbox."""
    result = ConnectorResult()
    try:
        first, last = validate_area_and_dates(request)
    except ValueError as error:
        return result.fail("invalid_request", str(error))

    days = (last - first).days + 1
    if days > request.max_days:
        last = first + timedelta(days=request.max_days - 1)
        result.warnings.append(f"FIRMS day limit reached: fetched the first {request.max_days} of {days} days")
    if last < instrument.first_day:
        result.warnings.append(f"{instrument.label} has no FIRMS data before {instrument.first_day.isoformat()}")
        return result

    countries = countries_for(request.bbox)
    if not countries:
        result.warnings.append("no FIRMS country overlaps the bbox; FIRMS country files cover land only")
        return result

    used = 0
    with http.client() as client:
        for year in range(max(first, instrument.first_day).year, last.year + 1):
            for country in countries:
                label = f"{instrument.label} {country} {year}"
                try:
                    manifest = fetch_country_year(
                        instrument, country, year, client, archive, already_ingested, request, request.max_bytes - used
                    )
                except Exception as error:
                    detail = str(error) if isinstance(error, ValueError) else type(error).__name__
                    result.warnings.append(f"{label}: {detail}")
                    continue

                if manifest is None:
                    continue
                used += sum(path.stat().st_size for path in archive.resolve(manifest).values())
                result.manifests.append(manifest)
    return result


def fetch_country_year(
    instrument: Instrument,
    country: str,
    year: int,
    client: httpx.Client,
    archive: Archive,
    already_ingested: AlreadyIngested,
    request: ConnectorRequest,
    budget: int,
) -> RawManifest | None:
    url = COUNTRY_FILE_URL.format(folder=instrument.folder, prefix=instrument.file_prefix, year=year, country=country)
    head = client.head(url)
    if head.status_code == 404:
        raise ValueError("no file; FIRMS publishes the country file of a year after the year ends")
    head.raise_for_status()

    if "last-modified" not in head.headers:
        raise ValueError("the file has no publication date (Last-Modified)")
    published = parsedate_to_datetime(head.headers["last-modified"]).astimezone(UTC)
    limit = min(budget, request.max_file_bytes)
    if int(head.headers.get("content-length", 0)) > limit:
        raise ValueError(f"the file exceeds the size limit of {limit} bytes")

    source_item_id = f"firms:{instrument.folder}:{country}:{year}:{bbox_key(request.bbox)}"
    source_key = f"{instrument.source_id}:{source_item_id}:{published.isoformat()}"
    if (cached := archive.cached(source_key)) is not None:
        return cached

    filename = url.rsplit("/", 1)[-1]
    artifact_id = f"firms-{instrument.folder}-{year}-{country}"
    with archive.store.staging() as staging:
        target = staging / filename
        http.download(url, target, max_bytes=limit, allowed_hosts=ALLOWED_HOSTS)
        versions = processing_versions(target, request.bbox)
        if not versions:
            raise ValueError("the file has no detections in the bbox")
        processing_version = ",".join(sorted(versions))
        if already_ingested(source_item_id, processing_version, "final"):
            return None

        retrieved_at = datetime.now(UTC)
        version, stored = archive.put(artifact_id, {filename: target}, STORAGE_FORMAT)

    start = datetime(year, 1, 1, tzinfo=UTC)
    end = datetime(year + 1, 1, 1, tzinfo=UTC)
    manifest = RawManifest(
        artifact_id=artifact_id,
        version=version,
        created_at=retrieved_at,
        access_scope=request.access_scope,
        source=SourceRef(name=f"NASA FIRMS {instrument.label}", url=url),
        storage=stored.storage,
        checksum=stored.checksum,
        retrieved_at=retrieved_at,
        coverage=Coverage(bbox=tuple(request.bbox), start=start, end=end),
        rights=RIGHTS,
        extensions=SourceItem(
            source_id=instrument.source_id,
            product=instrument.product,
            source_item_id=source_item_id,
            source_key=source_key,
            kind="tabular",
            time_start=start,
            time_end=end,
            time_precision=TimePrecision.COMPOSITE,
            available_at=published,
            processing_version=processing_version,
            assets={"detections": filename},
            properties={"requested_bbox": list(request.bbox), "country": country, "year": year},
        ),
    )
    return archive.record(manifest)


def processing_versions(path: Path, bbox: BBox) -> set[str]:
    """The `version` values of the detections in the bbox. A reply that is not a FIRMS CSV, such as
    `Invalid MAP_KEY.`, fails. A detection without coordinates counts, so the normalizer can quarantine the file.
    """
    west, south, east, north = bbox
    with path.open(newline="") as file:
        reader = csv.DictReader(file)
        if not CSV_HEADER_COLUMNS <= set(reader.fieldnames or ()):
            raise ValueError("the reply is not a FIRMS CSV")
        return {
            row["version"] for row in reader
            if not (row["longitude"] and row["latitude"])
            or (west <= float(row["longitude"]) <= east and south <= float(row["latitude"]) <= north)
        }

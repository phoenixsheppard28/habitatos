"""Water Quality Portal (USGS, EPA, NWQMC): one WQX 3.0 result CSV and one station CSV per query.

https://www.waterqualitydata.us/webservices_documentation/
"""

import hashlib
import json
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import urlencode

import pandas as pd

from habitat.archive import Archive
from habitat.archive.index import AlreadyIngested, never_ingested
from habitat.contracts import Coverage, ProductStatus, RawManifest, Rights, SourceItem, SourceRef, TimePrecision
from habitat.fetch import http
from habitat.fetch.connectors import ConnectorRequest, ConnectorResult, validate_area_and_dates
from habitat.normalize.sources.wqp import CHARACTERISTICS, FINAL_STATUSES, parse_change_date

RESULT_URL = "https://www.waterqualitydata.us/wqx3/Result/search"
STATION_URL = "https://www.waterqualitydata.us/wqx3/Station/search"
ALLOWED_HOSTS = {"www.waterqualitydata.us"}
PRODUCT = "wqp-wqx3-results"
STORAGE_FORMAT = "csv"
RIGHTS = Rights(
    license="U.S. public domain",
    retention_allowed=True,
    reuse_allowed=True,
    attribution="Water Quality Portal, National Water Quality Monitoring Council, https://doi.org/10.5066/P9QRKUVJ",
)

__all__ = ["fetch_wqp", "parse_change_date", "PRODUCT", "STORAGE_FORMAT"]


def characteristic_names(parameters: tuple[str, ...]) -> list[str]:
    wanted = set(parameters)
    return sorted(
        name for name, candidates in CHARACTERISTICS.items() if not wanted or wanted.intersection(candidates)
    )


def short_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]


def fetch_wqp(
    request: ConnectorRequest, archive: Archive, already_ingested: AlreadyIngested = never_ingested
) -> ConnectorResult:
    result = ConnectorResult()
    try:
        first, last = validate_area_and_dates(request)
    except ValueError as error:
        return result.fail("invalid_request", str(error))

    unknown = sorted(set(request.parameters) - {p for candidates in CHARACTERISTICS.values() for p in candidates})
    if unknown:
        return result.fail("invalid_request", f"wqp has no mapping for the parameter(s) {unknown}")

    names = characteristic_names(request.parameters)
    area = {"bBox": ",".join(str(float(value)) for value in request.bbox)}
    dates = {"startDateLo": f"{first:%m-%d-%Y}", "startDateHi": f"{last:%m-%d-%Y}"}
    query = {**area, **dates, "characteristicName": names, "dataProfile": "fullPhysChem", "mimeType": "csv"}
    source_key = f"wqp:{short_hash(query)}"
    if (cached := archive.cached(source_key)) is not None:
        result.manifests.append(cached)
        return result

    try:
        manifest = archive_query(request, archive, already_ingested, query, source_key, first, last, result)
    except Exception as error:
        detail = str(error) if isinstance(error, ValueError) else type(error).__name__
        return result.fail("download_failed", f"wqp: {detail}", retryable=True)

    if manifest is not None:
        result.manifests.append(manifest)
    return result


def archive_query(
    request: ConnectorRequest,
    archive: Archive,
    already_ingested: AlreadyIngested,
    query: dict,
    source_key: str,
    first: date,
    last: date,
    result: ConnectorResult,
) -> RawManifest | None:
    with archive.store.staging() as staging:
        results_path = staging / "results.csv"
        http.download(
            f"{RESULT_URL}?{urlencode(query, doseq=True)}", results_path, max_bytes=request.max_file_bytes,
            allowed_hosts=ALLOWED_HOSTS,
        )
        results = pd.read_csv(results_path, dtype=str, keep_default_na=False, na_values=[""])
        if results.empty:
            result.warnings.append("wqp: no samples for this region and these dates (a coverage gap, not an error)")
            return None

        changes = results["LastChangeDate"].map(parse_change_date).dropna() if "LastChangeDate" in results else []
        if len(changes) == 0:
            result.warnings.append("wqp: the result file gives no LastChangeDate; the publication date is unknown")
            return None

        published = max(changes)
        processing_version = published.isoformat()
        statuses = results["Result_MeasureStatusIdentifier"].fillna("").str.lower()
        status = ProductStatus.FINAL if statuses.isin(FINAL_STATUSES).all() else ProductStatus.PRELIMINARY
        bbox_key = ",".join(f"{value:.5f}" for value in request.bbox)
        source_item_id = f"wqp:{bbox_key}:{first}:{last}:{short_hash(query['characteristicName'])}"
        if already_ingested(source_item_id, processing_version, status.value):
            return None

        files = {"results.csv": results_path}
        station_query = {key: value for key, value in query.items() if key != "dataProfile"}
        try:
            stations_path = staging / "stations.csv"
            http.download(
                f"{STATION_URL}?{urlencode(station_query, doseq=True)}", stations_path,
                max_bytes=request.max_file_bytes, allowed_hosts=ALLOWED_HOSTS,
            )
            files["stations.csv"] = stations_path
        except Exception as error:
            result.warnings.append(f"wqp: station file not archived ({type(error).__name__}); sites use result columns")

        retrieved_at = datetime.now(UTC)
        version, stored = archive.put(f"wqp-{source_key.removeprefix('wqp:')}", files, STORAGE_FORMAT)

    start = datetime.combine(first, time.min, tzinfo=UTC)
    end = datetime.combine(last, time.min, tzinfo=UTC) + timedelta(days=1)
    manifest = RawManifest(
        artifact_id=f"wqp-{source_key.removeprefix('wqp:')}",
        version=version,
        created_at=retrieved_at,
        access_scope=request.access_scope,
        source=SourceRef(name="Water Quality Portal", url=f"{RESULT_URL}?{urlencode(query, doseq=True)}"),
        storage=stored.storage,
        checksum=stored.checksum,
        retrieved_at=retrieved_at,
        coverage=Coverage(bbox=tuple(request.bbox), start=start, end=end),
        rights=RIGHTS,
        extensions=SourceItem(
            source_id="wqp",
            product=PRODUCT,
            source_item_id=source_item_id,
            source_key=source_key,
            kind="tabular",
            time_start=start,
            time_end=end,
            time_precision=TimePrecision.COMPOSITE,
            available_at=published,
            processing_version=processing_version,
            product_status=status,
            assets={"results": "results.csv", **({"stations": "stations.csv"} if "stations.csv" in files else {})},
            properties={
                "requested_bbox": list(request.bbox),
                "characteristic_names": query["characteristicName"],
                "row_count": len(results),
            },
        ),
    )
    return archive.record(manifest)

from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta

import httpx

from habitat.contracts import (
    Coverage,
    ProductStatus,
    SourceItem,
    RawManifest,
    Rights,
    SourceRef,
    StorageRef,
    TimePrecision,
)
from habitat.fetch.archive import RawArchive

FINAL_URL = "https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_daily/tifs/p05/{d:%Y}/chirps-v2.0.{d:%Y.%m.%d}.tif.gz"
PRELIM_URL = "https://data.chc.ucsb.edu/products/CHIRPS-2.0/prelim/global_daily/tifs/p05/{d:%Y}/chirps-v2.0.{d:%Y.%m.%d}.tif.gz"
PRODUCT = "chirps-v2.0-daily-p05"
PROCESSING_VERSION = "2.0"
RIGHTS = Rights(
    license="CC-BY-4.0",
    retention_allowed=True,
    reuse_allowed=True,
    attribution="Funk et al. 2015, The climate hazards infrared precipitation with stations, Scientific Data 2, 150066",
)


def available_status(day: date, client: httpx.Client) -> ProductStatus | None:
    for status, template in ((ProductStatus.FINAL, FINAL_URL), (ProductStatus.PRELIMINARY, PRELIM_URL)):
        response = client.head(template.format(d=day))
        if response.status_code == 200:
            return status
        if response.status_code != 404:
            response.raise_for_status()
    return None


def item_id(day: date) -> str:
    return f"chirps-v2.0.{day:%Y.%m.%d}"


def fetch_chirps_day(
    day: date,
    archive: RawArchive,
    already_ingested: Callable[[str, str, str], bool] = lambda *_: False,
    access_scope: str = "public",
) -> RawManifest | None:
    """Download the final file if it exists, otherwise the preliminary file.

    Returns None if neither exists yet, or if the series already holds that file.
    """
    status = available_status(day, archive.client)
    if status is None or already_ingested(item_id(day), PROCESSING_VERSION, status.value):
        return None

    url = (FINAL_URL if status is ProductStatus.FINAL else PRELIM_URL).format(d=day)
    artifact_id = f"chirps-{status.value}-{day:%Y%m%d}"
    archived = archive.download(url, artifact_id, url.rsplit("/", 1)[-1])
    retrieved_at = datetime.now(UTC)
    start = datetime.combine(day, time.min, tzinfo=UTC)

    return RawManifest(
        artifact_id=artifact_id,
        version="1",
        created_at=retrieved_at,
        access_scope=access_scope,
        source=SourceRef(name="CHIRPS", url=url),
        storage=StorageRef(uri=str(archived.path), format="geotiff"),
        checksum=archived.checksum,
        retrieved_at=retrieved_at,
        coverage=Coverage(bbox=(-180.0, -50.0, 180.0, 50.0), start=start, end=start + timedelta(days=1)),
        rights=RIGHTS,
        extensions=SourceItem(
            source_id="chirps",
            product=PRODUCT,
            source_item_id=item_id(day),
            time_start=start,
            time_end=start + timedelta(days=1),
            time_precision=TimePrecision.DAY,
            available_at=archived.last_modified or retrieved_at,
            processing_version=PROCESSING_VERSION,
            product_status=status,
            assets={"precipitation": f"/vsigzip/{archived.path}"},
        ),
    )

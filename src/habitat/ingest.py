import argparse
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from habitat.catalog.ai import CatalogAssistant
from habitat.catalog.publish import publish_series_version
from habitat.catalog.store import LocalCatalog
from habitat.contracts import BBox, RawManifest
from habitat.fetch import chirps, stac
from habitat.fetch.archive import RawArchive
from habitat.grid import Grid, default_grid
from habitat.normalize.router import normalize
from habitat.normalize.rows import QuarantineError, series_id, series_id_for
from habitat.storage.series import AppendResult, SeriesStore

logger = logging.getLogger(__name__)

PRODUCTS = {
    "sentinel2": stac.SENTINEL2_PRODUCT,
    "modis_mod13q1": stac.MODIS_PRODUCT,
    "chirps": chirps.PRODUCT,
}

DESCRIPTIONS = {
    "sentinel2": "Sentinel-2 L2A NDVI, MNDWI and NDMI per 1 km cell and acquisition",
    "modis_mod13q1": "MODIS Terra MOD13Q1 16-day NDVI and EVI per 1 km cell",
    "chirps": "CHIRPS v2.0 daily rainfall per 1 km cell",
}


@dataclass
class Workspace:
    root: Path

    @property
    def archive(self) -> RawArchive:
        return RawArchive(self.root / "raw")

    @property
    def store(self) -> SeriesStore:
        return SeriesStore(self.root / "canonical")

    @property
    def catalog(self) -> LocalCatalog:
        return LocalCatalog(self.root / "catalog.json")


@dataclass
class IngestOutcome:
    manifest: RawManifest
    append: AppendResult | None
    quarantine_reason: str | None = None


def ingest_manifest(manifest: RawManifest, store: SeriesStore, grid: Grid, aoi: BBox | None) -> IngestOutcome:
    try:
        batch = normalize(manifest, grid, aoi)
    except QuarantineError as error:
        logger.warning("quarantined %s: %s", manifest.artifact_id, error)
        return IngestOutcome(manifest, None, str(error))

    return IngestOutcome(manifest, store.append_batch(series_id(manifest, grid), manifest, batch))


AlreadyIngested = Callable[[str, str, str], bool]


def already_ingested_in(store: SeriesStore, series: str) -> AlreadyIngested:
    latest = store.latest_version(series)
    if latest is None:
        return lambda *_: False

    return latest.has_item


def fetch_manifests(
    source: str, bbox: BBox, start: date, end: date, archive: RawArchive, already_ingested: AlreadyIngested
) -> list[RawManifest]:
    """Download only the items that the series does not hold yet."""
    start_time = datetime.combine(start, datetime.min.time(), tzinfo=UTC)
    end_time = datetime.combine(end, datetime.max.time(), tzinfo=UTC)

    if source == "sentinel2":
        items = stac.search_sentinel2(bbox, start_time, end_time)
        new = [i for i in items if not already_ingested(i.id, i.properties["s2:processing_baseline"], "final")]
        return [stac.sentinel2_manifest(item, archive) for item in new]

    if source == "modis_mod13q1":
        items = stac.search_modis_terra(bbox, start_time, end_time)
        new = [i for i in items if not already_ingested(i.id, stac.modis_processing_version(i), "final")]
        return [stac.modis_manifest(item, archive) for item in new]

    if source == "chirps":
        days = (start + timedelta(days=offset) for offset in range((end - start).days + 1))
        manifests = (chirps.fetch_chirps_day(day, archive, already_ingested) for day in days)
        return [manifest for manifest in manifests if manifest is not None]

    raise ValueError(f"unknown source {source!r}")


def run(source: str, bbox: BBox, start: date, end: date, workspace: Workspace, use_ai: bool) -> list[IngestOutcome]:
    grid = default_grid()
    store = workspace.store

    already_ingested = already_ingested_in(store, series_id_for(source, PRODUCTS[source], grid))
    manifests = fetch_manifests(source, bbox, start, end, workspace.archive, already_ingested)
    outcomes = [ingest_manifest(manifest, store, grid, bbox) for manifest in manifests]

    appended = [o for o in outcomes if o.append and o.append.appended]
    for series in sorted({o.append.series_id for o in appended}):
        publish_series_version(
            store,
            workspace.catalog,
            grid,
            series,
            source_id=source,
            description=DESCRIPTIONS[source],
            access_scope="public",
            assistant=CatalogAssistant() if use_ai else None,
        )
    return outcomes


def parse_bbox(value: str) -> BBox:
    west, south, east, north = (float(part) for part in value.split(","))
    return west, south, east, north


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch, normalize and append one source for an area and period.")
    parser.add_argument("source", choices=sorted(DESCRIPTIONS))
    parser.add_argument("--bbox", type=parse_bbox, required=True, help="west,south,east,north in WGS84")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--workspace", type=Path, default=Path("data"))
    parser.add_argument("--ai-tags", action="store_true", help="label new dataset versions with Claude")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    outcomes = run(args.source, args.bbox, args.start, args.end, Workspace(args.workspace), args.ai_tags)

    for outcome in outcomes:
        status = outcome.quarantine_reason or ("appended" if outcome.append.appended else "already present")
        print(f"{outcome.manifest.artifact_id}: {status}")


if __name__ == "__main__":
    main()

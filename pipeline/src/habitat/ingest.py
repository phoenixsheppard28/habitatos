import argparse
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import psycopg

from habitat.catalog.ai import CatalogAssistant
from habitat.catalog.publish import publish_series_version
from habitat.catalog.store import PostgresCatalog
from habitat.catalog.taxa import resolve_taxon
from habitat.contracts import BBox, RawManifest
from habitat.db import connect
from habitat.fetch import chirps, movebank, stac
from habitat.fetch.archive import RawArchive
from habitat.grid import Grid, default_grid
from habitat.normalize.router import normalize
from habitat.normalize.rows import NormalizedBatch, QuarantineError, series_id, series_id_for
from habitat.storage.series import AppendResult, SeriesStore

logger = logging.getLogger(__name__)

PRODUCTS = {
    "sentinel2": stac.SENTINEL2_PRODUCT,
    "modis_mod13q1": stac.MODIS_PRODUCT,
    "chirps": chirps.PRODUCT,
    "movebank": movebank.PRODUCT,
}

DESCRIPTIONS = {
    "sentinel2": "Sentinel-2 L2A NDVI, MNDWI and NDMI per 1 km cell and acquisition",
    "modis_mod13q1": "MODIS Terra MOD13Q1 16-day NDVI and EVI per 1 km cell",
    "chirps": "CHIRPS v2.0 daily rainfall per 1 km cell",
    "movebank": "Animal GPS fixes from published Movebank data packages",
}


@dataclass
class Workspace:
    """Raw downloads stay on local disk. Canonical rows and the catalog live in PostgreSQL."""

    raw_root: Path
    connection: psycopg.Connection
    grid: Grid

    @property
    def archive(self) -> RawArchive:
        return RawArchive(self.raw_root)

    @property
    def store(self) -> SeriesStore:
        return SeriesStore(self.connection, self.grid)

    @property
    def catalog(self) -> PostgresCatalog:
        return PostgresCatalog(self.connection)


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

    resolve_entity_taxa(batch)
    return IngestOutcome(manifest, store.append_batch(series_id(manifest, grid), manifest, batch))


def resolve_entity_taxa(batch: NormalizedBatch) -> None:
    """Attach GBIF keys to scientific names, so catalog search by species works. Unresolved names stay null."""
    names = {entity.taxon_name for entity in batch.entities if entity.taxon_name}
    keys = {}
    for name in names:
        resolution = resolve_taxon(name)
        if resolution.status == "resolved":
            keys[name] = resolution.taxa[0].gbif_key

    for entity in batch.entities:
        entity.gbif_taxon_key = keys.get(entity.taxon_name)


AlreadyIngested = Callable[[str, str, str], bool]


def already_ingested_in(store: SeriesStore, series: str) -> AlreadyIngested:
    latest = store.latest_version(series)
    if latest is None:
        return lambda *_: False

    return latest.has_item


def fetch_manifests(
    source: str,
    bbox: BBox | None,
    start: date | None,
    end: date | None,
    archive: RawArchive,
    already_ingested: AlreadyIngested,
    package: str | None = None,
) -> list[RawManifest]:
    """Download only the items that the series does not hold yet."""
    if source == "movebank":
        return movebank.fetch_data_package(package, archive, already_ingested)

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


def run(
    source: str,
    workspace: Workspace,
    bbox: BBox | None = None,
    start: date | None = None,
    end: date | None = None,
    package: str | None = None,
    use_ai: bool = False,
) -> list[IngestOutcome]:
    store = workspace.store
    grid = workspace.grid

    already_ingested = already_ingested_in(store, series_id_for(source, PRODUCTS[source], grid))
    manifests = fetch_manifests(source, bbox, start, end, workspace.archive, already_ingested, package)
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
    parser = argparse.ArgumentParser(description="Fetch, normalize and append one source to the PostgreSQL tables.")
    parser.add_argument("source", choices=sorted(DESCRIPTIONS))
    parser.add_argument("--bbox", type=parse_bbox, help="west,south,east,north in WGS84; required for satellite sources")
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--package", help="Movebank Data Repository item UUID; required for movebank")
    parser.add_argument("--raw", type=Path, default=Path("data/raw"), help="local folder for downloaded files")
    parser.add_argument("--ai-tags", action="store_true", help="label new dataset versions with Claude")
    args = parser.parse_args()

    if args.source == "movebank" and not args.package:
        parser.error("movebank needs --package")
    if args.source != "movebank" and not (args.bbox and args.start and args.end):
        parser.error(f"{args.source} needs --bbox, --start and --end")

    logging.basicConfig(level=logging.INFO)
    with connect() as connection:
        workspace = Workspace(args.raw, connection, default_grid())
        outcomes = run(args.source, workspace, args.bbox, args.start, args.end, args.package, args.ai_tags)

    for outcome in outcomes:
        status = outcome.quarantine_reason or ("appended" if outcome.append.appended else "already present")
        print(f"{outcome.manifest.artifact_id}: {status}")


if __name__ == "__main__":
    main()

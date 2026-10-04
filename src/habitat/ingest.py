import logging
from dataclasses import dataclass

import psycopg

from habitat.archive import Archive, ChecksumMismatch
from habitat.archive.index import PostgresArtifactIndex
from habitat.catalog.classifier import dataset_labeler
from habitat.catalog.publish import publish_series_version
from habitat.catalog.store import PostgresCatalog
from habitat.catalog.taxa import resolve_taxon
from habitat.contracts import BBox, RawManifest
from habitat.grid import Grid
from habitat.normalize.router import normalize
from habitat.normalize.rows import NormalizedBatch, QuarantineError, series_id
from habitat.sources import get_source
from habitat.storage.series import AppendResult, SeriesStore

logger = logging.getLogger(__name__)


@dataclass
class Workspace:
    """Raw files are temporary. Source metadata, normalized rows and catalog records stay in PostgreSQL."""

    connection: psycopg.Connection
    grid: Grid
    archive: Archive | None = None

    def __post_init__(self):
        if self.archive is None:
            self.archive = Archive(index=PostgresArtifactIndex(self.connection))

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


def ingest_manifest(
    manifest: RawManifest, archive: Archive, store: SeriesStore, grid: Grid, aoi: BBox | None
) -> IngestOutcome:
    try:
        archive.resolve(manifest)
        batch = normalize(manifest, archive.store, grid, aoi)
    except (QuarantineError, ChecksumMismatch, FileNotFoundError) as error:
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


def publish_changed(
    outcomes: list[IngestOutcome], workspace: Workspace, access_scope: str = "public", use_ai: bool = False
) -> list[str]:
    ingested = [o for o in outcomes if o.append]
    published = []
    for series in sorted({o.append.series_id for o in ingested}):
        source_id = next(o.manifest.extensions.source_id for o in ingested if o.append.series_id == series)
        descriptor = publish_series_version(
            workspace.store,
            workspace.catalog,
            workspace.grid,
            series,
            source_id=source_id,
            description=get_source(source_id).description,
            access_scope=access_scope,
            assistant=dataset_labeler(use_ai),
        )
        if descriptor is not None:
            published.append(series)
    return published

import logging
from dataclasses import dataclass

import psycopg
import pyarrow as pa

from habitat.archive import Archive, ChecksumMismatch
from habitat.archive.index import PostgresArtifactIndex
from habitat.catalog.ai import CatalogAssistant
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
    """Raw files stay in the archive. Manifests, canonical rows and the catalog live in PostgreSQL."""

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

    resolve_batch_taxa(batch)
    return IngestOutcome(manifest, store.append_batch(series_id(manifest, grid), manifest, batch))


def resolve_batch_taxa(batch: NormalizedBatch) -> None:
    """Attach GBIF keys to scientific names, so catalog search by species works. Unresolved names stay null.

    This applies to the family rows and to each reference table that has `taxon_name` and `gbif_taxon_key`.
    """
    batch.table = with_resolved_taxa(batch.table)
    batch.references = {name: with_resolved_taxa(table) for name, table in batch.references.items()}


def with_resolved_taxa(table: pa.Table) -> pa.Table:
    if not {"taxon_name", "gbif_taxon_key"} <= set(table.column_names):
        return table

    names = table.column("taxon_name").to_pylist()
    keys = table.column("gbif_taxon_key").to_pylist()
    unresolved_names = {name for name, key in zip(names, keys) if name and key is None}
    resolved_keys = {}
    for name in unresolved_names:
        resolution = resolve_taxon(name)
        if resolution.status == "resolved":
            resolved_keys[name] = resolution.taxa[0].gbif_key

    filled = [resolved_keys.get(name) if key is None else key for name, key in zip(names, keys)]
    index = table.schema.get_field_index("gbif_taxon_key")
    key_field = table.schema.field(index)
    return table.set_column(index, key_field, pa.array(filled, key_field.type))


def publish_changed(
    outcomes: list[IngestOutcome], workspace: Workspace, access_scope: str = "public", use_ai: bool = False
) -> list[str]:
    appended = [o for o in outcomes if o.append and o.append.appended]
    published = []
    for series in sorted({o.append.series_id for o in appended}):
        source_id = next(o.manifest.extensions.source_id for o in appended if o.append.series_id == series)
        descriptor = publish_series_version(
            workspace.store,
            workspace.catalog,
            workspace.grid,
            series,
            source_id=source_id,
            description=get_source(source_id).description,
            access_scope=access_scope,
            assistant=CatalogAssistant() if use_ai else None,
        )
        if descriptor is not None:
            published.append(series)
    return published


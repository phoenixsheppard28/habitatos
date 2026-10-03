from collections.abc import Callable
from typing import Protocol

import psycopg
from psycopg.types.json import Jsonb

from habitat.contracts import RawManifest
from habitat.storage.series import SeriesStore

AlreadyIngested = Callable[[str, str, str], bool]


class DuplicateSourceKey(ValueError):
    pass


class ArtifactIndex(Protocol):
    def find_by_source_key(self, source_key: str) -> RawManifest | None: ...

    def record(self, manifest: RawManifest) -> None: ...

    def replace(self, manifest: RawManifest) -> None: ...


class MemoryArtifactIndex:
    """In-process index for tests and runs without a database."""

    def __init__(self):
        self.manifests: dict[str, RawManifest] = {}

    def find_by_source_key(self, source_key: str) -> RawManifest | None:
        return self.manifests.get(source_key)

    def record(self, manifest: RawManifest) -> None:
        key = manifest.extensions.source_key
        if key in self.manifests:
            raise DuplicateSourceKey(f"source key {key!r} is already archived")

        self.manifests[key] = manifest

    def replace(self, manifest: RawManifest) -> None:
        self.manifests[manifest.extensions.source_key] = manifest


class PostgresArtifactIndex:
    """Manifests in the `raw_artifacts` table. The raw files stay in the archive."""

    def __init__(self, connection: psycopg.Connection):
        self.connection = connection

    def find_by_source_key(self, source_key: str) -> RawManifest | None:
        row = self.connection.execute(
            "SELECT manifest FROM raw_artifacts WHERE source_key = %s", (source_key,)
        ).fetchone()
        return RawManifest.model_validate(row[0]) if row else None

    def record(self, manifest: RawManifest) -> None:
        try:
            self.connection.execute(
                """
                INSERT INTO raw_artifacts (artifact_id, version, source_id, source_item_id, source_key,
                                           processing_version, product_status, checksum, storage_uri, access_scope,
                                           retrieved_at, manifest)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                row_values(manifest),
            )
        except psycopg.errors.UniqueViolation as error:
            raise DuplicateSourceKey(f"source key {manifest.extensions.source_key!r} is already archived") from error

    def replace(self, manifest: RawManifest) -> None:
        self.connection.execute(
            """
            UPDATE raw_artifacts
            SET artifact_id = %s, version = %s, source_id = %s, source_item_id = %s, source_key = %s,
                processing_version = %s, product_status = %s, checksum = %s, storage_uri = %s, access_scope = %s,
                retrieved_at = %s, manifest = %s
            WHERE source_key = %s
            """,
            (*row_values(manifest), manifest.extensions.source_key),
        )


def row_values(manifest: RawManifest) -> tuple:
    item = manifest.extensions
    return (
        manifest.artifact_id, manifest.version, item.source_id, item.source_item_id, item.source_key,
        item.processing_version, item.product_status.value, manifest.checksum, manifest.storage.uri,
        manifest.access_scope, manifest.retrieved_at, Jsonb(manifest.model_dump(mode="json")),
    )


def never_ingested(source_item_id: str, processing_version: str, product_status: str) -> bool:
    return False


def ingested(store: SeriesStore, series_id: str) -> AlreadyIngested:
    """True when the latest version of the series holds the item. Connectors ask before they download."""
    latest = store.latest_version(series_id)
    if latest is None:
        return never_ingested

    return latest.has_item

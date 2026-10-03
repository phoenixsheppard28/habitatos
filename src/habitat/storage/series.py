import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel

from habitat.contracts import CELL_OBSERVATIONS_SCHEMA, RawManifest
from habitat.normalize.rows import NormalizedBatch


class BatchRecord(BaseModel):
    batch_key: str
    source_item_id: str
    processing_version: str
    product_status: str
    mapping_version: str
    files: list[str]
    row_count: int
    created_at: datetime


class SeriesVersion(BaseModel):
    series_id: str
    version: int
    created_at: datetime
    parent_version: int | None
    batches: list[BatchRecord]

    @property
    def files(self) -> list[str]:
        return [file for batch in self.batches for file in batch.files]

    def has_batch(self, batch_key: str) -> bool:
        return any(batch.batch_key == batch_key for batch in self.batches)

    def has_item(self, source_item_id: str, processing_version: str, product_status: str) -> bool:
        return any(
            (b.source_item_id, b.processing_version, b.product_status)
            == (source_item_id, processing_version, product_status)
            for b in self.batches
        )


@dataclass
class AppendResult:
    series_id: str
    version: int
    batch_key: str
    appended: bool


def batch_key(manifest: RawManifest, mapping_version: str) -> str:
    item = manifest.extensions
    return "|".join(
        [item.source_id, item.source_item_id, item.processing_version, item.product_status.value, mapping_version]
    )


# One overlapping or replaced value per cell, variable, source and UTC day is current:
# final beats preliminary, then the newest processing, then the clearest view, then the latest publication.
CURRENT_ROW_ORDER = """
    (product_status = 'final') DESC,
    processing_version DESC,
    mapping_version DESC,
    valid_fraction DESC,
    available_at DESC
"""


class SeriesStore:
    """Append-only Parquet storage for one canonical family. A version is a list of immutable batch files."""

    def __init__(self, root: Path, family: str = "cell_observations"):
        self.root = Path(root)
        self.family = family

    def append_batch(
        self, series_id: str, manifest: RawManifest, batch: NormalizedBatch, supersedes: tuple[str, ...] = ()
    ) -> AppendResult:
        key = batch_key(manifest, batch.mapping_version)
        latest = self.latest_version(series_id)

        if latest is not None and latest.has_batch(key):
            return AppendResult(series_id, latest.version, key, appended=False)

        record = BatchRecord(
            batch_key=key,
            source_item_id=manifest.extensions.source_item_id,
            processing_version=manifest.extensions.processing_version,
            product_status=manifest.extensions.product_status.value,
            mapping_version=batch.mapping_version,
            files=self.write_batch_files(manifest, key, batch.table),
            row_count=batch.table.num_rows,
            created_at=datetime.now(UTC),
        )
        kept = [b for b in (latest.batches if latest else []) if b.batch_key not in supersedes]
        version = SeriesVersion(
            series_id=series_id,
            version=(latest.version + 1) if latest else 1,
            created_at=record.created_at,
            parent_version=latest.version if latest else None,
            batches=[*kept, record],
        )
        self.write_version(version)

        return AppendResult(series_id, version.version, key, appended=True)

    def write_batch_files(self, manifest: RawManifest, key: str, table: pa.Table) -> list[str]:
        source_id = manifest.extensions.source_id
        start = manifest.extensions.time_start
        relative = (
            Path(self.family)
            / f"source_id={source_id}"
            / f"year={start.year:04d}"
            / f"month={start.month:02d}"
            / f"batch={safe_name(key)}.parquet"
        )
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, path)
        return [relative.as_posix()]

    def write_version(self, version: SeriesVersion) -> None:
        directory = self.versions_dir(version.series_id)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{version.version:06d}.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(version.model_dump_json(indent=2))
        temporary.rename(target)

    def latest_version(self, series_id: str) -> SeriesVersion | None:
        directory = self.versions_dir(series_id)
        if not directory.exists():
            return None

        files = sorted(directory.glob("*.json"))
        return self.read_version_file(files[-1]) if files else None

    def version(self, series_id: str, version: int) -> SeriesVersion:
        return self.read_version_file(self.versions_dir(series_id) / f"{version:06d}.json")

    def all_rows(self, version: SeriesVersion, as_of: datetime | None = None) -> duckdb.DuckDBPyRelation:
        connection = duckdb.connect()
        connection.execute("SET TimeZone = 'UTC'")
        paths = [str(self.root / file) for file in version.files]
        if not paths:
            return connection.from_arrow(CELL_OBSERVATIONS_SCHEMA.empty_table())

        relation = connection.read_parquet(paths)
        if as_of is not None:
            relation = relation.filter(f"available_at <= TIMESTAMPTZ '{as_of.isoformat()}'")
        return relation

    def current_rows(self, version: SeriesVersion, as_of: datetime | None = None) -> pa.Table:
        """Rows after de-duplication. With `as_of`, only values that were public at that time take part."""
        rows = self.all_rows(version, as_of)
        return rows.query(
            "rows",
            f"""
            SELECT * FROM rows
            QUALIFY row_number() OVER (
                PARTITION BY cell_id, variable, source_id, date_trunc('day', time_start AT TIME ZONE 'UTC')
                ORDER BY {CURRENT_ROW_ORDER}
            ) = 1
            """,
        ).to_arrow_table()

    def versions_dir(self, series_id: str) -> Path:
        return self.root / "_series" / safe_name(series_id) / "versions"

    @staticmethod
    def read_version_file(path: Path) -> SeriesVersion:
        return SeriesVersion.model_validate(json.loads(path.read_text()))


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value)

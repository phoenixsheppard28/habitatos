"""Per-cell counts derived from the current point events of one series. See docs/ingestion/EVENTS.md section 5.

FIRMS gives daily fire counts and fire power. GBIF gives monthly counts of one taxon and of the observer effort.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

import pandas as pd
import pyarrow as pa

from habitat.contracts import (
    CELL_OBSERVATIONS_SCHEMA,
    Coverage,
    RawManifest,
    Rights,
    SourceItem,
    SourceRef,
    TimePrecision,
)
from habitat.ingest import Workspace
from habitat.normalize.rows import NormalizedBatch, series_id_for
from habitat.storage.series import AppendResult, SeriesVersion

MAPPING_VERSION = "event-counts-v1"
STORAGE_FORMAT = "csv"
# A GBIF date of publication does not change what was observed, so the record still counts.
COUNTED_FLAGS = ["ok", "available_at_from_dataset"]


@dataclass(frozen=True)
class CountSpec:
    interval: str
    product: str
    source_resolution_m: float
    needs_taxon: bool = False


SPECS = {
    "firms_modis": CountSpec("day", "fire-counts-daily", 1000.0),
    "firms_viirs": CountSpec("day", "fire-counts-daily", 375.0),
    "gbif_occurrence": CountSpec("month", "occurrence-counts-monthly", 1000.0, needs_taxon=True),
}

# One current row per source record at the pinned version, as in recipe_point_events.
CURRENT_EVENTS = """
    SELECT DISTINCT ON (e.source_record_id) e.*
    FROM point_events e
    JOIN ingest_batches b USING (series_id, batch_key)
    WHERE e.series_id = %(series_id)s
      AND b.added_in_version <= %(version)s
      AND (b.superseded_in_version IS NULL OR b.superseded_in_version > %(version)s)
    ORDER BY e.source_record_id, (e.product_status = 'final') DESC, b.added_in_version DESC, e.batch_key DESC
"""

FIRE_COUNTS = f"""
    SELECT cell_id, date_trunc('day', time_start, 'UTC') AS time_start, count(*)::float AS fire_count,
           sum(value) AS fire_frp_sum_mw, count(*) AS pixel_count, max(available_at) AS available_at
    FROM ({CURRENT_EVENTS}) current
    WHERE quality_flag = ANY(%(counted)s) AND event_type = 'active_fire'
    GROUP BY 1, 2
"""

OCCURRENCE_COUNTS = f"""
    WITH counted AS (
        SELECT * FROM ({CURRENT_EVENTS}) current
        WHERE quality_flag = ANY(%(counted)s) AND event_type = 'species_occurrence' AND occurrence_status = 'present'
    ),
    taxon_bases AS (SELECT DISTINCT basis FROM counted WHERE gbif_taxon_key = %(taxon_key)s)
    SELECT cell_id, date_trunc('month', time_start, 'UTC') AS time_start,
           count(*) FILTER (WHERE gbif_taxon_key = %(taxon_key)s)::float AS occurrence_count,
           count(*)::float AS occurrence_effort_count, count(*) AS pixel_count, max(available_at) AS available_at
    FROM counted
    WHERE basis IN (SELECT basis FROM taxon_bases)
    GROUP BY 1, 2
"""

UNITS = {"fire_count": "count", "fire_frp_sum_mw": "MW", "occurrence_count": "count", "occurrence_effort_count": "count"}
STATS = {"fire_count": "count", "fire_frp_sum_mw": "sum", "occurrence_count": "count", "occurrence_effort_count": "count"}


def derive_event_counts(
    workspace: Workspace, events_series_id: str, taxon_key: int | None = None
) -> list[AppendResult]:
    """Count the current events of the latest version of an event series. One batch per day or month.

    A cell_observations batch holds one value per cell and variable, so each interval is a batch.
    The first new batch supersedes all batches of the previous run. No batch when no event passes the filter.
    """
    store = workspace.store
    version = store.latest_version(events_series_id)
    if version is None:
        raise ValueError(f"no series {events_series_id!r}")

    source_id = events_series_id.split("--", 1)[0]
    spec = SPECS.get(source_id)
    if spec is None:
        raise ValueError(f"no event counts are defined for the source {source_id!r}")
    if spec.needs_taxon and taxon_key is None:
        raise ValueError("occurrence counts need a taxon_key; a count of all taxa is the effort, not a presence")

    product = f"{spec.product}-{taxon_key}" if spec.needs_taxon else spec.product
    derived_source_id = f"{source_id}_derived"
    derived_series_id = series_id_for(derived_source_id, product, workspace.grid)
    run_id = f"{events_series_id}@{version.version}"
    previous = store.latest_version(derived_series_id)
    if previous is not None and any(b.source_item_id.startswith(f"{run_id}:") for b in previous.batches):
        return []

    counts = counted_events(workspace, events_series_id, version, spec, taxon_key)
    if counts.empty:
        return []

    supersedes = tuple(b.batch_key for b in previous.batches) if previous else ()
    results = []
    for interval_start, bucket in counts.groupby("time_start"):
        manifest = derived_manifest(workspace, bucket, derived_source_id, product, run_id, version, spec)
        batch = NormalizedBatch(cell_rows(bucket, manifest, derived_series_id, spec), MAPPING_VERSION)
        results.append(store.append_batch(derived_series_id, manifest, batch, supersedes=supersedes))
        supersedes = ()
    return results


def counted_events(
    workspace: Workspace, events_series_id: str, version: SeriesVersion, spec: CountSpec, taxon_key: int | None
) -> pd.DataFrame:
    parameters = {
        "series_id": events_series_id, "version": version.version, "counted": COUNTED_FLAGS, "taxon_key": taxon_key,
    }
    sql = OCCURRENCE_COUNTS if spec.needs_taxon else FIRE_COUNTS
    counts = pd.DataFrame(
        workspace.connection.execute(sql, parameters).fetchall(),
        columns=["cell_id", "time_start", *variables_of(spec), "pixel_count", "available_at"],
    )

    counts["time_start"] = pd.to_datetime(counts["time_start"], utc=True)
    interval = pd.DateOffset(months=1) if spec.interval == "month" else pd.Timedelta(days=1)
    counts["time_end"] = counts["time_start"] + interval
    counts["available_at"] = pd.to_datetime(counts["available_at"], utc=True)
    return counts


def variables_of(spec: CountSpec) -> list[str]:
    return ["occurrence_count", "occurrence_effort_count"] if spec.needs_taxon else ["fire_count", "fire_frp_sum_mw"]


def cell_rows(counts: pd.DataFrame, manifest: RawManifest, dataset_id: str, spec: CountSpec) -> pa.Table:
    item = manifest.extensions
    rows = counts.melt(
        id_vars=["cell_id", "time_start", "time_end", "pixel_count", "available_at"],
        value_vars=variables_of(spec), var_name="variable", value_name="value",
    )
    # A zero count of a presence-only taxon is unknown, not an absence, so it gets no row.
    rows = rows[(rows["variable"] != "occurrence_count") | (rows["value"] > 0)]
    rows = rows.assign(
        pixel_count=rows["value"].where(rows["variable"].map(STATS) == "count", rows["pixel_count"]).astype(int),
        time_precision=(TimePrecision.COMPOSITE if spec.interval == "month" else TimePrecision.DAY).value,
        source_id=item.source_id,
        source_item_id=item.source_item_id,
        processing_version=item.processing_version,
        product_status=item.product_status.value,
        dataset_id=dataset_id,
        mapping_version=MAPPING_VERSION,
        quality_flag="ok",
        stat=rows["variable"].map(STATS),
        std=None,
        unit=rows["variable"].map(UNITS),
        valid_fraction=1.0,
        source_resolution_m=spec.source_resolution_m,
    )
    return pa.Table.from_pandas(
        rows[CELL_OBSERVATIONS_SCHEMA.names], schema=CELL_OBSERVATIONS_SCHEMA, preserve_index=False
    )


def derived_manifest(
    workspace: Workspace,
    bucket: pd.DataFrame,
    source_id: str,
    product: str,
    run_id: str,
    version: SeriesVersion,
    spec: CountSpec,
) -> RawManifest:
    """The counts of the interval are archived as a CSV, so the derived batch has an artifact like any batch."""
    events_series_id = run_id.rsplit("@", 1)[0]
    start, end = bucket["time_start"].min().to_pydatetime(), bucket["time_end"].max().to_pydatetime()
    source_item_id = f"{run_id}:{start:%Y-%m-%d}"
    artifact_id = f"{source_id}-{product}-v{version.version}-{start:%Y%m%d}"
    stored_version, stored = workspace.archive.put(
        artifact_id, {"counts.csv": bucket.to_csv(index=False).encode()}, STORAGE_FORMAT
    )
    created_at = datetime.now(UTC)
    manifest = RawManifest(
        artifact_id=artifact_id,
        version=stored_version,
        created_at=created_at,
        access_scope="public",
        source=SourceRef(name=f"Counts derived from {events_series_id}"),
        storage=stored.storage,
        checksum=stored.checksum,
        retrieved_at=created_at,
        coverage=Coverage(start=start, end=end),
        rights=Rights(attribution=f"Derived from {events_series_id} version {version.version}"),
        extensions=SourceItem(
            source_id=source_id,
            product=product,
            source_item_id=source_item_id,
            source_key=f"{source_id}:{source_item_id}:{MAPPING_VERSION}",
            kind="derived",
            time_start=start,
            time_end=end,
            time_precision=TimePrecision.COMPOSITE if spec.interval == "month" else TimePrecision.DAY,
            available_at=bucket["available_at"].max().to_pydatetime(),
            processing_version=str(version.version),
            assets={"counts": "counts.csv"},
            properties={
                "inputs": [{
                    "dataset_id": events_series_id,
                    "dataset_version": version.version,
                    "mapping_version": ",".join(sorted({batch.mapping_version for batch in version.batches})),
                    "variable": "species_occurrence" if spec.needs_taxon else "active_fire",
                }],
                "counted_quality_flags": COUNTED_FLAGS,
                "interval": spec.interval,
            },
        ),
    )
    return workspace.archive.record(manifest)

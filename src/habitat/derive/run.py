"""Run the vegetation derivation on stored series and append the results as `_derived` series.

Each run writes a RawManifest with `kind = "derived"`. `properties.inputs` gives the dataset id, the dataset version,
the mapping version and the variable of each input, so `ingest_batches.raw_manifest` keeps the provenance.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import pandas as pd
import pyarrow as pa
import psycopg
from psycopg.rows import dict_row

from habitat.archive import Archive
from habitat.catalog.publish import publish_series_version
from habitat.contracts import (
    CELL_OBSERVATIONS_SCHEMA,
    BBox,
    Coverage,
    ProductStatus,
    RawManifest,
    Rights,
    SourceItem,
    SourceRef,
    TimePrecision,
)
from habitat.db import connect
from habitat.derive.registry import (
    ANNUAL_MAPPING_VERSION,
    ANNUAL_PRODUCT,
    ANNUAL_SOURCE_ID,
    DERIVED_FORMAT,
    TREND_MAPPING_VERSION,
    TREND_PRODUCT,
    TREND_SOURCE_ID,
)
from habitat.derive.vegetation import Season, Window, annual_ndvi, annual_rain, vegetation_trends
from habitat.fetch.connectors import ConnectorRequest, validate_area_and_dates
from habitat.fetch.connectors.chirps import PRODUCT as CHIRPS_PRODUCT
from habitat.fetch.connectors.stac import MODIS_PRODUCT, bbox_key
from habitat.grid import Grid, default_grid
from habitat.ingest import Workspace
from habitat.normalize.rows import NormalizedBatch, QuarantineError, series_id_for
from habitat.sources import get_source
from habitat.storage.series import SeriesStore

NDVI_INPUT = ("modis_mod13q1", MODIS_PRODUCT, "ndvi", "index")
RAIN_INPUT = ("chirps", CHIRPS_PRODUCT, "rainfall_mm", "mm")
ANNUAL_VARIABLES = ("ndvi_annual_integral", "rain_annual_mm")
# MOD13Q1 starts on 2000-02-18, so 2001 is its first complete year. CHIRPS starts in 1981.
FIRST_INPUT_YEAR = 2001

DERIVED_RIGHTS = Rights(
    license="Derived values; the licenses of the inputs in properties.inputs apply (NASA open data, CC-BY-4.0)",
    retention_allowed=True,
    reuse_allowed=True,
    attribution="Derived from MODIS MOD13Q1 (NASA LP DAAC) and CHIRPS v2.0 (Funk et al. 2015)",
)

INPUT_ROWS = """
    SELECT cell_id, time_start, time_end, value, unit, quality_flag, product_status, available_at,
           processing_version, mapping_version, source_resolution_m, variable
    FROM current_cell_observations_at(%(series_id)s, %(version)s)
    WHERE variable = ANY(%(variables)s) AND cell_id = ANY(%(cell_ids)s)
      AND time_start >= %(start)s AND time_start < %(end)s
"""


@dataclass
class DerivedRun:
    series_id: str
    version: int
    appended: bool
    row_count: int
    inputs: list[dict[str, Any]]
    flags: dict[str, int] = field(default_factory=dict)

    def report(self) -> dict[str, Any]:
        return {
            "series_id": self.series_id, "version": self.version, "appended": self.appended,
            "row_count": self.row_count, "inputs": self.inputs, "quality_flags": self.flags,
        }


def cells_in(grid: Grid, bbox: BBox) -> list[str]:
    rows, cols = grid.cells_in_bbox(bbox)
    return grid.cell_ids(rows, cols).tolist()


def read_input(
    connection: psycopg.Connection,
    store: SeriesStore,
    series_id: str,
    variables: tuple[str, ...],
    unit: str | None,
    cell_ids: list[str],
    window: Window,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    version = store.latest_version(series_id)
    if version is None:
        raise QuarantineError(f"no dataset version of the input {series_id}; ingest it before the derivation")

    seasons = window.all_seasons()
    with connection.cursor(row_factory=dict_row) as cursor:
        records = cursor.execute(INPUT_ROWS, {
            "series_id": series_id, "version": version.version, "variables": list(variables), "cell_ids": cell_ids,
            "start": seasons[0].start.to_pydatetime(), "end": seasons[-1].end.to_pydatetime(),
        }).fetchall()

    rows = pd.DataFrame(records)
    if rows.empty:
        raise QuarantineError(f"the input {series_id} has no rows in the area and the window")

    for variable, group in rows.groupby("variable"):
        units = set(group["unit"])
        if len(units) > 1 or (unit is not None and units != {unit}):
            raise QuarantineError(f"the input {series_id} gives {variable} in the units {sorted(units)}")

    for column in ("time_start", "time_end", "available_at"):
        rows[column] = pd.to_datetime(rows[column], utc=True)
    rows["value"] = rows["value"].astype(float)

    inputs = [
        {
            "dataset_id": series_id,
            "dataset_version": version.version,
            "mapping_version": ",".join(sorted(set(group["mapping_version"]))),
            "variable": variable,
        }
        for variable, group in rows.groupby("variable")
    ]
    return rows, inputs


def processing_version(inputs: list[dict[str, Any]], parameters: dict[str, Any]) -> str:
    """A short hash of the sorted inputs and the method parameters. New inputs give a new version."""
    content = json.dumps({"inputs": sorted(inputs, key=json.dumps), "parameters": parameters}, sort_keys=True)
    return hashlib.sha256(content.encode()).hexdigest()[:12]


def derived_manifest(
    archive: Archive,
    *,
    source_id: str,
    product: str,
    method: str,
    period: Season | Window,
    bbox: BBox,
    inputs: list[dict[str, Any]],
    parameters: dict[str, Any],
    rows: pd.DataFrame,
) -> RawManifest:
    version_hash = processing_version(inputs, parameters)
    window_start, window_end = period.start.to_pydatetime(), period.end.to_pydatetime()
    item_id = f"{method}:{window_start:%Y-%m-%d}:{window_end:%Y-%m-%d}:{bbox_key(bbox)}:{version_hash}"
    source_key = f"{source_id}:{item_id}"
    if (cached := archive.cached(source_key)) is not None:
        return cached

    artifact_id = item_id.replace(":", "_").replace(",", "_")
    properties = {"method": method, "inputs": inputs, "parameters": parameters, "requested_bbox": list(bbox)}
    content = json.dumps(properties, indent=2, sort_keys=True).encode()
    version, stored = archive.put(artifact_id, {"inputs.json": content}, DERIVED_FORMAT)

    preliminary = bool((rows["product_status"] == ProductStatus.PRELIMINARY.value).any())
    now = datetime.now(UTC)
    manifest = RawManifest(
        artifact_id=artifact_id,
        version=version,
        created_at=now,
        access_scope="public",
        source=SourceRef(name=f"Habitat Watch derivation: {method}"),
        storage=stored.storage,
        checksum=stored.checksum,
        retrieved_at=now,
        coverage=Coverage(bbox=bbox, start=window_start, end=window_end),
        rights=DERIVED_RIGHTS,
        extensions=SourceItem(
            source_id=source_id,
            product=product,
            source_item_id=item_id,
            source_key=source_key,
            kind="derived",
            time_start=window_start,
            time_end=window_end,
            time_precision=TimePrecision.COMPOSITE,
            available_at=rows["available_at"].max().to_pydatetime(),
            processing_version=version_hash,
            product_status=ProductStatus.PRELIMINARY if preliminary else ProductStatus.FINAL,
            assets={"inputs": "inputs.json"},
            properties=properties,
        ),
    )
    return archive.record(manifest)


def cell_table(rows: pd.DataFrame, manifest: RawManifest, grid: Grid, mapping_version: str) -> pa.Table:
    item = manifest.extensions
    rows = rows.assign(
        time_precision=TimePrecision.COMPOSITE.value,
        source_id=item.source_id,
        source_item_id=item.source_item_id,
        processing_version=item.processing_version,
        dataset_id=series_id_for(item.source_id, item.product, grid),
        mapping_version=mapping_version,
    )
    return pa.Table.from_pandas(
        rows[CELL_OBSERVATIONS_SCHEMA.names], schema=CELL_OBSERVATIONS_SCHEMA, preserve_index=False
    )


def earlier_runs(connection: psycopg.Connection, series_id: str, manifest: RawManifest) -> tuple[str, ...]:
    """Live batches of the same method, window and area with other inputs. The new run supersedes them."""
    item = manifest.extensions
    prefix = item.source_item_id.rsplit(":", 1)[0] + ":"
    return tuple(
        batch_key
        for (batch_key,) in connection.execute(
            "SELECT batch_key FROM ingest_batches WHERE series_id = %s AND superseded_in_version IS NULL "
            "AND starts_with(source_item_id, %s) AND source_item_id <> %s",
            (series_id, prefix, item.source_item_id),
        )
    )


def append_derived(
    workspace, manifest: RawManifest, rows: pd.DataFrame, mapping_version: str, run: DerivedRun | None = None
) -> DerivedRun:
    """Append one derived batch. With `run`, add the batch to the summary of a run with several batches."""
    item = manifest.extensions
    series_id = series_id_for(item.source_id, item.product, workspace.grid)
    batch = NormalizedBatch(cell_table(rows, manifest, workspace.grid, mapping_version), mapping_version)

    supersedes = earlier_runs(workspace.connection, series_id, manifest)
    result = workspace.store.append_batch(series_id, manifest, batch, supersedes=supersedes)

    run = run or DerivedRun(series_id, result.version, False, 0, item.properties["inputs"])
    run.version = result.version
    run.appended = run.appended or result.appended
    run.row_count += len(rows)
    for flag, count in rows["quality_flag"].value_counts().items():
        run.flags[flag] = run.flags.get(flag, 0) + int(count)
    return run


def publish(workspace, run: DerivedRun, source_id: str, access_scope: str) -> None:
    publish_series_version(
        workspace.store, workspace.catalog, workspace.grid, run.series_id, source_id=source_id,
        description=get_source(source_id).description, access_scope=access_scope,
    )


def derive_vegetation_indicators(
    workspace, bbox: BBox, window: Window, publish_results: bool = True, access_scope: str = "public"
) -> dict[str, DerivedRun]:
    """Annual summaries first, one batch per year, then the trends of the stored annual summaries.

    Downloads nothing. `workspace` gives `connection`, `grid`, `archive`, `store` and `catalog`, as
    `habitat.ingest.Workspace` does.
    """
    grid, connection, store = workspace.grid, workspace.connection, workspace.store
    cell_ids = cells_in(grid, bbox)
    seasons = window.all_seasons()
    annual_parameters = {"season_start_month": window.season_start_month}
    trend_parameters = {
        **annual_parameters,
        "window": [window.first_year, window.last_year],
        "baseline": [window.baseline_first, window.baseline_last],
    }

    ndvi, ndvi_inputs = read_input(
        connection, store, series_id_for(*NDVI_INPUT[:2], grid), NDVI_INPUT[2:3], NDVI_INPUT[3], cell_ids, window
    )
    rain, rain_inputs = read_input(
        connection, store, series_id_for(*RAIN_INPUT[:2], grid), RAIN_INPUT[2:3], RAIN_INPUT[3], cell_ids, window
    )
    annual = pd.concat([annual_ndvi(ndvi, seasons), annual_rain(rain, seasons)], ignore_index=True)

    annual_run = None
    for season in seasons:
        year_rows = annual[annual["time_start"] == season.start]
        if year_rows.empty:
            continue

        manifest = derived_manifest(
            workspace.archive, source_id=ANNUAL_SOURCE_ID, product=ANNUAL_PRODUCT, method="vegetation_annual",
            period=season, bbox=bbox, inputs=ndvi_inputs + rain_inputs, parameters=annual_parameters, rows=year_rows,
        )
        annual_run = append_derived(workspace, manifest, year_rows, ANNUAL_MAPPING_VERSION, annual_run)

    stored_annual, annual_inputs = read_input(
        connection, store, annual_run.series_id, ANNUAL_VARIABLES, None, cell_ids, window
    )
    trends = vegetation_trends(stored_annual, window)
    trend_manifest = derived_manifest(
        workspace.archive, source_id=TREND_SOURCE_ID, product=TREND_PRODUCT, method="vegetation_trend",
        period=window, bbox=bbox, inputs=annual_inputs, parameters=trend_parameters, rows=trends,
    )
    trend_run = append_derived(workspace, trend_manifest, trends, TREND_MAPPING_VERSION)

    if publish_results:
        publish(workspace, annual_run, ANNUAL_SOURCE_ID, access_scope)
        publish(workspace, trend_run, TREND_SOURCE_ID, access_scope)

    return {ANNUAL_SOURCE_ID: annual_run, TREND_SOURCE_ID: trend_run}


def check_request(bbox: list[float], window: Window) -> None:
    this_year = datetime.now(UTC).year
    for first, last in ((window.first_year, window.last_year), (window.baseline_first, window.baseline_last)):
        if not FIRST_INPUT_YEAR <= first <= last <= this_year:
            raise ValueError(f"window and baseline years must be {FIRST_INPUT_YEAR}..{this_year}, first <= last")

    validate_area_and_dates(
        ConnectorRequest(bbox=tuple(bbox), start=date(window.first_year, 1, 1), end=date(window.last_year, 12, 31))
    )


def derive_habitat_indicators(
    bbox: list[float], window_start: int, window_end: int, baseline_start: int = 2001, baseline_end: int = 2015
) -> dict[str, Any]:
    """The fetch agent entry point: validate, run on the configured database and report. Never a degraded label."""
    try:
        window = Window(window_start, window_end, baseline_start, baseline_end)
        check_request(bbox, window)
    except ValueError as error:
        return {"status": "error", "message": str(error)}

    try:
        with connect() as connection:
            runs = derive_vegetation_indicators(Workspace(connection, default_grid()), tuple(bbox), window)
    except QuarantineError as error:
        return {"status": "insufficient_data", "message": str(error)}

    return {
        "status": "ok",
        "runs": {source_id: run.report() for source_id, run in runs.items()},
        "limitations": [
            "The values are indicators. Drought, fire, grazing or cropping can also lower NDVI.",
            "A year needs 16 valid MODIS composites and every CHIRPS day; fetch whole years first.",
        ],
    }

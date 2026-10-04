from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from habitat.contracts import CELL_OBSERVATIONS_SCHEMA
from habitat.derive.run import cells_in, derive_vegetation_indicators
from habitat.derive.vegetation import Window
from habitat.fetch.connectors.chirps import PRODUCT as CHIRPS_PRODUCT
from habitat.fetch.connectors.stac import MODIS_PRODUCT
from habitat.ingest import Workspace
from habitat.normalize.rows import QuarantineError, series_id_for
from habitat.storage.series import insert_grid_cells

BBOX = (36.900, -1.500, 36.915, -1.490)
YEARS = range(2001, 2013)
WINDOW = Window(first_year=2001, last_year=2012, baseline_first=2001, baseline_last=2012)
PUBLISHED = datetime(2013, 3, 1, tzinfo=UTC)
LATE = datetime(2014, 1, 1, tzinfo=UTC)


def rain_mm(year: int) -> float:
    return 600 + 150 * np.cos(np.pi * (year - 2006.5) / 2.5)


def store_items(workspace, source_id, product, rows: pd.DataFrame, version=1):
    """Write one batch per source item straight to the tables, as many ingests of single scenes or days would."""
    series = series_id_for(source_id, product, workspace.grid)
    rows = rows.assign(
        time_precision="composite", source_id=source_id, product_status="final", dataset_id=series,
        quality_flag="ok", std=None, valid_fraction=1.0, pixel_count=16,
    )
    rows["batch_key"] = (
        source_id + "|" + rows["source_item_id"] + "|" + rows["processing_version"] + "|final|"
        + rows["mapping_version"]
    )
    batches = rows.drop_duplicates("batch_key")

    with workspace.connection.transaction(), workspace.connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO series (series_id, family, source_id, product) VALUES (%s, 'cell_observations', %s, %s) "
            "ON CONFLICT DO NOTHING", (series, source_id, product),
        )
        cursor.execute("INSERT INTO series_versions (series_id, version) VALUES (%s, %s)", (series, version))
        cursor.executemany(
            "INSERT INTO ingest_batches (series_id, batch_key, source_item_id, processing_version, product_status, "
            "mapping_version, row_count, raw_manifest, added_in_version) "
            "VALUES (%s, %s, %s, %s, 'final', %s, 0, '{}', %s)",
            [(series, b.batch_key, b.source_item_id, b.processing_version, b.mapping_version, version)
             for b in batches.itertuples()],
        )
        cursor.execute("UPDATE series SET latest_version = %s WHERE series_id = %s", (version, series))
        insert_grid_cells(cursor, workspace.grid, rows["cell_id"].unique())
        columns = ["series_id", "batch_key", *CELL_OBSERVATIONS_SCHEMA.names]
        with cursor.copy(f"COPY cell_observations ({', '.join(columns)}) FROM STDIN") as copy:
            for row in rows.assign(series_id=series)[columns].itertuples(index=False):
                copy.write_row(row)


def append_modis(workspace, cell_ids, version=1, processing_version="061.2020001000000", shift=0.0):
    rows = []
    for cell_id in cell_ids:
        for year in YEARS:
            ndvi = 0.2 + 0.0002 * rain_mm(year) + 0.002 * (year - 2001) + shift
            for composite in range(23):
                start = datetime(year, 1, 1, tzinfo=UTC) + timedelta(days=16 * composite)
                rows.append({"cell_id": cell_id, "time_start": start, "time_end": start + timedelta(days=16),
                             "available_at": PUBLISHED, "variable": "ndvi", "value": ndvi, "unit": "index",
                             "stat": "mean", "source_resolution_m": 231.656358,
                             "source_item_id": f"MOD13Q1.A{start:%Y%j}", "processing_version": processing_version,
                             "mapping_version": "modis-mod13q1-v1"})
    store_items(workspace, "modis_mod13q1", MODIS_PRODUCT, pd.DataFrame(rows), version)


def append_chirps(workspace, cell_ids):
    rows = []
    for cell_id in cell_ids:
        for year in YEARS:
            days = pd.date_range(f"{year}-01-01", f"{year}-12-31", freq="D", tz="UTC")
            for day in days:
                available_at = LATE if (year, day.dayofyear) == (2010, 1) else PUBLISHED
                rows.append({"cell_id": cell_id, "time_start": day, "time_end": day + timedelta(days=1),
                             "available_at": available_at, "variable": "rainfall_mm",
                             "value": rain_mm(year) / len(days), "unit": "mm", "stat": "sum",
                             "source_resolution_m": 5566.0, "source_item_id": f"chirps-v2.0.{day:%Y.%m.%d}",
                             "processing_version": "2.0", "mapping_version": "chirps-v2-daily-v1"})
    store_items(workspace, "chirps", CHIRPS_PRODUCT, pd.DataFrame(rows))


@pytest.fixture
def workspace(database, grid):
    return Workspace(database, grid)


@pytest.fixture
def stored_inputs(workspace):
    cell_ids = cells_in(workspace.grid, BBOX)
    append_modis(workspace, cell_ids)
    append_chirps(workspace, cell_ids)
    return cell_ids


def raw_manifest(connection, series_id):
    return connection.execute(
        "SELECT raw_manifest FROM ingest_batches WHERE series_id = %s AND superseded_in_version IS NULL", (series_id,)
    ).fetchall()


def test_the_derivation_appends_annual_and_trend_series_with_their_inputs(workspace, stored_inputs):
    runs = derive_vegetation_indicators(workspace, BBOX, WINDOW)

    annual, trend = runs["vegetation_annual_derived"], runs["vegetation_trend_derived"]
    assert annual.appended and trend.appended
    annual_manifests = [manifest for (manifest,) in raw_manifest(workspace.connection, annual.series_id)]
    assert len(annual_manifests) == len(YEARS)
    annual_manifest = annual_manifests[0]
    assert annual_manifest["extensions"]["kind"] == "derived"
    inputs = annual_manifest["extensions"]["properties"]["inputs"]
    assert sorted((i["dataset_id"], i["dataset_version"], i["variable"]) for i in inputs) == [
        (series_id_for("chirps", CHIRPS_PRODUCT, workspace.grid), 1, "rainfall_mm"),
        (series_id_for("modis_mod13q1", MODIS_PRODUCT, workspace.grid), 1, "ndvi"),
    ]
    [(trend_manifest,)] = raw_manifest(workspace.connection, trend.series_id)
    assert {i["dataset_id"] for i in trend_manifest["extensions"]["properties"]["inputs"]} == {annual.series_id}

    rows = pd.DataFrame(workspace.store.current_cell_rows(trend.series_id))
    assert set(rows["cell_id"]) == set(stored_inputs)
    restrend = rows[rows["variable"] == "restrend_slope"]
    assert np.allclose(restrend["value"].astype(float), 0.002 * 23, atol=1e-6)
    assert set(restrend["quality_flag"]) == {"short_series"}
    assert (restrend["available_at"] == LATE).all()
    assert (rows[rows["variable"] == "ndvi_trend_slope"]["available_at"] == PUBLISHED).all()
    assert workspace.catalog.latest(trend.series_id) is not None


def test_a_second_run_with_the_same_inputs_appends_nothing(workspace, stored_inputs):
    derive_vegetation_indicators(workspace, BBOX, WINDOW)

    runs = derive_vegetation_indicators(workspace, BBOX, WINDOW)

    assert not any(run.appended for run in runs.values())


def test_a_new_input_version_supersedes_the_earlier_run(workspace, stored_inputs):
    first = derive_vegetation_indicators(workspace, BBOX, WINDOW)["vegetation_annual_derived"]
    append_modis(workspace, stored_inputs, version=2, processing_version="061.2030001000000", shift=0.01)

    second = derive_vegetation_indicators(workspace, BBOX, WINDOW)["vegetation_annual_derived"]

    assert second.appended and second.version == first.version + len(YEARS)
    live = raw_manifest(workspace.connection, second.series_id)
    assert len(live) == len(YEARS)
    assert all({i["dataset_version"] for i in manifest["extensions"]["properties"]["inputs"]} == {1, 2}
               for (manifest,) in live)


def test_a_missing_input_series_is_quarantined(workspace):
    with pytest.raises(QuarantineError, match="no dataset version"):
        derive_vegetation_indicators(workspace, BBOX, WINDOW)

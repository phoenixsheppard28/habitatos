from datetime import UTC, datetime

import pandas as pd
import pytest

from conftest import make_manifest
from habitat.contracts import TimePrecision
from habitat.event_counts import derive_event_counts
from habitat.ingest import Workspace
from habitat.normalize.events import to_point_events
from habitat.normalize.rows import series_id

CELL_POINT = (36.837, -1.2753)
OTHER_CELL_POINT = (37.1, -1.5)


def events(records: list[dict]) -> pd.DataFrame:
    defaults = {
        "occurrence_status": "present", "time_precision": "instant", "coordinate_uncertainty_m": 500.0,
        "basis": "satellite_detection", "method": "MODIS 6.2", "attributes": "{}", "unit": "MW",
        "sampling_design": "systematic", "event_type": "active_fire",
    }
    rows = []
    for record in records:
        row = defaults | record
        row["longitude"], row["latitude"] = row.pop("point")
        row["time_end"] = row.get("time_end", row["time_start"])
        rows.append(row)
    return pd.DataFrame(rows)


def append_events(workspace, source_id, item_id, rows, product):
    manifest = make_manifest(
        source_id, {}, datetime(2012, 1, 1, tzinfo=UTC), datetime(2013, 1, 1, tzinfo=UTC), item_id=item_id,
        product=product, precision=TimePrecision.COMPOSITE, storage_format="csv",
    )
    batch = to_point_events(rows, manifest, workspace.grid, "test-v1")
    series = series_id(manifest, workspace.grid)
    workspace.store.append_batch(series, manifest, batch)
    return series


def fire(record_id, day_time, frp, flag="ok", point=CELL_POINT, published="2024-12-04T00:00Z"):
    return {"source_record_id": record_id, "time_start": pd.Timestamp(day_time), "value": frp, "quality_flag": flag,
            "point": point, "available_at": pd.Timestamp(published)}


def test_fires_become_daily_counts_and_power_per_cell(database, grid):
    workspace = Workspace(database, grid)
    series = append_events(workspace, "firms_modis", "fires-2012", events([
        fire("a", "2012-03-12T08:00Z", 10.0, published="2024-12-01T00:00Z"),
        fire("b", "2012-03-12T23:49Z", 30.0),
        fire("c", "2012-03-12T23:49Z", 99.0, flag="low_confidence"),
        fire("d", "2012-03-13T11:00Z", 5.0),
        fire("e", "2012-03-12T11:00Z", 7.0, point=OTHER_CELL_POINT),
    ]), "firms-modis-sp")

    results = derive_event_counts(workspace, series)

    rows = pd.DataFrame(workspace.store.current_cell_rows(results[-1].series_id))
    assert len(results) == 2
    assert results[-1].series_id.startswith("firms_modis_derived--fire-counts-daily--")
    day = rows[(rows["time_start"] == pd.Timestamp("2012-03-12T00:00Z")) & (rows["pixel_count"] == 2)]
    assert dict(zip(day["variable"], day["value"])) == {"fire_count": 2.0, "fire_frp_sum_mw": 40.0}
    assert set(day["unit"]) == {"count", "MW"}
    assert set(day["time_end"]) == {pd.Timestamp("2012-03-13T00:00Z")}
    assert set(day["available_at"]) == {pd.Timestamp("2024-12-04T00:00Z")}
    assert set(rows["time_precision"]) == {"day"}
    assert len(rows) == 6


def test_a_derived_batch_records_its_inputs_and_replaces_the_previous_run(database, grid):
    workspace = Workspace(database, grid)
    series = append_events(workspace, "firms_modis", "fires-a", events([fire("a", "2012-03-12T08:00Z", 10.0)]),
                           "firms-modis-sp")
    [first] = derive_event_counts(workspace, series)
    append_events(workspace, "firms_modis", "fires-b", events([fire("b", "2012-03-12T09:00Z", 20.0)]),
                  "firms-modis-sp")

    [second] = derive_event_counts(workspace, series)

    counts = [r for r in workspace.store.current_cell_rows(second.series_id) if r["variable"] == "fire_count"]
    assert [r["value"] for r in counts] == [2.0]
    manifest = database.execute(
        "SELECT raw_manifest FROM ingest_batches WHERE series_id = %s AND batch_key = %s",
        (second.series_id, second.batch_key),
    ).fetchone()[0]
    assert manifest["extensions"]["kind"] == "derived"
    [source] = manifest["extensions"]["properties"]["inputs"]
    assert (source["dataset_id"], source["dataset_version"]) == (series, 2)
    assert source["mapping_version"] == "test-v1" and source["variable"] == "active_fire"
    assert first.version == 1 and second.version == 2
    assert derive_event_counts(workspace, series) == []


def test_occurrences_become_monthly_counts_of_the_taxon_and_of_the_effort(database, grid):
    workspace = Workspace(database, grid)

    def sighting(record_id, key, day, basis="HUMAN_OBSERVATION", flag="ok"):
        start = pd.Timestamp(day)
        return {"source_record_id": record_id, "time_start": start, "time_end": start + pd.Timedelta(days=1),
                "time_precision": "day", "quality_flag": flag, "point": CELL_POINT, "gbif_taxon_key": key,
                "taxon_name": f"taxon {key}", "basis": basis, "available_at": pd.Timestamp("2025-08-08T00:00Z"),
                "event_type": "species_occurrence", "sampling_design": "presence_only", "unit": None,
                "value": None, "method": None}

    series = append_events(workspace, "gbif_occurrence", "search-1", events([
        sighting("1", 2441105, "2012-03-02"),
        sighting("2", 2441105, "2012-03-20", flag="available_at_from_dataset"),
        sighting("3", 5229154, "2012-03-05"),
        sighting("4", 5229154, "2012-03-06", basis="PRESERVED_SPECIMEN"),
        sighting("5", 2441105, "2012-03-07", flag="geospatial_issue"),
    ]), "gbif-occurrence")

    [result] = derive_event_counts(workspace, series, taxon_key=2441105)

    rows = {r["variable"]: r for r in workspace.store.current_cell_rows(result.series_id)}
    assert result.series_id.startswith("gbif_occurrence_derived--occurrence-counts-monthly-2441105--")
    assert rows["occurrence_count"]["value"] == 2.0
    assert rows["occurrence_effort_count"]["value"] == 3.0
    assert rows["occurrence_count"]["time_start"] == datetime(2012, 3, 1, tzinfo=UTC)
    assert rows["occurrence_count"]["time_end"] == datetime(2012, 4, 1, tzinfo=UTC)
    assert rows["occurrence_count"]["time_precision"] == "composite"


def test_occurrence_counts_need_a_taxon(database, grid):
    workspace = Workspace(database, grid)
    series = append_events(workspace, "gbif_occurrence", "search-1", events([fire("a", "2012-03-12T08:00Z", 1.0)]),
                           "gbif-occurrence")

    with pytest.raises(ValueError, match="taxon_key"):
        derive_event_counts(workspace, series)


def test_no_counted_events_gives_no_batch(database, grid):
    workspace = Workspace(database, grid)
    series = append_events(workspace, "firms_modis", "fires-a",
                           events([fire("a", "2012-03-12T08:00Z", 10.0, flag="low_confidence")]), "firms-modis-sp")

    assert derive_event_counts(workspace, series) == []

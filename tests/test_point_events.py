from datetime import UTC, datetime

import pandas as pd
import pytest

from conftest import make_manifest
from habitat.catalog.ai import CatalogAssistant
from habitat.catalog.publish import ROW_GRAIN, publish_series_version
from habitat.catalog.store import PostgresCatalog
from habitat.contracts import POINT_EVENTS_SCHEMA, TimePrecision
from habitat.normalize.events import EVENT_TYPES, quality_flags, to_point_events
from habitat.normalize.rows import POINT_EVENTS, series_id
from habitat.storage.series import FAMILY_SUMMARIES, SeriesStore

FIRE_TIME = datetime(2012, 3, 12, 23, 49, tzinfo=UTC)


def event_rows(**changes) -> pd.DataFrame:
    values = {
        "source_record_id": ["Aqua:2012-03-12T2349Z:-1.2753:36.837"],
        "event_type": ["active_fire"],
        "occurrence_status": ["present"],
        "sampling_design": ["systematic"],
        "time_start": [pd.Timestamp(FIRE_TIME)],
        "time_end": [pd.Timestamp(FIRE_TIME)],
        "time_precision": ["instant"],
        "available_at": [pd.Timestamp("2024-12-04T21:17:10Z")],
        "longitude": [36.837],
        "latitude": [-1.2753],
        "coordinate_uncertainty_m": [1000.0],
        "value": [128.6],
        "unit": ["MW"],
        "basis": ["satellite_detection"],
        "method": ["MODIS 6.2"],
        "quality_flag": ["ok"],
        "attributes": ['{"satellite": "Aqua"}'],
    }
    return pd.DataFrame(values | changes)


def fire_manifest(item_id="firms:modis:Kenya:2012:36.70000,-1.60000,37.20000,-1.25000", processing_version="6.2"):
    return make_manifest(
        "firms_modis", {}, datetime(2012, 1, 1, tzinfo=UTC), datetime(2013, 1, 1, tzinfo=UTC), item_id=item_id,
        product="firms-modis-sp", precision=TimePrecision.COMPOSITE, processing_version=processing_version,
        available_at=datetime(2024, 12, 4, tzinfo=UTC), storage_format="csv",
    )


def test_rows_become_a_table_with_the_point_events_schema(grid):
    batch = to_point_events(event_rows(), fire_manifest(), grid, "firms-csv-v1")

    assert batch.family == POINT_EVENTS
    assert batch.table.schema.equals(POINT_EVENTS_SCHEMA)
    [row] = batch.table.to_pylist()
    assert row["cell_id"].startswith("E1K-r")
    assert row["taxon_name"] is None and row["individual_count"] is None
    assert row["source_item_id"] == fire_manifest().extensions.source_item_id
    assert row["dataset_id"] == series_id(fire_manifest(), grid)


def test_an_unknown_event_type_is_a_bug_not_a_quarantine(grid):
    with pytest.raises(ValueError, match="event_type"):
        to_point_events(event_rows(event_type=["volcano"]), fire_manifest(), grid, "firms-csv-v1")


def test_an_unknown_sampling_design_is_a_bug(grid):
    with pytest.raises(ValueError, match="sampling_design"):
        to_point_events(event_rows(sampling_design=["random"]), fire_manifest(), grid, "firms-csv-v1")


def test_the_vocabulary_matches_the_design():
    assert EVENT_TYPES == {
        "species_occurrence", "camera_trap_detection", "active_fire", "wildlife_mortality", "disease_outbreak",
        "human_wildlife_conflict",
    }


def test_quality_flag_is_the_first_matching_reason_in_the_design_order():
    rows = event_rows(
        coordinate_uncertainty_m=[5000.0, 10.0, 10.0, 10.0],
        time_start=[pd.Timestamp("2012-01-01T00:00Z")] * 4,
        time_end=[pd.Timestamp("2012-01-01T00:00Z"), pd.Timestamp("2013-01-01T00:00Z"),
                  pd.Timestamp("2012-01-02T00:00Z"), pd.Timestamp("2012-01-02T00:00Z")],
        source_record_id=["a", "b", "c", "d"], event_type=["active_fire"] * 4, occurrence_status=["present"] * 4,
        sampling_design=["systematic"] * 4, time_precision=["instant"] * 4, available_at=[FIRE_TIME] * 4,
        longitude=[36.8] * 4, latitude=[-1.3] * 4, value=[1.0] * 4, unit=["MW"] * 4, basis=["x"] * 4,
        method=["x"] * 4, quality_flag=["ok"] * 4, attributes=["{}"] * 4,
    )
    specific = {
        "low_confidence": pd.Series([True, True, True, False]),
        "geospatial_issue": pd.Series([False, False, False, False]),
    }

    flags = quality_flags(rows, specific)

    assert flags.tolist() == ["coordinate_uncertainty_too_large", "imprecise_date", "low_confidence", "ok"]


def test_point_events_have_a_row_grain_and_a_summary():
    assert ROW_GRAIN[POINT_EVENTS] == "one row per event"
    assert POINT_EVENTS in FAMILY_SUMMARIES


def test_parse_question_offers_the_point_events_family():
    requests = []

    class Messages:
        def create(self, **request):
            requests.append(request)
            raise RuntimeError("stop after the request")

    client = type("Client", (), {"beta": type("Beta", (), {"messages": Messages()})()})()
    with pytest.raises(RuntimeError):
        CatalogAssistant(client=client).parse_question("fires near Nairobi in 2012")

    schema = requests[0]["output_config"]["format"]["schema"]
    assert POINT_EVENTS in schema["properties"]["families"]["items"]["enum"]


def test_a_later_batch_with_the_same_record_gives_one_current_row(database, grid):
    store = SeriesStore(database, grid)
    first = fire_manifest()
    overlapping = fire_manifest(item_id="firms:modis:Kenya:2012:36.80000,-1.50000,37.20000,-1.20000")
    series = series_id(first, grid)

    store.append_batch(series, first, to_point_events(event_rows(), first, grid, "firms-csv-v1"))
    store.append_batch(series, overlapping, to_point_events(event_rows(value=[130.0]), overlapping, grid, "firms-csv-v1"))

    rows = database.execute("SELECT value, source_item_id FROM current_point_events").fetchall()
    assert rows == [(130.0, overlapping.extensions.source_item_id)]
    stored = database.execute("SELECT count(*), bool_and(geometry IS NOT NULL) FROM point_events").fetchone()
    assert stored == (2, True)


def test_summary_and_recipe_view_give_one_row_per_event(database, grid):
    store = SeriesStore(database, grid)
    catalog = PostgresCatalog(database)
    manifest = fire_manifest()
    series = series_id(manifest, grid)
    rows = event_rows(
        source_record_id=["a", "b"], event_type=["active_fire", "species_occurrence"],
        occurrence_status=["present", "absent"], sampling_design=["systematic", "presence_only"],
        time_start=[pd.Timestamp(FIRE_TIME)] * 2, time_end=[pd.Timestamp(FIRE_TIME)] * 2,
        time_precision=["instant", "day"], available_at=[pd.Timestamp(FIRE_TIME)] * 2,
        longitude=[36.837, 36.9], latitude=[-1.2753, -1.3], coordinate_uncertainty_m=[1000.0, None],
        value=[128.6, None], unit=["MW", None], basis=["satellite_detection", "HUMAN_OBSERVATION"],
        method=["MODIS 6.2", None], quality_flag=["ok", "ok"], attributes=["{}", "{}"],
        taxon_name=[None, "Connochaetes taurinus"], gbif_taxon_key=[None, 2441105],
    )
    store.append_batch(series, manifest, to_point_events(rows, manifest, grid, "firms-csv-v1"))

    descriptor = publish_series_version(store, catalog, grid, series, "firms_modis", "fires", "public")

    assert descriptor.family == POINT_EVENTS and descriptor.row_count == 2
    assert descriptor.variables == ["active_fire", "species_occurrence"]
    assert [(t.gbif_key, t.name) for t in descriptor.species] == [(2441105, "Connochaetes taurinus")]
    assert descriptor.coverage.start == FIRE_TIME
    view = database.execute(
        "SELECT dataset_version, species, occurrence_status FROM recipe_point_events ORDER BY source_record_id"
    ).fetchall()
    assert view == [("1", None, "present"), ("1", "Connochaetes taurinus", "absent")]

from datetime import UTC, datetime

import pandas as pd
import pytest
from pyproj import Geod
from shapely import wkt

from conftest import make_manifest
from habitat.catalog.publish import publish_series_version
from habitat.catalog.store import PostgresCatalog
from habitat.contracts import TimePrecision
from habitat.normalize.population import population_batch
from habitat.normalize.rows import POPULATION_COUNTS, series_id
from habitat.recipe_inputs import SEARCH_FILTERS, binding, to_recipe_datasets
from habitat.storage.series import SeriesStore
from test_ai_and_publish import fake_assistant
from test_population import area, record

PARK_WKT = "POLYGON ((36.80 -1.45, 36.95 -1.45, 36.95 -1.30, 36.80 -1.30, 36.80 -1.45))"
SMALLER_PARK_WKT = "POLYGON ((36.80 -1.45, 36.85 -1.45, 36.85 -1.40, 36.80 -1.40, 36.80 -1.45))"


def counts_manifest(processing_version="sha256:a"):
    return make_manifest(
        "test", {}, datetime(1977, 2, 5, tzinfo=UTC), item_id="item-1", product="counts",
        precision=TimePrecision.COMPOSITE, processing_version=processing_version,
        available_at=datetime(2016, 9, 27, tzinfo=UTC), storage_format="csv",
    )


def counts_batch(grid, manifest, value=42974.0, park_wkt=PARK_WKT):
    records = pd.DataFrame([record(value=value), record(source_record_id="r2", area_id="test:point",
                                                          taxon_name="Sheep and goats", gbif_taxon_key=None,
                                                          value=1000.0)])
    areas = pd.DataFrame([area(geometry_wkt=park_wkt), area("test:point", "POINT (37.5 -2.0)", "Test site")])
    return population_batch(records, areas, manifest, grid, "test-v1")


def append(database, grid, processing_version="sha256:a", **changes):
    manifest = counts_manifest(processing_version)
    store = SeriesStore(database, grid)
    store.append_batch(series_id(manifest, grid), manifest, counts_batch(grid, manifest, **changes))
    return store, series_id(manifest, grid)


def area_cells(database, area_id):
    return database.execute(
        "SELECT cell_id, overlap_fraction FROM count_area_cells WHERE area_id = %s", (area_id,)
    ).fetchall()


def test_an_appended_area_gets_its_cells_with_overlap_fractions(database, grid):
    append(database, grid)

    cells = area_cells(database, "test:park")
    fractions = [fraction for _, fraction in cells]
    assert all(0 < fraction <= 1 for fraction in fractions)
    park_km2 = abs(Geod(ellps="WGS84").geometry_area_perimeter(wkt.loads(PARK_WKT))[0]) / 1e6
    assert sum(fractions) * (grid.cell_size_m / 1000) ** 2 == pytest.approx(park_km2, rel=0.01)
    assert [fraction for _, fraction in area_cells(database, "test:point")] == [1.0]
    (geometry_type,) = database.execute(
        "SELECT GeometryType(geometry) FROM count_areas WHERE area_id = 'test:park'"
    ).fetchone()
    assert geometry_type == "POLYGON"


def test_a_changed_area_geometry_replaces_its_cells(database, grid):
    append(database, grid)
    before = len(area_cells(database, "test:park"))

    append(database, grid, processing_version="sha256:b", park_wkt=SMALLER_PARK_WKT)

    assert 0 < len(area_cells(database, "test:park")) < before


def test_a_newer_processing_version_replaces_the_row_in_the_recipe_view(database, grid):
    catalog = PostgresCatalog(database)
    store, series = append(database, grid, value=100.0)
    publish_series_version(store, catalog, grid, series, "test", "Test counts", "public")

    append(database, grid, processing_version="sha256:b", value=200.0)
    publish_series_version(store, catalog, grid, series, "test", "Test counts", "public")

    rows = database.execute(
        "SELECT dataset_version, value, area_type FROM recipe_population_counts "
        "WHERE dataset_id = %s AND source_record_id = 'r1' ORDER BY dataset_version",
        (series,),
    ).fetchall()
    assert rows == [("1", 100.0, "park"), ("2", 200.0, "park")]


def test_the_summary_gives_period_taxa_metrics_and_an_area_footprint(database, grid):
    store, series = append(database, grid)

    summary = store.summary(store.latest_version(series))

    assert summary.row_count == 2
    assert summary.start == datetime(1977, 2, 5, tzinfo=UTC)
    assert summary.end == datetime(1977, 2, 28, tzinfo=UTC)
    assert summary.taxa == [(2441105, "Connochaetes taurinus")]
    assert summary.variables == ["population_estimate"]
    footprint = wkt.loads(summary.footprint_wkt)
    assert footprint.contains(wkt.loads("POINT (36.9 -1.4)"))
    assert footprint.contains(wkt.loads("POINT (37.5 -2.0)"))


def test_publish_uses_the_area_footprint_and_the_row_grain(database, grid):
    store, series = append(database, grid)

    descriptor = publish_series_version(store, PostgresCatalog(database), grid, series, "test", "Test", "public")

    assert descriptor.family == "population_counts"
    assert descriptor.row_grain == "one row per count or estimate of one taxon, area and interval"
    west, south, east, north = descriptor.coverage.bbox
    assert west <= 36.8 and south <= -2.0 and east >= 37.5 and north >= -1.3


def test_the_question_parser_offers_the_population_family():
    assistant, messages = fake_assistant([{
        "families": ["population_counts"], "species_names": ["wildebeest"], "start": None, "end": None,
        "variables": [], "tags_any": [],
    }])

    filters = assistant.parse_question("Did the wildebeest of Athi-Kaputiei decline?")

    assert filters.families == ["population_counts"]
    schema = messages.requests[0]["output_config"]["format"]["schema"]
    assert "population_counts" in schema["properties"]["families"]["items"]["enum"]


def test_recipe_sees_population_counts_through_its_view(database, grid):
    store, series = append(database, grid)
    catalog = PostgresCatalog(database)
    publish_series_version(store, catalog, grid, series, "test", "Test counts", "public")

    [dataset] = to_recipe_datasets(catalog.latest(series))

    assert dataset.family == POPULATION_COUNTS
    roles = {column.role: column.name for column in dataset.columns if column.role}
    assert roles["species"] == "taxon_name" and roles["measurement"] == "value"
    assert {roles["interval_start"], roles["interval_end"], roles["available_at"]} == {
        "time_start", "time_end", "available_at"
    }
    view = binding(dataset, "public").table
    columns = ", ".join(column.name for column in dataset.columns)
    rows = database.execute(f"SELECT {columns} FROM {view} WHERE dataset_id = %s", (series,)).fetchall()
    assert len(rows) == 2
    assert "population_counts" in SEARCH_FILTERS["family"]

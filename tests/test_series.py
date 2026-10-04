from datetime import UTC, datetime

import pandas as pd
import pyarrow as pa
import pytest

from conftest import make_manifest
from habitat.catalog.publish import publish_series_version
from habitat.catalog.store import PostgresCatalog
from habitat.contracts import ProductStatus, TimePrecision
from habitat.normalize.rows import NormalizedBatch, series_id, to_cell_observations
from habitat.storage.series import (
    FAMILY_SUMMARIES,
    REFERENCE_UPSERTS,
    VERSION_BATCHES,
    SeriesStore,
    SeriesSummary,
    distinct_cell_ids,
    upsert_on_key,
    version_parameters,
)


def values(rows):
    return {row["value"] for row in rows}

DAY = datetime(2024, 3, 5, tzinfo=UTC)
NEXT_DAY = datetime(2024, 3, 6, tzinfo=UTC)


def rainfall_batch(grid, manifest, value, cells=("E1K-r1-c1", "E1K-r1-c2")):
    stats = pd.DataFrame(
        {"cell_id": list(cells), "variable": "rainfall_mm", "value": value, "std": None, "valid_fraction": 1.0, "pixel_count": 1}
    )
    return to_cell_observations(stats, manifest, grid, "chirps-v1", "sum", {"rainfall_mm": "mm"}, 5566)


def chirps_manifest(status, available_at, day=DAY, item_id="chirps-2024.03.05"):
    return make_manifest(
        "chirps", {}, day, day.replace(day=day.day + 1), item_id=item_id, product="chirps",
        precision=TimePrecision.DAY, status=status, available_at=available_at,
    )


def test_append_is_idempotent(database, grid):
    store = SeriesStore(database, grid)
    manifest = chirps_manifest(ProductStatus.FINAL, NEXT_DAY)
    series = series_id(manifest, grid)

    first = store.append_batch(series, manifest, rainfall_batch(grid, manifest, 3.0))
    second = store.append_batch(series, manifest, rainfall_batch(grid, manifest, 3.0))

    assert first.appended and first.version == 1
    assert not second.appended and second.version == 1


def test_each_append_makes_a_new_version_and_keeps_the_old_one(database, grid):
    store = SeriesStore(database, grid)
    day_one = chirps_manifest(ProductStatus.FINAL, NEXT_DAY)
    day_two = chirps_manifest(ProductStatus.FINAL, NEXT_DAY, day=NEXT_DAY, item_id="chirps-2024.03.06")
    series = series_id(day_one, grid)

    store.append_batch(series, day_one, rainfall_batch(grid, day_one, 3.0))
    store.append_batch(series, day_two, rainfall_batch(grid, day_two, 7.0))

    assert len(store.current_cell_rows(series, version=1)) == 2
    assert len(store.current_cell_rows(series)) == 4
    assert store.latest_version(series).version == 2


def test_final_replaces_preliminary_but_as_of_still_sees_preliminary(database, grid):
    store = SeriesStore(database, grid)
    preliminary = chirps_manifest(ProductStatus.PRELIMINARY, datetime(2024, 3, 7, tzinfo=UTC))
    final = chirps_manifest(ProductStatus.FINAL, datetime(2024, 3, 25, tzinfo=UTC))
    series = series_id(final, grid)

    store.append_batch(series, preliminary, rainfall_batch(grid, preliminary, 2.0))
    result = store.append_batch(series, final, rainfall_batch(grid, final, 5.0))

    assert result.appended
    assert values(store.current_cell_rows(series)) == {5.0}
    assert database.execute("SELECT count(*) FROM cell_observations").fetchone()[0] == 4
    assert values(store.current_cell_rows(series, as_of=datetime(2024, 3, 10, tzinfo=UTC))) == {2.0}
    rainfall = database.execute("SELECT DISTINCT rainfall_mm, product_status FROM rainfall_observations")
    assert rainfall.fetchall() == [(5.0, "final")]


def test_overlapping_scenes_keep_the_clearest_view(database, grid):
    store = SeriesStore(database, grid)
    acquired = datetime(2024, 3, 9, 8, 47, tzinfo=UTC)
    tile_a = make_manifest("sentinel2", {}, acquired, item_id="T33KXV", product="s2")
    tile_b = make_manifest("sentinel2", {}, acquired, item_id="T33KYV", product="s2")
    series = series_id(tile_a, grid)

    def ndvi(manifest, valid_fraction, value):
        stats = pd.DataFrame(
            {"cell_id": ["E1K-r1-c1"], "variable": "ndvi", "value": value, "std": 0.0,
             "valid_fraction": valid_fraction, "pixel_count": 5000}
        )
        return to_cell_observations(stats, manifest, grid, "s2-v1", "mean", {"ndvi": "index"}, 10)

    store.append_batch(series, tile_a, ndvi(tile_a, 0.6, 0.30))
    store.append_batch(series, tile_b, ndvi(tile_b, 0.9, 0.42))

    assert [row["value"] for row in store.current_cell_rows(series)] == [0.42]
    vegetation = database.execute("SELECT index_name, index_value, ST_GeometryType(geometry) FROM vegetation_observations")
    assert vegetation.fetchall() == [("ndvi", 0.42, "ST_Polygon")]


def test_rebuild_supersedes_old_batches(database, grid):
    store = SeriesStore(database, grid)
    manifest = chirps_manifest(ProductStatus.FINAL, NEXT_DAY)
    series = series_id(manifest, grid)
    old = store.append_batch(series, manifest, rainfall_batch(grid, manifest, 3.0))

    rebuilt = rainfall_batch(grid, manifest, 4.0)
    rebuilt.mapping_version = "chirps-v2"
    store.append_batch(series, manifest, rebuilt, supersedes=(old.batch_key,))

    latest = store.latest_version(series)
    assert [b.mapping_version for b in latest.batches] == ["chirps-v2"]
    assert values(store.current_cell_rows(series)) == {4.0}
    assert values(store.current_cell_rows(series, version=1)) == {3.0}


AREAS_SCHEMA = pa.schema(
    [
        pa.field("area_id", pa.string(), nullable=False),
        pa.field("name", pa.string(), nullable=True),
        pa.field("cell_id", pa.string(), nullable=True),
        pa.field("attributes", pa.string(), nullable=False),
    ]
)
COUNTS_SCHEMA = pa.schema(
    [
        pa.field("area_id", pa.string(), nullable=False),
        pa.field("time_start", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("time_end", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("value", pa.float64(), nullable=True),
    ]
)


def summarize_test_counts(connection, version):
    start, end, row_count = connection.execute(
        f"SELECT min(time_start), max(time_end), count(*) FROM test_counts "
        f"WHERE series_id = %(series_id)s AND batch_key IN ({VERSION_BATCHES})",
        version_parameters(version),
    ).fetchone()
    return SeriesSummary(start, end, row_count, ["animal_count"], [])


@pytest.fixture
def count_family(database, monkeypatch):
    """A family without cell ids and with an area reference table, as a new shape has."""
    database.execute(
        "CREATE TABLE test_areas (area_id text PRIMARY KEY, name text, cell_id text REFERENCES grid_cells, "
        "attributes jsonb NOT NULL DEFAULT '{}')"
    )
    database.execute(
        "CREATE TABLE test_counts (series_id text NOT NULL, batch_key text NOT NULL, "
        "area_id text NOT NULL REFERENCES test_areas, time_start timestamptz NOT NULL, "
        "time_end timestamptz NOT NULL, value double precision, "
        "FOREIGN KEY (series_id, batch_key) REFERENCES ingest_batches (series_id, batch_key))"
    )
    monkeypatch.setitem(REFERENCE_UPSERTS, "test_areas", upsert_on_key("test_areas", ("area_id",)))
    monkeypatch.setitem(FAMILY_SUMMARIES, "test_counts", summarize_test_counts)


def count_batch(area_name, value):
    areas = pa.Table.from_pylist(
        [{"area_id": "kajiado", "name": area_name, "cell_id": "E1K-r9-c9", "attributes": '{"county": 34}'}],
        schema=AREAS_SCHEMA,
    )
    counts = pa.Table.from_pylist(
        [{"area_id": "kajiado", "time_start": DAY, "time_end": NEXT_DAY, "value": value}], schema=COUNTS_SCHEMA
    )
    return NormalizedBatch(counts, "counts-v1", family="test_counts", references={"test_areas": areas})


def count_manifest(item_id):
    return make_manifest("counts", {}, DAY, NEXT_DAY, item_id=item_id, product="counts", precision=TimePrecision.COMPOSITE)


def test_reference_rows_are_upserted_before_the_rows_that_use_them(database, grid, count_family):
    store = SeriesStore(database, grid)
    first, second = count_manifest("survey-1"), count_manifest("survey-2")
    series = series_id(first, grid)

    store.append_batch(series, first, count_batch("Kajiado", 120.0))
    store.append_batch(series, second, count_batch("Kajiado County", 95.0))

    areas = database.execute("SELECT area_id, name, cell_id, attributes FROM test_areas").fetchall()
    assert areas == [("kajiado", "Kajiado County", "E1K-r9-c9", {"county": 34})]
    assert database.execute("SELECT count(*) FROM grid_cells WHERE cell_id = 'E1K-r9-c9'").fetchone() == (1,)
    assert database.execute("SELECT count(*) FROM test_counts").fetchone() == (2,)


def test_a_new_family_summarizes_with_its_registered_function(database, grid, count_family):
    store = SeriesStore(database, grid)
    manifest = count_manifest("survey-1")
    series = series_id(manifest, grid)
    store.append_batch(series, manifest, count_batch("Kajiado", 120.0))

    summary = store.summary(store.latest_version(series))

    assert (summary.start, summary.end, summary.row_count) == (DAY, NEXT_DAY, 1)
    assert summary.variables == ["animal_count"]


def test_a_batch_with_an_unregistered_reference_table_is_refused(database, grid):
    store = SeriesStore(database, grid)
    manifest = chirps_manifest(ProductStatus.FINAL, NEXT_DAY)
    series = series_id(manifest, grid)
    batch = rainfall_batch(grid, manifest, 3.0)
    batch.references = {"unknown_areas": pa.table({"area_id": ["a"]})}

    with pytest.raises(ValueError, match="unknown_areas"):
        store.append_batch(series, manifest, batch)

    assert store.latest_version(series) is None


def test_a_family_without_a_summary_function_fails_with_its_name(database, grid, count_family, monkeypatch):
    store = SeriesStore(database, grid)
    manifest = count_manifest("survey-1")
    series = series_id(manifest, grid)
    store.append_batch(series, manifest, count_batch("Kajiado", 120.0))
    monkeypatch.delitem(FAMILY_SUMMARIES, "test_counts")

    with pytest.raises(ValueError, match="test_counts"):
        store.summary(store.latest_version(series))


def test_cell_observations_summary_lists_variables_and_cells(database, grid):
    store = SeriesStore(database, grid)
    manifest = chirps_manifest(ProductStatus.FINAL, NEXT_DAY)
    series = series_id(manifest, grid)
    store.append_batch(series, manifest, rainfall_batch(grid, manifest, 3.0))
    version = store.latest_version(series)

    summary = store.summary(version)

    assert summary.variables == ["rainfall_mm"]
    assert summary.row_count == 2
    assert sorted(summary.cell_ids) == sorted(distinct_cell_ids(database, "cell_observations", version))
    assert sorted(summary.cell_ids) == ["E1K-r1-c1", "E1K-r1-c2"]


def test_two_windows_with_the_same_start_and_different_ends_are_both_current(database, grid):
    store = SeriesStore(database, grid)
    year_start = datetime(2020, 1, 1, tzinfo=UTC)
    one_year = make_manifest(
        "ndvi_derived", {}, year_start, datetime(2021, 1, 1, tzinfo=UTC), item_id="annual:2020",
        product="ndvi-summary", precision=TimePrecision.COMPOSITE,
    )
    five_years = make_manifest(
        "ndvi_derived", {}, year_start, datetime(2025, 1, 1, tzinfo=UTC), item_id="trend:2020-2024",
        product="ndvi-summary", precision=TimePrecision.COMPOSITE,
    )
    series = series_id(one_year, grid)

    def ndvi_mean(manifest, value):
        stats = pd.DataFrame(
            {"cell_id": ["E1K-r1-c1"], "variable": "ndvi_mean", "value": value, "std": None,
             "valid_fraction": 1.0, "pixel_count": 23}
        )
        return to_cell_observations(stats, manifest, grid, "ndvi-summary-v1", "mean", {"ndvi_mean": "index"}, 250)

    store.append_batch(series, one_year, ndvi_mean(one_year, 0.41))
    store.append_batch(series, five_years, ndvi_mean(five_years, 0.38))
    publish_series_version(store, PostgresCatalog(database), grid, series, "ndvi_derived", "NDVI summary", "public")

    assert values(store.current_cell_rows(series)) == {0.41, 0.38}
    current = database.execute("SELECT value FROM current_cell_observations").fetchall()
    assert {value for (value,) in current} == {0.41, 0.38}
    recipe = database.execute("SELECT value FROM recipe_cell_observations").fetchall()
    assert {value for (value,) in recipe} == {0.41, 0.38}


def test_two_instant_scenes_of_one_utc_day_still_compete(database, grid):
    store = SeriesStore(database, grid)
    morning = make_manifest("sentinel2", {}, datetime(2024, 3, 9, 8, 47, tzinfo=UTC), item_id="morning", product="s2")
    noon = make_manifest("sentinel2", {}, datetime(2024, 3, 9, 12, 5, tzinfo=UTC), item_id="noon", product="s2")
    series = series_id(morning, grid)

    def ndvi(manifest, valid_fraction, value):
        stats = pd.DataFrame(
            {"cell_id": ["E1K-r1-c1"], "variable": "ndvi", "value": value, "std": 0.0,
             "valid_fraction": valid_fraction, "pixel_count": 5000}
        )
        return to_cell_observations(stats, manifest, grid, "s2-v1", "mean", {"ndvi": "index"}, 10)

    store.append_batch(series, morning, ndvi(morning, 0.6, 0.30))
    store.append_batch(series, noon, ndvi(noon, 0.9, 0.42))

    assert values(store.current_cell_rows(series)) == {0.42}

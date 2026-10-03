from datetime import UTC, datetime

import pandas as pd

from conftest import make_manifest
from habitat.contracts import ProductStatus, TimePrecision
from habitat.normalize.rows import series_id, to_cell_observations
from habitat.storage.series import SeriesStore


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

from datetime import UTC, datetime

import pandas as pd

from conftest import make_manifest
from habitat.contracts import ProductStatus, TimePrecision
from habitat.normalize.rows import series_id, to_cell_observations
from habitat.storage.series import SeriesStore

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


def test_append_is_idempotent(tmp_path, grid):
    store = SeriesStore(tmp_path)
    manifest = chirps_manifest(ProductStatus.FINAL, NEXT_DAY)
    series = series_id(manifest, grid)

    first = store.append_batch(series, manifest, rainfall_batch(grid, manifest, 3.0))
    second = store.append_batch(series, manifest, rainfall_batch(grid, manifest, 3.0))

    assert first.appended and first.version == 1
    assert not second.appended and second.version == 1


def test_each_append_makes_a_new_version_and_keeps_the_old_one(tmp_path, grid):
    store = SeriesStore(tmp_path)
    day_one = chirps_manifest(ProductStatus.FINAL, NEXT_DAY)
    day_two = chirps_manifest(ProductStatus.FINAL, NEXT_DAY, day=NEXT_DAY, item_id="chirps-2024.03.06")
    series = series_id(day_one, grid)

    store.append_batch(series, day_one, rainfall_batch(grid, day_one, 3.0))
    store.append_batch(series, day_two, rainfall_batch(grid, day_two, 7.0))

    assert store.current_rows(store.version(series, 1)).num_rows == 2
    assert store.current_rows(store.latest_version(series)).num_rows == 4


def test_final_replaces_preliminary_but_as_of_still_sees_preliminary(tmp_path, grid):
    store = SeriesStore(tmp_path)
    preliminary = chirps_manifest(ProductStatus.PRELIMINARY, datetime(2024, 3, 7, tzinfo=UTC))
    final = chirps_manifest(ProductStatus.FINAL, datetime(2024, 3, 25, tzinfo=UTC))
    series = series_id(final, grid)

    store.append_batch(series, preliminary, rainfall_batch(grid, preliminary, 2.0))
    result = store.append_batch(series, final, rainfall_batch(grid, final, 5.0))
    latest = store.latest_version(series)

    assert result.appended
    assert set(store.current_rows(latest).column("value").to_pylist()) == {5.0}
    assert store.all_rows(latest).count("*").fetchone()[0] == 4

    before_final = store.current_rows(latest, as_of=datetime(2024, 3, 10, tzinfo=UTC))
    assert set(before_final.column("value").to_pylist()) == {2.0}


def test_overlapping_scenes_keep_the_clearest_view(tmp_path, grid):
    store = SeriesStore(tmp_path)
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

    current = store.current_rows(store.latest_version(series))
    assert current.column("value").to_pylist() == [0.42]


def test_rebuild_supersedes_old_batches(tmp_path, grid):
    store = SeriesStore(tmp_path)
    manifest = chirps_manifest(ProductStatus.FINAL, NEXT_DAY)
    series = series_id(manifest, grid)
    old = store.append_batch(series, manifest, rainfall_batch(grid, manifest, 3.0))

    rebuilt = rainfall_batch(grid, manifest, 4.0)
    rebuilt.mapping_version = "chirps-v2"
    store.append_batch(series, manifest, rebuilt, supersedes=(old.batch_key,))

    latest = store.latest_version(series)
    assert [b.mapping_version for b in latest.batches] == ["chirps-v2"]

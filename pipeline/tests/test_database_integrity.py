from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from habitat.db import connect, database_url
from habitat.normalize.rows import series_id
from habitat.storage.series import SeriesStore
from test_series import NEXT_DAY, chirps_manifest, rainfall_batch, values
from habitat.contracts import ProductStatus


def test_failed_copy_rolls_back_series_and_version(database, grid):
    manifest = chirps_manifest(ProductStatus.FINAL, NEXT_DAY)
    series = series_id(manifest, grid)
    batch = rainfall_batch(grid, manifest, 3.0, cells=('E1K-r1-c1', 'E1K-r1-c1'))
    with pytest.raises(psycopg.errors.UniqueViolation):
        SeriesStore(database, grid).append_batch(series, manifest, batch)
    for table in ('series', 'series_versions', 'ingest_batches', 'cell_observations', 'grid_cells'):
        assert database.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0
    assert SeriesStore(database, grid).append_batch(series, manifest, rainfall_batch(grid, manifest, 3.0)).version == 1


def test_concurrent_duplicate_writers_append_once(database, grid):
    manifest = chirps_manifest(ProductStatus.FINAL, NEXT_DAY)
    series = series_id(manifest, grid)
    schema = database.execute('SELECT current_schema()').fetchone()[0]
    def append(_):
        with connect(database_url()) as connection:
            connection.execute(psycopg.sql.SQL('SET search_path = {}, extensions').format(psycopg.sql.Identifier(schema)))
            return SeriesStore(connection, grid).append_batch(series, manifest, rainfall_batch(grid, manifest, 3.0))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(append, range(4)))
    assert sum(result.appended for result in results) == 1
    assert {result.version for result in results} == {1}
    assert database.execute('SELECT count(*) FROM cell_observations').fetchone()[0] == 2


def test_equal_quality_uses_deterministic_item_tiebreak(database, grid):
    store = SeriesStore(database, grid)
    a = chirps_manifest(ProductStatus.FINAL, NEXT_DAY, item_id='item-a')
    z = chirps_manifest(ProductStatus.FINAL, NEXT_DAY, item_id='item-z')
    series = series_id(a, grid)
    store.append_batch(series, z, rainfall_batch(grid, z, 9.0))
    store.append_batch(series, a, rainfall_batch(grid, a, 1.0))
    assert values(store.current_cell_rows(series)) == {9.0}
    assert {row[0] for row in database.execute('SELECT value FROM current_cell_observations').fetchall()} == {9.0}


def test_database_writer_and_reader_roles(database, grid):
    from habitat.catalog.store import PostgresCatalog
    from habitat.catalog.publish import publish_series_version
    schema = database.execute('SELECT current_schema()').fetchone()[0]
    database.execute(psycopg.sql.SQL('GRANT USAGE ON SCHEMA {} TO habitat_reader').format(psycopg.sql.Identifier(schema)))
    manifest = chirps_manifest(ProductStatus.FINAL, NEXT_DAY)
    series = series_id(manifest, grid)
    try:
        database.execute('SET ROLE habitat_writer')
        store = SeriesStore(database, grid)
        store.append_batch(series, manifest, rainfall_batch(grid, manifest, 3.0))
        published = publish_series_version(store, PostgresCatalog(database), grid, series, 'chirps', 'rainfall', 'public')
        assert published.version == 1
        database.execute('SET ROLE habitat_reader')
        assert database.execute('SELECT count(*) FROM rainfall_observations').fetchone()[0] == 2
        assert PostgresCatalog(database).latest(series).row_count == 2
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            database.execute("INSERT INTO series(series_id, family, source_id, product) VALUES ('denied', 'cell_observations', 'chirps', 'chirps')")
    finally:
        database.execute('RESET ROLE')

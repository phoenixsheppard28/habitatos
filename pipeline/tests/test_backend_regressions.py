from contextlib import nullcontext
from datetime import UTC, date, datetime
from types import SimpleNamespace

import httpx
import pytest

from habitat import ingest
from habitat.catalog.store import MemoryCatalog
from habitat.contracts import SearchFilters
from habitat.fetch.archive import RawArchive, parse_http_date
from habitat.fetch.chirps import available_status
from habitat.normalize.rows import QuarantineError
from habitat.normalize.sources.movebank import parse_time
from test_catalog import dataset


def test_retry_publishes_previously_committed_series(monkeypatch, grid, tmp_path):
    store = SimpleNamespace(latest_version=lambda series: None)
    workspace = SimpleNamespace(store=store, grid=grid, archive=nullcontext(object()), catalog=object())
    monkeypatch.setattr(ingest, 'fetch_manifests', lambda *args: [])
    calls = []
    monkeypatch.setattr(ingest, 'publish_series_version', lambda *args, **kwargs: calls.append(args[3]))
    assert ingest.run('chirps', workspace, (0, 0, 1, 1), date(2024, 1, 1), date(2024, 1, 2)) == []
    assert calls == [f'chirps--chirps-v2.0-daily-p05--{grid.grid_id}']


@pytest.mark.parametrize('source,bbox,start,end,package', [
    ('unknown', None, None, None, None),
    ('chirps', None, None, None, None),
    ('chirps', (0, 0, 1, 1), date(2024, 2, 1), date(2024, 1, 1), None),
    ('movebank', None, None, None, None),
    ('chirps', (0, 0, float('nan'), 1), date(2024, 1, 1), date(2024, 1, 2), None),
])
def test_invalid_ingest_inputs_fail_before_workspace_access(source, bbox, start, end, package):
    with pytest.raises(ValueError):
        ingest.run(source, None, bbox, start, end, package)


def test_archive_failed_stream_keeps_original_and_removes_partial(tmp_path):
    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'partial'
            raise httpx.ReadError('interrupted')
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=BrokenStream())))
    target = tmp_path / 'item' / 'data.csv'
    target.parent.mkdir()
    target.write_bytes(b'original')
    with client, pytest.raises(httpx.ReadError):
        RawArchive(tmp_path, client).download('https://example.org/data', 'item', 'data.csv')
    assert target.read_bytes() == b'original'
    assert list(target.parent.iterdir()) == [target]


def test_archive_checksums_and_bad_optional_date(tmp_path):
    import hashlib
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b'data', headers={'last-modified': 'invalid'}))) as client:
        archived = RawArchive(tmp_path, client).download('https://example.org/data', 'item', 'data.csv')
    assert archived.path.read_bytes() == b'data'
    assert archived.checksum == 'sha256:' + hashlib.sha256(b'data').hexdigest()
    assert archived.last_modified is None
    assert parse_http_date('Mon, 01 Jan 2024 00:00:00 GMT') == datetime(2024, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize('artifact,filename', [('../outside', 'data.csv'), ('item', '../data.csv'), ('/outside', 'data.csv')])
def test_archive_rejects_escaping_paths(tmp_path, artifact, filename):
    with httpx.Client(transport=httpx.MockTransport(lambda req: pytest.fail('network called'))) as client:
        with pytest.raises(ValueError):
            RawArchive(tmp_path, client).download('https://example.org/data', artifact, filename)


def test_chirps_service_failure_is_not_missing_data():
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(503))) as client:
        with pytest.raises(httpx.HTTPStatusError):
            available_status(date(2024, 1, 1), client)


def test_deployment_time_accepts_timezone_offsets():
    assert parse_time('2024-01-01T01:00:00+01:00') == datetime(2024, 1, 1, tzinfo=UTC)
    with pytest.raises(QuarantineError):
        parse_time('invalid-date')


def test_memory_catalog_published_versions_are_snapshots():
    from shapely.geometry import box
    catalog = MemoryCatalog()
    descriptor = dataset('test', 'cell_observations', box(0, 0, 1, 1), None, None)
    catalog.register_dataset(descriptor)
    descriptor.description = 'changed'
    catalog.latest('test').description = 'also changed'
    matches = catalog.search_datasets(SearchFilters(access_scope=['public']))
    matches[0].dataset.description = 'changed through search'
    assert catalog.latest('test').description == 'cell_observations test data'


def test_end_to_end_ingest_and_catalog_retry(database, grid, sentinel2_scene, tmp_path, monkeypatch):
    from habitat.catalog.store import PostgresCatalog
    from habitat.ingest import Workspace
    from habitat.normalize.rows import series_id
    from habitat.storage.series import SeriesStore
    monkeypatch.setattr(ingest, 'fetch_manifests', lambda *args: [sentinel2_scene])
    workspace = Workspace(tmp_path, database, grid)
    first = ingest.run('sentinel2', workspace, (15, -20, 17, -18), date(2024, 3, 9), date(2024, 3, 9))
    assert first[0].append.appended
    series = series_id(sentinel2_scene, grid)
    published = PostgresCatalog(database).latest(series)
    assert published.version == 1
    assert set(published.variables) == {'ndvi', 'mndwi', 'ndmi'}
    assert published.row_count == SeriesStore(database, grid).summary(SeriesStore(database, grid).latest_version(series)).row_count
    second = ingest.run('sentinel2', workspace, (15, -20, 17, -18), date(2024, 3, 9), date(2024, 3, 9))
    assert not second[0].append.appended
    assert PostgresCatalog(database).latest(series).version == 1


def test_taxonomy_outage_does_not_discard_tracking_batch(monkeypatch):
    from habitat.contracts import AnimalEntity
    entity = AnimalEntity(entity_id='movebank:1:a', source_id='movebank', study_id='1', local_identifier='a', taxon_name='Example species')
    monkeypatch.setattr(ingest, 'resolve_taxon', lambda name: (_ for _ in ()).throw(httpx.ConnectError('offline')))
    ingest.resolve_entity_taxa(SimpleNamespace(entities=[entity]))
    assert entity.gbif_taxon_key is None

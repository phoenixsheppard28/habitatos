from datetime import UTC, date, datetime

import httpx
import pytest
import pystac

from habitat.catalog.taxa import resolve_taxon
from habitat.contracts import ProductStatus
from habitat.fetch.archive import RawArchive
from habitat.fetch.chirps import fetch_chirps_day
from habitat.fetch import stac


@pytest.mark.parametrize('mode,expected', [('exact', 'resolved'), ('higher', 'ambiguous'), ('single', 'resolved'), ('multiple', 'ambiguous'), ('none', 'not_found')])
def test_taxonomy_resolution_modes(mode, expected):
    def handler(request):
        if request.url.path == '/species/match':
            if mode in ('exact', 'higher'):
                return httpx.Response(200, json={'matchType': 'EXACT', 'rank': 'SPECIES' if mode == 'exact' else 'GENUS', 'usageKey': 1, 'scientificName': 'Example species'})
            return httpx.Response(200, json={'matchType': 'NONE'})
        count = {'higher': 2, 'single': 1, 'multiple': 2, 'none': 0}[mode]
        return httpx.Response(200, json={'results': [{'key': i+1, 'scientificName': f'Example {i}'} for i in range(count)]})
    with httpx.Client(base_url='https://example.org', transport=httpx.MockTransport(handler)) as client:
        result = resolve_taxon('example', client)
    assert result.status == expected


@pytest.mark.parametrize('final,prelim,expected', [(200, 200, ProductStatus.FINAL), (404, 200, ProductStatus.PRELIMINARY), (404, 404, None)])
def test_chirps_download_and_publication_status(tmp_path, final, prelim, expected):
    def handler(request):
        if request.method == 'HEAD':
            return httpx.Response(prelim if '/prelim/' in request.url.path else final)
        return httpx.Response(200, content=b'original raster bytes', headers={'last-modified': 'Mon, 01 Jan 2024 00:00:00 GMT'})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        manifest = fetch_chirps_day(date(2023, 12, 31), RawArchive(tmp_path, client))
    if expected is None:
        assert manifest is None
    else:
        assert manifest.extensions.product_status == expected
        assert manifest.extensions.time_end == datetime(2024, 1, 1, tzinfo=UTC)
        assert manifest.extensions.available_at == datetime(2024, 1, 1, tzinfo=UTC)
        assert manifest.checksum.startswith('sha256:')


def test_chirps_idempotency_skips_download(tmp_path):
    calls = []
    def handler(request):
        calls.append(request.method)
        return httpx.Response(200)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert fetch_chirps_day(date(2024, 1, 1), RawArchive(tmp_path, client), lambda *_: True) is None
    assert calls == ['HEAD']


def test_stac_manifests_keep_processing_and_temporal_metadata(tmp_path):
    acquired = datetime(2024, 1, 1, tzinfo=UTC)
    s2 = pystac.Item('S2-test', None, [0, 0, 1, 1], acquired, {'s2:processing_baseline':'05.10'})
    modis = pystac.Item('MOD13Q1.A2024001.h00v00.061.2024020000000', None, [0, 0, 1, 1], acquired,
        {'start_datetime':'2024-01-01T00:00:00Z', 'end_datetime':'2024-01-16T23:59:59Z'})
    for item, names in ((s2, stac.SENTINEL2_ASSETS), (modis, stac.MODIS_ASSETS)):
        for name in names.values():
            item.add_asset(name, pystac.Asset(f'https://example.org/{name}.tif'))
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b'original'))) as client:
        archive = RawArchive(tmp_path, client)
        s2_manifest = stac.sentinel2_manifest(s2, archive)
        modis_manifest = stac.modis_manifest(modis, archive)
    assert s2_manifest.extensions.properties['boa_add_offset'] == -1000
    assert len(s2_manifest.extensions.assets) == 5
    assert modis_manifest.extensions.processing_version == '061.2024020000000'
    assert modis_manifest.extensions.time_precision == 'composite'
    assert len(modis_manifest.extensions.assets) == 3


def test_movebank_repository_download_and_idempotency(tmp_path):
    from habitat.fetch.movebank import fetch_data_package
    from test_movebank import GPS_CSV, REFERENCE_CSV
    files = []
    payloads = {'animal.csv': GPS_CSV, 'animal-reference-data.csv': REFERENCE_CSV, 'other.csv': 'value\n1\n'}
    for filename in payloads:
        files.append({'name': filename, 'checkSum': {'value': 'test-checksum'},
                      '_links': {'content': {'href': f'https://example.org/files/{filename}'}}})
    item = {'handle': '123/study', 'metadata': {
        'mdr.study.id': [{'value':'208413731'}], 'dc.date.available': [{'value':'2020-12-01T00:00:00Z'}],
        'dwc.ScientificName': [{'value':'Connochaetes taurinus'}]},
        '_embedded': {'bundles': {'_embedded': {'bundles': [
            {'name':'ORIGINAL', '_embedded': {'bitstreams': {'_embedded': {'bitstreams': files}}}}]}}}}
    downloads = []
    def handler(request):
        if '/items/' in request.url.path:
            return httpx.Response(200, json=item)
        downloads.append(request.url.path)
        return httpx.Response(200, content=payloads[request.url.path.rsplit('/', 1)[-1]].encode())
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        archive = RawArchive(tmp_path, client)
        manifests = fetch_data_package('package-id', archive)
        assert len(manifests) == 1
        manifest = manifests[0]
        assert manifest.extensions.kind == 'tabular'
        assert manifest.extensions.properties['study_id'] == '208413731'
        assert manifest.extensions.available_at == datetime(2020, 12, 1, tzinfo=UTC)
        assert set(manifest.extensions.assets) == {'locations', 'reference'}
        assert manifest.coverage.species == ['Connochaetes taurinus']
        downloads.clear()
        assert fetch_data_package('package-id', archive, lambda *_: True) == []
        assert downloads == []


def test_repository_without_original_bundle_has_no_downloads(tmp_path):
    from habitat.fetch.movebank import fetch_data_package
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={'handle':'123/study'}))) as client:
        assert fetch_data_package('package-id', RawArchive(tmp_path, client)) == []


def test_repository_search_keeps_only_data_packages():
    from habitat.fetch.movebank import search_data_packages
    def entry(kind):
        return {'_embedded': {'indexableObject': {'uuid':kind, 'metadata': {
            'dspace.entity.type': [{'value':kind}], 'dc.title':[{'value':'Study title'}]}}}}
    payload = {'_embedded': {'searchResult': {'_embedded': {'objects': [entry('Datapackage'), entry('Publication')]}}}}
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload))) as client:
        assert search_data_packages('animals', client) == [{'uuid':'Datapackage', 'title':'Study title', 'taxon':None, 'study_id':None}]

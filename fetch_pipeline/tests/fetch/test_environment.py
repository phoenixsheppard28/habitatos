from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
import pytest
from fetch.connectors import environment as env
from fetch.archive import resolve_artifact_path
from fetch.models import RawManifest

BBOX = [10, -2, 11, -1]


def test_validation_before_network():
    with patch.object(env, 'fetch_json') as network:
        for bbox, start, end in [([0,0,0,1], '2025-01-01', '2025-01-02'),
                                  ([float('nan'),0,1,1], '2025-01-01', '2025-01-02'),
                                  (BBOX, '2025-02-01', '2025-01-01')]:
            with pytest.raises(ValueError):
                env.fetch_environment(bbox, start, end)
        network.assert_not_called()


def test_chirps_outside_coverage():
    result = env.fetch_environment([-85, 51, -84, 52], '2025-01-01', '2025-01-02', sources=['chirps'])
    assert result['status'] == 'insufficient_data'
    assert not result['raw_artifacts']
    assert '50°' in result['warnings'][0]


def test_chirps_discovery_is_not_download(isolated_data_dir):
    with patch.object(env, 'download_to_path') as transfer:
        result = env.fetch_environment(BBOX, '2024-02-28', '2024-03-01', sources=['chirps'], discover_only=True)
    assert result['status'] == 'discovered'
    assert len(result['outcomes']) == 3
    assert '2024.02.29' in result['outcomes'][1]['url']
    assert result['raw_artifacts'] == []
    transfer.assert_not_called()


def test_chirps_download_cache_corruption_and_limits(isolated_data_dir):
    def transfer(url, path, **kwargs):
        path.write_bytes(b'\x1f\x8btest')
        return 6
    with patch.object(env, 'download_to_path', side_effect=transfer) as network:
        result = env.fetch_environment(BBOX, '2025-01-01', '2025-01-03', sources=['chirps'], max_bytes=6)
        assert result['status'] == 'partial'
        assert len(result['raw_artifacts']) == 1
        manifest = RawManifest.model_validate(result['raw_artifacts'][0])
        assert manifest.coverage.bbox == [-180, -50, 180, 50]
        assert manifest.extensions['interval_end_exclusive'] is True
        again = env.fetch_environment(BBOX, '2025-01-01', '2025-01-01', sources=['chirps'])
        assert again['downloaded_bytes'] == 0
        assert network.call_count == 1
        next(resolve_artifact_path(manifest).iterdir()).write_bytes(b'corrupt')
        env.fetch_environment(BBOX, '2025-01-01', '2025-01-01', sources=['chirps'])
        assert network.call_count == 2


def test_modis_filters_terra_and_keeps_quality_metadata():
    def catalog(url):
        if '/collections/' in url:
            return {'license': 'proprietary', 'providers': [{'name': 'NASA'}]}
        query = parse_qs(urlsplit(url).query)
        assert 'query' not in query  # historical MODIS platform fields can be blank
        return {'features': [
            {'id': 'aqua', 'properties': {'platform': 'aqua'}, 'assets': {}},
            {'id': 'MOD13Q1.terra', 'bbox': BBOX, 'properties': {'platform': '', 'start_datetime': '2025-01-01T00:00:00Z', 'end_datetime': '2025-01-16T23:59:59Z'},
             'assets': {key: {'href': f'https://modiseuwest.blob.core.windows.net/test/{key}.tif',
                               'raster:bands': [{'scale': 0.0001}]}
                        for key in env.PRODUCTS['modis'][1]}}]}
    with patch.object(env, 'fetch_json', side_effect=catalog):
        candidates, warnings = env.discover_stac('modis', BBOX, '2025-01-01', '2025-01-16', 3, 30)
    assert len(candidates) == 4
    assert all(c['metadata']['item_id'] == 'MOD13Q1.terra' for c in candidates)
    assert any('Quality' in c['metadata']['asset_key'] for c in candidates)
    assert candidates[0]['coverage']['end'] == '2025-01-16T23:59:59Z'
    assert not warnings


def test_source_failure_does_not_abort_other_sources(isolated_data_dir):
    with patch.object(env, 'fetch_json', side_effect=TimeoutError):
        result = env.fetch_environment(BBOX, '2025-01-01', '2025-01-01', sources=['modis', 'chirps'], discover_only=True)
    assert any(x.get('source') == 'chirps' for x in result['outcomes'])
    assert result['warnings']


def test_reject_html_payload(isolated_data_dir):
    def transfer(url, path, **kwargs):
        path.write_bytes(b'<html>login</html>')
        return 18
    with patch.object(env, 'download_to_path', side_effect=transfer):
        result = env.fetch_environment(BBOX, '2025-01-01', '2025-01-01', sources=['chirps'])
    assert result['status'] == 'insufficient_data'
    assert not result['raw_artifacts']

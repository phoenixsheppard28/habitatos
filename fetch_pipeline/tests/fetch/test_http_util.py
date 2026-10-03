import io
from unittest.mock import patch
import pytest
from fetch.http_util import download_to_path, fetch_bytes


class Response(io.BytesIO):
    def __init__(self, data, headers=None):
        super().__init__(data)
        self.headers = headers or {}


def test_small_download_exact_limit():
    with patch('urllib.request.urlopen', return_value=Response(b'1234')):
        assert fetch_bytes('https://example.org', max_bytes=4) == b'1234'


def test_stream_limit_deletes_partial_file(tmp_path):
    target = tmp_path / 'file'
    with patch('urllib.request.build_opener') as factory:
        factory.return_value.open.return_value = Response(b'12345')
        with pytest.raises(ValueError, match='budget'):
            download_to_path('https://example.org/data', target, max_bytes=4, allowed_hosts={'example.org'})
    assert not target.exists()


def test_stream_exact_limit(tmp_path):
    target = tmp_path / 'file'
    with patch('urllib.request.build_opener') as factory:
        factory.return_value.open.return_value = Response(b'1234', {'Content-Length':'4'})
        assert download_to_path('https://example.org/data', target, max_bytes=4, allowed_hosts={'example.org'}) == 4
    assert target.read_bytes() == b'1234'


def test_unapproved_host_is_blocked_before_network(tmp_path):
    with patch('urllib.request.build_opener') as factory:
        with pytest.raises(ValueError, match='allowed'):
            download_to_path('http://127.0.0.1/internal', tmp_path/'file', max_bytes=10, allowed_hosts={'example.org'})
        factory.assert_not_called()

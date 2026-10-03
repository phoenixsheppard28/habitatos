import httpx
import pytest

from habitat.fetch import http

HOSTS = {"example.org"}


def test_exact_limit_is_allowed(mock_http, tmp_path):
    mock_http(lambda request: httpx.Response(200, content=b"1234"))

    assert http.download("https://example.org/data", tmp_path / "file", max_bytes=4, allowed_hosts=HOSTS) == 4
    assert (tmp_path / "file").read_bytes() == b"1234"


def test_over_limit_deletes_the_partial_file(mock_http, tmp_path):
    mock_http(lambda request: httpx.Response(200, content=b"12345"))

    with pytest.raises(http.DownloadError, match="budget"):
        http.download("https://example.org/data", tmp_path / "file", max_bytes=4, allowed_hosts=HOSTS)
    assert not (tmp_path / "file").exists()


def test_unapproved_host_is_blocked_before_network(mock_http, tmp_path):
    calls = mock_http(lambda request: httpx.Response(200, content=b"x"))

    with pytest.raises(http.DownloadError, match="allowed"):
        http.download("http://127.0.0.1/internal", tmp_path / "file", max_bytes=10, allowed_hosts=HOSTS)
    assert calls == []


def test_redirect_to_an_unapproved_host_is_blocked(mock_http, tmp_path):
    def handle(request):
        if request.url.host == "example.org":
            return httpx.Response(302, headers={"location": "https://169.254.169.254/secret"})
        return httpx.Response(200, content=b"secret")

    calls = mock_http(handle)

    with pytest.raises(http.DownloadError, match="allowed"):
        http.download("https://example.org/data", tmp_path / "file", max_bytes=10, allowed_hosts=HOSTS)
    assert [c.url.host for c in calls] == ["example.org"]


def test_transient_errors_are_retried(mock_http, tmp_path, monkeypatch):
    monkeypatch.setattr(http.time, "sleep", lambda seconds: None)
    responses = iter([httpx.Response(503), httpx.Response(200, content=b"ok")])
    mock_http(lambda request: next(responses))

    assert http.download("https://example.org/data", tmp_path / "file", max_bytes=10, allowed_hosts=HOSTS) == 2

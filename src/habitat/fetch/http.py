import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

USER_AGENT = "HabitatWatch/0.1 (ecological data retrieval)"
TIMEOUT_S = 60
CHUNK_BYTES = 1 << 20
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_REDIRECTS = 5

# Tests replace this with an httpx.MockTransport, so unit tests make no network calls.
TRANSPORT: httpx.BaseTransport | None = None


class DownloadError(ValueError):
    pass


def client(**options) -> httpx.Client:
    return httpx.Client(
        transport=TRANSPORT,
        headers={"User-Agent": USER_AGENT},
        timeout=options.pop("timeout", TIMEOUT_S),
        follow_redirects=options.pop("follow_redirects", True),
        **options,
    )


def get_json(url: str, params: dict | None = None, timeout: float = TIMEOUT_S) -> Any:
    with client(timeout=timeout) as http:
        response = http.get(url, params=params)
        response.raise_for_status()
        return response.json()


def check_host(url: str, allowed_hosts: set[str]) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in allowed_hosts or parts.port not in (None, 443):
        raise DownloadError(f"download URL host {parts.hostname!r} is not an allowed HTTPS host of this connector")


def download(url: str, target: Path, *, max_bytes: int, allowed_hosts: set[str], auth=None, attempts: int = 3) -> int:
    """Stream one file to `target`. Every redirect target must be an allowed host. Retries transient errors only."""
    for attempt in range(attempts):
        try:
            return stream_once(url, target, max_bytes, allowed_hosts, auth)
        except (httpx.TransportError, httpx.HTTPStatusError) as error:
            target.unlink(missing_ok=True)
            transient = isinstance(error, httpx.TransportError) or error.response.status_code in RETRY_STATUS
            if not transient or attempt == attempts - 1:
                raise
            time.sleep(2**attempt)
        except Exception:
            target.unlink(missing_ok=True)
            raise
    raise DownloadError("download retries exhausted")


def stream_once(url: str, target: Path, max_bytes: int, allowed_hosts: set[str], auth) -> int:
    with client(follow_redirects=False, auth=auth) as http:
        for _ in range(MAX_REDIRECTS + 1):
            check_host(url, allowed_hosts)
            with http.stream("GET", url) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers["location"])
                    continue

                response.raise_for_status()
                length = response.headers.get("content-length")
                if length and int(length) > max_bytes:
                    raise DownloadError(f"download exceeds the size budget of {max_bytes} bytes")

                total = 0
                with target.open("wb") as file:
                    for chunk in response.iter_bytes(CHUNK_BYTES):
                        total += len(chunk)
                        if total > max_bytes:
                            raise DownloadError(f"download exceeds the size budget of {max_bytes} bytes")
                        file.write(chunk)

                if length and total != int(length):
                    raise DownloadError("incomplete download")
                if total == 0:
                    raise DownloadError("empty download")
                return total
    raise DownloadError("too many redirects")

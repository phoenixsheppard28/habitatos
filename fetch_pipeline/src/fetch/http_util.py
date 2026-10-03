"""Small HTTP helpers (stdlib only)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

USER_AGENT = "HabitatWatchFetch/0.1 (ecological data retrieval)"
DEFAULT_TIMEOUT_S = 30


def fetch_json(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def fetch_bytes(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
    max_bytes: int,
) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        chunks: list[bytes] = []
        total = 0
        while True:
            block = resp.read(min(65536, max_bytes - total + 1))
            if not block:
                break
            chunks.append(block)
            total += len(block)
            if total > max_bytes:
                raise ValueError(f"Download exceeds max size ({max_bytes} bytes)")
        return b"".join(chunks)


def download_to_path(url: str, path, *, max_bytes: int, allowed_hosts: set[str]) -> int:
    """Stream to disk, enforce limits across redirects, retry transient errors only."""
    import time
    from urllib.parse import urlsplit

    def validate(target):
        parsed = urlsplit(target)
        if parsed.scheme != 'https' or parsed.hostname not in allowed_hosts or parsed.port not in (None, 443):
            raise ValueError('Download URL is outside this connector\'s allowed HTTPS hosts')

    class SafeRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            validate(newurl)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    validate(url)
    opener = urllib.request.build_opener(SafeRedirect())
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
            with opener.open(req, timeout=60) as response, path.open('wb') as out:
                length = response.headers.get('Content-Length')
                if length and int(length) > max_bytes:
                    raise ValueError(f'Download exceeds size budget ({max_bytes} bytes)')
                total = 0
                while block := response.read(min(1024 * 1024, max_bytes - total + 1)):
                    total += len(block)
                    if total > max_bytes:
                        raise ValueError(f'Download exceeds size budget ({max_bytes} bytes)')
                    out.write(block)
                if length and total != int(length):
                    raise ValueError('Incomplete download')
                if not total:
                    raise ValueError('Empty download')
                return total
        except Exception as exc:
            path.unlink(missing_ok=True)
            transient = (isinstance(exc, urllib.error.HTTPError) and exc.code in (429, 500, 502, 503, 504)) or (isinstance(exc, (urllib.error.URLError, TimeoutError)) and not isinstance(exc, urllib.error.HTTPError))
            if not transient or attempt == 2:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError('Download retry exhausted')

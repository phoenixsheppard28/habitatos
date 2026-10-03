import hashlib
import tempfile
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

CHUNK_BYTES = 1 << 20


@dataclass
class ArchivedFile:
    path: Path
    checksum: str
    last_modified: datetime | None


class RawArchive:
    """Keeps the original downloaded bytes. Normalize reads from here, never from the remote URL."""

    def __init__(self, root: Path, client: httpx.Client | None = None):
        self.root = Path(root)
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=120, follow_redirects=True)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if self._owns_client:
            self.client.close()

    def download(self, url: str, artifact_id: str, filename: str) -> ArchivedFile:
        if (Path(artifact_id).is_absolute() or ".." in Path(artifact_id).parts
            or Path(filename).name != filename or filename in ("", ".", "..")):
            raise ValueError("archive paths must stay within the raw archive")
        target = self.root / artifact_id / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        partial = None
        try:
            with self.client.stream("GET", url) as response:
                response.raise_for_status()
                last_modified = parse_http_date(response.headers.get("last-modified"))
                with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".part", delete=False) as file:
                    partial = Path(file.name)
                    size = 0
                    for chunk in response.iter_bytes(CHUNK_BYTES):
                        digest.update(chunk)
                        file.write(chunk)
                        size += len(chunk)
                if size == 0:
                    raise ValueError("empty download")
            os.replace(partial, target)
        finally:
            if partial is not None:
                partial.unlink(missing_ok=True)
        return ArchivedFile(target, f"sha256:{digest.hexdigest()}", last_modified)


def parse_http_date(value: str | None) -> datetime | None:
    if value is None:
        return None

    try:
        return parsedate_to_datetime(value).astimezone(UTC)
    except (ValueError, TypeError, OverflowError):
        return None

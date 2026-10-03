import hashlib
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
        self.client = client or httpx.Client(timeout=120, follow_redirects=True)

    def download(self, url: str, artifact_id: str, filename: str) -> ArchivedFile:
        target = self.root / artifact_id / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(target.suffix + ".part")
        digest = hashlib.sha256()

        with self.client.stream("GET", url) as response:
            response.raise_for_status()
            last_modified = parse_http_date(response.headers.get("last-modified"))
            with partial.open("wb") as file:
                for chunk in response.iter_bytes(CHUNK_BYTES):
                    digest.update(chunk)
                    file.write(chunk)

        partial.rename(target)
        return ArchivedFile(target, f"sha256:{digest.hexdigest()}", last_modified)


def parse_http_date(value: str | None) -> datetime | None:
    if value is None:
        return None

    return parsedate_to_datetime(value).astimezone(UTC)

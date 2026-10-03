import fcntl
import hashlib
import os
import shutil
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from habitat.archive.paths import PARTIAL_SUFFIX, contained, safe_name
from habitat.config import settings
from habitat.contracts import ARTIFACT_URI_PREFIX, RawManifest, StorageRef

CHUNK_BYTES = 1 << 20


class ChecksumMismatch(ValueError):
    pass


@dataclass(frozen=True)
class StoredArtifact:
    storage: StorageRef
    checksum: str


class ArtifactStore(Protocol):
    def put(
        self, artifact_id: str, version: str, files: Mapping[str, Path | bytes], storage_format: str
    ) -> StoredArtifact: ...

    def open(self, manifest: RawManifest, asset: str) -> Path: ...

    def exists(self, artifact_id: str, version: str) -> bool: ...

    def verify(self, manifest: RawManifest) -> None: ...

    def next_version(self, artifact_id: str) -> str: ...

    def list_files(self) -> list[str]: ...

    def lock(self) -> AbstractContextManager[None]: ...

    def staging(self) -> AbstractContextManager[Path]: ...


def artifact_uri(artifact_id: str, version: str) -> str:
    return f"{ARTIFACT_URI_PREFIX}{artifact_id}/{version}"


def parse_artifact_uri(uri: str) -> tuple[str, str]:
    parts = uri.removeprefix(ARTIFACT_URI_PREFIX).split("/")
    if not uri.startswith(ARTIFACT_URI_PREFIX) or len(parts) != 2:
        raise ValueError(f"not an artifact uri: {uri!r}")

    return safe_name(parts[0]), safe_name(parts[1])


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(CHUNK_BYTES):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


class LocalArtifactStore:
    """Raw files at `<data_dir>/raw/<artifact_id>/<version>/<filename>`. Normalize reads from here, never from a URL."""

    def __init__(self, root: Path | None = None):
        self.root = (root or settings().raw_dir).resolve()

    def directory(self, artifact_id: str, version: str) -> Path:
        return contained(self.root, safe_name(artifact_id), safe_name(version))

    def put(
        self, artifact_id: str, version: str, files: Mapping[str, Path | bytes], storage_format: str
    ) -> StoredArtifact:
        if safe_name(artifact_id) != artifact_id:
            raise ValueError(f"unsafe artifact id: {artifact_id!r}")
        if not files:
            raise ValueError(f"artifact {artifact_id} has no files")

        directory = self.directory(artifact_id, version)
        directory.mkdir(parents=True, exist_ok=True)
        for leftover in directory.glob(f"*{PARTIAL_SUFFIX}"):
            leftover.unlink()

        for filename, content in files.items():
            target = contained(directory, safe_name(filename))
            partial = target.with_name(target.name + PARTIAL_SUFFIX)
            if isinstance(content, bytes):
                partial.write_bytes(content)
            else:
                shutil.copyfile(content, partial)
            os.replace(partial, target)

        return StoredArtifact(
            storage=StorageRef(uri=artifact_uri(artifact_id, version), format=storage_format),
            checksum=self.checksum(artifact_id, version),
        )

    def open(self, manifest: RawManifest, asset: str) -> Path:
        filename = manifest.extensions.assets.get(asset)
        if filename is None:
            raise KeyError(f"{manifest.artifact_id} has no asset {asset!r}")

        path = contained(self.directory(*parse_artifact_uri(manifest.storage.uri)), safe_name(filename))
        if not path.is_file():
            raise FileNotFoundError(f"{manifest.storage.uri} has no file {filename!r}")
        return path

    def exists(self, artifact_id: str, version: str) -> bool:
        directory = self.directory(artifact_id, version)
        if not directory.is_dir():
            return False

        names = [path.name for path in directory.iterdir()]
        return bool(names) and not any(name.endswith(PARTIAL_SUFFIX) for name in names)

    def verify(self, manifest: RawManifest) -> None:
        artifact_id, version = parse_artifact_uri(manifest.storage.uri)
        if not self.exists(artifact_id, version):
            raise FileNotFoundError(f"{manifest.storage.uri} is missing or incomplete")

        actual = self.checksum(artifact_id, version)
        if actual != manifest.checksum:
            raise ChecksumMismatch(f"{manifest.storage.uri}: checksum {actual} does not match the manifest")

    def files(self, artifact_id: str, version: str) -> list[Path]:
        return sorted(
            path for path in self.directory(artifact_id, version).iterdir()
            if path.is_file() and not path.name.endswith(PARTIAL_SUFFIX)
        )

    def checksum(self, artifact_id: str, version: str) -> str:
        return ",".join(sha256_file(path) for path in self.files(artifact_id, version))

    def next_version(self, artifact_id: str) -> str:
        version = 1
        while self.directory(artifact_id, str(version)).exists():
            version += 1
        return str(version)

    def list_files(self) -> list[str]:
        if not self.root.is_dir():
            return []

        return sorted(
            str(path.relative_to(self.root)) for path in self.root.rglob("*")
            if path.is_file() and not path.name.endswith(PARTIAL_SUFFIX)
        )

    @contextmanager
    def lock(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root.parent / ".archive.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    @contextmanager
    def staging(self) -> Iterator[Path]:
        """A temporary folder for downloads. It is on the same disk as the archive, so a move is cheap."""
        parent = self.root.parent / "staging"
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            yield Path(directory)

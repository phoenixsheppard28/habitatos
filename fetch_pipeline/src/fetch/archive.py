"""Raw byte storage, checksums, manifests, and cache lookup."""

from __future__ import annotations

import hashlib
import json
import shutil
import os
import tempfile
import fcntl
from functools import wraps
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fetch import paths
from fetch.models import Coverage, RawManifest, Rights, SourceRef, StorageRef


def _atomic_write(path: Path, text: str) -> None:
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as out:
        out.write(text)
        tmp = out.name
    os.replace(tmp, path)


def _archive_lock(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        paths.ensure_data_dirs()
        with (paths.DATA_ROOT / ".archive.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                return function(*args, **kwargs)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
    return wrapped


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def sha256_bytes(content: bytes) -> str:
    digest = hashlib.sha256(content).hexdigest()
    return f"sha256:{digest}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def artifact_uri(artifact_id: str, version: str) -> str:
    return f"artifact://{artifact_id}/{version}"


def resolve_artifact_path(manifest: RawManifest) -> Path:
    """Map artifact://id/version to on-disk layout."""
    parts = manifest.storage.uri.removeprefix("artifact://").split("/")
    if len(parts) != 2:
        raise ValueError(f"Unsupported storage uri: {manifest.storage.uri}")
    artifact_id, version = parts
    return paths.RAW_ROOT / artifact_id / version


def _load_index() -> dict[str, Any]:
    paths.ensure_data_dirs()
    if not paths.INDEX_PATH.exists():
        return {"by_checksum": {}, "by_source_key": {}}
    return json.loads(paths.INDEX_PATH.read_text())


def _save_index(index: dict[str, Any]) -> None:
    paths.ensure_data_dirs()
    _atomic_write(paths.INDEX_PATH, json.dumps(index, indent=2, default=str))


def list_downloaded_files() -> list[str]:
    """List relative paths of files under the raw archive."""
    paths.ensure_data_dirs()
    if not paths.RAW_ROOT.exists():
        return []
    return sorted(
        str(path.relative_to(paths.RAW_ROOT))
        for path in paths.RAW_ROOT.rglob("*")
        if path.is_file()
    )


def load_manifest(artifact_id: str) -> RawManifest | None:
    path = paths.MANIFEST_ROOT / f"{artifact_id}.json"
    if not path.exists():
        return None
    return RawManifest.model_validate_json(path.read_text())


def save_manifest(manifest: RawManifest) -> None:
    paths.ensure_data_dirs()
    path = paths.MANIFEST_ROOT / f"{manifest.artifact_id}.json"
    _atomic_write(path, manifest.model_dump_json(indent=2))


def load_cached_by_source_key(source_key: str) -> RawManifest | None:
    """Return a previously stored artifact for this source key, if still on disk."""
    index = _load_index()
    entry = index.get("by_source_key", {}).get(source_key)
    if not entry:
        return None
    artifact_id = entry.get("artifact_id")
    expected = entry.get("checksum")
    if not artifact_id:
        return None
    manifest = load_manifest(artifact_id)
    if manifest is None or manifest.checksum != expected:
        return None
    stored = resolve_artifact_path(manifest)
    if not stored.is_dir():
        return None
    files = list(stored.iterdir())
    if len(files) != 1 or not files[0].is_file() or sha256_file(files[0]) != manifest.checksum:
        return None
    return manifest


def find_cached_by_source_key(source_key: str, checksum: str) -> RawManifest | None:
    index = _load_index()
    entry = index.get("by_source_key", {}).get(source_key)
    if not entry:
        return None
    artifact_id = entry.get("artifact_id")
    if not artifact_id:
        return None
    manifest = load_manifest(artifact_id)
    if manifest is None:
        return None
    if manifest.checksum != checksum:
        return None
    stored = resolve_artifact_path(manifest)
    if not stored.is_dir():
        return None
    files = list(stored.iterdir())
    if len(files) != 1 or not files[0].is_file() or sha256_file(files[0]) != manifest.checksum:
        return None
    return manifest


@_archive_lock
def register_raw_artifact(
    *,
    content: bytes,
    filename: str,
    source: SourceRef,
    coverage: Coverage,
    rights: Rights,
    source_key: str,
    access_scope: str = "public",
    file_format: str = "csv",
) -> RawManifest:
    """
    Write bytes to the archive and persist a manifest.
    Reuses an existing artifact when source_key and checksum match.
    """
    paths.ensure_data_dirs()
    checksum = sha256_bytes(content)
    cached = find_cached_by_source_key(source_key, checksum)
    if cached is not None:
        return cached

    artifact_id = f"raw-{uuid4().hex[:8]}"
    version = "1"
    dest_dir = paths.RAW_ROOT / artifact_id / version
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_file = dest_dir / Path(filename).name
    dest_file.write_bytes(content)

    now = _utc_now()
    manifest = RawManifest(
        artifact_id=artifact_id,
        version=version,
        created_at=now,
        access_scope=access_scope,
        source=source,
        storage=StorageRef(uri=artifact_uri(artifact_id, version), format=file_format),
        checksum=checksum,
        retrieved_at=now,
        coverage=coverage,
        rights=rights,
        extensions={"filename": dest_file.name, "bytes": len(content)},
    )
    save_manifest(manifest)

    index = _load_index()
    index.setdefault("by_checksum", {})[checksum] = artifact_id
    index.setdefault("by_source_key", {})[source_key] = {
        "artifact_id": artifact_id,
        "checksum": checksum,
    }
    _save_index(index)
    return manifest


@_archive_lock
def register_raw_artifact_from_path(
    *,
    src_path: Path,
    source: SourceRef,
    coverage: Coverage,
    rights: Rights,
    source_key: str,
    access_scope: str = "public",
    file_format: str | None = None,
) -> RawManifest:
    fmt = file_format or src_path.suffix.lstrip(".") or "bin"
    checksum = sha256_file(src_path)
    cached = find_cached_by_source_key(source_key, checksum)
    if cached is not None:
        return cached
    artifact_id = f"raw-{uuid4().hex}"
    dest = paths.RAW_ROOT / artifact_id / "1" / src_path.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src_path, dest)
    now = _utc_now()
    manifest = RawManifest(
        artifact_id=artifact_id, version="1", created_at=now,
        access_scope=access_scope, source=source,
        storage=StorageRef(uri=artifact_uri(artifact_id, "1"), format=fmt),
        checksum=checksum, retrieved_at=now, coverage=coverage, rights=rights,
        extensions={"filename": dest.name, "bytes": dest.stat().st_size},
    )
    save_manifest(manifest)
    index = _load_index()
    index.setdefault("by_checksum", {})[checksum] = artifact_id
    index.setdefault("by_source_key", {})[source_key] = {
        "artifact_id": artifact_id, "checksum": checksum,
    }
    _save_index(index)
    return manifest

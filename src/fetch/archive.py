"""Raw byte storage, checksums, manifests, and cache lookup."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fetch import paths
from fetch.models import Coverage, RawManifest, Rights, SourceRef, StorageRef


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def sha256_bytes(content: bytes) -> str:
    digest = hashlib.sha256(content).hexdigest()
    return f"sha256:{digest}"


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


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
    paths.INDEX_PATH.write_text(json.dumps(index, indent=2, default=str))


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
    path.write_text(manifest.model_dump_json(indent=2))


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
    if not stored.exists():
        return None
    return manifest


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
    dest_file = dest_dir / filename
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
    return register_raw_artifact(
        content=src_path.read_bytes(),
        filename=src_path.name,
        source=source,
        coverage=coverage,
        rights=rights,
        source_key=source_key,
        access_scope=access_scope,
        file_format=fmt,
    )

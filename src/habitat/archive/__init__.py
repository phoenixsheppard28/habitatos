from collections.abc import Mapping
from pathlib import Path

from habitat.archive.index import ArtifactIndex, MemoryArtifactIndex
from habitat.archive.store import ArtifactStore, ChecksumMismatch, LocalArtifactStore, StoredArtifact
from habitat.contracts import RawManifest


class Archive:
    """Raw files in an ArtifactStore, their manifests in an ArtifactIndex, looked up by source key."""

    def __init__(self, store: ArtifactStore | None = None, index: ArtifactIndex | None = None):
        self.store = store or LocalArtifactStore()
        self.index = index or MemoryArtifactIndex()

    def cached(self, source_key: str) -> RawManifest | None:
        """The archived manifest for this source key, if its files are still complete and unchanged."""
        manifest = self.index.find_by_source_key(source_key)
        if manifest is None:
            return None

        try:
            self.store.verify(manifest)
        except (ChecksumMismatch, FileNotFoundError):
            return None
        return manifest

    def put(self, artifact_id: str, files: Mapping[str, Path | bytes], storage_format: str) -> tuple[str, StoredArtifact]:
        with self.store.lock():
            version = self.store.next_version(artifact_id)
            return version, self.store.put(artifact_id, version, files, storage_format)

    def record(self, manifest: RawManifest) -> RawManifest:
        if self.index.find_by_source_key(manifest.extensions.source_key) is None:
            self.index.record(manifest)
        else:
            self.index.replace(manifest)
        return manifest

    def resolve(self, manifest: RawManifest) -> dict[str, Path]:
        """Verify the checksum, then return the local path of every asset."""
        self.store.verify(manifest)
        return {asset: self.store.open(manifest, asset) for asset in manifest.extensions.assets}


__all__ = ["Archive", "ChecksumMismatch", "LocalArtifactStore", "StoredArtifact"]

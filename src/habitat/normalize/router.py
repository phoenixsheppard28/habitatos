from habitat.archive.store import ArtifactStore
from habitat.contracts import BBox, RawManifest
from habitat.grid import Grid
from habitat.normalize.rows import NormalizedBatch, QuarantineError
from habitat.sources import get_source


def normalize(manifest: RawManifest, store: ArtifactStore, grid: Grid, aoi: BBox | None = None) -> NormalizedBatch:
    source_id = manifest.extensions.source_id
    source = get_source(source_id)
    if source is None:
        raise QuarantineError(f"unknown source_id {source_id!r}; register the source before ingest")

    if source.normalizer is None:
        raise QuarantineError(f"source {source_id!r} has no canonical mapping; propose a mapping before ingest")

    if manifest.storage.format != source.storage_format:
        raise QuarantineError(
            f"no normalizer for {source_id!r} files in format {manifest.storage.format!r}; "
            f"expected {source.storage_format!r}"
        )

    return source.normalizer(manifest, store, grid, aoi)

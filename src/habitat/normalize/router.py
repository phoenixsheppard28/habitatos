from collections.abc import Callable

from habitat.contracts import BBox, RawManifest
from habitat.grid import Grid
from habitat.normalize.rows import NormalizedBatch, QuarantineError
from habitat.normalize.sources.chirps import normalize_chirps
from habitat.normalize.sources.modis import normalize_modis
from habitat.normalize.sources.sentinel2 import normalize_sentinel2

Normalizer = Callable[[RawManifest, Grid, BBox | None], NormalizedBatch]

NORMALIZERS: dict[tuple[str, str], Normalizer] = {
    ("sentinel2", "cog"): normalize_sentinel2,
    ("modis_mod13q1", "cog"): normalize_modis,
    ("chirps", "geotiff"): normalize_chirps,
    ("chirps", "cog"): normalize_chirps,
}


def normalize(manifest: RawManifest, grid: Grid, aoi: BBox | None = None) -> NormalizedBatch:
    key = (manifest.extensions.source_id, manifest.storage.format)
    normalizer = NORMALIZERS.get(key)
    if normalizer is None:
        raise QuarantineError(f"no normalizer for source/format {key}; propose a mapping before ingest")

    return normalizer(manifest, grid, aoi)

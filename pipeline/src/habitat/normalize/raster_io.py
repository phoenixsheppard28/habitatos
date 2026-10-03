from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass

import numpy as np
import rasterio
from affine import Affine
from rasterio.enums import Resampling
from rasterio.errors import WindowError
from rasterio.warp import transform_bounds
from rasterio.windows import Window, from_bounds

from habitat.contracts import BBox
from habitat.normalize.rows import QuarantineError


@dataclass
class RasterBlock:
    transform: Affine
    crs: str
    bands: dict[str, np.ndarray]

    @property
    def shape(self) -> tuple[int, int]:
        return next(iter(self.bands.values())).shape


def iter_aligned_blocks(
    assets: dict[str, str],
    reference: str,
    aoi: BBox | None = None,
    block_rows: int = 1024,
) -> Iterator[RasterBlock]:
    """Yield row blocks of the reference asset with every other asset resampled onto the same pixels.

    Assets must share a CRS, as the bands of one Sentinel-2 or MODIS item do.
    """
    if block_rows <= 0:
        raise ValueError("block_rows must be positive")
    with ExitStack() as stack:
        sources = {name: stack.enter_context(rasterio.open(uri)) for name, uri in assets.items()}
        reference_source = sources[reference]
        if reference_source.crs is None or any(source.crs != reference_source.crs for source in sources.values()):
            raise QuarantineError("raster assets must have the same known CRS")
        full = reference_window(reference_source, aoi)

        for row_offset in range(int(full.row_off), int(full.row_off + full.height), block_rows):
            height = min(block_rows, int(full.row_off + full.height) - row_offset)
            window = Window(full.col_off, row_offset, full.width, height)
            bounds = rasterio.windows.bounds(window, reference_source.transform)
            shape = (height, int(full.width))

            bands = {}
            for name, source in sources.items():
                if source is reference_source:
                    bands[name] = source.read(1, window=window)
                else:
                    bands[name] = source.read(
                        1,
                        window=from_bounds(*bounds, transform=source.transform),
                        out_shape=shape,
                        resampling=Resampling.nearest,
                        boundless=True,
                        fill_value=source.nodata or 0,
                    )

            yield RasterBlock(
                transform=rasterio.windows.transform(window, reference_source.transform),
                crs=reference_source.crs.to_string(),
                bands=bands,
            )


def reference_window(source: rasterio.io.DatasetReader, aoi: BBox | None) -> Window:
    full = Window(0, 0, source.width, source.height)
    if aoi is None:
        return full

    aoi_in_source_crs = transform_bounds("EPSG:4326", source.crs, *aoi)
    clipped = from_bounds(*aoi_in_source_crs, transform=source.transform).round_offsets().round_lengths()
    try:
        return clipped.intersection(full)
    except WindowError:
        return Window(0, 0, 0, 0)


def pixel_centres(transform: Affine, shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    rows, cols = np.indices(shape)
    cols = cols.ravel() + 0.5
    rows = rows.ravel() + 0.5
    x = transform.c + transform.a * cols + transform.b * rows
    y = transform.f + transform.d * cols + transform.e * rows
    return x, y


def pixel_area_m2(transform: Affine) -> float:
    return abs(transform.a * transform.e - transform.b * transform.d)

from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass

import math

import numpy as np
import rasterio
from affine import Affine
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds
from rasterio.windows import Window, from_bounds

from habitat.contracts import BBox
from habitat.grid import transformer


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
    with ExitStack() as stack:
        sources = {name: stack.enter_context(rasterio.open(uri)) for name, uri in assets.items()}
        reference_source = sources[reference]
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


def iter_warped_blocks(
    assets: dict[str, str],
    reference: str,
    crs: str,
    aoi: BBox | None = None,
    resolution_m: float | None = None,
    block_rows: int = 1024,
    resampling: Resampling = Resampling.nearest,
) -> Iterator[RasterBlock]:
    """Yield row blocks of every asset warped onto one pixel grid in `crs`, for example the grid CRS.

    Use it for a geographic raster, which `aggregate_blocks` rejects. The default pixel size keeps the area of a
    reference pixel at the centre of the AOI. Nearest resampling keeps class codes. Pixels outside an asset get its
    nodata value.
    """
    with ExitStack() as stack:
        sources = {name: stack.enter_context(rasterio.open(uri)) for name, uri in assets.items()}
        reference_source = sources[reference]
        bounds = source_bounds(reference_source, aoi)
        if bounds is None:
            return

        resolution = resolution_m or native_resolution_m(reference_source, crs, bounds)
        transform, width, height = target_pixels(reference_source.crs, crs, bounds, resolution)
        warped = {
            name: stack.enter_context(
                WarpedVRT(source, crs=crs, transform=transform, width=width, height=height, resampling=resampling)
            )
            for name, source in sources.items()
        }

        for row_offset in range(0, height, block_rows):
            window = Window(0, row_offset, width, min(block_rows, height - row_offset))
            yield RasterBlock(
                transform=rasterio.windows.transform(window, transform),
                crs=crs,
                bands={name: vrt.read(1, window=window) for name, vrt in warped.items()},
            )


def source_bounds(source: rasterio.io.DatasetReader, aoi: BBox | None) -> tuple[float, float, float, float] | None:
    """The part of the source inside the AOI, in the source CRS. None when they do not overlap."""
    left, bottom, right, top = source.bounds
    if aoi is not None:
        aoi_left, aoi_bottom, aoi_right, aoi_top = transform_bounds("EPSG:4326", source.crs, *aoi, densify_pts=21)
        left, bottom = max(left, aoi_left), max(bottom, aoi_bottom)
        right, top = min(right, aoi_right), min(top, aoi_top)

    if right <= left or top <= bottom:
        return None

    return left, bottom, right, top


def native_resolution_m(
    source: rasterio.io.DatasetReader, crs: str, bounds: tuple[float, float, float, float]
) -> float:
    """Side of a square with the area of one source pixel at the centre of `bounds`, measured in `crs`."""
    left, bottom, right, top = bounds
    x, y = (left + right) / 2, (top + bottom) / 2
    width, height = source.res
    corners_x = np.array([x, x + width, x + width, x])
    corners_y = np.array([y, y, y - height, y - height])
    target_x, target_y = transformer(source.crs.to_string(), crs).transform(corners_x, corners_y)
    target_x, target_y = np.asarray(target_x), np.asarray(target_y)

    area = abs(np.dot(target_x, np.roll(target_y, 1)) - np.dot(target_y, np.roll(target_x, 1))) / 2
    return float(math.sqrt(area))


def target_pixels(
    source_crs, crs: str, bounds: tuple[float, float, float, float], resolution: float
) -> tuple[Affine, int, int]:
    left, bottom, right, top = transform_bounds(source_crs, crs, *bounds, densify_pts=21)
    left, top = math.floor(left / resolution) * resolution, math.ceil(top / resolution) * resolution
    width = math.ceil((right - left) / resolution)
    height = math.ceil((top - bottom) / resolution)
    return Affine(resolution, 0.0, left, 0.0, -resolution, top), width, height


def reference_window(source: rasterio.io.DatasetReader, aoi: BBox | None) -> Window:
    full = Window(0, 0, source.width, source.height)
    if aoi is None:
        return full

    aoi_in_source_crs = transform_bounds("EPSG:4326", source.crs, *aoi)
    clipped = from_bounds(*aoi_in_source_crs, transform=source.transform).round_offsets().round_lengths()
    return clipped.intersection(full)


def pixel_centres(transform: Affine, shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    rows, cols = np.indices(shape)
    cols = cols.ravel() + 0.5
    rows = rows.ravel() + 0.5
    x = transform.c + transform.a * cols + transform.b * rows
    y = transform.f + transform.d * cols + transform.e * rows
    return x, y


def pixel_area_m2(transform: Affine) -> float:
    return abs(transform.a * transform.e - transform.b * transform.d)

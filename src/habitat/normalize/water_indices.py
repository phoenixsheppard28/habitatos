"""Satellite water-quality proxies on water pixels only. See docs/ingestion/WATER_POLLUTION.md, section 6.

NDTI is from Lacaux et al. (2007). NDCI is from Mishra and Mishra (2012). Both are relative indices, not
concentrations.
"""

import numpy as np

from habitat.normalize.indices import normalized_difference, sentinel2_clear_mask, sentinel2_reflectance

# Sentinel-2 Scene Classification class 6 is water.
SENTINEL2_SCL_WATER = 6
# A cell value from fewer water pixels than this is flagged `few_water_pixels`.
MIN_WATER_PIXELS = 9
WATER_VARIABLES = frozenset({"ndti", "ndci"})


def erode(mask: np.ndarray) -> np.ndarray:
    """Keep a pixel only when it and its eight neighbours are in the mask. This removes shore pixels of mixed land
    and water. Pixels outside the array count as inside the mask, so a block edge does not erode."""
    padded = np.pad(mask, 1, constant_values=True)
    rows, cols = mask.shape
    kept = np.ones_like(mask, dtype=bool)
    for row_offset in range(3):
        for col_offset in range(3):
            kept &= padded[row_offset : row_offset + rows, col_offset : col_offset + cols]
    return kept


def sentinel2_water_mask(green: np.ndarray, swir: np.ndarray, scl: np.ndarray, boa_add_offset: float) -> np.ndarray:
    """Clear view, SCL water and MNDWI above 0, then eroded by one pixel."""
    mndwi = normalized_difference(
        sentinel2_reflectance(green, boa_add_offset), sentinel2_reflectance(swir, boa_add_offset)
    )
    water = sentinel2_clear_mask(scl) & (scl == SENTINEL2_SCL_WATER) & (np.nan_to_num(mndwi, nan=-1.0) > 0)
    return erode(water)


def on_water(values: np.ndarray, water: np.ndarray) -> np.ndarray:
    values = values.copy()
    values[~water] = np.nan
    return values


def sentinel2_ndti(
    green: np.ndarray, red: np.ndarray, swir: np.ndarray, scl: np.ndarray, boa_add_offset: float
) -> np.ndarray:
    water = sentinel2_water_mask(green, swir, scl, boa_add_offset)
    ndti = normalized_difference(
        sentinel2_reflectance(red, boa_add_offset), sentinel2_reflectance(green, boa_add_offset)
    )
    return on_water(ndti, water)


def sentinel2_ndci(
    green: np.ndarray, red: np.ndarray, rededge: np.ndarray, swir: np.ndarray, scl: np.ndarray, boa_add_offset: float
) -> np.ndarray:
    water = sentinel2_water_mask(green, swir, scl, boa_add_offset)
    ndci = normalized_difference(
        sentinel2_reflectance(rededge, boa_add_offset), sentinel2_reflectance(red, boa_add_offset)
    )
    return on_water(ndci, water)

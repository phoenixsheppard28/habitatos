import numpy as np

# Sentinel-2 Scene Classification classes that are not a clear view of the surface:
# 0 no data, 1 saturated/defective, 3 cloud shadow, 8-9 cloud, 10 cirrus, 11 snow/ice.
# https://sentiwiki.copernicus.eu/web/s2-processing#S2Processing-ClassificationMask
SENTINEL2_SCL_INVALID = (0, 1, 3, 8, 9, 10, 11)
SENTINEL2_SCL_NODATA = 0
SENTINEL2_QUANTIFICATION = 10000.0

# MOD13Q1 C6.1 user guide: scale 0.0001, fill -3000, pixel_reliability 0 good / 1 marginal.
MODIS_VI_SCALE = 0.0001
MODIS_VI_FILL = -3000
MODIS_RELIABLE_CLASSES = (0, 1)

CHIRPS_NODATA = -9999.0


def normalized_difference(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a = a.astype(np.float64)
    b = b.astype(np.float64)
    denominator = a + b

    with np.errstate(divide="ignore", invalid="ignore"):
        result = (a - b) / denominator

    result[denominator == 0] = np.nan
    return result


def sentinel2_reflectance(digital_numbers: np.ndarray, boa_add_offset: float) -> np.ndarray:
    reflectance = (digital_numbers.astype(np.float64) + boa_add_offset) / SENTINEL2_QUANTIFICATION
    reflectance[digital_numbers == 0] = np.nan
    return reflectance


def sentinel2_clear_mask(scl: np.ndarray) -> np.ndarray:
    return ~np.isin(scl, SENTINEL2_SCL_INVALID)


def sentinel2_indices(
    b3: np.ndarray, b4: np.ndarray, b8: np.ndarray, b11: np.ndarray, scl: np.ndarray, boa_add_offset: float
) -> dict[str, np.ndarray]:
    green, red, nir, swir = (sentinel2_reflectance(band, boa_add_offset) for band in (b3, b4, b8, b11))
    clear = sentinel2_clear_mask(scl)

    indices = {
        "ndvi": normalized_difference(nir, red),
        "mndwi": normalized_difference(green, swir),
        "ndmi": normalized_difference(nir, swir),
    }

    for values in indices.values():
        values[~clear] = np.nan
    return indices


def modis_vegetation_index(raw: np.ndarray, pixel_reliability: np.ndarray) -> np.ndarray:
    values = raw.astype(np.float64) * MODIS_VI_SCALE
    values[raw == MODIS_VI_FILL] = np.nan
    values[~np.isin(pixel_reliability, MODIS_RELIABLE_CLASSES)] = np.nan
    return values


def chirps_rainfall(raw: np.ndarray) -> np.ndarray:
    values = raw.astype(np.float64)
    values[(raw == CHIRPS_NODATA) | (raw < 0)] = np.nan
    return values

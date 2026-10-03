import numpy as np

from habitat.normalize.indices import (
    chirps_rainfall,
    modis_vegetation_index,
    normalized_difference,
    sentinel2_indices,
    sentinel2_reflectance,
)


def test_normalized_difference_handles_zero_denominator():
    result = normalized_difference(np.array([0.5, 0.0]), np.array([0.1, 0.0]))

    assert np.isclose(result[0], 0.4 / 0.6)
    assert np.isnan(result[1])


def test_sentinel2_reflectance_applies_offset_and_masks_nodata():
    result = sentinel2_reflectance(np.array([2000, 0], dtype=np.uint16), -1000.0)

    assert np.isclose(result[0], 0.1)
    assert np.isnan(result[1])


def test_sentinel2_indices_mask_clouds():
    shape = (2,)
    scl = np.array([4, 9], dtype=np.uint8)

    indices = sentinel2_indices(
        b3=np.full(shape, 1500), b4=np.full(shape, 2000), b8=np.full(shape, 6000), b11=np.full(shape, 4000),
        scl=scl, boa_add_offset=-1000.0,
    )

    assert np.isclose(indices["ndvi"][0], (0.5 - 0.1) / (0.5 + 0.1))
    assert np.isclose(indices["mndwi"][0], (0.05 - 0.3) / (0.05 + 0.3))
    assert all(np.isnan(values[1]) for values in indices.values())


def test_modis_scale_fill_and_reliability():
    result = modis_vegetation_index(np.array([5000, -3000, 5000]), np.array([0, 0, 3]))

    assert np.isclose(result[0], 0.5)
    assert np.isnan(result[1]) and np.isnan(result[2])


def test_chirps_nodata():
    assert np.isnan(chirps_rainfall(np.array([-9999.0]))[0])

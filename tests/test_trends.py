import numpy as np
import pytest
from scipy import stats

from habitat.derive.trends import linear_fit, mann_kendall_p_value, sen_slope

YEARS = np.arange(2001, 2021, dtype=float)


def test_sen_slope_is_the_median_of_pairwise_slopes():
    values = 2.0 + 0.5 * YEARS
    values[3] = 1000.0

    assert sen_slope(YEARS, values) == pytest.approx(0.5)


def test_sen_slope_uses_the_year_gaps():
    assert sen_slope(np.array([2001.0, 2005.0]), np.array([1.0, 3.0])) == pytest.approx(0.5)


def test_a_monotonic_series_has_a_small_mann_kendall_p_value():
    assert mann_kendall_p_value(YEARS * 0.1) < 0.001


def test_mann_kendall_matches_the_normal_approximation_with_ties():
    values = np.array([1, 2, 2, 3, 1, 4, 4, 5, 3, 6], float)
    n = len(values)
    s = sum(np.sign(values[j] - values[i]) for i in range(n) for j in range(i + 1, n))
    four_pairs_of_ties = 4 * (2 * 1 * 9)
    variance = (n * (n - 1) * (2 * n + 5) - four_pairs_of_ties) / 18
    z = (s - 1) / np.sqrt(variance)

    assert mann_kendall_p_value(values) == pytest.approx(2 * stats.norm.sf(abs(z)))


def test_a_constant_series_has_no_trend():
    assert mann_kendall_p_value(np.ones(12)) == 1.0


def test_linear_fit_matches_scipy():
    rng = np.random.default_rng(1)
    x = rng.uniform(300, 900, 15)
    y = 1.0 + 0.004 * x + rng.normal(0, 0.2, 15)
    expected = stats.linregress(x, y)

    fit = linear_fit(x, y)

    assert fit.slope == pytest.approx(expected.slope)
    assert fit.intercept == pytest.approx(expected.intercept)
    assert fit.r2 == pytest.approx(expected.rvalue**2)
    assert fit.p_value == pytest.approx(expected.pvalue)


def test_a_perfect_fit_has_p_value_zero():
    fit = linear_fit(YEARS, 3.0 * YEARS)

    assert fit.p_value == 0.0
    assert fit.r2 == pytest.approx(1.0)

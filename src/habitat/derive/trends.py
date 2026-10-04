import math
from dataclasses import dataclass

import numpy as np
from scipy import stats


@dataclass(frozen=True)
class LinearFit:
    intercept: float
    slope: float
    r2: float
    p_value: float


def sen_slope(years: np.ndarray, values: np.ndarray) -> float:
    """Theil-Sen slope: the median of the slopes between all pairs of years."""
    first, second = np.triu_indices(len(years), k=1)
    return float(np.median((values[second] - values[first]) / (years[second] - years[first])))


def mann_kendall_p_value(values: np.ndarray) -> float:
    """Two-sided Mann-Kendall p-value, normal approximation with the tie correction of the variance."""
    n = len(values)
    first, second = np.triu_indices(n, k=1)
    s = float(np.sign(values[second] - values[first]).sum())

    _, tie_sizes = np.unique(values, return_counts=True)
    ties = float((tie_sizes * (tie_sizes - 1) * (2 * tie_sizes + 5)).sum())
    variance = (n * (n - 1) * (2 * n + 5) - ties) / 18
    if s == 0 or variance <= 0:
        return 1.0

    z = (s - math.copysign(1, s)) / math.sqrt(variance)
    return float(math.erfc(abs(z) / math.sqrt(2)))


def linear_fit(x: np.ndarray, y: np.ndarray) -> LinearFit:
    """Ordinary least squares of y on x, with the two-sided t-test p-value of the slope."""
    n = len(x)
    x_mean, y_mean = x.mean(), y.mean()
    sxx = float(((x - x_mean) ** 2).sum())
    slope = float(((x - x_mean) * (y - y_mean)).sum() / sxx)
    intercept = float(y_mean - slope * x_mean)

    residual_ss = float(((y - (intercept + slope * x)) ** 2).sum())
    total_ss = float(((y - y_mean) ** 2).sum())
    r2 = 1.0 - residual_ss / total_ss if total_ss > 0 else 0.0

    standard_error = math.sqrt(residual_ss / (n - 2) / sxx)
    if standard_error == 0:
        return LinearFit(intercept, slope, r2, 0.0 if slope != 0 else 1.0)

    p_value = float(2 * stats.t.sf(abs(slope / standard_error), n - 2))
    return LinearFit(intercept, slope, r2, p_value)

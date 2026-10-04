"""Annual vegetation summaries, NDVI trend, rain-use efficiency and RESTREND per cell, from stored series.

The method is in docs/ingestion/HABITAT_DEGRADATION.md, section 5. The rows are indicators. Analysis decides if a cell
is degraded.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from habitat.derive.trends import linear_fit, mann_kendall_p_value, sen_slope


USABLE_FLAGS = ("ok", "preliminary")
PRELIMINARY = "preliminary"
FINAL = "final"

COMPOSITES_PER_YEAR = 23
MIN_COMPOSITES = 16
MIN_TREND_YEARS = 10
FULL_TREND_YEARS = 16
MIN_RAIN_MM = 50.0
MAX_FIT_P_VALUE = 0.05
MIN_FIT_YEARS = 3

ANNUAL_UNITS = {
    "ndvi_annual_mean": ("index", "mean"),
    "ndvi_annual_integral": ("index", "sum"),
    "ndvi_seasonal_amplitude": ("index", "percentile"),
    "ndvi_dry_floor": ("index", "percentile"),
    "rain_annual_mm": ("mm", "sum"),
}
NDVI_SUMMARIES = ("ndvi_annual_mean", "ndvi_annual_integral", "ndvi_seasonal_amplitude", "ndvi_dry_floor")
TREND_UNITS = {
    "ndvi_trend_slope": ("index/year", "trend"),
    "ndvi_trend_p_value": ("probability", "trend"),
    "rue_mean": ("index/mm", "mean"),
    "rue_trend_slope": ("index/mm/year", "trend"),
    "rue_trend_p_value": ("probability", "trend"),
    "restrend_slope": ("index/year", "trend"),
    "restrend_p_value": ("probability", "trend"),
    "ndvi_rain_r2": ("ratio", "trend"),
}


@dataclass(frozen=True)
class Season:
    year: int
    start: pd.Timestamp
    end: pd.Timestamp

    @property
    def days(self) -> int:
        return (self.end - self.start).days


@dataclass(frozen=True)
class Window:
    """Inclusive years of the trend window and of the RESTREND baseline. A year starts in `season_start_month`."""

    first_year: int
    last_year: int
    baseline_first: int = 2001
    baseline_last: int = 2015
    season_start_month: int = 1

    @property
    def years(self) -> int:
        return self.last_year - self.first_year + 1

    @property
    def start(self) -> pd.Timestamp:
        return season_start(self.first_year, self.season_start_month)

    @property
    def end(self) -> pd.Timestamp:
        return season_start(self.last_year + 1, self.season_start_month)

    def all_seasons(self) -> list[Season]:
        first = min(self.first_year, self.baseline_first)
        last = max(self.last_year, self.baseline_last)
        return seasons(first, last, self.season_start_month)


def season_start(year: int, month: int) -> pd.Timestamp:
    return pd.Timestamp(year=year, month=month, day=1, tz="UTC")


def seasons(first_year: int, last_year: int, start_month: int = 1) -> list[Season]:
    return [
        Season(year, season_start(year, start_month), season_start(year + 1, start_month))
        for year in range(first_year, last_year + 1)
    ]


def assign_seasons(rows: pd.DataFrame, all_seasons: list[Season]) -> pd.DataFrame:
    starts = np.array([season.start.value for season in all_seasons])
    ends = np.array([season.end.value for season in all_seasons])
    utc_times = pd.to_datetime(rows["time_start"], utc=True).dt.tz_convert(None)
    times = utc_times.to_numpy("datetime64[ns]").astype(np.int64)

    index = np.searchsorted(starts, times, side="right") - 1
    inside = (index >= 0) & (times < ends[np.clip(index, 0, len(ends) - 1)])
    rows = rows[inside].copy()
    rows["season"] = index[inside]
    rows["usable"] = rows["quality_flag"].isin(USABLE_FLAGS) & rows["value"].notna()
    return rows


def first_reason(*reasons: tuple[bool, str]) -> str:
    return next((flag for applies, flag in reasons if applies), "ok")


@dataclass(frozen=True)
class InputStatus:
    preliminary: bool
    available_at: pd.Timestamp
    resolution_m: float


def input_status(group: pd.DataFrame) -> InputStatus:
    """A derived value is public when its last input is, and it has the resolution of its coarsest input."""
    return InputStatus(
        preliminary=bool((group["product_status"] == PRELIMINARY).any()),
        available_at=pd.Timestamp(group["available_at"].max()),
        resolution_m=float(group["source_resolution_m"].max()),
    )


def mixes_collections(group: pd.DataFrame) -> bool:
    """MODIS versions are `<collection>.<production time>`, so only the collection part must agree."""
    return group["processing_version"].astype(str).str.split(".").str[0].nunique() > 1


def output_row(
    cell_id, variable, units, period, value, valid_fraction, pixel_count, flag, inputs: InputStatus
) -> dict:
    unit, stat = units[variable]
    return {
        "cell_id": cell_id,
        "variable": variable,
        "time_start": period.start,
        "time_end": period.end,
        "value": float(value) if value is not None and np.isfinite(value) else np.nan,
        "std": None,
        "unit": unit,
        "stat": stat,
        "valid_fraction": float(min(valid_fraction, 1.0)),
        "pixel_count": int(pixel_count),
        "quality_flag": flag,
        "product_status": PRELIMINARY if inputs.preliminary else FINAL,
        "available_at": inputs.available_at,
        "source_resolution_m": inputs.resolution_m,
    }


def annual_ndvi(rows: pd.DataFrame, all_seasons: list[Season]) -> pd.DataFrame:
    """Annual mean, integral, seasonal amplitude and dry-season floor of the valid 16-day NDVI composites."""
    output = []
    for (cell_id, season_index), group in assign_seasons(rows, all_seasons).groupby(["cell_id", "season"]):
        season = all_seasons[season_index]
        inputs = input_status(group)
        values = group.loc[group["usable"], "value"].to_numpy(float)
        enough = len(values) >= MIN_COMPOSITES
        flag = first_reason(
            (not enough, "low_valid_fraction"), (mixes_collections(group), "cross_version_inputs"),
            (inputs.preliminary, "preliminary"),
        )

        summary = dict.fromkeys(NDVI_SUMMARIES)
        if enough:
            p10, p90 = np.percentile(values, [10, 90])
            summary = {
                "ndvi_annual_mean": values.mean(),
                "ndvi_annual_integral": values.mean() * COMPOSITES_PER_YEAR,
                "ndvi_seasonal_amplitude": p90 - p10,
                "ndvi_dry_floor": p10,
            }

        valid_fraction = len(values) / COMPOSITES_PER_YEAR
        output += [
            output_row(cell_id, variable, ANNUAL_UNITS, season, value, valid_fraction, len(values), flag, inputs)
            for variable, value in summary.items()
        ]

    return pd.DataFrame(output)


def annual_rain(rows: pd.DataFrame, all_seasons: list[Season]) -> pd.DataFrame:
    """Sum of daily rainfall. A year with a missing day has no sum, because a partial sum is biased low."""
    output = []
    for (cell_id, season_index), group in assign_seasons(rows, all_seasons).groupby(["cell_id", "season"]):
        season = all_seasons[season_index]
        inputs = input_status(group)
        days = group.loc[group["usable"]].drop_duplicates("time_start")
        complete = len(days) >= season.days
        flag = first_reason(
            (not complete, "low_valid_fraction"), (mixes_collections(group), "cross_version_inputs"),
            (inputs.preliminary, "preliminary"),
        )

        total = days["value"].sum() if complete else None
        output.append(output_row(
            cell_id, "rain_annual_mm", ANNUAL_UNITS, season, total, len(days) / season.days, len(days), flag, inputs
        ))

    return pd.DataFrame(output)


def valid_series(rows: pd.DataFrame, variable: str, first_year: int, last_year: int) -> pd.Series:
    selected = rows[(rows["variable"] == variable) & rows["usable"]]
    selected = selected[selected["year"].between(first_year, last_year)]
    return selected.set_index("year")["value"].astype(float).sort_index()


def trend_values(series: pd.Series) -> tuple[float | None, float | None]:
    if len(series) < MIN_TREND_YEARS:
        return None, None

    years, values = series.index.to_numpy(float), series.to_numpy(float)
    return sen_slope(years, values), mann_kendall_p_value(values)


def short_series(count: int) -> bool:
    return count < FULL_TREND_YEARS


def vegetation_trends(annual: pd.DataFrame, window: Window) -> pd.DataFrame:
    """NDVI trend, rain-use efficiency and RESTREND per cell, from the annual integral and the annual rainfall."""
    rows = annual.copy()
    rows["year"] = pd.to_datetime(rows["time_start"], utc=True).dt.year
    rows["usable"] = rows["quality_flag"].isin(USABLE_FLAGS) & rows["value"].notna()
    first_year = min(window.first_year, window.baseline_first)
    last_year = max(window.last_year, window.baseline_last)
    rows = rows[rows["year"].between(first_year, last_year)]

    output = []
    for cell_id, group in rows.groupby("cell_id"):
        output += cell_trends(cell_id, group, window)

    return pd.DataFrame(output)


def cell_trends(cell_id: str, rows: pd.DataFrame, window: Window) -> list[dict]:
    integral = valid_series(rows, "ndvi_annual_integral", window.first_year, window.last_year)
    rain = valid_series(rows, "rain_annual_mm", window.first_year, window.last_year)
    ndvi_inputs = input_status(rows[rows["variable"] == "ndvi_annual_integral"])
    all_inputs = input_status(rows)

    def row(variable, value, count, flag, inputs, years=window.years):
        return output_row(cell_id, variable, TREND_UNITS, window, value, count / years, count, flag, inputs)

    def flag_for(count, inputs, weak=False):
        return first_reason(
            (weak, "weak_rain_relation"), (short_series(count), "short_series"), (inputs.preliminary, "preliminary")
        )

    slope, p_value = trend_values(integral)
    output = [
        row("ndvi_trend_slope", slope, len(integral), flag_for(len(integral), ndvi_inputs), ndvi_inputs),
        row("ndvi_trend_p_value", p_value, len(integral), flag_for(len(integral), ndvi_inputs), ndvi_inputs),
    ]

    wet_years = rain[rain >= MIN_RAIN_MM].index.intersection(integral.index)
    rue = integral[wet_years] / rain[wet_years]
    rue_slope, rue_p_value = trend_values(rue)
    rue_mean = rue.mean() if len(rue) >= MIN_TREND_YEARS else None
    rue_flag = flag_for(len(rue), all_inputs)
    output += [
        row("rue_mean", rue_mean, len(rue), rue_flag, all_inputs),
        row("rue_trend_slope", rue_slope, len(rue), rue_flag, all_inputs),
        row("rue_trend_p_value", rue_p_value, len(rue), rue_flag, all_inputs),
    ]

    return output + restrend(rows, integral, rain, window, all_inputs, row, flag_for)


def restrend(rows, integral, rain, window, inputs, row, flag_for) -> list[dict]:
    baseline_integral = valid_series(rows, "ndvi_annual_integral", window.baseline_first, window.baseline_last)
    baseline_rain = valid_series(rows, "rain_annual_mm", window.baseline_first, window.baseline_last)
    baseline_years = baseline_integral.index.intersection(baseline_rain.index)
    baseline_length = window.baseline_last - window.baseline_first + 1

    fit = None
    if len(baseline_years) >= MIN_FIT_YEARS and baseline_rain[baseline_years].nunique() > 1:
        fit = linear_fit(
            baseline_rain[baseline_years].to_numpy(float), baseline_integral[baseline_years].to_numpy(float)
        )

    weak = fit is None or fit.slope <= 0 or fit.p_value > MAX_FIT_P_VALUE
    r2_flag = first_reason((fit is None, "weak_rain_relation"), (inputs.preliminary, "preliminary"))
    r2_row = row("ndvi_rain_r2", fit.r2 if fit else None, len(baseline_years), r2_flag, inputs, baseline_length)

    window_years = integral.index.intersection(rain.index)
    slope = p_value = None
    if not weak and len(window_years) >= MIN_TREND_YEARS:
        residual = integral[window_years] - (fit.intercept + fit.slope * rain[window_years])
        residual_fit = linear_fit(window_years.to_numpy(float), residual.to_numpy(float))
        slope, p_value = residual_fit.slope, residual_fit.p_value

    flag = flag_for(len(window_years), inputs, weak)
    return [
        row("restrend_slope", slope, len(window_years), flag, inputs),
        row("restrend_p_value", p_value, len(window_years), flag, inputs),
        r2_row,
    ]

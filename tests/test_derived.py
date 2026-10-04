from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from habitat.derive.vegetation import Window, annual_ndvi, annual_rain, seasons, vegetation_trends

CELL = "E1K-r100-c200"
PUBLISHED = pd.Timestamp("2020-06-01", tz="UTC")


def ndvi_rows(year, values, flag="ok", status="final", available_at=PUBLISHED, version="061.2020001000000"):
    starts = [pd.Timestamp(datetime(year, 1, 1, tzinfo=UTC) + timedelta(days=16 * i)) for i in range(len(values))]
    return pd.DataFrame({
        "cell_id": CELL, "time_start": starts, "value": values, "quality_flag": flag, "product_status": status,
        "available_at": available_at, "processing_version": version, "source_resolution_m": 231.656358,
    })


def rain_rows(year, daily_mm=2.0, missing_days=0, status="final"):
    days = pd.date_range(f"{year}-01-01", f"{year}-12-31", freq="D", tz="UTC")[missing_days:]
    return pd.DataFrame({
        "cell_id": CELL, "time_start": days, "value": daily_mm,
        "quality_flag": "ok" if status == "final" else "preliminary",
        "product_status": status, "available_at": PUBLISHED, "processing_version": "2.0", "source_resolution_m": 5566.0,
    })


def by_variable(rows: pd.DataFrame) -> dict[str, pd.Series]:
    return {variable: group.iloc[0] for variable, group in rows.groupby("variable")}


def test_seasons_start_in_the_given_month():
    [first, second] = seasons(2005, 2006, start_month=10)

    assert first.year == 2005
    assert (first.start, first.end) == (pd.Timestamp("2005-10-01", tz="UTC"), pd.Timestamp("2006-10-01", tz="UTC"))
    assert second.start == first.end


def test_annual_ndvi_summarizes_the_valid_composites_of_a_year():
    values = np.linspace(0.2, 0.64, 23)

    rows = by_variable(annual_ndvi(ndvi_rows(2005, values), seasons(2005, 2005)))

    assert rows["ndvi_annual_mean"]["value"] == pytest.approx(values.mean())
    assert rows["ndvi_annual_integral"]["value"] == pytest.approx(values.mean() * 23)
    p10, p90 = np.percentile(values, [10, 90])
    assert rows["ndvi_seasonal_amplitude"]["value"] == pytest.approx(p90 - p10)
    assert rows["ndvi_dry_floor"]["value"] == pytest.approx(np.percentile(values, 10))
    assert all(row["quality_flag"] == "ok" and row["valid_fraction"] == 1.0 for row in rows.values())
    assert rows["ndvi_annual_mean"]["time_start"] == pd.Timestamp("2005-01-01", tz="UTC")
    assert rows["ndvi_annual_mean"]["time_end"] == pd.Timestamp("2006-01-01", tz="UTC")


def test_a_year_with_fewer_than_16_valid_composites_is_null():
    values = np.full(23, 0.5)
    values[:8] = np.nan

    rows = annual_ndvi(ndvi_rows(2005, values), seasons(2005, 2005))

    assert rows["value"].isna().all()
    assert set(rows["quality_flag"]) == {"low_valid_fraction"}
    assert set(rows["pixel_count"]) == {15}


def test_flagged_composites_count_as_missing():
    rows = ndvi_rows(2005, np.full(23, 0.5))
    rows.loc[:7, "quality_flag"] = "low_valid_fraction"

    assert annual_ndvi(rows, seasons(2005, 2005))["value"].isna().all()


def test_a_preliminary_composite_makes_the_year_preliminary():
    rows = ndvi_rows(2005, np.full(23, 0.5))
    rows.loc[22, ["quality_flag", "product_status"]] = ["preliminary", "preliminary"]

    result = annual_ndvi(rows, seasons(2005, 2005))

    assert set(result["quality_flag"]) == {"preliminary"}
    assert set(result["product_status"]) == {"preliminary"}


def test_two_collections_in_one_year_are_flagged():
    rows = ndvi_rows(2005, np.full(23, 0.5))
    rows.loc[22, "processing_version"] = "062.2030001000000"

    assert set(annual_ndvi(rows, seasons(2005, 2005))["quality_flag"]) == {"cross_version_inputs"}


def test_annual_rain_is_the_sum_of_a_complete_year():
    [row] = annual_rain(rain_rows(2005), seasons(2005, 2005)).to_dict("records")

    assert row["variable"] == "rain_annual_mm"
    assert row["value"] == pytest.approx(2.0 * 365)
    assert row["unit"] == "mm" and row["stat"] == "sum" and row["quality_flag"] == "ok"
    assert row["source_resolution_m"] == 5566.0


def test_annual_rain_with_a_missing_day_is_null():
    [row] = annual_rain(rain_rows(2005, missing_days=1), seasons(2005, 2005)).to_dict("records")

    assert np.isnan(row["value"])
    assert row["quality_flag"] == "low_valid_fraction"
    assert row["valid_fraction"] == pytest.approx(364 / 365)


def test_annual_rain_from_preliminary_days_is_preliminary():
    [row] = annual_rain(rain_rows(2005, status="preliminary"), seasons(2005, 2005)).to_dict("records")

    assert row["value"] == pytest.approx(730.0)
    assert row["quality_flag"] == "preliminary"


def annual_series(years, integral, rain, available_at=PUBLISHED):
    rows = []
    for year, ndvi, mm in zip(years, integral, rain):
        start = pd.Timestamp(f"{year}-01-01", tz="UTC")
        for variable, value, resolution in [("ndvi_annual_integral", ndvi, 231.656358), ("rain_annual_mm", mm, 5566.0)]:
            rows.append({
                "cell_id": CELL, "variable": variable, "time_start": start, "value": value, "quality_flag": "ok",
                "product_status": "final", "available_at": available_at, "source_resolution_m": resolution,
            })
    return pd.DataFrame(rows)


YEARS = np.arange(2001, 2021)
# Symmetric about 2008, so the rainfall of the baseline 2001-2015 has no linear relation with the year.
SYMMETRIC_RAIN = 600 + 150 * np.cos(np.pi * (YEARS - 2008) / 2.5)
WINDOW = Window(first_year=2001, last_year=2020, baseline_first=2001, baseline_last=2015)


def test_restrend_removes_the_rainfall_effect_and_keeps_the_trend():
    integral = 5 + 0.05 * (YEARS - 2001) + 0.004 * SYMMETRIC_RAIN

    rows = by_variable(vegetation_trends(annual_series(YEARS, integral, SYMMETRIC_RAIN), WINDOW))

    assert rows["restrend_slope"]["value"] == pytest.approx(0.05)
    assert rows["restrend_p_value"]["value"] < 0.001
    assert rows["restrend_slope"]["quality_flag"] == "ok"
    assert rows["ndvi_rain_r2"]["value"] > 0.5
    assert rows["rue_mean"]["value"] == pytest.approx(np.mean(integral / SYMMETRIC_RAIN))
    assert rows["restrend_slope"]["time_start"] == pd.Timestamp("2001-01-01", tz="UTC")
    assert rows["restrend_slope"]["time_end"] == pd.Timestamp("2021-01-01", tz="UTC")
    assert rows["restrend_slope"]["source_resolution_m"] == 5566.0
    assert rows["ndvi_trend_slope"]["source_resolution_m"] == 231.656358


def test_ndvi_trend_is_the_sen_slope_of_the_annual_integral():
    integral = 5 + 0.05 * (YEARS - 2001)

    rows = by_variable(vegetation_trends(annual_series(YEARS, integral, SYMMETRIC_RAIN), WINDOW))

    assert rows["ndvi_trend_slope"]["value"] == pytest.approx(0.05)
    assert rows["ndvi_trend_p_value"]["value"] < 0.001
    assert rows["ndvi_trend_slope"]["unit"] == "index/year"
    assert rows["ndvi_trend_p_value"]["unit"] == "probability"
    assert rows["ndvi_trend_slope"]["valid_fraction"] == 1.0
    assert rows["ndvi_trend_slope"]["pixel_count"] == 20


def test_a_series_without_a_rainfall_relation_gives_weak_rain_relation():
    rng = np.random.default_rng(7)
    rain = rng.uniform(400, 900, len(YEARS))
    integral = 8 + rng.normal(0, 0.5, len(YEARS))

    rows = by_variable(vegetation_trends(annual_series(YEARS, integral, rain), WINDOW))

    assert np.isnan(rows["restrend_slope"]["value"]) and np.isnan(rows["restrend_p_value"]["value"])
    assert rows["restrend_slope"]["quality_flag"] == "weak_rain_relation"
    assert not np.isnan(rows["ndvi_rain_r2"]["value"])


def test_a_9_year_series_gives_short_series_and_null_values():
    years = YEARS[:9]
    integral = 5 + 0.05 * (years - 2001) + 0.004 * SYMMETRIC_RAIN[:9]

    rows = vegetation_trends(annual_series(years, integral, SYMMETRIC_RAIN[:9]), WINDOW)
    trend = rows[rows["variable"].isin(["ndvi_trend_slope", "rue_mean", "rue_trend_slope"])]

    assert trend["value"].isna().all()
    assert set(trend["quality_flag"]) == {"short_series"}


def test_a_12_year_series_is_computed_and_flagged_short_series():
    years = YEARS[:12]
    integral = 5 + 0.05 * (years - 2001)

    rows = by_variable(vegetation_trends(annual_series(years, integral, SYMMETRIC_RAIN[:12]), WINDOW))

    assert rows["ndvi_trend_slope"]["value"] == pytest.approx(0.05)
    assert rows["ndvi_trend_slope"]["quality_flag"] == "short_series"
    assert rows["ndvi_trend_slope"]["valid_fraction"] == pytest.approx(12 / 20)


def test_rue_ignores_years_with_less_than_50_mm_of_rain():
    integral = np.full(len(YEARS), 6.0)
    rain = np.full(len(YEARS), 600.0)
    rain[0] = 10.0

    rows = by_variable(vegetation_trends(annual_series(YEARS, integral, rain), WINDOW))

    assert rows["rue_mean"]["value"] == pytest.approx(0.01)
    assert rows["rue_mean"]["pixel_count"] == 19


def test_a_derived_value_is_available_when_its_last_input_is():
    integral = 5 + 0.05 * (YEARS - 2001)
    series = annual_series(YEARS, integral, SYMMETRIC_RAIN)
    late = pd.Timestamp("2024-03-01", tz="UTC")
    series.loc[(series["variable"] == "rain_annual_mm") & (series["time_start"].dt.year == 2010), "available_at"] = late

    rows = by_variable(vegetation_trends(series, WINDOW))

    assert rows["restrend_slope"]["available_at"] == late
    assert rows["ndvi_trend_slope"]["available_at"] == PUBLISHED


def test_window_rows_stay_inside_the_window():
    integral = 5 + 0.05 * (YEARS - 2001)
    window = Window(first_year=2005, last_year=2018, baseline_first=2001, baseline_last=2015)

    rows = by_variable(vegetation_trends(annual_series(YEARS, integral, SYMMETRIC_RAIN), window))

    assert rows["ndvi_trend_slope"]["pixel_count"] == 14
    assert rows["ndvi_trend_slope"]["time_start"] == pd.Timestamp("2005-01-01", tz="UTC")


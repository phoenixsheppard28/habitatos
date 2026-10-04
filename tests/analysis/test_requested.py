from unittest.mock import Mock

import numpy as np
import pytest

from analysis.service import run
from contracts.models import AnalysisOptions
from support import movement_frame, movement_request, replace_frame


def test_relationship_question_uses_actual_pairs_and_statistics_instead_of_median_bars(tmp_path):
    request, store = movement_request(tmp_path, question="Is displacement related to rainfall?")
    frame = movement_frame()
    frame["rain_mm"] = [0, 1, 2, 3, 4, 5, None]
    frame["km_moved"] = [10, 8, 6, 4, 2, 0, 99]
    replace_frame(tmp_path, request, frame)

    response = run(request, store=store)
    result = response["output"]["result"]
    pair = result["metrics"]["correlations"][0]

    assert response["status"] == "ok"
    assert response["output"]["analysis_spec"]["method"] == "correlation"
    assert pair["n"] == 6
    assert pair["excluded_rows"] == 1
    assert pair["pearson_r"] == pytest.approx(-1)
    assert pair["spearman_rho"] == pytest.approx(-1)
    assert pair["slope"] == pytest.approx(-2)
    assert pair["intercept"] == pytest.approx(10)
    assert "charts" not in result
    assert any("not independent" in note for note in result["limitations"])
    assert result["tables"][0]["rows"][0][1:3] == [6, 1]


def test_statistics_report_missing_rows_sample_sd_and_quartiles_without_coordinate_requirements(tmp_path):
    request, store = movement_request(tmp_path, question="Give descriptive statistics for movement")
    artifact = request["input"]["feature_artifact"]
    artifact["columns"] = [column for column in artifact["columns"] if column["role"] not in {"longitude", "latitude"}]
    frame = movement_frame()
    frame["km_moved"] = [0, 1, 2, 3, None, np.inf, -np.inf]
    replace_frame(tmp_path, request, frame)

    response = run(request, store=store)
    result = response["output"]["result"]
    stats = result["metrics"]["variables"]["daily_displacement"]

    assert response["status"] == "ok"
    assert stats["n"] == 4
    assert stats["missing"] == 3
    assert stats["mean"] == 1.5
    assert stats["std"] == pytest.approx(np.std([0, 1, 2, 3], ddof=1))
    assert [stats[key] for key in ("min", "q1", "median", "q3", "max")] == [0, 0.75, 1.5, 2.25, 3]
    assert result["tables"][0]["columns"][5] == "Sample SD"
    assert any("non-finite" in warning for warning in response["warnings"])


def test_distribution_reports_statistics_for_the_requested_variable(tmp_path):
    request, store = movement_request(tmp_path, question="Show a histogram of rainfall",
                                      analysis={"method": "distribution", "variables": ["rain_mm"]})

    result = run(request, store=store)["output"]["result"]

    assert list(result["metrics"]["variables"]) == ["rainfall"]
    assert result["metrics"]["variables"]["rainfall"]["n"] == 7
    assert result["tables"][0]["title"] == "Descriptive statistics"


def test_trends_include_each_requested_variable_and_preserve_missing_values(tmp_path):
    request, store = movement_request(tmp_path, question="Chart rainfall and movement trends")

    response = run(request, store=store)
    series = response["output"]["result"]["timeline"]["series"]

    assert response["output"]["analysis_spec"]["method"] == "trend"
    assert {point["unit"] for point in series} == {"km", "mm"}
    assert series[0]["median"] is None


@pytest.mark.parametrize("values", [[2] * 7, [None] * 7, [1, 2, None, None, None, None, None]])
def test_correlation_refuses_constant_missing_or_insufficient_pairs(tmp_path, values):
    request, store = movement_request(tmp_path, question="Correlate rainfall and movement")
    frame = movement_frame()
    frame["rain_mm"] = values
    replace_frame(tmp_path, request, frame)

    response = run(request, store=store)

    assert response["status"] == "insufficient_data"
    assert response["output"]["result"]["code"] == "insufficient_pairs"


def test_explicit_missing_variable_fails_before_reading_the_table(tmp_path):
    request, _ = movement_request(tmp_path, analysis={"method": "correlation", "variables": ["temperature", "daily_displacement"]})
    store = Mock()

    response = run(request, store=store)

    assert response["status"] == "insufficient_data"
    assert response["output"]["result"]["code"] == "missing_variables"
    store.read_dataset.assert_not_called()


def test_explicit_axis_order_is_respected(tmp_path):
    request, store = movement_request(tmp_path, analysis={"method": "correlation", "variables": ["km_moved", "rain_mm"]})

    response = run(request, store=store)
    result = response["output"]["result"]

    assert result["metrics"]["correlations"][0]["x_role"] == "daily_displacement"
    assert result["tables"][0]["title"] == "Correlation statistics"


def test_environment_only_correlation_does_not_require_animal_movement(tmp_path):
    request, store = movement_request(tmp_path, analysis={"method": "correlation", "variables": ["vegetation_index", "rainfall"]})
    artifact = request["input"]["feature_artifact"]
    artifact["columns"] = [column for column in artifact["columns"] if column["role"] in {"event_time", "rainfall"}]
    artifact["columns"].append({"name": "ndvi", "type": "number", "nullable": True, "role": "vegetation_index", "unit": "index"})
    frame = movement_frame()
    frame["ndvi"] = np.arange(7) / 10
    replace_frame(tmp_path, request, frame)

    response = run(request, store=store)
    result = response["output"]["result"]

    assert response["status"] == "ok"
    assert result["metrics"]["correlations"][0]["n"] == 7
    assert not any("tracked" in note.lower() for note in result["limitations"])


def test_display_hints_no_longer_override_the_method():
    options = AnalysisOptions.model_validate({"method": "correlation", "chart": "bar"})

    assert options.model_dump() == {"method": "correlation", "variables": []}


def test_numeric_columns_without_ecological_roles_support_statistics_by_name(tmp_path):
    request, store = movement_request(tmp_path, analysis={"method": "statistics", "variables": ["temperature_c"]})
    request["input"]["feature_artifact"]["columns"].append({"name": "temperature_c", "type": "number", "nullable": True, "unit": "°C"})
    frame = movement_frame()
    frame["temperature_c"] = [0, 2, 4, 6, 8, 10, 12]
    replace_frame(tmp_path, request, frame)

    response = run(request, store=store)

    assert response["status"] == "ok"
    stats = response["output"]["result"]["metrics"]["variables"]["temperature_c"]
    assert stats["mean"] == 6
    assert stats["unit"] == "°C"


def test_explicit_residence_method_takes_precedence_over_relationship_keyword_detection(tmp_path):
    request, store = movement_request(tmp_path, question="Is residence time related to NDVI?", analysis_method="residence_time")
    artifact = request["input"]["feature_artifact"]
    artifact["sampling_grain"] = "animal_day"
    artifact["columns"].append({"name": "ndvi", "type": "number", "nullable": True, "role": "vegetation_index"})

    response = run(request, store=store)

    assert response["status"] == "insufficient_data"
    assert response["output"]["result"]["code"] == "fix_grain_required"


def test_spearman_uses_average_ranks_for_ties_instead_of_reusing_pearson(tmp_path):
    request, store = movement_request(tmp_path, question="Correlate rainfall and movement")
    frame = movement_frame()
    frame["rain_mm"] = [1, 1, 2, 3, 3, 4, None]
    frame["km_moved"] = [3, 4, 1, 2, 2, 0, 99]
    replace_frame(tmp_path, request, frame)

    pair = run(request, store=store)["output"]["result"]["metrics"]["correlations"][0]

    assert pair["spearman_rho"] == pytest.approx(-13.5 / np.sqrt(16.5 * 17))
    assert pair["pearson_r"] != pytest.approx(pair["spearman_rho"])


def test_date_comparison_reports_each_requested_window(tmp_path):
    request, store = movement_request(tmp_path, question="Compare two date windows", comparison_windows=[
        {"name": "First", "start": "2026-01-01T00:00:00Z", "end": "2026-01-02T23:59:59Z"},
        {"name": "Second", "start": "2026-01-03T00:00:00Z", "end": "2026-01-04T23:59:59Z"},
    ])

    result = run(request, store=store)["output"]["result"]

    assert [window["name"] for window in result["metrics"]["comparison"]["windows"]] == ["First", "Second"]


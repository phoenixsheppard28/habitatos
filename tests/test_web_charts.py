import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.charts import ChartSpec
from habitat.web_charts import chart_specs


def test_frontend_examples_share_the_backend_chart_contract():
    examples = json.loads((Path(__file__).parents[1] / "web/tests/fixtures/charts.json").read_text())

    charts = [ChartSpec.model_validate(example) for example in examples]

    assert {chart.kind for chart in charts} == {"line", "bar", "scatter"}
    assert charts[0].series[0].points[0].y is None
    assert charts[1].series[0].points[0].y == -2
    assert charts[2].x_axis.type == "numeric"


def test_analysis_timelines_become_generic_series_without_replacing_missing_values():
    result = {"result_id": "r", "timeline": {"series": [
        {"date": "2024-01-01", "median_daily_displacement": None, "unit": "km", "n_animals": 2},
        {"date": "2024-01-02", "median_daily_displacement": 0, "unit": "km", "n_animals": 2},
        {"date": "2024-01-03", "median_daily_displacement": 7, "unit": "km", "predicted": True},
        {"date": "2024-01-01", "median": -0.2, "unit": "index", "role": "vegetation_index"},
    ]}}

    charts = chart_specs(result)

    assert len(charts) == 2
    assert charts[0]["kind"] == "line"
    assert charts[0]["x_axis"] == {"label": "Date (UTC)", "type": "temporal"}
    assert charts[0]["y_axis"]["unit"] == "km"
    assert charts[0]["max_gap"] == 86_400_000
    assert charts[0]["series"][0]["points"][0]["y"] is None
    assert charts[0]["series"][0]["points"][1]["y"] == 0
    assert charts[0]["series"][1]["name"] == "Predicted"
    assert charts[0]["series"][1]["style"] == "dashed"
    assert charts[1]["series"][0]["points"][0]["y"] == -0.2


def test_comparison_metrics_become_labeled_bar_charts():
    result = {"result_id": "r", "metrics": {
        "comparison": {"windows": [
            {"name": "Wet season", "start": "2024-01-01", "end": "2024-03-31", "unit": "km",
             "median_displacement": 8, "n_animals": 2, "n_displacement_rows": 40},
            {"name": "Dry season", "start": "2024-06-01", "end": "2024-08-31", "unit": "km",
             "median_displacement": None, "n_animals": 0, "n_displacement_rows": 0},
        ]},
        "habitat": {"rainfall": {"label": "Rainfall", "median_environment": 5, "environment_unit": "mm",
                                 "above_median_displacement": 8, "below_median_displacement": 4,
                                 "displacement_unit": "km", "n_above": 20, "n_below": 25}},
    }}

    charts = chart_specs(result)

    assert [chart["kind"] for chart in charts] == ["bar", "bar"]
    assert [point["x"] for point in charts[0]["series"][0]["points"]] == ["Wet season", "Dry season"]
    assert charts[0]["series"][0]["points"][1]["y"] is None
    assert "40 measured rows" in charts[0]["series"][0]["points"][0]["note"]
    assert charts[1]["description"] == "Environmental median: 5 mm."
    assert [point["y"] for point in charts[1]["series"][0]["points"]] == [4, 8]


@pytest.mark.parametrize("point", [{"x": 1, "y": float("nan")}, {"x": "not a number", "y": 2}])
def test_chart_contract_rejects_invalid_numeric_coordinates(point):
    with pytest.raises(ValidationError):
        ChartSpec.model_validate({
            "chart_id": "r", "title": "Numeric chart", "kind": "scatter",
            "x_axis": {"label": "Input", "type": "numeric"}, "y_axis": {"label": "Output"},
            "series": [{"name": "Measured", "points": [point]}],
        })

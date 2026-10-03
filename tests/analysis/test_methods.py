"""Before/after windows, route overlap, habitat-only summaries, and cell-use forecasts."""

import pandas as pd

from analysis.service import run
from tests.analysis.test_forecast import _forecast_request
from tests.support import movement_request

EARLY = {"name": "early", "start": "2026-01-01T00:00:00Z", "end": "2026-01-02T00:00:00Z"}
LATE = {"name": "late", "start": "2026-01-03T00:00:00Z", "end": "2026-01-04T00:00:00Z"}
CORE = {
    "type": "Polygon",
    "coordinates": [[[9, -1], [11, -1], [11, 1], [9, 1], [9, -1]]],
}


def test_before_and_after_windows_report_both_samples(tmp_path):
    request, store = movement_request(
        tmp_path,
        comparison_windows=[EARLY, LATE],
    )
    response = run(request, store=store)
    windows = response["output"]["result"]["metrics"]["comparison"]["windows"]
    early, late = windows
    assert response["status"] == "ok"
    assert early["median_displacement"] == 7
    assert early["n_displacement_rows"] == 2
    assert early["n_animals"] == 2
    assert late["median_displacement"] == 4
    assert late["n_displacement_rows"] == 3
    assert late["n_animals"] == 2
    assert "not a restoration outcome" in response["output"]["result"]["report"]


def test_one_comparison_window_is_invalid(tmp_path):
    request, store = movement_request(tmp_path)
    request["input"]["query"]["comparison_windows"] = [EARLY]
    response = run(request, store=store)
    assert response["status"] == "error"
    assert "two" in response["error"]["message"]


def test_route_overlap_counts_only_the_inside_track(tmp_path):
    request, store = movement_request(tmp_path)
    request["input"]["boundaries"] = [{"name": "core", "geometry": CORE}]
    response = run(request, store=store)
    layer = response["output"]["result"]["metrics"]["overlap"]["boundaries"][0]
    by_animal = {row["entity_id"]: row for row in layer["animals"]}
    assert by_animal["A"]["path_share"] == 1
    assert by_animal["B"]["path_share"] == 0
    assert layer["n_points_inside"] == 3
    assert layer["n_points"] == 7
    assert "not a population" in response["output"]["result"]["report"]


def test_unusable_boundary_is_skipped(tmp_path):
    request, store = movement_request(tmp_path)
    request["input"]["boundaries"] = [{"name": "pin", "geometry": {"type": "Point", "coordinates": [10, 0]}}]
    response = run(request, store=store)
    assert response["status"] == "ok"
    assert response["output"]["result"]["metrics"]["overlap"]["boundaries"] == []
    assert any("pin" in warning for warning in response["warnings"])


def test_habitat_only_does_not_claim_movement(tmp_path):
    request, store = movement_request(tmp_path)
    columns = request["input"]["feature_artifact"]["columns"]
    request["input"]["feature_artifact"]["columns"] = [
        column for column in columns if column["role"] in {"event_time", "rainfall"}
    ]
    response = run(request, store=store)
    report = response["output"]["result"]["report"]
    rainfall = response["output"]["result"]["metrics"]["variables"]["rainfall"]
    assert response["status"] == "ok"
    assert rainfall["median"] == 1
    assert rainfall["n"] == 7
    assert "No tracked animals" in report
    assert "displacement" not in report
    assert "wildlife census" not in report


def test_constant_cell_stays_with_the_baseline(tmp_path):
    request, store = _forecast_request(tmp_path, days=80, value=lambda index: 5.0, cutoff_day=40)
    request["input"]["query"]["forecast"]["target"] = "cell_use"
    response = run(request, store=store)
    metrics = response["output"]["result"]["metrics"]
    assert response["status"] == "ok"
    assert metrics["shown_forecast"] == "baseline"
    assert metrics["baseline_accuracy"] == 1
    assert response["output"]["analysis_spec"]["baseline"] == "previous_cell"
    assert response["output"]["analysis_spec"]["method"] == "cell_use"


def test_alternating_cells_use_the_transition_model(tmp_path):
    request, store = _forecast_request(tmp_path, days=80, value=lambda index: float(index), cutoff_day=40, horizon=7)
    frame = pd.read_parquet(tmp_path / "forecast.parquet")
    frame["cell_id"] = ["c0" if index % 2 == 0 else "c1" for index in range(len(frame))]
    frame.to_parquet(tmp_path / "forecast.parquet", index=False)
    request["input"]["query"]["forecast"]["target"] = "cell_use"
    response = run(request, store=store)
    metrics = response["output"]["result"]["metrics"]
    steps = metrics["cell_forecast"][0]["steps"]
    kinds = {item["geometry"]["type"] for item in response["output"]["result"]["map"]["features"]}
    assert metrics["shown_forecast"] == "model"
    assert [step["cell_id"] for step in steps] == ["c0", "c1", "c0", "c1", "c0", "c1", "c0"]
    assert all(step["predicted"] is True for step in steps)
    assert kinds == {"Point"}
    assert "not a migration route" in response["output"]["result"]["report"]


def test_per_animal_model_can_beat_that_animals_baseline(tmp_path):
    request, store = _forecast_request(tmp_path, days=80, value=lambda index: float(index), cutoff_day=40)
    response = run(request, store=store)
    models = response["output"]["result"]["metrics"]["per_animal_models"]
    assert models[0]["presented"] == "model"
    assert "beat that animal's own baseline" in response["output"]["result"]["report"]

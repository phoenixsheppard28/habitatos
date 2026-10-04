import copy
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from analysis.forecast import FittedModel
from analysis.service import run
from test_forecast import _forecast_request
from support import movement_frame, movement_request, replace_frame
from workflow.coordinator import Coordinator


def test_constant_rainfall_scenario_is_not_applied_when_the_model_drops_it(tmp_path):
    dry, store = _forecast_request(tmp_path, days=80, value=lambda index: float(index), cutoff_day=40)
    dry["input"]["query"]["forecast"]["scenario"] = {"name": "drier", "rainfall_mm": 0}
    wet = copy.deepcopy(dry)
    wet["input"]["query"]["forecast"]["scenario"] = {"name": "wetter", "rainfall_mm": 40}
    dry_result = run(dry, store=store)["output"]["result"]
    wet_result = run(wet, store=store)["output"]["result"]
    assert dry_result["metrics"]["used_features"] == ["daily_displacement"]
    assert "does not apply it" in dry_result["report"]
    assert dry_result["metrics"]["forward"][0]["predicted_displacement_km"] == (
        wet_result["metrics"]["forward"][0]["predicted_displacement_km"]
    )


def test_recursive_error_is_reported_separately_from_the_one_step_score(tmp_path, monkeypatch):
    from analysis import forecast as forecast_module

    def persistence(train, test, feature_names, seed):
        del train, seed
        return FittedModel(
            mae=0.01,
            test_predictions=np.zeros(len(test)),
            coefficients={name: 1.0 for name in feature_names},
            intercept=0.0,
            feature_names=["daily_displacement"],
            scaler_mean=[0.0],
            scaler_scale=[1.0],
            predict=lambda row: float(row["daily_displacement"]),
        )

    monkeypatch.setattr(forecast_module, "fit_ridge", persistence)
    request, store = _forecast_request(tmp_path, days=80, value=lambda index: float(index), cutoff_day=40)
    metrics = run(request, store=store)["output"]["result"]["metrics"]
    assert metrics["shown_mae"] == 0.01
    assert metrics["recursive_mae"] > 1
    assert "Recursive holdout MAE" in run(request, store=store)["output"]["result"]["report"]


def test_one_animals_future_does_not_change_anothers_baseline(tmp_path):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for index in range(60):
        day = start + timedelta(days=index)
        rows.append(_row("A", day, 2.0))
        rows.append(_row("B", day, 100.0 if index >= 40 else 2.0))
    request, store = _forecast_request(tmp_path, days=60, value=lambda index: 2.0, cutoff_day=40)
    pd.DataFrame(rows).to_parquet(tmp_path / "forecast.parquet", index=False)
    request["input"]["feature_artifact"]["row_count"] = len(rows)
    end = (start + timedelta(days=59)).strftime("%Y-%m-%dT%H:%M:%SZ")
    request["input"]["query"]["time_range"]["end"] = end
    request["input"]["feature_artifact"]["coverage"]["end"] = end
    metrics = run(request, store=store)["output"]["result"]["metrics"]
    by_animal = {item["entity_id"]: item["one_step_mae"] for item in metrics["per_animal_one_step_mae"]}
    assert metrics["pooled_animals"] is True
    assert by_animal["A"] == 0


def test_species_column_filters_rows_and_can_refuse_the_table(tmp_path):
    request, store = movement_request(tmp_path)
    frame = movement_frame()
    frame["species"] = ["antelope" if animal == "A" else "zebra" for animal in frame["animal_id"]]
    replace_frame(tmp_path, request, frame)
    request["input"]["feature_artifact"]["columns"].append(
        {"name": "species", "type": "string", "nullable": False, "role": "species"}
    )
    response = run(request, store=store)
    assert response["output"]["result"]["metrics"]["n_observations"] == 3
    assert response["output"]["result"]["metrics"]["displacement_km_total"] == 20
    assert any("other species" in warning for warning in response["warnings"])

    frame["species"] = "zebra"
    replace_frame(tmp_path, request, frame)
    refused = run(request, store=store)
    assert refused["status"] == "insufficient_data"
    assert refused["output"]["result"]["code"] == "no_rows_for_species"
    assert refused["output"]["model_artifact"] is None


def test_population_migration_and_extinction_questions_produce_no_trajectory(tmp_path):
    request, store = movement_request(tmp_path)
    request["input"]["feature_artifact"]["storage"]["uri"] = "artifact://missing.parquet"
    request["input"]["query"]["question"] = "Where will the population migrate over the next month?"
    migration = run(request, store=store)
    assert migration["status"] == "insufficient_data"
    assert migration["output"]["result"]["code"] == "unsupported_question"
    assert migration["output"]["result"]["metrics"]["reason"] == "population_migration"
    assert "map" not in migration["output"]["result"]
    assert migration["output"]["model_artifact"] is None

    request["input"]["query"]["question"] = "Has this species gone extinct in the region?"
    extinction = run(request, store=store)
    assert extinction["output"]["result"]["metrics"]["reason"] == "extinction"
    assert "no finding was produced" in extinction["output"]["result"]["report"].lower()

    request["input"]["feature_artifact"]["storage"]["uri"] = "artifact://movement.parquet"
    request["input"]["query"]["question"] = "Where might these tracked antelopes move next week?"
    assert run(request, store=store)["status"] == "ok"


def test_random_tracks_keep_metric_invariants(tmp_path):
    rng = np.random.default_rng(7)
    start = datetime(2026, 3, 1, tzinfo=timezone.utc)
    rows = []
    for animal in ("A", "B", "C"):
        for index in range(12):
            missing_distance = rng.random() < 0.2
            missing_coord = rng.random() < 0.15
            rows.append(
                {
                    "animal_id": animal,
                    "date": start + timedelta(days=index),
                    "lon": None if missing_coord else float(rng.normal(10, 0.2)),
                    "lat": None if missing_coord else float(rng.normal(0, 0.2)),
                    "km_moved": None if missing_distance else float(rng.uniform(0, 8)),
                    "cell_id": "w1",
                    "rain_mm": float(rng.uniform(0, 5)),
                }
            )
    frame = pd.DataFrame(rows)
    request, store = movement_request(tmp_path)
    replace_frame(tmp_path, request, frame)
    end = (start + timedelta(days=11)).strftime("%Y-%m-%dT%H:%M:%SZ")
    request["input"]["query"]["time_range"] = {"start": "2026-03-01T00:00:00Z", "end": end}
    request["input"]["feature_artifact"]["coverage"] = {
        "species": ["antelope"],
        "start": "2026-03-01T00:00:00Z",
        "end": end,
    }
    result = run(request, store=store)["output"]["result"]
    values = frame["km_moved"].dropna()
    metrics = result["metrics"]
    assert metrics["displacement_km_total"] == pytest.approx(float(values.sum()))
    assert values.min() <= metrics["displacement_km_median"] <= values.max()
    points = [item for item in result["map"]["features"] if item["geometry"]["type"] == "Point"]
    assert len(points) + result["map"]["missing_coordinates"] == metrics["n_observations"]
    assert metrics["n_observations"] == len(frame)


def test_same_request_id_does_not_run_twice(tmp_path):
    request = movement_request(tmp_path)[0]
    calls = {"n": 0}

    def analyze(incoming):
        calls["n"] += 1
        return {
            "contract_version": "1.0",
            "request_id": incoming["request_id"],
            "query_id": incoming["query_id"],
            "access_scope": incoming["access_scope"],
            "status": "ok",
            "output": {"result": {"status": "complete", "metrics": {}, "findings": []}},
            "warnings": [],
            "error": None,
            "extensions": {},
        }

    coordinator = Coordinator(handlers={"analysis": analyze})
    first = coordinator.submit_query(request)
    repeat = copy.deepcopy(request)
    repeat["input"]["query"]["question"] = "Where will the population migrate?"
    second = coordinator.submit_query(repeat)
    assert second["job_id"] == first["job_id"]
    assert calls["n"] == 1
    assert second["query"]["question"] == request["input"]["query"]["question"]


def test_missing_cutoff_guarantee_has_no_model(tmp_path):
    request, store = _forecast_request(tmp_path, days=80, value=lambda index: float(index), cutoff_day=40)
    request["input"]["recipe"]["features_respect_cutoff"] = False
    response = run(request, store=store)
    assert response["output"]["result"]["code"] == "cutoff_not_guaranteed"
    assert response["output"]["model_artifact"] is None


def _row(animal, day, distance):
    return {
        "animal_id": animal,
        "date": day,
        "lon": 10.0,
        "lat": 0.0,
        "km_moved": distance,
        "cell_id": "w1",
        "rain_mm": 1.0,
    }


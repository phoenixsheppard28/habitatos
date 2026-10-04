import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from analysis.forecast import FittedModel, _forward
from analysis.service import run
from test_forecast import _forecast_request


def test_holdout_boundary_is_exact(tmp_path):
    enough, store = _forecast_request(tmp_path, days=80, value=lambda index: 5.0, cutoff_day=50)
    ready = run(enough, store=store)
    assert ready["status"] == "ok"
    assert ready["output"]["result"]["metrics"]["holdout_rows"] == 30

    short, short_store = _forecast_request(tmp_path, days=80, value=lambda index: 5.0, cutoff_day=51)
    refused = run(short, store=short_store)
    assert refused["output"]["result"]["code"] == "insufficient_holdout"
    assert refused["output"]["result"]["metrics"]["holdout_rows"] == 29


def test_training_boundary_is_exact(tmp_path):
    enough, store = _forecast_request(tmp_path, days=80, value=lambda index: float(index), cutoff_day=11)
    ready = run(enough, store=store)
    assert ready["status"] == "ok"
    assert ready["output"]["result"]["metrics"]["train_rows"] == 10

    short, short_store = _forecast_request(tmp_path, days=80, value=lambda index: 5.0, cutoff_day=10)
    refused = run(short, store=short_store)
    assert refused["output"]["result"]["code"] == "insufficient_train"
    assert refused["output"]["result"]["metrics"]["train_rows"] == 9


def test_a_gap_is_skipped_instead_of_joined_across_days(tmp_path):
    request, store = _forecast_request(tmp_path, days=80, value=lambda index: 5.0, cutoff_day=40)
    frame = pd.read_parquet(tmp_path / "forecast.parquet")
    frame["date"] = pd.to_datetime(frame["date"], utc=True)
    gap_day = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=10)
    frame = frame.loc[frame["date"] != gap_day]
    frame.to_parquet(tmp_path / "forecast.parquet", index=False)
    request["input"]["feature_artifact"]["row_count"] = len(frame)
    metrics = run(request, store=store)["output"]["result"]["metrics"]
    assert metrics["skipped_gap_rows"] == 1
    assert metrics["holdout_rows"] >= 30


def test_two_animals_stay_separate_in_the_baseline(tmp_path):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for animal, pace in (("A", 2.0), ("B", 8.0)):
        for index in range(50):
            rows.append(
                {
                    "animal_id": animal,
                    "date": start + timedelta(days=index),
                    "lon": 10.0,
                    "lat": 0.0,
                    "km_moved": pace,
                    "cell_id": "w1",
                    "rain_mm": 1.0,
                }
            )
    request, store = _forecast_request(tmp_path, days=50, value=lambda index: 1.0, cutoff_day=15)
    pd.DataFrame(rows).to_parquet(tmp_path / "forecast.parquet", index=False)
    request["input"]["feature_artifact"]["row_count"] = len(rows)
    request["input"]["query"]["time_range"]["end"] = (start + timedelta(days=49)).strftime("%Y-%m-%dT%H:%M:%SZ")
    request["input"]["feature_artifact"]["coverage"]["end"] = request["input"]["query"]["time_range"]["end"]
    result = run(request, store=store)["output"]["result"]
    assert result["metrics"]["n_animals"] == 2
    assert result["metrics"]["baseline_mae"] == 0
    by_animal = {}
    for point in result["metrics"]["forward"]:
        by_animal.setdefault(point["entity_id"], []).append(point["predicted_displacement_km"])
    assert by_animal["A"] == [2.0] * 7
    assert by_animal["B"] == [8.0] * 7
    assert len(result["map"]["features"]) == 2
    assert {item["properties"]["entity_id"] for item in result["map"]["features"]} == {"A", "B"}


def test_uncertainty_band_widens_with_the_square_root_of_the_step():
    frame = pd.DataFrame(
        {
            "animal_id": ["A"],
            "date": [pd.Timestamp("2026-01-01T00:00:00Z")],
            "km": [5.0],
        }
    )
    roles = {
        "entity_id": type("Column", (), {"name": "animal_id"})(),
        "event_time": type("Column", (), {"name": "date"})(),
        "daily_displacement": type("Column", (), {"name": "km", "unit": "km"})(),
    }
    points = _forward(frame, roles, 4, "baseline", None, -2.0, 4.0, None, ["daily_displacement"])
    widths = [point["high_km"] - point["low_km"] for point in points]
    assert widths[0] == pytest.approx(6)
    assert widths[3] == pytest.approx(12)


def test_forward_series_starts_after_the_last_observation_and_widens(tmp_path):
    request, store = _forecast_request(tmp_path, days=80, value=lambda index: float(index), cutoff_day=40, horizon=4)
    result = run(request, store=store)["output"]["result"]
    last_observed = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=79)
    forward = result["metrics"]["forward"]
    assert forward[0]["date"] == (last_observed + timedelta(days=1)).date().isoformat()
    assert [point["date"] for point in forward] == [
        (last_observed + timedelta(days=step)).date().isoformat() for step in range(1, 5)
    ]
    predicted = [row for row in result["timeline"]["series"] if row["predicted"]]
    observed = [row for row in result["timeline"]["series"] if not row["predicted"]]
    assert len(predicted) == 4
    assert observed[-1]["date"] < predicted[0]["date"]
    assert all(item["geometry"]["type"] == "Point" for item in result["map"]["features"])


def test_maximum_horizon_is_accepted_and_one_day_past_it_is_not(tmp_path):
    request, store = _forecast_request(tmp_path, days=80, value=lambda index: 5.0, cutoff_day=40, horizon=30)
    assert len(run(request, store=store)["output"]["result"]["metrics"]["forward"]) == 30

    request["input"]["query"]["forecast"]["horizon_days"] = 31
    assert run(request, store=store)["error"]["code"] == "invalid_request"


def test_cutoff_mismatch_and_unsupported_target(tmp_path):
    request, store = _forecast_request(tmp_path, days=80, value=lambda index: 5.0, cutoff_day=40)
    request["input"]["feature_artifact"]["storage"]["uri"] = "artifact://missing.parquet"
    shifted = json.loads(json.dumps(request))
    shifted["input"]["recipe"]["cutoff"] = "2026-03-01T00:00:00Z"
    assert run(shifted, store=store)["error"]["code"] == "cutoff_mismatch"

    equivalent = json.loads(json.dumps(request))
    equivalent["input"]["recipe"]["cutoff"] = request["input"]["query"]["forecast"]["cutoff"].replace("Z", "+00:00")
    equivalent["input"]["feature_artifact"]["storage"]["uri"] = "artifact://forecast.parquet"
    assert run(equivalent, store=store)["status"] == "ok"

    unsupported = json.loads(json.dumps(request))
    unsupported["input"]["query"]["forecast"]["target"] = "arrival_date"
    refused = run(unsupported, store=store)
    assert refused["output"]["result"]["code"] == "unsupported_target"
    assert refused["output"]["model_artifact"] is None


def test_saved_model_records_intended_use_and_is_stable(tmp_path):
    request, store = _forecast_request(tmp_path, days=80, value=lambda index: float(index), cutoff_day=40)
    first = run(request, store=store, seed=1)
    second = run(request, store=store, seed=1)
    artifact = first["output"]["model_artifact"]
    assert artifact["artifact_id"] == second["output"]["model_artifact"]["artifact_id"]
    assert artifact["evaluation"]["presented"] is True
    assert artifact["evaluation"]["beat_baseline"] is True
    assert "not a migration route" in artifact["intended_use"]
    assert artifact["access_scope"] == "public"
    saved = json.loads(store.resolve(artifact["storage"]["uri"]).read_text())
    assert saved["evaluation"]["presented"] is True
    spec = first["output"]["analysis_spec"]
    assert spec["baseline"] == "recent_mean_7d"
    assert spec["seed"] == 1
    assert spec["temporal_split"]["holdout"] == "target_day >= cutoff"
    changed_seed = run(request, store=store, seed=2)["output"]["model_artifact"]["artifact_id"]
    assert changed_seed != artifact["artifact_id"]


def test_scenario_changes_the_forward_prediction_only_when_the_model_uses_rainfall(tmp_path, monkeypatch):
    from analysis import forecast as forecast_module

    def fake_fit(train, test, feature_names, seed):
        del train, seed
        return FittedModel(
            mae=0.01,
            test_predictions=np.zeros(len(test)),
            coefficients={name: 1.0 for name in feature_names},
            intercept=0.0,
            feature_names=feature_names,
            scaler_mean=[0.0] * len(feature_names),
            scaler_scale=[1.0] * len(feature_names),
            predict=lambda row: float(row["rainfall"]) * 10 + float(row["daily_displacement"]),
        )

    monkeypatch.setattr(forecast_module, "fit_ridge", fake_fit)
    dry, dry_store = _forecast_request(tmp_path, days=80, value=lambda index: float(index + 1), cutoff_day=40)
    dry["input"]["query"]["forecast"]["scenario"] = {"name": "drier", "rainfall_mm": 0}
    wet = json.loads(json.dumps(dry))
    wet["input"]["query"]["forecast"]["scenario"] = {"name": "wetter", "rainfall_mm": 5}
    dry_result = run(dry, store=dry_store)["output"]["result"]
    wet_result = run(wet, store=dry_store)["output"]["result"]
    assert dry_result["metrics"]["shown_forecast"] == "model"
    assert "constant assumption" in dry_result["report"]
    assert dry_result["metrics"]["forward"][0]["predicted_displacement_km"] != (
        wet_result["metrics"]["forward"][0]["predicted_displacement_km"]
    )

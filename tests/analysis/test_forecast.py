from datetime import datetime, timedelta, timezone

import pandas as pd

from analysis.forecast import select_presented
from analysis.service import run
from analysis.store import ArtifactStore
from support import movement_request


def test_select_presented_hides_a_tie_or_a_loss():
    assert select_presented(1.0, 0.5) == "model"
    assert select_presented(1.0, 1.0) == "baseline"
    assert select_presented(1.0, 2.0) == "baseline"
    assert select_presented(1.0, None) == "baseline"


def test_constant_series_uses_baseline(tmp_path):
    request, store = _forecast_request(tmp_path, days=80, value=lambda index: 5.0, cutoff_day=40)
    response = run(request, store=store)
    assert response["status"] == "ok"
    metrics = response["output"]["result"]["metrics"]
    assert metrics["shown_forecast"] == "baseline"
    assert metrics["baseline_mae"] == 0
    assert metrics["model_status"] == "not_fit"
    assert response["output"]["model_artifact"] is None
    assert metrics["holdout_rows"] >= 30
    assert all(point["predicted_displacement_km"] == 5 for point in metrics["forward"])
    kinds = {item["properties"]["kind"] for item in response["output"]["result"]["map"]["features"]}
    assert kinds == {"last_observed"}
    assert "not a migration route" in response["output"]["result"]["report"]
    assert "recent-mean baseline" in response["output"]["result"]["report"]


def test_ramp_model_beats_baseline(tmp_path):
    request, store = _forecast_request(tmp_path, days=80, value=lambda index: float(index), cutoff_day=40)
    response = run(request, store=store)
    metrics = response["output"]["result"]["metrics"]
    assert response["status"] == "ok"
    assert metrics["shown_forecast"] == "model"
    assert metrics["model_mae"] + 1 < metrics["baseline_mae"]
    assert response["output"]["model_artifact"]["evaluation"]["presented"] is True
    assert len(metrics["forward"]) == 7


def test_worse_model_is_saved_but_not_shown(tmp_path, monkeypatch):
    from analysis import forecast as forecast_module

    def fake_fit(train, test, feature_names, seed):
        import numpy as np

        from analysis.forecast import FittedModel

        predictions = np.full(len(test), 1000.0)
        return FittedModel(
            mae=float(np.mean(np.abs(test["target"].to_numpy() - predictions))),
            test_predictions=predictions,
            coefficients={name: 0.0 for name in feature_names},
            intercept=1000.0,
            feature_names=feature_names,
            scaler_mean=[0.0] * len(feature_names),
            scaler_scale=[1.0] * len(feature_names),
            predict=lambda row: 1000.0,
        )

    monkeypatch.setattr(forecast_module, "fit_ridge", fake_fit)
    request, store = _forecast_request(tmp_path, days=80, value=lambda index: float(index), cutoff_day=40)
    response = run(request, store=store)
    result = response["output"]["result"]
    assert result["metrics"]["shown_forecast"] == "baseline"
    assert "recent-mean baseline" in result["report"]
    assert response["output"]["model_artifact"]["evaluation"]["presented"] is False
    assert result["metrics"]["forward"][0]["predicted_displacement_km"] != 1000


def test_short_holdout_is_insufficient(tmp_path):
    request, store = _forecast_request(tmp_path, days=20, value=lambda index: 5.0, cutoff_day=15)
    response = run(request, store=store)
    assert response["status"] == "insufficient_data"
    assert response["output"]["result"]["code"] == "insufficient_holdout"
    assert response["output"]["model_artifact"] is None


def test_missing_cutoff_guarantee(tmp_path):
    request, store = _forecast_request(tmp_path, days=80, value=lambda index: 5.0, cutoff_day=40)
    request["input"]["recipe"]["features_respect_cutoff"] = False
    request["input"]["feature_artifact"]["storage"]["uri"] = "artifact://missing.parquet"
    response = run(request, store=store)
    assert response["status"] == "insufficient_data"
    assert response["output"]["result"]["code"] == "cutoff_not_guaranteed"


def test_a_non_numeric_scenario_is_ignored(tmp_path):
    request, store = _forecast_request(tmp_path, days=80, value=lambda index: 5.0, cutoff_day=40)
    request["input"]["query"]["forecast"]["scenario"] = {"name": "drier", "rainfall_mm": "wet"}
    response = run(request, store=store)
    assert response["status"] == "ok"
    assert "not a number" in response["output"]["result"]["report"]


def test_scenario_is_an_assumption_for_the_baseline(tmp_path):
    request, store = _forecast_request(tmp_path, days=80, value=lambda index: 5.0, cutoff_day=40)
    request["input"]["query"]["forecast"]["scenario"] = {"name": "drier", "rainfall_mm": 0}
    response = run(request, store=store)
    assert "does not apply it" in response["output"]["result"]["report"]


def _forecast_request(tmp_path, *, days, value, cutoff_day, horizon=7):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    cutoff = start + timedelta(days=cutoff_day)
    end = start + timedelta(days=days - 1)
    rows = []
    for index in range(days):
        day = start + timedelta(days=index)
        rows.append(
            {
                "animal_id": "A",
                "date": day,
                "lon": 10.0,
                "lat": 0.0,
                "km_moved": value(index),
                "cell_id": "w1",
                "rain_mm": 1.0,
            }
        )
    frame = pd.DataFrame(rows)
    frame.to_parquet(tmp_path / "forecast.parquet", index=False)
    request, _store = movement_request(tmp_path)
    request["input"]["query"]["task_type"] = "forecast"
    request["input"]["query"]["time_range"] = {
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    request["input"]["query"]["forecast"] = {
        "cutoff": cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "horizon_days": horizon,
        "target": "next_day_displacement",
        "scenario": None,
    }
    request["input"]["recipe"]["cutoff"] = request["input"]["query"]["forecast"]["cutoff"]
    request["input"]["recipe"]["features_respect_cutoff"] = True
    artifact = request["input"]["feature_artifact"]
    artifact["storage"]["uri"] = "artifact://forecast.parquet"
    artifact["row_count"] = days
    artifact["coverage"] = {
        "species": ["antelope"],
        "start": request["input"]["query"]["time_range"]["start"],
        "end": request["input"]["query"]["time_range"]["end"],
    }
    return request, ArtifactStore(tmp_path)

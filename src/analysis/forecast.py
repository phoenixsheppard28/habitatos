"""Next-day displacement forecasts with a baseline and a holdout."""

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from analysis.constants import (
    FORECAST_TARGET_LIMITATION,
    HOLDOUT_LIMITATION,
    MIN_HOLDOUT_ROWS,
    MIN_TRAIN_ROWS,
    SAMPLE_LIMITATION,
)
from analysis.report import fmt


@dataclass
class FittedModel:
    mae: float
    test_predictions: np.ndarray
    coefficients: dict[str, float]
    intercept: float
    feature_names: list[str]
    scaler_mean: list[float]
    scaler_scale: list[float]
    predict: Callable[[dict], float]


def select_presented(baseline_mae: float, model_mae: float | None) -> str:
    if model_mae is None:
        return "baseline"
    if model_mae < baseline_mae - 1e-9:
        return "model"
    return "baseline"


def forecast_next_day(frame: pd.DataFrame, roles: dict, cutoff, horizon_days: int, scenario: dict | None, seed: int) -> dict:
    entity = roles["entity_id"].name
    event_time = roles["event_time"].name
    displacement = roles["daily_displacement"].name
    unit = roles["daily_displacement"].unit or "unknown"
    cutoff_ts = pd.Timestamp(cutoff)
    if cutoff_ts.tzinfo is None:
        cutoff_ts = cutoff_ts.tz_localize("UTC")
    else:
        cutoff_ts = cutoff_ts.tz_convert("UTC")

    examples, skipped_gaps = _examples(frame, entity, event_time, displacement, roles)
    train = examples.loc[examples["target_day"] < cutoff_ts].copy()
    test = examples.loc[examples["target_day"] >= cutoff_ts].copy()
    counts = {
        "train_rows": int(len(train)),
        "holdout_rows": int(len(test)),
        "skipped_gap_rows": skipped_gaps,
        "n_animals": int(frame[entity].nunique()),
        "displacement_unit": unit,
    }
    if len(train) < MIN_TRAIN_ROWS:
        return _insufficient(
            "insufficient_train",
            f"Need at least {MIN_TRAIN_ROWS} training animal-days before the cutoff; found {len(train)}.",
            counts,
        )
    if len(test) < MIN_HOLDOUT_ROWS:
        return _insufficient(
            "insufficient_holdout",
            f"Need at least {MIN_HOLDOUT_ROWS} holdout animal-days to score a next-day forecast; found {len(test)}.",
            counts,
        )

    feature_names = _feature_names(train, roles)
    train = train.dropna(subset=feature_names)
    test = test.dropna(subset=feature_names).copy()
    counts["train_rows"] = int(len(train))
    counts["holdout_rows"] = int(len(test))
    if len(train) < MIN_TRAIN_ROWS:
        return _insufficient(
            "insufficient_train",
            f"Need at least {MIN_TRAIN_ROWS} training animal-days before the cutoff; found {len(train)}.",
            counts,
        )
    if len(test) < MIN_HOLDOUT_ROWS:
        return _insufficient(
            "insufficient_holdout",
            f"Need at least {MIN_HOLDOUT_ROWS} holdout animal-days to score a next-day forecast; found {len(test)}.",
            counts,
        )
    test["baseline_prediction"] = _baseline_predictions(examples, test.index)
    if test["baseline_prediction"].isna().any():
        return _insufficient("baseline_unavailable", "The recent-mean baseline could not be computed for every holdout row.", counts)

    baseline_mae = float(np.mean(np.abs(test["target"] - test["baseline_prediction"])))
    fitted = fit_ridge(train, test, feature_names, seed) if feature_names else None
    model_mae = None if fitted is None else fitted.mae
    presented = select_presented(baseline_mae, model_mae)
    if presented == "model":
        shown_predictions = fitted.test_predictions
        shown_mae = model_mae
        label = "linear model"
    else:
        shown_predictions = test["baseline_prediction"].to_numpy()
        shown_mae = baseline_mae
        label = "recent-mean baseline"

    residuals = test["target"].to_numpy() - shown_predictions
    residual_p10 = float(np.quantile(residuals, 0.1))
    residual_p90 = float(np.quantile(residuals, 0.9))
    used_features = list(fitted.feature_names) if presented == "model" and fitted is not None else []
    forward = _forward(
        frame,
        roles,
        horizon_days,
        presented,
        fitted,
        residual_p10,
        residual_p90,
        scenario,
        used_features,
    )
    uses_scenario = presented == "model" and _scenario_rain(scenario) is not None and "rainfall" in used_features
    recursive_mae = _recursive_mae(examples, cutoff_ts, presented, fitted, used_features)
    per_animal = _per_animal_one_step(test, shown_predictions)
    limitations = [SAMPLE_LIMITATION, FORECAST_TARGET_LIMITATION, HOLDOUT_LIMITATION]
    if scenario:
        if uses_scenario:
            limitations.append(
                "The forward series uses the supplied rainfall scenario as a constant assumption. It is not a weather forecast."
            )
        else:
            limitations.append(
                "The rainfall scenario is an assumption supplied with the question. "
                "This forecast does not apply it, because the presented method does not use rainfall."
            )
    limitations.append("The model pools tracked animals in this sample. It is not an individual route.")

    metrics = {
        **counts,
        "baseline_mae": baseline_mae,
        "model_mae": model_mae,
        "shown_mae": shown_mae,
        "shown_forecast": presented,
        "model_status": "fit" if fitted else "not_fit",
        "holdout_residual_p10": residual_p10,
        "holdout_residual_p90": residual_p90,
        "horizon_days": horizon_days,
        "cutoff": cutoff_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "forward": forward,
        "missing_displacement_fraction": float(frame[displacement].isna().mean()),
        "one_step_mae": shown_mae,
        "recursive_mae": recursive_mae,
        "pooled_animals": True,
        "used_features": used_features,
        "per_animal_one_step_mae": per_animal,
        "per_animal_models": _fit_own_models(examples, cutoff_ts, feature_names, seed, per_animal),
    }
    finding = (
        f"Experimental next-day displacement forecast for {counts['n_animals']} tracked animals, "
        f"horizon {horizon_days} days. Holdout MAE is {fmt(shown_mae)} {unit} "
        f"({counts['holdout_rows']} animal-days) against a baseline MAE of {fmt(baseline_mae)} {unit}. "
        f"The shown forecast is the {label}. "
        f"Recursive holdout MAE is {fmt(recursive_mae)} {unit}; "
        "it feeds predicted displacement forward instead of the observed value."
    )
    own_models = [row for row in metrics["per_animal_models"] if row["presented"] == "model"]
    if own_models:
        names = ", ".join(row["entity_id"] for row in own_models)
        finding += f" A model fitted only on {names} beat that animal's own baseline."
    return {
        "status": "complete",
        "code": None,
        "findings": [finding],
        "metrics": metrics,
        "limitations": limitations,
        "warnings": [],
        "map": _last_points(frame, roles),
        "timeline": _forecast_timeline(frame, roles, forward, unit),
        "fitted": fitted,
        "presented": presented,
        "feature_names": feature_names,
        "used_features": used_features,
    }


def fit_ridge(train: pd.DataFrame, test: pd.DataFrame, feature_names: list[str], seed: int) -> FittedModel | None:
    usable = [name for name in feature_names if float(train[name].std(skipna=True) or 0) > 1e-8]
    if not usable:
        return None
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    pipe = Pipeline([("scaler", StandardScaler()), ("model", Ridge(alpha=1.0, random_state=seed))])
    pipe.fit(train[usable], train["target"])
    predictions = pipe.predict(test[usable])
    scaler = pipe.named_steps["scaler"]
    model = pipe.named_steps["model"]
    coefficients = {name: float(coef) for name, coef in zip(usable, model.coef_, strict=True)}

    def predict(row: dict) -> float:
        values = pd.DataFrame([{name: row[name] for name in usable}])
        return float(pipe.predict(values)[0])

    return FittedModel(
        mae=float(np.mean(np.abs(test["target"].to_numpy() - predictions))),
        test_predictions=predictions,
        coefficients=coefficients,
        intercept=float(model.intercept_),
        feature_names=usable,
        scaler_mean=[float(value) for value in scaler.mean_],
        scaler_scale=[float(value) for value in scaler.scale_],
        predict=predict,
    )


def _examples(frame, entity, event_time, displacement, roles):
    work = frame.sort_values([entity, event_time]).copy()
    work["_entity"] = work[entity]
    work["feature_day"] = work[event_time].dt.floor("D")
    grouped = work.groupby("_entity", sort=False)
    work["target_day"] = grouped["feature_day"].shift(-1)
    work["target"] = grouped[displacement].shift(-1)
    work["daily_displacement"] = work[displacement]
    for role in ("rainfall", "vegetation_index"):
        if role in roles:
            work[role] = work[roles[role].name]
    consecutive = work["target_day"] == work["feature_day"] + pd.Timedelta(days=1)
    skipped = int((~consecutive & work["target_day"].notna()).sum())
    examples = work.loc[consecutive & work["target"].notna() & work["daily_displacement"].notna()].copy()
    return examples, skipped


def _feature_names(train: pd.DataFrame, roles: dict) -> list[str]:
    names = ["daily_displacement"]
    for role in ("rainfall", "vegetation_index"):
        if role in roles and role in train.columns and train[role].notna().any():
            names.append(role)
    return names


def _baseline_predictions(examples: pd.DataFrame, test_index) -> pd.Series:
    predictions = pd.Series(index=examples.index, dtype=float)
    for _, group in examples.groupby("_entity", sort=False):
        ordered = group.sort_values("feature_day")
        series = ordered.set_index("feature_day")["daily_displacement"]
        rolled = series.rolling("7D", min_periods=1).mean()
        predictions.loc[ordered.index] = rolled.to_numpy()
    return predictions.loc[test_index]


def _forward(frame, roles, horizon_days, presented, fitted, residual_p10, residual_p90, scenario, feature_names):
    entity = roles["entity_id"].name
    event_time = roles["event_time"].name
    displacement = roles["daily_displacement"].name
    last_day = frame[event_time].max()
    recent = frame.loc[frame[displacement].notna()].sort_values(event_time)
    window_start = last_day - pd.Timedelta(days=7)
    window = recent.loc[recent[event_time] > window_start, displacement]
    baseline_value = float(window.mean()) if len(window) else float(recent[displacement].iloc[-1])
    last_row = recent.iloc[-1]
    current = {"daily_displacement": float(last_row[displacement])}
    for role in ("rainfall", "vegetation_index"):
        if role in feature_names:
            current[role] = float(last_row[roles[role].name]) if pd.notna(last_row[roles[role].name]) else 0.0
    scenario_rain = _scenario_rain(scenario)
    points = []
    for step in range(1, horizon_days + 1):
        if presented == "model" and fitted is not None:
            row = dict(current)
            if scenario_rain is not None and "rainfall" in feature_names:
                row["rainfall"] = scenario_rain
            predicted = float(fitted.predict(row))
            current["daily_displacement"] = predicted
        else:
            predicted = baseline_value
        scale = math.sqrt(step)
        day = (pd.Timestamp(last_day).tz_convert("UTC") + pd.Timedelta(days=step)).date().isoformat()
        points.append(
            {
                "date": day,
                "predicted_displacement_km": predicted,
                "low_km": predicted + residual_p10 * scale,
                "high_km": predicted + residual_p90 * scale,
                "predicted": True,
            }
        )
    return points


def _per_animal_one_step(test: pd.DataFrame, predictions) -> list[dict]:
    frame = test.copy()
    frame["_pred"] = np.asarray(predictions)
    rows = []
    for entity, group in frame.groupby("_entity", sort=True):
        error = np.abs(group["target"].to_numpy() - group["_pred"].to_numpy())
        baseline_error = np.abs(group["target"].to_numpy() - group["baseline_prediction"].to_numpy())
        rows.append(
            {
                "entity_id": str(entity),
                "one_step_mae": float(np.mean(error)),
                "baseline_mae": float(np.mean(baseline_error)),
                "n": int(len(group)),
            }
        )
    return rows


def _fit_own_models(examples, cutoff_ts, feature_names, seed, per_animal) -> list[dict]:
    from analysis.constants import MIN_OWN_HOLDOUT, MIN_TRAIN_ROWS

    baseline = {row["entity_id"]: row["baseline_mae"] for row in per_animal}
    models = []
    for entity, group in examples.groupby("_entity", sort=True):
        entity_id = str(entity)
        if entity_id not in baseline:
            continue
        train = group.loc[group["target_day"] < cutoff_ts].dropna(subset=feature_names)
        holdout = group.loc[group["target_day"] >= cutoff_ts].dropna(subset=feature_names)
        if len(train) < MIN_TRAIN_ROWS or len(holdout) < MIN_OWN_HOLDOUT:
            continue
        fitted = fit_ridge(train, holdout, feature_names, seed)
        model_mae = None if fitted is None else fitted.mae
        presented = "model" if model_mae is not None and model_mae < baseline[entity_id] - 1e-9 else "baseline"
        models.append(
            {
                "entity_id": entity_id,
                "baseline_mae": baseline[entity_id],
                "model_mae": model_mae,
                "presented": presented,
                "n_holdout": int(len(holdout)),
            }
        )
    return models


def _recursive_mae(examples, cutoff_ts, presented, fitted, used_features) -> float | None:
    errors = []
    for _, group in examples.groupby("_entity", sort=False):
        ordered = group.sort_values("feature_day")
        history = ordered.loc[ordered["feature_day"] < cutoff_ts]
        holdout = ordered.loc[ordered["target_day"] >= cutoff_ts]
        if history.empty or holdout.empty:
            continue
        if presented == "model" and fitted is not None:
            origin = history.iloc[-1]
            state = {}
            for name in used_features:
                if pd.isna(origin[name]):
                    state = None
                    break
                state[name] = float(origin[name])
            if state is None:
                continue
            for _, row in holdout.iterrows():
                predicted = float(fitted.predict(state))
                errors.append(abs(float(row["target"]) - predicted))
                if "daily_displacement" in state:
                    state["daily_displacement"] = predicted
            continue
        last = history["feature_day"].max()
        window = history.loc[history["feature_day"] > last - pd.Timedelta(days=7), "daily_displacement"]
        anchor = float(window.mean())
        errors.extend(abs(float(value) - anchor) for value in holdout["target"])
    if not errors:
        return None
    return float(np.mean(errors))


def _scenario_rain(scenario: dict | None) -> float | None:
    if not scenario or "rainfall_mm" not in scenario or scenario["rainfall_mm"] is None:
        return None
    return float(scenario["rainfall_mm"])


def _last_points(frame, roles) -> dict:
    if "longitude" not in roles or "latitude" not in roles:
        return {"type": "FeatureCollection", "features": []}
    entity = roles["entity_id"].name
    event_time = roles["event_time"].name
    lon = roles["longitude"].name
    lat = roles["latitude"].name
    features = []
    for animal, group in frame.groupby(entity, sort=True):
        row = group.sort_values(event_time).iloc[-1]
        if pd.isna(row[lon]) or pd.isna(row[lat]):
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [float(row[lon]), float(row[lat])]},
                "properties": {
                    "entity_id": str(animal),
                    "observed_at": pd.Timestamp(row[event_time]).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "kind": "last_observed",
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def _forecast_timeline(frame, roles, forward, unit) -> dict:
    from analysis.historical import _timeline

    timeline = _timeline(
        frame,
        roles["entity_id"].name,
        roles["event_time"].name,
        roles["daily_displacement"].name,
        unit,
    )
    for point in forward:
        timeline["series"].append(
            {
                "date": point["date"],
                "median_daily_displacement": point["predicted_displacement_km"],
                "unit": unit,
                "n_animals": None,
                "predicted": True,
                "low": point["low_km"],
                "high": point["high_km"],
            }
        )
    return timeline


def _insufficient(code: str, message: str, counts: dict) -> dict:
    return {
        "status": "insufficient_data",
        "code": code,
        "message": message,
        "findings": [],
        "metrics": counts,
        "limitations": [SAMPLE_LIMITATION],
        "warnings": [],
        "map": None,
        "timeline": None,
        "fitted": None,
        "presented": None,
        "feature_names": [],
    }

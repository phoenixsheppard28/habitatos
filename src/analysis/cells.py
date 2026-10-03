"""Next-cell forecast for tracked animals. Not a migration route."""

from collections import Counter, defaultdict

import pandas as pd

from analysis.constants import (
    CELL_USE_METHOD,
    CELL_USE_VERSION,
    MIN_HOLDOUT_ROWS,
    MIN_TRAIN_ROWS,
    SAMPLE_LIMITATION,
)
from analysis.report import fmt


def forecast_cell_use(frame: pd.DataFrame, roles: dict, cutoff, horizon_days: int) -> dict:
    entity = roles["entity_id"].name
    event_time = roles["event_time"].name
    cell = roles["cell_id"].name
    cutoff_ts = pd.Timestamp(cutoff)
    if cutoff_ts.tzinfo is None:
        cutoff_ts = cutoff_ts.tz_localize("UTC")
    else:
        cutoff_ts = cutoff_ts.tz_convert("UTC")
    examples = _examples(frame, entity, event_time, cell)
    train = examples.loc[examples["target_day"] < cutoff_ts]
    test = examples.loc[examples["target_day"] >= cutoff_ts]
    counts = {"train_rows": int(len(train)), "holdout_rows": int(len(test)), "n_animals": int(frame[entity].nunique())}
    if len(train) < MIN_TRAIN_ROWS:
        return _insufficient("insufficient_train", f"Need at least {MIN_TRAIN_ROWS} training animal-days; found {len(train)}.", counts)
    if len(test) < MIN_HOLDOUT_ROWS:
        return _insufficient(
            "insufficient_holdout",
            f"Need at least {MIN_HOLDOUT_ROWS} holdout animal-days to score cell use; found {len(test)}.",
            counts,
        )
    baseline_accuracy = float((test["cell"] == test["target_cell"]).mean())
    transitions = _transitions(train)
    predicted = test["cell"].map(lambda value: transitions.get(value, value))
    model_accuracy = float((predicted.to_numpy() == test["target_cell"].to_numpy()).mean())
    presented = "model" if model_accuracy > baseline_accuracy + 1e-9 else "baseline"
    label = "cell-transition model" if presented == "model" else "previous-cell baseline"
    shown = model_accuracy if presented == "model" else baseline_accuracy
    forecast = _forward(frame, roles, transitions if presented == "model" else {}, horizon_days)
    per_animal = []
    for animal, group in test.groupby("animal", sort=True):
        choice = group["cell"].map(lambda value: transitions.get(value, value)) if presented == "model" else group["cell"]
        per_animal.append(
            {
                "entity_id": str(animal),
                "accuracy": float((choice.to_numpy() == group["target_cell"].to_numpy()).mean()),
                "n": int(len(group)),
            }
        )
    metrics = {
        **counts,
        "baseline_accuracy": baseline_accuracy,
        "model_accuracy": model_accuracy,
        "shown_accuracy": shown,
        "shown_forecast": presented,
        "cell_forecast": forecast,
        "per_animal_accuracy": per_animal,
        "pooled_animals": True,
        "cutoff": cutoff_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    finding = (
        f"Experimental next-cell forecast for {counts['n_animals']} tracked animals, horizon {horizon_days} days. "
        f"Holdout accuracy is {fmt(shown)} ({counts['holdout_rows']} animal-days) against a previous-cell "
        f"baseline accuracy of {fmt(baseline_accuracy)}. The shown forecast is the {label}. "
        "This is not a migration route."
    )
    return {
        "status": "complete",
        "code": None,
        "findings": [finding],
        "metrics": metrics,
        "limitations": [
            SAMPLE_LIMITATION,
            "The target is the next observed cell of tracked animals, not where a population will move.",
        ],
        "warnings": [],
        "map": _last_points(frame, roles),
        "timeline": {"series": []},
        "fitted": None,
        "presented": presented,
        "used_features": ["cell_id"],
        "method_name": CELL_USE_METHOD,
        "method_version": CELL_USE_VERSION,
        "baseline_name": "previous_cell",
        "parameters": {"baseline": "previous_cell", "n_known_transitions": len(transitions)},
    }


def _examples(frame, entity, event_time, cell) -> pd.DataFrame:
    work = frame.sort_values([entity, event_time]).copy()
    work["animal"] = work[entity].astype(str)
    work["feature_day"] = work[event_time].dt.floor("D")
    grouped = work.groupby("animal", sort=False)
    work["target_day"] = grouped["feature_day"].shift(-1)
    work["target_cell"] = grouped[cell].shift(-1)
    work["cell"] = work[cell].astype(str)
    consecutive = work["target_day"] == work["feature_day"] + pd.Timedelta(days=1)
    return work.loc[consecutive & work["target_cell"].notna() & work["cell"].notna(), ["animal", "cell", "target_cell", "target_day", "feature_day"]].copy()


def _transitions(train: pd.DataFrame) -> dict[str, str]:
    counts: dict[str, Counter] = defaultdict(Counter)
    for current, target in zip(train["cell"], train["target_cell"], strict=True):
        counts[str(current)][str(target)] += 1
    return {current: counter.most_common(1)[0][0] for current, counter in counts.items()}


def _forward(frame, roles, transitions: dict[str, str], horizon_days: int) -> list[dict]:
    entity = roles["entity_id"].name
    event_time = roles["event_time"].name
    cell = roles["cell_id"].name
    rows = []
    for animal, group in frame.groupby(entity, sort=True):
        ordered = group.sort_values(event_time)
        last = ordered.iloc[-1]
        if pd.isna(last[cell]):
            continue
        current = str(last[cell])
        day = pd.Timestamp(last[event_time]).tz_convert("UTC")
        steps = []
        for step in range(1, horizon_days + 1):
            current = transitions.get(current, current)
            steps.append(
                {
                    "date": (day + pd.Timedelta(days=step)).date().isoformat(),
                    "cell_id": current,
                    "predicted": True,
                }
            )
        rows.append({"entity_id": str(animal), "steps": steps})
    return rows


def _last_points(frame, roles) -> dict:
    if "longitude" not in roles or "latitude" not in roles:
        return {"type": "FeatureCollection", "features": []}
    from analysis.forecast import _last_points as points

    return points(frame, roles)


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
        "used_features": [],
        "method_name": CELL_USE_METHOD,
        "method_version": CELL_USE_VERSION,
        "baseline_name": "previous_cell",
        "parameters": {},
    }

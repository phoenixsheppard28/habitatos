"""Compare two explicit date windows on the tracked sample."""

import pandas as pd

from analysis.prepare import as_utc
from analysis.report import fmt


def window_comparison(frame: pd.DataFrame, roles: dict, windows: list) -> dict:
    displacement = roles["daily_displacement"].name
    entity = roles["entity_id"].name
    event_time = roles["event_time"].name
    unit = roles["daily_displacement"].unit
    blocks = [_one_window(frame, window, entity, event_time, displacement, unit) for window in windows]
    first, second = blocks
    finding = (
        f"{_window_phrase(first, unit)} {_window_phrase(second, unit)} "
        "This compares the tracked sample in those windows. It is not a restoration outcome."
    )
    return {
        "findings": [finding],
        "metrics": {"windows": blocks},
        "limitations": ["A before/after difference in tracked movement is not evidence of restoration success."],
    }


def _window_phrase(block: dict, unit: str) -> str:
    span = f"{block['name']} ({block['start']} to {block['end']})"
    if block["n_observations"] == 0:
        return f"In {span}, there were no observations."
    return (
        f"In {span}, median daily displacement was {fmt(block['median_displacement'])} {unit} "
        f"({block['n_animals']} animals, {block['n_displacement_rows']} displacement-days)."
    )


def _one_window(frame, window, entity, event_time, displacement, unit) -> dict:
    start = pd.Timestamp(as_utc(window.start))
    end = pd.Timestamp(as_utc(window.end))
    selected = frame.loc[(frame[event_time] >= start) & (frame[event_time] <= end)]
    values = selected[displacement].dropna()
    return {
        "name": window.name,
        "start": start.tz_convert("UTC").date().isoformat(),
        "end": end.tz_convert("UTC").date().isoformat(),
        "n_animals": int(selected[entity].nunique()) if len(selected) else 0,
        "n_observations": int(len(selected)),
        "n_displacement_rows": int(len(values)),
        "median_displacement": None if values.empty else float(values.median()),
        "unit": unit,
    }

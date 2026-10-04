"""Environmental summary when the feature table has no tracked animals.

This path is only for a table that has time and rainfall or vegetation and
does not carry animal, coordinate, or displacement roles.
"""

import pandas as pd

from analysis.report import fmt


def environment_summary(frame: pd.DataFrame, roles: dict) -> dict:
    event_time = roles["event_time"].name
    start = pd.Timestamp(frame[event_time].min()).tz_convert("UTC").date().isoformat()
    end = pd.Timestamp(frame[event_time].max()).tz_convert("UTC").date().isoformat()
    variables = {}
    findings = []
    for role in ("rainfall", "vegetation_index"):
        column = roles.get(role)
        if column is None:
            continue
        values = frame[column.name].dropna()
        median = None if values.empty else float(values.median())
        label = column.description or column.name
        unit = column.unit or ""
        variables[role] = {"median": median, "n": int(len(values)), "unit": unit, "label": label}
        findings.append(
            f"Median {label} was {fmt(median)} {unit} across {len(values)} observations "
            f"from {start} to {end}. No tracked animals were in this table."
        )
    return {
        "findings": findings,
        "metrics": {"n_rows": int(len(frame)), "start": start, "end": end, "variables": variables},
        "limitations": [
            "This summary describes environmental observations only. It does not describe animal movement."
        ],
        "timeline": _timeline(frame, event_time, roles),
        "map": {"type": "FeatureCollection", "features": [], "missing_coordinates": 0},
    }


def _timeline(frame, event_time, roles) -> dict:
    role = next((name for name in ("rainfall", "vegetation_index") if name in roles), None)
    if role is None:
        return {"series": []}
    column = roles[role].name
    series = []
    day = frame[event_time].dt.floor("D")
    for stamp, group in frame.groupby(day, sort=True):
        values = group[column].dropna()
        series.append(
            {
                "date": pd.Timestamp(stamp).date().isoformat(),
                "median": None if values.empty else float(values.median()),
                "unit": roles[role].unit,
                "role": role,
                "predicted": False,
            }
        )
    return {"series": series}

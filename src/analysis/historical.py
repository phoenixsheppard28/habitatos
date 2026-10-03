"""Historical movement, revisit, and habitat summaries.

Displacement totals skip nulls. A gap is a missing calendar day inside an
animal's own first-to-last span, not a day before the animal was tagged.
"""

import pandas as pd

from analysis.constants import MIN_HABITAT_PAIRS, MIN_HABITAT_SIDE, SAMPLE_LIMITATION
from analysis.report import fmt


def movement_summary(frame: pd.DataFrame, roles: dict) -> dict:
    entity = roles["entity_id"].name
    event_time = roles["event_time"].name
    displacement = roles["daily_displacement"].name
    unit = roles["daily_displacement"].unit
    observed = frame[displacement].dropna()
    gap_count, gaps = _gap_animal_days(frame, entity, event_time)
    start = frame[event_time].min()
    end = frame[event_time].max()
    metrics = {
        "n_animals": int(frame[entity].nunique()),
        "n_observations": int(len(frame)),
        "n_days": int(frame[event_time].dt.floor("D").nunique()),
        "n_displacement_rows": int(observed.shape[0]),
        "missing_displacement_rows": int(frame[displacement].isna().sum()),
        "displacement_km_total": float(observed.sum()) if len(observed) else None,
        "displacement_km_median": float(observed.median()) if len(observed) else None,
        "displacement_unit": unit,
        "gap_animal_days": gap_count,
        "gaps": gaps,
        "start": pd.Timestamp(start).tz_convert("UTC").date().isoformat(),
        "end": pd.Timestamp(end).tz_convert("UTC").date().isoformat(),
    }
    finding = (
        f"Across {metrics['n_animals']} tracked animals from {metrics['start']} to {metrics['end']}, "
        f"median daily displacement was {fmt(metrics['displacement_km_median'])} {unit} "
        f"(total {fmt(metrics['displacement_km_total'])} {unit}). "
        f"{metrics['gap_animal_days']} animal-days in those spans had no location."
    )
    limitations = [SAMPLE_LIMITATION]
    if gap_count:
        noun = "animal-day" if gap_count == 1 else "animal-days"
        verb = "has" if gap_count == 1 else "have"
        limitations.append(
            f"{gap_count} {noun} inside an animal's first-to-last span {verb} no location."
        )
    return {
        "findings": [finding],
        "metrics": metrics,
        "limitations": limitations,
        "map": _map(frame, entity, event_time, roles["longitude"].name, roles["latitude"].name),
        "timeline": _timeline(frame, entity, event_time, displacement, unit),
    }


def revisit_summary(frame: pd.DataFrame, roles: dict) -> dict | None:
    if "cell_id" not in roles:
        return None
    entity = roles["entity_id"].name
    cell = roles["cell_id"].name
    usable = frame.loc[frame[cell].notna(), [entity, cell]]
    if usable.empty:
        return {"findings": [], "metrics": {"revisits": []}, "warnings": ["Cell ids were present but empty."]}
    cells = []
    for cell_id, group in usable.groupby(cell, sort=True):
        per_animal = group.groupby(entity).size()
        cells.append(
            {
                "cell_id": str(cell_id),
                "n_observations": int(len(group)),
                "n_animals": int(group[entity].nunique()),
                "n_animals_with_repeat": int((per_animal >= 2).sum()),
            }
        )
    cells.sort(key=lambda item: (-item["n_observations"], item["cell_id"]))
    top = cells[0]
    verb = "was" if top["n_animals_with_repeat"] == 1 else "were"
    finding = (
        f"Cell {top['cell_id']} has {top['n_observations']} observations from {top['n_animals']} animals; "
        f"{top['n_animals_with_repeat']} of those animals {verb} recorded there on more than one day."
    )
    return {"findings": [finding], "metrics": {"revisits": cells}, "warnings": []}


def habitat_summary(frame: pd.DataFrame, roles: dict) -> dict:
    findings = []
    metrics = {}
    warnings = []
    displacement = roles["daily_displacement"].name
    unit = roles["daily_displacement"].unit
    for role in ("rainfall", "vegetation_index"):
        column = roles.get(role)
        if column is None:
            continue
        label = column.description or column.name
        paired = frame[[column.name, displacement]].dropna()
        if len(paired) < MIN_HABITAT_PAIRS:
            warnings.append(
                f"{label} was present but there were fewer than {MIN_HABITAT_PAIRS} overlapping displacement rows, "
                "so no habitat comparison was made."
            )
            continue
        median_env = float(paired[column.name].median())
        above = paired.loc[paired[column.name] > median_env, displacement]
        below = paired.loc[paired[column.name] <= median_env, displacement]
        if len(above) < MIN_HABITAT_SIDE or len(below) < MIN_HABITAT_SIDE:
            warnings.append(
                f"{label} was present but fewer than {MIN_HABITAT_SIDE} days fell on each side of the median, "
                "so no habitat comparison was made."
            )
            continue
        env_unit = column.unit or ""
        block = {
            "role": role,
            "label": label,
            "median_environment": median_env,
            "environment_unit": env_unit,
            "above_median_displacement": float(above.median()),
            "below_median_displacement": float(below.median()),
            "n_above": int(len(above)),
            "n_below": int(len(below)),
            "displacement_unit": unit,
        }
        metrics[role] = block
        findings.append(
            f"When {label} was above its median ({fmt(median_env)} {env_unit}), "
            f"median daily displacement was {fmt(block['above_median_displacement'])} {unit} "
            f"(n={block['n_above']}); at or below that median it was "
            f"{fmt(block['below_median_displacement'])} {unit} (n={block['n_below']}). "
            "This compares tracked animals in this sample. It does not show that the environment caused the change."
        )
    return {"findings": findings, "metrics": metrics, "warnings": warnings}


def _gap_animal_days(frame: pd.DataFrame, entity: str, event_time: str):
    total = 0
    gaps = []
    for animal, group in frame.groupby(entity, sort=True):
        days = pd.DatetimeIndex(group[event_time].dt.floor("D").unique()).tz_convert("UTC")
        full = pd.date_range(days.min(), days.max(), freq="D", tz="UTC")
        missing = full.difference(days)
        total += len(missing)
        for day in missing:
            gaps.append({"entity_id": str(animal), "date": pd.Timestamp(day).date().isoformat()})
    return total, gaps


def _map(frame: pd.DataFrame, entity: str, event_time: str, lon: str, lat: str) -> dict:
    features = []
    missing = 0
    for animal, group in frame.groupby(entity, sort=True):
        group = group.sort_values(event_time)
        coordinates = []
        for _, row in group.iterrows():
            if pd.isna(row[lon]) or pd.isna(row[lat]):
                missing += 1
                continue
            point = [float(row[lon]), float(row[lat])]
            coordinates.append(point)
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": point},
                    "properties": {
                        "entity_id": str(animal),
                        "observed_at": _iso(row[event_time]),
                        "kind": "observed",
                    },
                }
            )
        if len(coordinates) >= 2:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": coordinates},
                    "properties": {"entity_id": str(animal), "kind": "daily_path"},
                }
            )
    return {"type": "FeatureCollection", "features": features, "missing_coordinates": missing}


def _timeline(frame: pd.DataFrame, entity: str, event_time: str, displacement: str, unit: str) -> dict:
    series = []
    day = frame[event_time].dt.floor("D")
    for stamp, group in frame.groupby(day, sort=True):
        values = group[displacement].dropna()
        series.append(
            {
                "date": pd.Timestamp(stamp).date().isoformat(),
                "median_daily_displacement": None if values.empty else float(values.median()),
                "unit": unit,
                "n_animals": int(group[entity].nunique()),
                "predicted": False,
            }
        )
    return {"series": series}


def _iso(value) -> str:
    return pd.Timestamp(value).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")

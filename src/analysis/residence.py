import numpy as np
import pandas as pd


def residence_summary(frame, roles, max_gap_hours):
    entity = roles["entity_id"].name
    time = roles["event_time"].name
    vegetation = roles["vegetation_index"].name
    coordinates = [roles[role].name for role in ("longitude", "latitude")]
    columns = [entity, time, vegetation, *coordinates]
    if "cell_id" in roles:
        columns.append(roles["cell_id"].name)
    validity = [roles[role].name for role in ("vegetation_valid_from", "vegetation_valid_until") if role in roles]
    columns.extend(validity)
    work = frame[columns].sort_values([entity, time]).reset_index(drop=True)
    following = work.groupby(entity, sort=False).shift(-1)
    hours = (following[time] - work[time]).dt.total_seconds() / 3600
    has_interval = hours.notna() & (hours > 0)
    gaps = has_interval & (hours > max_gap_hours)
    measurements = [vegetation, *coordinates]
    measured = np.isfinite(work[measurements].to_numpy(dtype=float, na_value=np.nan)).all(axis=1)
    measured &= np.isfinite(following[measurements].to_numpy(dtype=float, na_value=np.nan)).all(axis=1)
    midpoint = work[time] + (following[time] - work[time]) / 2
    if "vegetation_valid_from" in roles:
        start = roles["vegetation_valid_from"].name
        measured &= (work[start] <= work[time]) & (following[start] <= midpoint)
    if "vegetation_valid_until" in roles:
        end = roles["vegetation_valid_until"].name
        measured &= (work[end] >= midpoint) & (following[end] > following[time])
    usable = has_interval & ~gaps & measured
    unmeasured = has_interval & ~gaps & ~measured
    diagnostics = {
        "n_observations": len(work), "n_tracked_animals": int(work[entity].nunique()),
        "n_intervals": int(usable.sum()), "max_tracking_gap_hours": max_gap_hours,
        "excluded_gap_intervals": int(gaps.sum()), "excluded_gap_hours": float(hours[gaps].sum()),
        "excluded_unmeasured_intervals": int(unmeasured.sum()),
        "excluded_unmeasured_hours": float(hours[unmeasured].sum()),
    }
    if not usable.any():
        return {"status": "insufficient_data", "metrics": diagnostics,
                "message": "No consecutive fixes have valid vegetation and coordinates within the tracking gap limit."}

    starts = work.loc[usable].copy()
    ends = following.loc[usable].copy()
    ends[entity] = starts[entity]
    starts["hours"] = hours[usable] / 2
    ends["hours"] = hours[usable] / 2
    exposure = pd.concat([starts, ends], ignore_index=True)
    unique_fixes = exposure.drop_duplicates([entity, time])
    threshold = float(unique_fixes[vegetation].median())
    exposure["greener"] = exposure[vegetation] > threshold
    total = float(exposure["hours"].sum())
    above = float(exposure.loc[exposure["greener"], "hours"].sum())
    per_animal = []
    for animal, group in exposure.groupby(entity, sort=True):
        animal_hours = float(group["hours"].sum())
        greener_hours = float(group.loc[group["greener"], "hours"].sum())
        per_animal.append({"entity_id": str(animal), "tracked_hours": animal_hours,
                           "above_median_hours": greener_hours,
                           "above_median_share": greener_hours / animal_hours})
    metrics = {
        **diagnostics, "n_animals": len(per_animal), "tracked_hours": total,
        "median_vegetation_index": threshold, "threshold_basis": "median of unique fixes in usable intervals",
        "above_median_hours": above, "at_or_below_median_hours": total - above,
        "above_median_share": above / total, "at_or_below_median_share": (total - above) / total,
        "time_weighted_vegetation_index": float(np.average(exposure[vegetation], weights=exposure["hours"])),
        "per_animal": per_animal, "allocation_method": "half of each interval to each endpoint",
    }
    label = roles["vegetation_index"].description or vegetation
    findings = [
        f"Estimated tracked time above the sampled-fix median {label} ({threshold:.3g}) was "
        f"{above:.3g} hours ({100 * above / total:.3g}%).",
        f"Estimated tracked time at or below that median was {total - above:.3g} hours "
        f"({100 * (total - above) / total:.3g}%).",
    ]
    warnings = []
    if gaps.any():
        warnings.append(f"Excluded {int(gaps.sum())} tracking intervals longer than {max_gap_hours:g} hours.")
    if unmeasured.any():
        warnings.append(f"Excluded {int(unmeasured.sum())} intervals with missing or expired vegetation or coordinates.")

    return {
        "status": "complete", "metrics": {"residence": metrics}, "findings": findings, "warnings": warnings,
        "limitations": [
            "Each interval contributes half its duration to each endpoint. The animal's position between fixes is unknown.",
            "Only consecutive fixes within the query window contribute time. The first and last fixes have no extrapolated tails.",
            "The greenness threshold is the median at sampled fixes, not the vegetation distribution across available habitat.",
            "Time allocation describes the tracked sample. It does not establish habitat preference or causation.",
            "Hours are pooled animal-hours. Animals with longer valid tracking periods contribute more time.",
        ],
    }

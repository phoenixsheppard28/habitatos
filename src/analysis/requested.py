import re
from itertools import combinations

import numpy as np
import pandas as pd

from analysis.prepare import PrepareError
from analysis.report import fmt
from contracts.models import AnalysisOptions

ALIASES = {
    "daily_displacement": ("displacement", "movement", "distance", "move", "travel"),
    "rainfall": ("rainfall", "rain", "precipitation", "wet", "dry"),
    "vegetation_index": ("ndvi", "vegetation", "greenness", "greener", "green"),
}
LABELS = {"daily_displacement": "Daily displacement", "rainfall": "Rainfall", "vegetation_index": "Vegetation index"}


def select_analysis(query, roles):
    options = query.analysis.model_copy(deep=True)
    text = query.question.lower()
    if query.analysis_method == "residence_time" and options.method == "auto":
        options.method = "summary"

    if options.method == "auto":
        if query.comparison_windows:
            options.method = "comparison"
        elif re.search(r"\b(correlat\w*|relationship|related|association|associated|versus|vs|scatter\w*)\b", text):
            options.method = "correlation"
        elif re.search(r"\b(distribution|histogram|box\s*plot)\b", text):
            options.method = "distribution"
        elif re.search(r"\b(statistics|stats|descriptive|standard deviation|variance|quartiles|percentiles)\b", text):
            options.method = "statistics"
        elif re.search(r"\b(trends?|time\s*series|over time|line chart)\b", text):
            options.method = "trend"
        else:
            options.method = "summary"

    options = AnalysisOptions.model_validate(options.model_dump())
    available = {role: column for role, column in roles.items()
                 if column.type in {"number", "integer"}
                 and role not in {"longitude", "latitude", "entity_id", "cell_id"}}
    selected = []
    if options.variables:
        for variable in options.variables:
            role = next((role for role, column in available.items() if variable in {role, column.name}), None)
            if role is None:
                raise PrepareError("missing_variables", f"Numeric variable {variable} is not available in the prepared table.")
            if role not in selected:
                selected.append(role)
    else:
        for role, column in available.items():
            terms = (role, column.name, *ALIASES.get(role, ()))
            if any(re.search(r"\b" + re.escape(term) + r"\b", text) for term in terms):
                selected.append(role)
        if options.method == "correlation" and len(selected) == 1 and selected[0] != "daily_displacement":
            if "daily_displacement" in available:
                selected.append("daily_displacement")
        if not selected:
            if options.method == "correlation":
                environment = [role for role in ("vegetation_index", "rainfall") if role in available]
                selected = [*environment, "daily_displacement"] if "daily_displacement" in available else list(available)
            else:
                selected = ["daily_displacement"] if "daily_displacement" in available else list(available)
        if options.method == "correlation" and "daily_displacement" in selected:
            selected = [role for role in selected if role != "daily_displacement"] + ["daily_displacement"]

    options.variables = selected
    if options.method in {"correlation", "distribution", "statistics", "trend"}:
        minimum = 2 if options.method == "correlation" else 1
        if len(selected) < minimum:
            raise PrepareError("missing_variables", f"{options.method.capitalize()} needs at least {minimum} numeric variables.")

    return options


def requested_summary(frame, roles, options):
    variables = {role: _statistics(frame[roles[role].name], roles[role], role) for role in options.variables}
    tables = [_statistics_table(variables)] if options.method in {"statistics", "distribution"} else []
    findings = []
    metrics = {"analysis": options.model_dump(), "variables": variables, "n_rows": len(frame)}
    timeline = {"series": []}
    limitations = ["Missing and non-finite measurements are excluded. Missing values are not zero."]
    warnings = []

    if options.method == "correlation":
        pairs = []
        for x_role, y_role in combinations(options.variables, 2):
            x_column, y_column = roles[x_role], roles[y_role]
            paired = frame[[x_column.name, y_column.name]].dropna()
            paired = paired.loc[np.isfinite(paired).all(axis=1)]
            n = len(paired)
            varying = n >= 3 and all(paired[column].nunique() > 1 for column in paired)
            if not varying:
                warnings.append(f"{LABELS.get(x_role, x_role)} and {LABELS.get(y_role, y_role)} need three paired rows and variation.")
                continue

            x_values, y_values = paired[x_column.name], paired[y_column.name]
            pearson = float(x_values.corr(y_values))
            spearman = float(x_values.rank().corr(y_values.rank()))
            slope = float(((x_values - x_values.mean()) * (y_values - y_values.mean())).sum()
                          / ((x_values - x_values.mean()) ** 2).sum())
            intercept = float(y_values.mean() - slope * x_values.mean())
            pair = {"x_role": x_role, "y_role": y_role, "n": n, "excluded_rows": len(frame) - n,
                    "pearson_r": pearson, "spearman_rho": spearman, "r_squared": pearson ** 2,
                    "slope": slope, "intercept": intercept,
                    "x_unit": x_column.unit, "y_unit": y_column.unit}
            pairs.append(pair)
            x_label, y_label = variables[x_role]["label"], variables[y_role]["label"]
            findings.append(f"{x_label} versus {y_label}: Pearson r = {fmt(pearson)}, Spearman rho = {fmt(spearman)} (n={n}).")
        if not pairs:
            raise PrepareError("insufficient_pairs", "Correlation needs three finite paired measurements and variation in both variables.")
        metrics["correlations"] = pairs
        tables = [{"title": "Correlation statistics", "columns": ["Variables", "Paired rows", "Excluded rows", "Pearson r", "Spearman rho", "R²"],
                   "rows": [[f"{variables[pair['x_role']]['label']} / {variables[pair['y_role']]['label']}",
                             pair["n"], pair["excluded_rows"], pair["pearson_r"], pair["spearman_rho"], pair["r_squared"]]
                            for pair in pairs]}]
        limitations.append("Correlation does not establish causation. The linear fit is descriptive, not a forecast.")
        if "entity_id" in roles:
            limitations.append("Repeated observations from the same animal are not independent. Pooled correlations do not isolate within-animal effects.")
        limitations.append("No significance test is reported. Spatial and temporal dependence require a separate inferential model.")
    else:
        for role, stats in variables.items():
            if not stats["n"]:
                warnings.append(f"{stats['label']} has no finite measurements.")
                continue
            findings.append(f"{stats['label']}: n={stats['n']}, mean {fmt(stats['mean'])}, median {fmt(stats['median'])} {stats['unit'] or ''}.")
            if options.method == "trend":
                day = frame[roles["event_time"].name].dt.floor("D")
                timeline["series"].extend({"date": pd.Timestamp(stamp).date().isoformat(), "role": role,
                                           "median": None if group[roles[role].name].dropna().empty else float(group[roles[role].name].median()),
                                           "unit": stats["unit"], "predicted": False}
                                          for stamp, group in frame.groupby(day, sort=True))
        if not any(stats["n"] for stats in variables.values()):
            raise PrepareError("no_measurements", "The requested variables have no finite measurements in this date range.")

    return {"findings": findings, "metrics": metrics, "limitations": limitations, "warnings": warnings,
            "tables": tables, "timeline": timeline,
            "map": {"type": "FeatureCollection", "features": [], "missing_coordinates": 0}}


def _statistics(values, column, role):
    total = len(values)
    values = values.dropna()
    values = values.loc[np.isfinite(values)]
    n = len(values)
    return {"label": LABELS.get(role, column.description or column.name), "unit": column.unit,
            "n": n, "missing": total - n,
            "mean": None if not n else float(values.mean()),
            "std": None if n < 2 else float(values.std(ddof=1)),
            "min": None if not n else float(values.min()), "q1": None if not n else float(values.quantile(0.25)),
            "median": None if not n else float(values.median()), "q3": None if not n else float(values.quantile(0.75)),
            "max": None if not n else float(values.max())}


def _statistics_table(variables):
    keys = ["label", "unit", "n", "missing", "mean", "std", "min", "q1", "median", "q3", "max"]
    return {"title": "Descriptive statistics", "columns": ["Variable", "Unit", "Count", "Missing", "Mean", "Sample SD", "Minimum", "Q1", "Median", "Q3", "Maximum"],
            "rows": [[stats[key] for key in keys] for stats in variables.values()]}

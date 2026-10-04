from contracts.charts import ChartSpec


def chart_specs(result):
    charts = []
    metrics = result.get("metrics") or {}
    result_id = result.get("result_id", "analysis")
    windows = (metrics.get("comparison") or {}).get("windows") or []
    if windows:
        charts.append({
            "chart_id": f"{result_id}-windows", "title": "Date-window comparison",
            "kind": "bar", "x_axis": {"label": "Date window", "type": "category"},
            "y_axis": {"label": "Median daily displacement", "unit": windows[0]["unit"]},
            "series": [{"name": "Median daily displacement", "points": [
                {"x": window["name"], "y": window["median_displacement"],
                 "note": f"{window['start']} to {window['end']} · {window['n_animals']} animals · "
                         f"{window['n_displacement_rows']} measured rows"} for window in windows]}],
        })
    for role, comparison in (metrics.get("habitat") or {}).items():
        charts.append({
            "chart_id": f"{result_id}-habitat-{role}", "title": f"{comparison['label']} and daily displacement",
            "kind": "bar", "x_axis": {"label": comparison["label"], "type": "category"},
            "y_axis": {"label": "Median daily displacement", "unit": comparison["displacement_unit"]},
            "description": f"Environmental median: {comparison['median_environment']:g} "
                           f"{comparison['environment_unit']}.",
            "series": [{"name": "Median daily displacement", "points": [
                {"x": "At or below median", "y": comparison["below_median_displacement"],
                 "note": f"{comparison['n_below']} paired rows"},
                {"x": "Above median", "y": comparison["above_median_displacement"],
                 "note": f"{comparison['n_above']} paired rows"},
            ]}],
        })

    timelines = {}
    for point in (result.get("timeline") or {}).get("series", []):
        movement = "median_daily_displacement" in point
        role = "movement" if movement else point.get("role", "measurement")
        key = (role, point.get("unit"))
        group = timelines.setdefault(key, {"observed": [], "predicted": []})
        group["predicted" if point.get("predicted") else "observed"].append({
            "x": point["date"], "y": point.get("median_daily_displacement" if movement else "median"),
            "note": f"{point['n_animals']} tracked animals" if "n_animals" in point else None,
        })
    labels = {"movement": "Daily median displacement", "rainfall": "Daily median rainfall",
              "vegetation_index": "Daily median vegetation index"}
    for index, ((role, unit), groups) in enumerate(timelines.items()):
        series = []
        for name, points in groups.items():
            if not points:
                continue
            series.append({"name": "Predicted" if name == "predicted" else "Observed",
                           "points": sorted(points, key=lambda point: point["x"]),
                           "style": "dashed" if name == "predicted" else "solid"})
        label = labels.get(role, role.replace("_", " ").capitalize())
        charts.append({
            "chart_id": f"{result_id}-timeline-{index}", "title": label,
            "kind": "line", "x_axis": {"label": "Date (UTC)", "type": "temporal"},
            "y_axis": {"label": label, "unit": unit}, "max_gap": 86_400_000,
            "description": "Missing values have no marks. Lines stop at missing days.", "series": series,
        })

    return [ChartSpec.model_validate(chart).model_dump(mode="json") for chart in charts]

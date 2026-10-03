"""Analysis entry point. Reads a feature table and returns a cited result.

Checks that can be decided from the question and the descriptor happen before
the file is opened. A refused question therefore does not depend on the table.
"""

import hashlib
import json
from datetime import datetime, timezone

from pydantic import ValidationError

from analysis.cells import forecast_cell_use
from analysis.constants import (
    CELL_USE_METHOD,
    FORECAST_METHOD,
    FORECAST_VERSION,
    MOVEMENT_METHOD,
    MOVEMENT_VERSION,
    SAMPLE_LIMITATION,
    SCHEMA_VERSION,
)
from analysis.forecast import forecast_next_day
from analysis.habitat_only import environment_summary
from analysis.historical import habitat_summary, movement_summary, revisit_summary
from analysis.overlap import route_overlap
from analysis.windows import window_comparison
from analysis.prepare import PrepareError, bind_roles, coverage_problem, missing_roles, prepare_frame, scope_allows
from analysis.report import render_report
from analysis.store import StorageError
from contracts.models import AnalysisRequest

MOVEMENT_ROLES = ["entity_id", "event_time", "longitude", "latitude", "daily_displacement"]
FORECAST_ROLES = ["entity_id", "event_time", "daily_displacement"]


def run(request: dict, *, store, seed: int = 0) -> dict:
    if not isinstance(request, dict):
        return _error({}, "invalid_request", "request must be an object", False)
    if request.get("contract_version") != "1.0":
        return _error(request, "unsupported_contract", "unsupported contract_version", False)
    try:
        parsed = AnalysisRequest.model_validate(request)
    except ValidationError as exc:
        return _error(request, "invalid_request", _validation_message(exc), False)

    query = parsed.input.query
    artifact = parsed.input.feature_artifact
    recipe = parsed.input.recipe
    if query.task_type == "discovery":
        return _error(request, "discovery_not_analyzed", "Discovery does not produce an analysis result.", False)
    blocked = unsupported_question(query.question)
    if blocked:
        return _insufficient(request, "unsupported_question", _unsupported_message(blocked), {"reason": blocked})
    if query.query_id != parsed.query_id:
        return _error(request, "query_id_mismatch", "query_id does not match the request", False)
    if query.access_scope != parsed.access_scope:
        return _error(request, "access_scope_mismatch", "query access_scope does not match the request", False)
    if not str(artifact.schema_version).startswith("1."):
        return _error(request, "unsupported_contract", "unsupported feature schema_version", False)
    if not scope_allows(parsed.access_scope, artifact.access_scope):
        return _error(request, "access_scope_mismatch", "request scope cannot read this feature artifact", False)
    if artifact.recipe_ref.recipe_id != recipe.recipe_id or artifact.recipe_ref.version != recipe.version:
        return _error(request, "recipe_mismatch", "feature artifact recipe reference does not match the recipe", False)

    problem = coverage_problem(artifact, query)
    if problem:
        return _insufficient(
            request,
            "coverage_mismatch",
            f"Feature coverage does not overlap the query {problem}.",
            {"reason": "coverage_mismatch", "dimension": problem},
        )

    try:
        roles = bind_roles(artifact.columns)
    except PrepareError as exc:
        return _error(request, exc.code, exc.message, False)

    required, analysis_mode = _required_roles(query, roles)
    absent = missing_roles(roles, required)
    displacement = roles.get("daily_displacement")
    if analysis_mode in {"movement", "displacement"} and displacement is not None and not displacement.unit:
        absent.append("daily_displacement.unit")
    if absent:
        return _insufficient(
            request,
            "missing_roles",
            "The feature table is missing roles this method needs: " + ", ".join(absent) + ".",
            {"missing_roles": absent},
        )

    if query.task_type == "forecast":
        if query.forecast is None:
            return _error(request, "invalid_request", "forecast requests need a cutoff, horizon, and target", False)
        if query.forecast.target not in {FORECAST_METHOD, CELL_USE_METHOD}:
            return _insufficient(
                request,
                "unsupported_target",
                f"Forecast target {query.forecast.target} is not available.",
                {"target": query.forecast.target},
            )
        if recipe.cutoff is None or not recipe.features_respect_cutoff:
            return _insufficient(
                request,
                "cutoff_not_guaranteed",
                "The recipe does not guarantee that features were built only from inputs available at the forecast cutoff.",
                {},
            )
        if _same_instant(recipe.cutoff, query.forecast.cutoff) is False:
            return _error(request, "cutoff_mismatch", "recipe cutoff does not match the query cutoff", False)

    try:
        frame = store.read_dataset(artifact.storage)
        frame, warnings = prepare_frame(frame, artifact, query, roles)
    except StorageError as exc:
        return _error(request, "storage_error", str(exc), False)
    except PrepareError as exc:
        status_code = exc.code
        if status_code in {"no_rows_in_range", "no_rows_for_species"}:
            return _insufficient(request, status_code, exc.message, {})
        return _error(request, status_code, exc.message, False)

    if query.task_type == "forecast":
        if query.forecast.target == CELL_USE_METHOD:
            outcome = forecast_cell_use(frame, roles, query.forecast.cutoff, query.forecast.horizon_days)
        else:
            outcome = forecast_next_day(
                frame,
                roles,
                query.forecast.cutoff,
                query.forecast.horizon_days,
                query.forecast.scenario,
                seed,
            )
        if outcome["status"] != "complete":
            return _insufficient(request, outcome["code"], outcome["message"], outcome["metrics"])
        return _finish_forecast(request, parsed, outcome, warnings, seed, store)

    if analysis_mode == "environment":
        return _finish_environment(request, parsed, frame, roles, warnings)
    return _finish_historical(request, parsed, frame, roles, warnings)


def _required_roles(query, roles: dict) -> tuple[list[str], str]:
    if query.task_type == "forecast":
        target = query.forecast.target if query.forecast else FORECAST_METHOD
        if target == CELL_USE_METHOD:
            return ["entity_id", "event_time", "cell_id"], "cell_use"
        return list(FORECAST_ROLES), "displacement"
    if all(role in roles for role in MOVEMENT_ROLES):
        return list(MOVEMENT_ROLES), "movement"
    if any(role in roles for role in ("entity_id", "longitude", "latitude", "daily_displacement")):
        return list(MOVEMENT_ROLES), "movement"
    environment = [role for role in ("rainfall", "vegetation_index") if role in roles]
    if environment and "event_time" in roles:
        return ["event_time", *environment], "environment"
    return list(MOVEMENT_ROLES), "movement"


def _finish_environment(request, parsed, frame, roles, warnings):
    summary = environment_summary(frame, roles)
    result = _result(
        parsed,
        status="complete",
        findings=summary["findings"],
        metrics=summary["metrics"],
        limitations=summary["limitations"],
        method="environment_summary@1",
        map_payload=summary["map"],
        timeline=summary["timeline"],
        model_reference=None,
        sample_limitation=False,
    )
    spec = _spec(parsed, "environment_summary", "1", target=None, horizon=None, baseline=None, seed=None)
    return _ok(request, "ok", {"analysis_spec": spec, "result": result, "model_artifact": None}, warnings)


def _finish_historical(request, parsed, frame, roles, warnings):
    summary = movement_summary(frame, roles)
    metrics = dict(summary["metrics"])
    findings = list(summary["findings"])
    limitations = list(summary["limitations"])
    revisits = revisit_summary(frame, roles)
    if revisits:
        findings.extend(revisits["findings"])
        metrics.update(revisits["metrics"])
        warnings.extend(revisits["warnings"])
    habitat = habitat_summary(frame, roles)
    findings.extend(habitat["findings"])
    if habitat["metrics"]:
        metrics["habitat"] = habitat["metrics"]
    warnings.extend(habitat["warnings"])
    if parsed.input.query.comparison_windows:
        compared = window_comparison(
            frame,
            roles,
            parsed.input.query.comparison_windows,
            parsed.input.query.time_range,
        )
        findings.extend(compared["findings"])
        metrics["comparison"] = compared["metrics"]
        limitations.extend(compared["limitations"])
        warnings.extend(compared["warnings"])
    if parsed.input.boundaries:
        overlap = route_overlap(frame, roles, parsed.input.boundaries)
        findings.extend(overlap["findings"])
        metrics["overlap"] = overlap["metrics"]
        warnings.extend(overlap["warnings"])
    if summary["map"]["missing_coordinates"]:
        warnings.append(
            f"{summary['map']['missing_coordinates']} rows had no coordinates and were left off the map."
        )
    result = _result(
        parsed,
        status="partial" if summary["map"]["missing_coordinates"] else "complete",
        findings=findings,
        metrics=metrics,
        limitations=limitations,
        method=f"{MOVEMENT_METHOD}@{MOVEMENT_VERSION}",
        map_payload=summary["map"],
        timeline=summary["timeline"],
        model_reference=None,
    )
    spec = _spec(parsed, MOVEMENT_METHOD, MOVEMENT_VERSION, target=None, horizon=None, baseline=None, seed=None)
    status = "partial" if result["status"] == "partial" else "ok"
    return _ok(request, status, {"analysis_spec": spec, "result": result, "model_artifact": None}, warnings)


def _finish_forecast(request, parsed, outcome, warnings, seed, store):
    fitted = outcome["fitted"]
    model_artifact = None
    if fitted is not None:
        model_artifact = _model_artifact(parsed, outcome, fitted, seed)
        relative = f"models/{model_artifact['artifact_id']}.json"
        model_artifact["storage"] = {"uri": f"artifact://{relative}", "format": "json"}
        store.write_json(relative, model_artifact)
    result = _result(
        parsed,
        status="complete",
        findings=outcome["findings"],
        metrics=outcome["metrics"],
        limitations=outcome["limitations"],
        method=f"{outcome.get('method_name', FORECAST_METHOD)}@{outcome.get('method_version', FORECAST_VERSION)}",
        map_payload=outcome["map"],
        timeline=outcome["timeline"],
        model_reference=None if model_artifact is None else model_artifact["artifact_id"],
    )
    spec = _spec(
        parsed,
        outcome.get("method_name", FORECAST_METHOD),
        outcome.get("method_version", FORECAST_VERSION),
        target=parsed.input.query.forecast.target,
        horizon=parsed.input.query.forecast.horizon_days,
        baseline=outcome.get("baseline_name", "recent_mean_7d"),
        seed=seed,
        split={
            "train": "target_day < cutoff",
            "holdout": "target_day >= cutoff",
            "cutoff": outcome["metrics"]["cutoff"],
        },
        parameters=outcome.get("parameters")
        or {
            "alpha": 1.0,
            "seed": seed,
            "features": list(outcome.get("used_features") or []),
            "pooled_animals": True,
        },
    )
    output = {"analysis_spec": spec, "result": result, "model_artifact": model_artifact}
    return _ok(request, "ok", output, warnings)


def _model_artifact(parsed, outcome, fitted, seed) -> dict:
    presented = outcome["presented"] == "model"
    evaluation = {
        "baseline_mae": outcome["metrics"]["baseline_mae"],
        "model_mae": outcome["metrics"]["model_mae"],
        "holdout_rows": outcome["metrics"]["holdout_rows"],
        "train_rows": outcome["metrics"]["train_rows"],
        "beat_baseline": presented,
        "presented": presented,
        "metric": "mae",
        "unit": outcome["metrics"]["displacement_unit"],
    }
    parameters = {
        "alpha": 1.0,
        "feature_names": fitted.feature_names,
        "scaler_mean": fitted.scaler_mean,
        "scaler_scale": fitted.scaler_scale,
        "coefficients": fitted.coefficients,
        "intercept": fitted.intercept,
        "seed": seed,
    }
    payload = {
        "training_feature_version": parsed.input.feature_artifact.version,
        "method": f"{FORECAST_METHOD}@{FORECAST_VERSION}",
        "parameters": _rounded(parameters),
        "evaluation": _rounded(evaluation),
        "access_scope": parsed.access_scope,
    }
    artifact_id = "model-" + _digest(payload)
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_id": artifact_id,
        "version": "1",
        "created_at": _now(),
        "access_scope": parsed.access_scope,
        "storage": None,
        "training_feature_version": parsed.input.feature_artifact.version,
        "input_schema": fitted.feature_names,
        "method": f"{FORECAST_METHOD}@{FORECAST_VERSION}",
        "dependency_versions": _dependency_versions(),
        "parameters": parameters,
        "seed": seed,
        "evaluation": evaluation,
        "intended_use": (
            "next-day displacement of tracked animals in this sample; "
            f"horizon {parsed.input.query.forecast.horizon_days} days; not a migration route"
        ),
    }


def _result(
    parsed,
    *,
    status,
    findings,
    metrics,
    limitations,
    method,
    map_payload,
    timeline,
    model_reference,
    sample_limitation=True,
):
    artifact = parsed.input.feature_artifact
    recipe = parsed.input.recipe
    datasets = [f"{item.dataset_id}@{item.version}" for item in artifact.input_dataset_refs]
    attribution = artifact.rights.attribution if artifact.rights else None
    evidence = {
        "query_id": parsed.query_id,
        "feature_artifact_id": artifact.artifact_id,
        "feature_version": artifact.version,
        "recipe_id": recipe.recipe_id,
        "recipe_version": recipe.version,
        "datasets": [item.model_dump() for item in artifact.input_dataset_refs],
        "rights": artifact.rights.model_dump() if artifact.rights else None,
    }
    if sample_limitation and SAMPLE_LIMITATION not in limitations:
        limitations = [SAMPLE_LIMITATION, *limitations]
    identity = {
        "query_id": parsed.query_id,
        "access_scope": parsed.access_scope,
        "method": method,
        "metrics": _rounded(metrics),
        "evidence": evidence,
        "limitations": limitations,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "result_id": "result-" + _digest(identity),
        "created_at": _now(),
        "status": status,
        "question": parsed.input.query.question,
        "findings": findings,
        "report": render_report(findings, limitations, attribution, datasets),
        "metrics": metrics,
        "evidence": evidence,
        "limitations": limitations,
        "map": map_payload,
        "timeline": timeline,
        "artifact_versions": {
            "feature": f"{artifact.artifact_id}@{artifact.version}",
            "recipe": f"{recipe.recipe_id}@{recipe.version}",
            "method": method,
        },
        "model_reference": model_reference,
    }


def _spec(parsed, method, version, *, target, horizon, baseline, seed, split=None, parameters=None):
    artifact = parsed.input.feature_artifact
    return {
        "schema_version": SCHEMA_VERSION,
        "feature_ref": {"artifact_id": artifact.artifact_id, "version": artifact.version},
        "method": method,
        "method_version": version,
        "target": target,
        "horizon_days": horizon,
        "temporal_split": split,
        "baseline": baseline,
        "parameters": parameters or {},
        "seed": seed,
    }


def unsupported_question(question: str) -> str | None:
    text = question.lower()
    if any(term in text for term in ("extinct", "extinction", "wiped out", "species is gone", "died out")):
        return "extinction"
    if "migration route" in text or (
        "population" in text and any(term in text for term in ("migrat", "where will", "where might"))
    ):
        return "population_migration"
    return None


def _unsupported_message(reason: str) -> str:
    if reason == "extinction":
        return (
            "This question asks about extinction or disappearance. "
            "Tracked points are not evidence of that, so no finding was produced."
        )
    return (
        "This question asks for a population migration route. "
        "No route was produced. The available target is next-day displacement of tracked animals."
    )


def _insufficient(request, code, message, metrics):
    result = {
        "schema_version": SCHEMA_VERSION,
        "status": "insufficient_data",
        "findings": [],
        "report": message,
        "metrics": metrics,
        "limitations": [SAMPLE_LIMITATION],
        "evidence": {},
        "code": code,
    }
    return _ok(
        request,
        "insufficient_data",
        {"analysis_spec": None, "result": result, "model_artifact": None},
        [],
    )


def _ok(request, status, output, warnings):
    return {
        "contract_version": request.get("contract_version", "1.0"),
        "request_id": request.get("request_id", "unknown"),
        "query_id": request.get("query_id", "unknown"),
        "access_scope": request.get("access_scope", "unknown"),
        "status": status,
        "output": output,
        "warnings": warnings,
        "error": None,
        "extensions": {},
    }


def _error(request, code, message, retryable):
    return {
        "contract_version": request.get("contract_version", "1.0") if isinstance(request, dict) else "1.0",
        "request_id": request.get("request_id", "unknown") if isinstance(request, dict) else "unknown",
        "query_id": request.get("query_id", "unknown") if isinstance(request, dict) else "unknown",
        "access_scope": request.get("access_scope", "unknown") if isinstance(request, dict) else "unknown",
        "status": "error",
        "output": {},
        "warnings": [],
        "error": {"code": code, "message": message, "retryable": retryable},
        "extensions": {},
    }


def _validation_message(exc: ValidationError) -> str:
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first["loc"])
    return f"{location}: {first['msg']}"


def _same_instant(left, right) -> bool:
    from analysis.prepare import as_utc

    return as_utc(left) == as_utc(right)


def _digest(payload: dict) -> str:
    """Identity of a result or model. created_at is excluded so a repeat matches."""
    raw = json.dumps(_rounded(payload), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _rounded(value):
    if isinstance(value, float):
        return round(value, 8)
    if isinstance(value, dict):
        return {key: _rounded(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_rounded(item) for item in value]
    return value


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dependency_versions() -> dict:
    import sklearn

    return {"scikit-learn": sklearn.__version__}


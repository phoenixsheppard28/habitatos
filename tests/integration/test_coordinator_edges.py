import copy

import pytest

from tests.support import movement_request
from workflow.coordinator import Coordinator
from workflow.validate import QueryValidationError, normalize_request


def _envelope(request, status="ok", result_status="complete"):
    return {
        "contract_version": request["contract_version"],
        "request_id": request["request_id"],
        "query_id": request["query_id"],
        "access_scope": request["access_scope"],
        "status": status,
        "output": {"result": {"status": result_status, "metrics": {}, "findings": []}},
        "warnings": [],
        "error": None,
        "extensions": {},
    }


def _without_features(request):
    bare = copy.deepcopy(request)
    bare["input"].pop("feature_artifact", None)
    bare["input"].pop("recipe", None)
    return bare


def test_full_pipeline_runs_in_order_and_passes_prior_outputs(tmp_path):
    request = _without_features(movement_request(tmp_path)[0])
    seen = []

    def handler(name):
        def run_stage(incoming):
            seen.append((name, list(incoming["extensions"]["stage_outputs"])))
            return _envelope(incoming)

        return run_stage

    coordinator = Coordinator(
        handlers={name: handler(name) for name in ("fetch", "normalize", "recipe", "analysis")}
    )
    submitted = coordinator.submit_query(request)
    assert [name for name, _prior in seen] == ["fetch", "normalize", "recipe", "analysis"]
    assert seen[3][1] == ["fetch", "normalize", "recipe"]
    assert submitted["status"] == "complete"
    assert [stage["state"] for stage in submitted["stages"]] == ["succeeded"] * 4
    assert coordinator.get_result(submitted["job_id"])["result"]["status"] == "complete"


def test_insufficient_stage_stops_the_pipeline_and_is_not_retried(tmp_path):
    request = _without_features(movement_request(tmp_path)[0])
    calls = []

    def fetch(incoming):
        calls.append("fetch")
        return _envelope(incoming)

    def normalize(incoming):
        calls.append("normalize")
        response = _envelope(incoming, status="insufficient_data", result_status="insufficient_data")
        return response

    def later(name):
        def run_stage(incoming):
            calls.append(name)
            return _envelope(incoming)

        return run_stage

    coordinator = Coordinator(
        handlers={"fetch": fetch, "normalize": normalize, "recipe": later("recipe"), "analysis": later("analysis")}
    )
    submitted = coordinator.submit_query(request)
    coordinator.resume(submitted["job_id"])
    assert calls == ["fetch", "normalize"]
    assert submitted["job_status"] == "succeeded"
    assert submitted["status"] == "insufficient_data"


def test_non_retryable_error_and_invalid_payload_stop_immediately(tmp_path):
    request = movement_request(tmp_path)[0]
    calls = {"n": 0}

    def broken(incoming):
        calls["n"] += 1
        return {
            "status": "error",
            "error": {"code": "bad_input", "message": "do not retry", "retryable": False},
            "output": {},
        }

    coordinator = Coordinator(handlers={"analysis": broken})
    submitted = coordinator.submit_query(request)
    coordinator.resume(submitted["job_id"])
    assert calls["n"] == 1
    assert submitted["error"]["retryable"] is False
    assert submitted["error"]["message"] == "do not retry"

    def invalid(incoming):
        calls["n"] += 1
        return ["not", "an envelope"]

    calls["n"] = 0
    coordinator = Coordinator(handlers={"analysis": invalid})
    submitted = coordinator.submit_query(request)
    coordinator.resume(submitted["job_id"])
    assert calls["n"] == 1
    assert submitted["error"]["retryable"] is False


def test_partial_status_is_preserved(tmp_path):
    request = movement_request(tmp_path)[0]
    coordinator = Coordinator(handlers={"analysis": lambda incoming: _envelope(incoming, status="partial", result_status="partial")})
    assert coordinator.submit_query(request)["status"] == "partial"


def test_jobs_are_isolated_and_unknown_ids_fail(tmp_path):
    request = movement_request(tmp_path)[0]
    coordinator = Coordinator(handlers={"analysis": lambda incoming: _envelope(incoming)})
    first = coordinator.submit_query(request)
    second_request = copy.deepcopy(request)
    second_request["request_id"] = "request-002"
    second = coordinator.submit_query(second_request)
    assert first["job_id"] != second["job_id"]
    assert coordinator.get_result(first["job_id"])["job_id"] == first["job_id"]
    missing = coordinator.get_result("job-missing")
    assert missing["error"]["code"] == "job_not_found"
    assert coordinator.resume("job-missing")["error"]["code"] == "job_not_found"


def test_discovery_fetch_runs_only_when_sources_are_absent(tmp_path):
    request = _without_features(movement_request(tmp_path)[0])
    request["input"]["query"]["task_type"] = "discovery"
    calls = {"n": 0}

    def fetch(incoming):
        calls["n"] += 1
        response = _envelope(incoming)
        response["output"]["result"]["sources"] = [{"name": "Movebank"}]
        return response

    found = Coordinator(handlers={"fetch": fetch}).submit_query(request)
    assert calls["n"] == 1
    assert found["status"] == "complete"

    request["input"]["sources"] = [{"name": "already-known"}]
    calls["n"] = 0
    skipped = Coordinator(handlers={"fetch": fetch}).submit_query(request)
    assert calls["n"] == 0
    assert skipped["result"]["sources"] == [{"name": "already-known"}]
    assert skipped["result"]["metrics"] == {}


def test_absolute_dates_win_and_the_caller_request_is_not_mutated(tmp_path):
    request = movement_request(tmp_path)[0]
    request["input"]["query"]["time_range"] = {
        "start": "2026-01-02T00:00:00Z",
        "end": "2026-01-04T00:00:00Z",
        "relative_days": 30,
        "as_of": "2026-06-01T00:00:00Z",
    }
    original = copy.deepcopy(request)
    normalized = normalize_request(request)
    assert request == original
    assert normalized["input"]["query"]["time_range"]["start"].startswith("2026-01-02")
    assert "relative_days" not in normalized["input"]["query"]["time_range"]


def test_query_validation_rejects_unusable_questions(tmp_path):
    request = movement_request(tmp_path)[0]

    def submit(**changes):
        candidate = copy.deepcopy(request)
        for key, value in changes.items():
            if key == "query":
                candidate["input"]["query"].update(value)
            else:
                candidate[key] = value
        return Coordinator().submit_query(candidate)

    assert submit(contract_version="9.0")["error"]["code"] == "invalid_query"
    assert submit(query={"task_type": "forecast", "forecast": None})["error"]["code"] == "invalid_query"
    assert submit(query={"region": {"type": "FeatureCollection", "coordinates": []}})["error"]["code"] == "invalid_query"
    assert submit(query={"time_range": {"relative_days": True, "as_of": "2026-01-04T00:00:00Z"}})["error"]["code"] == "invalid_query"
    assert submit(query={"time_range": {"start": "2026-01-04T00:00:00Z", "end": "2026-01-04T00:00:00Z"}})["error"]["code"] == "invalid_query"
    assert submit(query={"query_id": "someone-else"})["error"]["code"] == "invalid_query"
    assert submit(query={"access_scope": "org-9"})["error"]["code"] == "invalid_query"
    with pytest.raises(QueryValidationError):
        normalize_request([])
    with pytest.raises(QueryValidationError):
        normalize_request({"contract_version": "1.0", "request_id": "r", "query_id": "q", "access_scope": "public", "input": {}})

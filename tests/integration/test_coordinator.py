from analysis.service import run
from support import movement_request
from workflow.coordinator import Coordinator


def _ok(request):
    return {
        "contract_version": "1.0",
        "request_id": request["request_id"],
        "query_id": request["query_id"],
        "access_scope": request["access_scope"],
        "status": "ok",
        "output": {"result": {"status": "complete", "findings": ["ok"], "metrics": {}}},
        "warnings": [],
        "error": None,
        "extensions": {},
    }


def test_end_to_end_and_resume_does_not_rerun(tmp_path):
    request, store = movement_request(tmp_path)
    calls = {"n": 0}

    def analyze(incoming):
        calls["n"] += 1
        return run(incoming, store=store)

    coordinator = Coordinator(handlers={"analysis": analyze})
    submitted = coordinator.submit_query(request)
    assert submitted["status"] == "complete"
    assert submitted["result"]["metrics"]["displacement_km_median"] == 4
    again = coordinator.resume(submitted["job_id"])
    assert again["result"]["result_id"] == submitted["result"]["result_id"]
    assert calls["n"] == 1


def test_relative_dates_are_pinned(tmp_path):
    request, store = movement_request(tmp_path)
    request["input"]["query"]["time_range"] = {"relative_days": 1, "as_of": "2026-01-02T00:00:00Z"}
    coordinator = Coordinator(handlers={"analysis": lambda incoming: run(incoming, store=store)})
    submitted = coordinator.submit_query(request)
    assert submitted["query"]["time_range"]["start"].startswith("2026-01-01")
    assert submitted["query"]["time_range"]["end"].startswith("2026-01-02")
    assert submitted["result"]["metrics"]["n_observations"] == 4
    assert submitted["result"]["metrics"]["displacement_km_median"] == 7


def test_resume_skips_succeeded_stage(tmp_path):
    calls = {"prepare": 0, "analysis": 0}

    def prepare(request):
        calls["prepare"] += 1
        return _ok(request)

    def analysis(request):
        calls["analysis"] += 1
        if calls["analysis"] == 1:
            raise RuntimeError("boom")
        return _ok(request)

    request, _store = _bare_request(tmp_path)
    coordinator = Coordinator(handlers={"prepare": prepare, "analysis": analysis})
    coordinator._plan = lambda _request: ["prepare", "analysis"]
    submitted = coordinator.submit_query(request)
    assert submitted["job_status"] == "failed"
    resumed = coordinator.resume(submitted["job_id"])
    assert resumed["status"] == "complete"
    assert calls == {"prepare": 1, "analysis": 2}


def test_retries_stop_after_the_bound(tmp_path):
    calls = {"n": 0}

    def analysis(request):
        calls["n"] += 1
        raise RuntimeError("still broken")

    request, _store = _bare_request(tmp_path)
    coordinator = Coordinator(handlers={"analysis": analysis}, max_attempts=2)
    submitted = coordinator.submit_query(request)
    coordinator.resume(submitted["job_id"])
    stopped = coordinator.resume(submitted["job_id"])
    assert calls["n"] == 2
    assert stopped["error"]["retryable"] is False


def test_missing_feature_is_not_retried(tmp_path):
    calls = {"n": 0}

    def analysis(request):
        calls["n"] += 1
        return _ok(request)

    request, _store = _bare_request(tmp_path)
    del request["input"]["feature_artifact"]
    del request["input"]["recipe"]
    coordinator = Coordinator(handlers={"analysis": analysis})
    submitted = coordinator.submit_query(request)
    assert submitted["status"] == "insufficient_data"
    assert submitted["job_status"] == "succeeded"
    coordinator.resume(submitted["job_id"])
    assert calls["n"] == 0


def test_discovery_does_not_invent_a_finding(tmp_path):
    request, _store = _bare_request(tmp_path)
    request["input"]["query"]["task_type"] = "discovery"
    request["input"]["sources"] = [{"name": "Movebank", "study_id": "study-1"}]
    del request["input"]["feature_artifact"]
    del request["input"]["recipe"]
    submitted = Coordinator().submit_query(request)
    assert submitted["status"] == "complete"
    assert submitted["result"]["metrics"] == {}
    assert submitted["result"]["findings"] == []
    assert submitted["result"]["sources"] == [{"name": "Movebank", "study_id": "study-1"}]


def test_invalid_query_has_no_job(tmp_path):
    request, _store = _bare_request(tmp_path)
    request["input"]["query"]["species"] = []
    submitted = Coordinator().submit_query(request)
    assert submitted["job_id"] is None
    assert submitted["error"]["code"] == "invalid_query"


def _bare_request(tmp_path):
    return movement_request(tmp_path)

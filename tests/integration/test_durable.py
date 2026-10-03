"""Jobs that survive a new process, and monitors that rerun only when data changes."""

from workflow.coordinator import Coordinator
from workflow.jobs import SqliteJobStore
from workflow.monitor import MonitorRegistry
from tests.integration.test_coordinator import _bare_request, _ok


def test_sqlite_job_survives_a_new_coordinator(tmp_path):
    request, _store = _bare_request(tmp_path)
    calls = {"n": 0}

    def analyze(incoming):
        calls["n"] += 1
        return _ok(incoming)

    path = tmp_path / "jobs.sqlite"
    first = Coordinator(handlers={"analysis": analyze}, job_store=SqliteJobStore(path))
    submitted = first.submit_query(request)
    second = Coordinator(handlers={"analysis": analyze}, job_store=SqliteJobStore(path))
    loaded = second.get_result(submitted["job_id"])
    again = second.submit_query(request)
    assert loaded["status"] == "complete"
    assert loaded["job_id"] == submitted["job_id"]
    assert again["job_id"] == submitted["job_id"]
    assert calls["n"] == 1


def test_failed_job_resumes_from_another_coordinator(tmp_path):
    request, _store = _bare_request(tmp_path)
    calls = {"n": 0}

    def analyze(incoming):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return _ok(incoming)

    path = tmp_path / "jobs.sqlite"
    first = Coordinator(handlers={"analysis": analyze}, job_store=SqliteJobStore(path))
    submitted = first.submit_query(request)
    second = Coordinator(handlers={"analysis": analyze}, job_store=SqliteJobStore(path))
    resumed = second.resume(submitted["job_id"])
    assert submitted["job_status"] == "failed"
    assert resumed["status"] == "complete"
    assert calls["n"] == 2


def test_monitor_reruns_only_when_the_checksum_changes(tmp_path):
    request, _store = _bare_request(tmp_path)

    def analyze(incoming):
        response = _ok(incoming)
        response["output"]["result"]["echo"] = incoming["request_id"]
        return response

    coordinator = Coordinator(handlers={"analysis": analyze})
    registry = MonitorRegistry(tmp_path / "monitors.sqlite")
    registry.register("watch-1", request)
    first = registry.run_if_changed("watch-1", "aaa", coordinator)
    repeat = registry.run_if_changed("watch-1", "aaa", coordinator)
    changed = registry.run_if_changed("watch-1", "bbb", coordinator)
    assert first["ran"] is True
    assert first["result_changed"] is False
    assert repeat == {"ran": False, "job_id": first["job_id"], "result_changed": False}
    assert changed["ran"] is True
    assert changed["result_changed"] is True
    assert changed["job_id"] != first["job_id"]


def test_a_failed_monitor_run_keeps_the_checksum_available(tmp_path):
    request, _store = _bare_request(tmp_path)
    calls = {"n": 0}

    def analyze(incoming):
        calls["n"] += 1
        raise RuntimeError("down")

    coordinator = Coordinator(handlers={"analysis": analyze})
    registry = MonitorRegistry(tmp_path / "monitors.sqlite")
    registry.register("watch-2", request)
    first = registry.run_if_changed("watch-2", "aaa", coordinator)
    second = registry.run_if_changed("watch-2", "aaa", coordinator)
    assert first["accepted"] is False
    assert second["accepted"] is False
    assert calls["n"] == 2

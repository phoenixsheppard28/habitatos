import json
import threading
from contextlib import contextmanager
from functools import partial
from http.server import ThreadingHTTPServer

import httpx
import pytest

from habitat import web
from habitat.web_assistant import QueryContext, RetrievalContext


@pytest.fixture
def api(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(web.Handler, directory=str(tmp_path)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    yield f"http://127.0.0.1:{server.server_port}"

    server.shutdown()
    server.server_close()
    thread.join()


def test_backend_failure_returns_no_placeholder_records(api, monkeypatch):
    def unavailable():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(web, "catalog", unavailable)
    response = httpx.get(f"{api}/api/catalog")

    assert response.status_code == 503
    assert "datasets" not in response.json()
    assert "backend" in response.json()["error"]


def test_catalog_uses_json_without_exposing_server_settings(api, monkeypatch):
    monkeypatch.setattr(web, "catalog", lambda: {"datasets": [], "assistant_available": True})
    response = httpx.get(f"{api}/api/catalog")

    assert response.status_code == 200
    assert response.json() == {"datasets": [], "assistant_available": True}
    assert response.headers["cache-control"] == "no-store"


def test_health_checks_catalog_readiness(api, monkeypatch):
    queries = []

    @contextmanager
    def available():
        class Database:
            def execute(self, query):
                queries.append(query)

        yield Database()

    monkeypatch.setattr(web, "connection", available)
    response = httpx.get(f"{api}/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert queries == ["SELECT dataset_id FROM latest_datasets LIMIT 0"]


def test_health_fails_when_database_is_unavailable(api, monkeypatch):
    def unavailable():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(web, "connection", unavailable)
    response = httpx.get(f"{api}/api/health")

    assert response.status_code == 503
    assert "status" not in response.json()


def test_invalid_chat_does_not_call_the_assistant(api, monkeypatch):
    def unexpected_call(request):
        pytest.fail("invalid chat reached the model")

    monkeypatch.setattr("habitat.web_assistant.answer", unexpected_call)
    response = httpx.post(f"{api}/api/chat", json={"messages": [{"role": "system", "content": "invalid"}]})

    assert response.status_code == 400


def test_unknown_dataset_is_not_treated_as_an_empty_success(api, monkeypatch):
    def missing(dataset_id):
        raise LookupError("This dataset is not available in the public catalog.")

    monkeypatch.setattr(web, "dataset_features", missing)
    response = httpx.get(f"{api}/api/features", params={"dataset_id": "private"})

    assert response.status_code == 404
    assert "features" not in response.json()


def test_retrieval_rejects_fixtures_and_unbounded_environment_requests():
    base = {"question": "Find rainfall", "bbox": [36, -2, 37, -1], "start": "2024-01-01", "end": "2024-01-02"}
    with pytest.raises(ValueError, match="live source"):
        RetrievalContext.model_validate({**base, "source_id": "fixture", "package": "development"})
    with pytest.raises(ValueError, match="at most one year"):
        RetrievalContext.model_validate({**base, "source_id": "chirps", "end": "2026-01-01"})
    with pytest.raises(ValueError, match="start date"):
        QueryContext.model_validate({**base, "end": "2023-01-01"})


def test_chat_progress_arrives_before_the_answer(api, monkeypatch):
    release = threading.Event()

    def answer(request, on_progress):
        on_progress({"stage": "preparation", "message": "Prepare the table", "status": "running"})
        assert release.wait(5), "the client did not receive progress before completion"
        return {"answer": "Prepared.", "updated": False}

    monkeypatch.setattr("habitat.web_assistant.answer", answer)
    try:
        with httpx.stream("POST", f"{api}/api/chat", headers={"Accept": "application/x-ndjson"},
                          json={"messages": [{"role": "user", "content": "Prepare movement"}]}) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("application/x-ndjson")
            lines = response.iter_lines()
            first = json.loads(next(lines))
            assert first["type"] == "progress"
            assert first["stage"] == "preparation"
            release.set()
            result = json.loads(next(lines))
            assert result == {"type": "result", "response": {"answer": "Prepared.", "updated": False}}
    finally:
        release.set()


def test_streaming_errors_use_a_stream_event(api, monkeypatch):
    def unavailable(request, on_progress):
        on_progress({"stage": "catalog", "message": "Read the catalog"})
        raise RuntimeError("private connection details")

    monkeypatch.setattr("habitat.web_assistant.answer", unavailable)
    response = httpx.post(f"{api}/api/chat", headers={"Accept": "application/x-ndjson"},
                          json={"messages": [{"role": "user", "content": "Movement?"}]})
    events = [json.loads(line) for line in response.text.splitlines()]

    assert [event["type"] for event in events] == ["progress", "error"]
    assert "private" not in response.text


def test_invalid_streaming_chat_returns_json_before_starting_the_stream(api):
    response = httpx.post(f"{api}/api/chat", headers={"Accept": "application/x-ndjson"},
                          json={"messages": [{"role": "system", "content": "invalid"}]})

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/json")

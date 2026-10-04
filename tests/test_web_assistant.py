import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from habitat import web_assistant
from habitat.web import ChatRequest


class ResponseBlock(SimpleNamespace):
    def model_dump(self, **kwargs):
        return vars(self)


def text_response(text):
    return SimpleNamespace(content=[ResponseBlock(type="text", text=text)])


def tool_response(name, payload):
    return SimpleNamespace(content=[ResponseBlock(type="tool_use", id=f"tool-{name}", name=name, input=payload)])


@pytest.fixture
def assistant(monkeypatch):
    calls = []
    responses = []

    def create(**kwargs):
        calls.append(deepcopy(kwargs))
        return responses.pop(0)

    monkeypatch.setattr(web_assistant, "settings", lambda: SimpleNamespace(anthropic_api_key="test"))
    monkeypatch.setattr(web_assistant, "client", lambda: SimpleNamespace(messages=SimpleNamespace(create=create)))
    monkeypatch.setattr(web_assistant, "catalog", lambda: {"datasets": [], "assistant_available": True})

    return responses, calls


def test_missing_data_offer_does_not_retrieve_before_the_user_agrees(assistant, monkeypatch):
    responses, _ = assistant
    offer = "Would you like me to fetch CHIRPS rainfall for this region and date range?"
    responses.append(text_response(offer))
    retrieve = Mock()
    monkeypatch.setattr(web_assistant, "retrieve", retrieve)
    request = ChatRequest(messages=[{"role": "user", "content": "How much rainfall was there?"}])

    response = web_assistant.answer(request)

    assert response["answer"] == offer
    assert response["updated"] is False
    assert response["retrieved_dataset_ids"] == []
    retrieve.assert_not_called()


def test_confirmation_retrieves_refreshes_evidence_and_returns_the_published_dataset(assistant, monkeypatch):
    responses, calls = assistant
    payload = {"question": "How much rainfall was there?", "source_id": "chirps",
               "bbox": [36, -2, 37, -1], "start": "2024-01-01", "end": "2024-01-02"}
    responses.extend([
        tool_response("retrieve", payload),
        tool_response("summarize_dataset", {"dataset_id": "rainfall"}),
        text_response("## Rainfall\nThe new dataset contains two daily observations."),
    ])
    dataset = {"dataset_id": "rainfall", "variables": ["rainfall_mm"], "version": 1}
    catalog = Mock(side_effect=[
        {"datasets": [], "assistant_available": True},
        {"datasets": [dataset], "assistant_available": True},
    ])
    retrieve = Mock(return_value={"status": "ok", "published": ["rainfall"], "warnings": [], "outcomes": []})
    citation = {"source": {"name": "CHIRPS", "url": "https://www.chc.ucsb.edu/data/chirps"}}
    snapshot = {"dataset_id": "rainfall", "version": 1, "monthly": [{"month": "2024-01", "records": 2}],
                "total_records": 2, "sources": [citation], "grain": "Daily rainfall", "truncated": False}
    monkeypatch.setattr(web_assistant, "catalog", catalog)
    monkeypatch.setattr(web_assistant, "retrieve", retrieve)
    summarize = Mock(return_value=snapshot)
    monkeypatch.setattr(web_assistant, "dataset_features", summarize)
    request = ChatRequest(messages=[
        {"role": "user", "content": "How much rainfall fell in bbox [36,-2,37,-1] on January 1–2, 2024?"},
        {"role": "assistant", "content": "There is no rainfall data. Would you like me to fetch it from CHIRPS?"},
        {"role": "user", "content": "yes"},
    ])

    response = web_assistant.answer(request)

    retrieve.assert_called_once_with(payload)
    summarize.assert_called_once_with("rainfall")
    assert response["updated"] is True
    assert response["retrieved_dataset_ids"] == ["rainfall"]
    assert response["citations"] == [citation]
    refreshed_context = json.loads(calls[1]["system"].split("Workspace context: ", 1)[1])
    assert refreshed_context["datasets"] == [dataset]
    assert refreshed_context["retrieved_dataset_ids"] == ["rainfall"]
    assert calls[0]["messages"][-1] == {"role": "user", "content": "yes"}


def test_empty_retrieval_does_not_report_a_workspace_update(assistant, monkeypatch):
    responses, _ = assistant
    responses.extend([
        tool_response("retrieve", {"source_id": "chirps"}),
        text_response("The source returned no observations for those dates."),
    ])
    monkeypatch.setattr(web_assistant, "retrieve", Mock(return_value={
        "status": "insufficient_data", "published": [], "warnings": [], "outcomes": [],
    }))
    request = ChatRequest(messages=[{"role": "user", "content": "Fetch the rainfall data."}])

    response = web_assistant.answer(request)

    assert response["updated"] is False
    assert response["retrieved_dataset_ids"] == []


def test_retrieval_always_runs_the_fetch_agent_with_the_species(monkeypatch):
    run = Mock(return_value=SimpleNamespace(
        status="ok", warnings=[], outcomes=[], published=["emu"],
        fetch=SimpleNamespace(extensions={"agent_summary": "Found an emu package."}),
    ))
    monkeypatch.setattr(web_assistant, "run_pipeline", run)

    result = web_assistant.retrieve({"question": "Emu movement", "bbox": [137, -32.5, 140, -29.5],
                                     "start": "2025-10-01", "end": "2026-09-30",
                                     "species": ["Dromaius novaehollandiae"]})

    request = run.call_args.args[0]
    assert run.call_args.kwargs == {"use_agent": True}
    assert request.input.requirements.species == ["Dromaius novaehollandiae"]
    assert request.input.requirements.package is None
    assert result["agent_summary"] == "Found an emu package."
    assert result["published"] == ["emu"]


def test_retrieval_without_a_source_keeps_the_area_limit():
    with pytest.raises(ValueError, match="at most one year"):
        web_assistant.RetrievalContext.model_validate({"question": "Emus", "bbox": [129, -38, 141, -26],
                                                       "start": "2025-01-01", "end": "2025-06-01"})


def test_many_analyses_reuse_one_prepared_table(tmp_path, monkeypatch):
    prepared_id = "a" * 32
    directory = tmp_path / "web" / prepared_id
    directory.mkdir(parents=True)
    query = {"query_id": "q", "question": "Prepare emu movement and rainfall", "task_type": "historical"}
    (directory / "prepared.json").write_text(json.dumps({"query": query, "recipe": {"status": "ok"}}))
    monkeypatch.setattr(web_assistant, "settings", lambda: SimpleNamespace(runs_dir=tmp_path))
    handoff = Mock(side_effect=lambda query, recipe, request_id: {"query": query})
    monkeypatch.setattr(web_assistant, "analysis_request", handoff)
    run = Mock(return_value={"status": "ok", "output": {}, "warnings": [], "error": None})
    monkeypatch.setattr(web_assistant, "run_analysis", run)

    first = web_assistant.analyze({"prepared_id": prepared_id, "question": "Does rainfall predict displacement?"})
    second = web_assistant.analyze({"prepared_id": prepared_id, "question": "Compare wet and dry seasons",
                                    "comparison_windows": [
                                        {"name": "wet", "start": "2026-01-01T00:00:00Z",
                                         "end": "2026-03-31T00:00:00Z"},
                                        {"name": "dry", "start": "2026-06-01T00:00:00Z",
                                         "end": "2026-08-31T00:00:00Z"}]})

    queries = [call.args[0] for call in handoff.call_args_list]
    assert [query["question"] for query in queries] == ["Does rainfall predict displacement?",
                                                        "Compare wet and dry seasons"]
    assert queries[0]["comparison_windows"] is None
    assert [window["name"] for window in queries[1]["comparison_windows"]] == ["wet", "dry"]
    assert first["analysis_id"] != second["analysis_id"]
    assert run.call_count == 2


def test_analysis_without_a_prepared_table_asks_for_prepare(tmp_path, monkeypatch):
    monkeypatch.setattr(web_assistant, "settings", lambda: SimpleNamespace(runs_dir=tmp_path))

    result = web_assistant.analyze({"prepared_id": "b" * 32, "question": "Any trend?"})

    assert result["status"] == "error"
    assert "prepare" in result["error"]


def test_analysis_rejects_a_prepared_id_that_is_a_path():
    with pytest.raises(ValueError):
        web_assistant.AnalysisContext.model_validate({"prepared_id": "../../etc", "question": "x"})


def test_progress_reports_model_and_tool_timings(assistant, monkeypatch):
    responses, _ = assistant
    responses.extend([tool_response("prepare", {"question": "Movement"}), text_response("No table is available.")])
    monkeypatch.setattr(web_assistant, "prepare", Mock(return_value={"status": "error", "error": {"code": "INVALID_PLAN"}}))
    events = []

    result = web_assistant.answer(ChatRequest(messages=[{"role": "user", "content": "Movement?"}]),
                                  on_progress=events.append)

    assert result["answer"] == "No table is available."
    assert {event["request_id"] for event in events} == {result["request_id"]}
    assert any(event["stage"] == "assistant" and event["status"] == "running" for event in events)
    assert any(event["stage"] == "preparation" and event["status"] == "complete" for event in events)
    assert all(event["duration_seconds"] >= 0 for event in result["timings"])
    assert result["timings"][-1]["stage"] == "chat"


def test_identical_failed_preparation_is_not_executed_twice(assistant, monkeypatch):
    responses, _ = assistant
    payload = {"question": "Movement"}
    responses.extend([tool_response("prepare", payload), tool_response("prepare", payload), text_response("Preparation failed.")])
    prepare = Mock(return_value={"status": "error", "error": {"code": "INVALID_PLAN"}})
    monkeypatch.setattr(web_assistant, "prepare", prepare)

    result = web_assistant.answer(ChatRequest(messages=[{"role": "user", "content": "Movement?"}]))

    prepare.assert_called_once_with(payload)
    assert result["answer"] == "Preparation failed."

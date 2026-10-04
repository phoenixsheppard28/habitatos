import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from habitat import web_assistant
from habitat.web import ChatRequest
from support import movement_request


class ResponseBlock(SimpleNamespace):
    def model_dump(self, **kwargs):
        return {"role": "assistant", "content": self.content,
                "tool_calls": [{"id": call.id, "type": "function",
                                "function": {"name": call.function.name, "arguments": call.function.arguments}}
                               for call in self.tool_calls or []]}


def text_response(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=ResponseBlock(content=text, tool_calls=None))])


def tool_response(name, payload):
    call = SimpleNamespace(id=f"tool-{name}", function=SimpleNamespace(name=name, arguments=json.dumps(payload)))
    return SimpleNamespace(choices=[SimpleNamespace(message=ResponseBlock(content=None, tool_calls=[call]))])


@pytest.fixture
def assistant(monkeypatch):
    calls = []
    responses = []

    def create(**kwargs):
        calls.append(deepcopy(kwargs))
        return responses.pop(0)

    monkeypatch.setattr(web_assistant, "settings", lambda: SimpleNamespace(openai_api_key="test"))
    monkeypatch.setattr(web_assistant, "client", lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
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


def test_planner_uses_openai_answer_function(monkeypatch):
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return tool_response("answer", {"value": [{"dataset_id": "rainfall"}]})

    monkeypatch.setattr(web_assistant, "client", lambda: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))))

    result = web_assistant.generate_plan(
        instructions="Select datasets", context={"datasets": []}, schema={"type": "array", "items": {"type": "object"}},
    )

    assert result == [{"dataset_id": "rainfall"}]
    assert calls[0]["tool_choice"] == {"type": "function", "function": {"name": "answer"}}
    assert calls[0]["tools"][0]["function"]["parameters"]["properties"]["value"]["type"] == "array"


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
    refreshed_context = json.loads(calls[1]["messages"][0]["content"].split("Workspace context: ", 1)[1])
    assert refreshed_context["datasets"] == [dataset]
    assert refreshed_context["retrieved_dataset_ids"] == ["rainfall"]
    assert calls[0]["messages"][-1] == {"role": "user", "content": "yes"}
    assert calls[1]["messages"][-2]["tool_calls"][0]["function"]["name"] == "retrieve"
    assert calls[1]["messages"][-1]["role"] == "tool"


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


def test_retrieved_satellite_source_is_preserved_for_preparation(assistant, monkeypatch):
    responses, _ = assistant
    payload = {"question": "Bear residence time", "analysis_method": "residence_time"}
    responses.extend([tool_response("retrieve", {"source_id": "sentinel2"}),
                      tool_response("prepare", payload), text_response("Preparation failed.")])
    monkeypatch.setattr(web_assistant, "retrieve", Mock(return_value={"published": [], "status": "ok"}))
    prepare = Mock(return_value={"status": "error"})
    monkeypatch.setattr(web_assistant, "prepare", prepare)

    web_assistant.answer(ChatRequest(messages=[{"role": "user", "content": "Use Sentinel-2"}]))

    prepare.assert_called_once_with({**payload, "vegetation_source_id": "sentinel2"})


def test_preparation_query_preserves_source_method_and_gap_limit():
    query = web_assistant.QueryContext.model_validate({
        "question": "Time spent in greener places", "bbox": [-73.37, 41.49, -72.59, 42.06],
        "start": "2019-02-20", "end": "2020-02-19", "vegetation_source_id": "modis_mod13q1",
        "analysis_method": "residence_time", "max_tracking_gap_hours": 4,
    }).query()

    assert query["extensions"]["vegetation_source_id"] == "modis_mod13q1"
    assert query["analysis_method"] == "residence_time"
    assert query["max_tracking_gap_hours"] == 4


def test_analysis_results_reach_the_browser_without_the_map_or_model(assistant, monkeypatch):
    responses, calls = assistant
    responses.extend([
        tool_response("analyze", {"prepared_id": "a" * 32, "question": "Chart daily movement"}),
        tool_response("analyze", {"prepared_id": "a" * 32, "question": "Compare wet and dry windows"}),
        text_response("The analyses are in Charts."),
    ])
    result = {
        "result_id": "movement", "question": "Chart daily movement", "created_at": "2026-10-04T00:00:00Z",
        "status": "complete", "findings": ["Median displacement was 7 km."],
        "metrics": {"displacement_km_median": 7},
        "timeline": {"series": [{"date": "2026-01-01", "median_daily_displacement": None,
                                  "unit": "km", "predicted": False},
                                 {"date": "2026-01-02", "median_daily_displacement": 7,
                                  "unit": "km", "predicted": False}]},
        "evidence": {"datasets": [{"dataset_id": "movement", "version": 1}]},
        "limitations": ["Tracked sample only."], "artifact_versions": {"method": "movement_summary@1"},
        "map": {"type": "FeatureCollection", "features": []}, "model_reference": None,
    }
    second = {**result, "result_id": "comparison", "question": "Compare wet and dry windows", "status": "partial"}
    analyze = Mock(side_effect=[
        {"analysis_id": "first", "prepared_id": "a" * 32, "status": "ok", "warnings": [],
         "output": {"result": result, "model_artifact": None}},
        {"analysis_id": "second", "prepared_id": "a" * 32, "status": "partial", "warnings": ["One gap."],
         "output": {"result": second, "model_artifact": None}},
    ])
    monkeypatch.setattr(web_assistant, "analyze", analyze)

    response = web_assistant.answer(ChatRequest(messages=[{"role": "user", "content": "Analyze and chart movement"}]))

    assert response["updated"] is False
    assert [analysis["analysis_id"] for analysis in response["analyses"]] == ["first", "second"]
    assert response["analyses"][0]["result"]["timeline"] == result["timeline"]
    assert response["analyses"][1]["warnings"] == ["One gap."]
    assert response["analyses"][0]["result"]["evidence"] == result["evidence"]
    assert response["analyses"][0]["charts"] == []
    assert "map" not in response["analyses"][0]["result"]
    assert "model_reference" not in response["analyses"][0]["result"]
    assert "Charts tab" in calls[0]["messages"][0]["content"]


@pytest.mark.parametrize("status", ["error", "insufficient_data"])
def test_failed_analysis_does_not_create_a_chart(assistant, monkeypatch, status):
    responses, _ = assistant
    responses.extend([tool_response("analyze", {}), text_response("No evidence is available.")])
    monkeypatch.setattr(web_assistant, "analyze", Mock(return_value={
        "status": status, "output": {"result": {"status": "insufficient_data", "findings": []}},
    }))

    response = web_assistant.answer(ChatRequest(messages=[{"role": "user", "content": "Analyze movement"}]))

    assert response["analyses"] == []


def test_tool_limit_preserves_completed_analysis_charts(assistant, monkeypatch):
    responses, _ = assistant
    responses.append(tool_response("analyze", {}))
    monkeypatch.setattr(web_assistant, "MAX_TOOL_ROUNDS", 1)
    monkeypatch.setattr(web_assistant, "analyze", Mock(return_value={
        "analysis_id": "completed", "prepared_id": "a" * 32, "status": "ok", "warnings": [],
        "output": {"result": {"status": "complete", "findings": ["Result available."]}},
    }))

    response = web_assistant.answer(ChatRequest(messages=[{"role": "user", "content": "Analyze movement"}]))

    assert response["analyses"][0]["analysis_id"] == "completed"


@pytest.fixture
def prepared(assistant, tmp_path, monkeypatch):
    request, store = movement_request(tmp_path)
    prepared_id = "a" * 32
    directory = tmp_path / "web" / prepared_id
    directory.mkdir(parents=True)
    (directory / "prepared.json").write_text(json.dumps({"query": request["input"]["query"], "recipe": {}}))
    monkeypatch.setattr(web_assistant, "settings", lambda: SimpleNamespace(runs_dir=tmp_path, openai_api_key="test"))
    monkeypatch.setattr(web_assistant, "RecipeArtifactReader", lambda *args: store)
    monkeypatch.setattr(web_assistant, "uuid4", lambda: SimpleNamespace(hex="b" * 32))

    def handoff(query, recipe, request_id):
        return {**request, "request_id": request_id, "query_id": query["query_id"],
                "input": {**request["input"], "query": query}}

    monkeypatch.setattr(web_assistant, "analysis_request", handoff)
    return prepared_id


def test_plot_code_runs_on_the_prepared_table_and_the_figure_reaches_the_browser(assistant, prepared):
    responses, calls = assistant
    code = "fig = px.scatter(df, x='rain_mm', y='km_moved', title='Movement and rainfall')"
    responses.extend([
        tool_response("analyze", {"prepared_id": prepared, "question": "Is movement related to rainfall?"}),
        tool_response("plot", {"analysis_id": "b" * 32, "title": "Movement and rainfall", "code": code}),
        text_response("The chart is in the Charts tab."),
    ])

    response = web_assistant.answer(ChatRequest(messages=[{"role": "user", "content": "Chart movement and rainfall"}]))

    chart = response["analyses"][0]["charts"][0]
    assert chart["title"] == "Movement and rainfall"
    assert chart["code"] == code
    assert chart["figure"]["data"][0]["type"] == "scatter"
    assert chart["figure"]["layout"]["template"]["layout"]["colorway"][0] == "#387c78"
    tool_result = json.loads(calls[2]["messages"][-1]["content"])
    assert tool_result == {"status": "ok", "chart_id": "b" * 32, "figure": {
        "title": "Movement and rainfall", "traces": [{"type": "scatter", "mode": "markers"}]}}


def test_plot_errors_return_the_traceback_so_the_model_can_fix_the_code(assistant, prepared):
    responses, calls = assistant
    responses.extend([
        tool_response("analyze", {"prepared_id": prepared, "question": "Chart movement"}),
        tool_response("plot", {"analysis_id": "b" * 32, "title": "Movement", "code": "fig = px.scatter(df, x='missing')"}),
        text_response("The chart failed."),
    ])

    response = web_assistant.answer(ChatRequest(messages=[{"role": "user", "content": "Chart movement"}]))

    assert response["analyses"][0]["charts"] == []
    tool_result = json.loads(calls[2]["messages"][-1]["content"])
    assert tool_result["status"] == "error"
    assert "missing" in tool_result["error"]
    assert 'File "<plot>"' in tool_result["error"]


def test_plot_needs_an_analysis_from_the_same_turn(assistant):
    responses, calls = assistant
    responses.extend([
        tool_response("plot", {"analysis_id": "c" * 32, "title": "Movement", "code": "fig = go.Figure()"}),
        text_response("Analyze first."),
    ])

    web_assistant.answer(ChatRequest(messages=[{"role": "user", "content": "Chart movement"}]))

    tool_result = json.loads(calls[1]["messages"][-1]["content"])
    assert "Call analyze first" in tool_result["error"]


def test_statistics_tables_and_method_selection_reach_the_browser(assistant, tmp_path, monkeypatch):
    responses, calls = assistant
    request, store = movement_request(tmp_path)
    prepared_id = "c" * 32
    directory = tmp_path / "web" / prepared_id
    directory.mkdir(parents=True)
    (directory / "prepared.json").write_text(json.dumps({"query": request["input"]["query"], "recipe": {}}))
    monkeypatch.setattr(web_assistant, "settings", lambda: SimpleNamespace(runs_dir=tmp_path, openai_api_key="test"))
    monkeypatch.setattr(web_assistant, "RecipeArtifactReader", lambda *args: store)
    monkeypatch.setattr(web_assistant, "analysis_request", lambda query, recipe, request_id: {
        **request, "request_id": request_id, "query_id": query["query_id"], "input": {**request["input"], "query": query},
    })
    responses.extend([
        tool_response("analyze", {"prepared_id": prepared_id, "question": "Show rainfall statistics",
                                  "analysis": {"method": "statistics", "variables": ["rain_mm"]}}),
        text_response("The statistics table is in Charts."),
    ])

    response = web_assistant.answer(ChatRequest(messages=[{"role": "user", "content": "Show rainfall statistics"}]))

    analysis = response["analyses"][0]
    assert analysis["charts"] == []
    assert analysis["result"]["artifact_versions"]["method"] == "statistics@1"
    assert analysis["result"]["tables"][0]["title"] == "Descriptive statistics"
    assert analysis["result"]["metrics"]["variables"]["rainfall"]["n"] == 7
    tool = next(tool for tool in calls[0]["tools"] if tool["function"]["name"] == "analyze")
    assert "analysis" in tool["function"]["parameters"]["properties"]

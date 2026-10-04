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

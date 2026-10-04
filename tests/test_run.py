import asyncio

import pytest

from fake_openai import FakeOpenAI, text, tool_use
from habitat import llm
from habitat.contracts import FetchRequest, FetchRequestInput, FetchRequirements, QuerySpec, TimeRange
from habitat.fetch import agent, run as fetch_run, service
from habitat.fetch.run import run, run_with_agent
from habitat.fetch.session import Receipts, current_receipts, record


def request(question="demo", **requirements) -> FetchRequest:
    return FetchRequest(
        request_id="req-1", query_id="q-1",
        input=FetchRequestInput(
            query=QuerySpec(query_id="q-1", question=question, task_type="discovery"),
            requirements=FetchRequirements(**requirements),
        ),
    )


@pytest.fixture
def fake_openai(monkeypatch):
    def install(turns, repeat_last=False):
        fake = FakeOpenAI(turns, repeat_last)
        monkeypatch.setattr(llm, "client", fake.client)
        return fake

    return install


def test_run_deterministic_returns_manifests():
    req = FetchRequest(
        request_id="req-1", query_id="q-1",
        input=FetchRequestInput(
            query=QuerySpec(
                query_id="q-1", question="Demo movement data", task_type="discovery", species=["example-antelope"],
                time_range=TimeRange(start="2025-01-01T00:00:00Z", end="2025-01-07T23:59:59Z"),
            ),
            requirements=FetchRequirements(species=["example-antelope"], data_kinds=["animal_locations"]),
        ),
    )

    resp = run(req, use_agent=False)

    assert resp.status == "ok"
    assert len(resp.output.raw_artifacts) == 1
    assert resp.output.raw_artifacts[0].source.study_id == "study-demo-1"
    assert resp.output.raw_artifacts[0].storage.uri.startswith("artifact://")


def test_coverage_mismatch_reported():
    req = request("Demo", species=["example-antelope"], start="2026-01-01", end="2026-01-31", bbox=[0, 0, 1, 1])
    req.input.query.species = ["example-antelope"]

    result = run(req)

    assert result.status == "partial"
    assert any("requested" in warning for warning in result.warnings)
    assert result.output.raw_artifacts[0].coverage.start.year == 2025


def test_agent_searches_then_downloads_one_artifact(fake_openai):
    fake = fake_openai([
        [tool_use("search_catalog", query="antelope movement", include_internet=False)],
        [tool_use("download_dataset", dataset_id="fixture-movement-001")],
        text("Downloaded the demo antelope tracks."),
    ])

    result = asyncio.run(run_with_agent(request()))

    assert result.status == "ok"
    assert [a.artifact_id for a in result.output.raw_artifacts] == ["fixture-movement-001"]
    assert result.extensions["agent_summary"] == "Downloaded the demo antelope tracks."
    assert "fixture-movement-001" in fake.tool_results()[0]
    first = fake.requests[0]
    assert first["model"] == llm.FETCH_MODEL
    assert first["messages"][0] == {"role": "system", "content": agent.FETCH_INSTRUCTIONS}
    assert {t["function"]["name"] for t in first["tools"]} == {t.__name__ for t in agent.FETCH_AGENT_TOOLS}


def test_agent_loop_limit_returns_partial(fake_openai, monkeypatch):
    monkeypatch.setattr(agent, "MAX_ITERATIONS", 3)
    monkeypatch.setattr(fetch_run, "run_agent", lambda prompt: agent.run_agent(prompt, max_iterations=3))
    fake = fake_openai([
        [tool_use("download_dataset", dataset_id="fixture-movement-001")],
        [tool_use("list_downloaded_files")],
    ], repeat_last=True)

    result = asyncio.run(run_with_agent(request()))

    assert len(fake.requests) == 3
    assert result.status == "partial"
    assert result.error.code == "iteration_limit"
    assert any("limit" in warning for warning in result.warnings)
    assert len(result.output.raw_artifacts) == 1


def test_agent_handoff_uses_only_this_requests_receipts(fake_openai):
    service.download_dataset("fixture-rainfall-001")
    fake_openai([[tool_use("download_dataset", dataset_id="fixture-movement-001")], text("done")])

    result = asyncio.run(run_with_agent(request()))

    assert [a.source.study_id for a in result.output.raw_artifacts] == ["study-demo-1"]


def test_agent_claim_without_download_is_insufficient(fake_openai):
    fake_openai([text("I downloaded everything")])

    result = asyncio.run(run_with_agent(request()))

    assert result.status == "insufficient_data"
    assert not result.output.raw_artifacts


def test_agent_keeps_downloads_after_failure(fake_openai, monkeypatch):
    def failing_agent(prompt):
        service.download_dataset("fixture-movement-001")
        raise TimeoutError()

    monkeypatch.setattr(fetch_run, "run_agent", failing_agent)

    result = asyncio.run(run_with_agent(request()))

    assert result.status == "partial"
    assert len(result.output.raw_artifacts) == 1
    assert result.error.code == "agent_failed"


def test_account_scoped_downloads_are_not_in_the_public_handoff():
    from conftest import make_manifest
    from datetime import UTC, datetime

    manifest = make_manifest("movebank_study", {}, datetime(2024, 1, 1, tzinfo=UTC)).model_copy(
        update={"access_scope": "movebank-account"}
    )
    receipts = Receipts()
    token = current_receipts.set(receipts)
    try:
        record(manifest)
    finally:
        current_receipts.reset(token)

    assert receipts.artifacts == {}
    assert "excluded from public handoff" in receipts.warnings[0]


@pytest.mark.live
def test_live_agent_answers_a_fixture_request():
    result = asyncio.run(run_with_agent(request("Download the demo antelope movement fixture")))

    assert result.status in ("ok", "partial")

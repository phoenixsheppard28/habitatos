"""Contract v1 adapter: run(fetch_request) -> fetch_response."""

from __future__ import annotations

import asyncio
from typing import Any

from agents import Runner

from fetch.agent import build_fetch_agent
from fetch.models import (
    FetchError,
    FetchRequest,
    FetchResponse,
    FetchResponseOutput,
    RawManifest,
)
from fetch import service


def _requirements_prompt(req: FetchRequest) -> str:
    q = req.input.query
    r = req.input.requirements
    parts = [
        f"Question: {q.question}",
        f"Task: {q.task_type}",
        f"Species: {', '.join(q.species) or 'unspecified'}",
        f"Time range: {q.time_range.start} .. {q.time_range.end}",
        f"Required data kinds: {', '.join(r.data_kinds) or 'any supported'}",
        "Find suitable datasets, check access, download permitted matches, "
        "and summarize raw artifacts retrieved.",
    ]
    return "\n".join(parts)


def run_deterministic(req: FetchRequest) -> FetchResponse:
    """
    Non-LLM path: search fixture catalog from requirements and download matches.
    Useful for tests and CI without OPENROUTER_API_KEY.
    """
    base = FetchResponse(
        request_id=req.request_id,
        query_id=req.query_id,
        access_scope=req.access_scope,
        status="ok",
    )
    species = req.input.requirements.species or req.input.query.species
    kinds = set(req.input.requirements.data_kinds)
    hits = service.search_catalog(req.input.query.question, species=species or None)
    if not hits and species:
        hits = service.search_catalog("", species=species)

    manifests: list[RawManifest] = []
    for hit in hits:
        if kinds and hit.get("data_kind") not in kinds:
            continue
        ds_id = hit["dataset_id"]
        access = service.check_access(ds_id)
        if access.get("status") != "available":
            base.warnings.append(f"Skipped {ds_id}: {access.get('status')}")
            continue
        dl = service.download_dataset(ds_id)
        if isinstance(dl, RawManifest):
            manifests.append(dl)
        else:
            base.warnings.append(f"Download failed for {ds_id}: {dl.get('message')}")

    if not manifests:
        base.status = "insufficient_data"
        base.error = FetchError(
            code="no_datasets",
            message="No matching datasets could be retrieved.",
            retryable=False,
        )
    base.output = FetchResponseOutput(raw_artifacts=manifests)
    return base


async def run_with_agent(req: FetchRequest) -> FetchResponse:
    agent = build_fetch_agent()
    prompt = _requirements_prompt(req)
    try:
        result = await Runner.run(agent, prompt)
    except Exception as exc:  # noqa: BLE001 — surface agent failures in contract
        return FetchResponse(
            request_id=req.request_id,
            query_id=req.query_id,
            access_scope=req.access_scope,
            status="error",
            error=FetchError(
                code="agent_failed",
                message=str(exc),
                retryable=True,
            ),
        )

    # After agent session, collect manifests registered this run from archive index
    # (agent tools write via download_dataset). Re-run deterministic merge for ids
    # mentioned is fragile; instead list manifests for fixture downloads done in session.
    deterministic = run_deterministic(req)
    text = (result.final_output or "").strip()
    deterministic.extensions["agent_summary"] = text
    if deterministic.output.raw_artifacts:
        deterministic.status = "ok"
        deterministic.error = None
    elif text:
        deterministic.status = "partial"
        deterministic.warnings.append(
            "Agent completed but no new raw artifacts were registered; see agent_summary."
        )
    return deterministic


def run(req: FetchRequest | dict[str, Any], *, use_agent: bool = False) -> FetchResponse:
    """Entry point for Coordinator -> Fetch boundary."""
    request = req if isinstance(req, FetchRequest) else FetchRequest.model_validate(req)
    if use_agent:
        return asyncio.run(run_with_agent(request))
    return run_deterministic(request)

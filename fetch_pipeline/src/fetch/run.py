"""Contract v1 adapter: run(fetch_request) -> fetch_response."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4
from fetch import paths
from fetch.session import Receipts, current_receipts
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
from fetch.coverage import coverage_gaps


def _requirements_prompt(req: FetchRequest) -> str:
    q = req.input.query
    r = req.input.requirements
    parts = [
        f"Question: {q.question}",
        f"Task: {q.task_type}",
        f"Species: {', '.join(q.species) or 'unspecified'}",
        f"Time range: {q.time_range.start} .. {q.time_range.end}",
        f"Region: {q.region}; bounding box: {r.bbox}",
        f"Requirement species: {r.species}; dates: {r.start} .. {r.end}",
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

    # Environmental fixtures have no species; do not discard rainfall with a species filter.
    if "rainfall_observations" in kinds:
        known = {hit.get("dataset_id") for hit in hits}
        hits.extend(hit for hit in service.search_catalog("rainfall") if hit.get("dataset_id") not in known)
    manifests: list[RawManifest] = []
    retrieved_kinds = set()
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
            retrieved_kinds.add(hit.get("data_kind"))
            base.warnings.extend(coverage_gaps(dl, req))
        else:
            base.warnings.append(f"Download failed for {ds_id}: {dl.get('message')}")

    for kind in sorted(kinds - retrieved_kinds):
        base.warnings.append(f"No artifacts retrieved for required kind: {kind}")
    if manifests and base.warnings:
        base.status = "partial"
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
    receipts = Receipts()
    token = current_receipts.set(receipts)
    failure = None
    summary = ""
    try:
        result = await Runner.run(build_fetch_agent(), _requirements_prompt(req), max_turns=20)
        summary = str(result.final_output or "")
    except Exception as exc:
        failure = FetchError(code="agent_failed", message=f"Agent failed ({type(exc).__name__}).", retryable=True)
    finally:
        current_receipts.reset(token)
    warnings = list(dict.fromkeys(receipts.warnings))
    artifacts = []
    for artifact in receipts.artifacts.values():
        if artifact.access_scope == req.access_scope:
            artifacts.append(artifact)
        else:
            warnings.append(f"Skipped {artifact.artifact_id}: artifact access scope does not match request.")
    for artifact in artifacts:
        warnings.extend(coverage_gaps(artifact, req))
    found_kinds = {artifact.extensions.get("data_kind") for artifact in artifacts}
    for missing in sorted(set(req.input.requirements.data_kinds) - found_kinds):
        warnings.append(f"No artifacts retrieved for required kind: {missing}")
    # Completion means bytes were retrieved, never that scientific sufficiency was established.
    if artifacts:
        status = "partial" if failure or warnings else "ok"
    else:
        status = "error" if failure else "insufficient_data"
    return FetchResponse(
        request_id=req.request_id, query_id=req.query_id, access_scope=req.access_scope,
        status=status, output=FetchResponseOutput(raw_artifacts=artifacts),
        warnings=warnings, error=failure,
        extensions={"agent_summary": summary, "coverage_verified": False},
    )


def save_run(request: dict, response: dict) -> str:
    """Persist request-scoped handoff; no scan of unrelated global archive entries."""
    directory = paths.DATA_ROOT / "runs" / uuid4().hex
    directory.mkdir(parents=True, exist_ok=True)
    response = {**response, "extensions": {**response.get("extensions", {}), "run_directory": str(directory)}}
    (directory / "request.json").write_text(json.dumps(request, indent=2))
    (directory / "response.json").write_text(json.dumps(response, indent=2))
    artifacts = response.get("output", {}).get("raw_artifacts", response.get("raw_artifacts", []))
    (directory / "manifest.jsonl").write_text("".join(json.dumps(a) + "\n" for a in artifacts))
    return str(directory)


def run(req: FetchRequest | dict[str, Any], *, use_agent: bool = False) -> FetchResponse:
    """Entry point for Coordinator -> Fetch boundary."""
    request = req if isinstance(req, FetchRequest) else FetchRequest.model_validate(req)
    if request.access_scope != "public":
        return FetchResponse(request_id=request.request_id, query_id=request.query_id,
                             access_scope=request.access_scope, status="error",
                             error=FetchError(code="unsupported_scope", message="This fetcher only handles public data."))
    response = asyncio.run(run_with_agent(request)) if use_agent else run_deterministic(request)
    response.extensions["run_directory"] = save_run(request.model_dump(mode="json"), response.model_dump(mode="json"))
    return response

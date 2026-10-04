"""Contract v1 adapter: run(fetch_request) -> fetch_response."""

import asyncio
import json
from datetime import date
from typing import Any
from uuid import uuid4

from habitat.archive import Archive
from habitat.config import settings
from habitat.contracts import FetchError, FetchRequest, FetchResponse, FetchResponseOutput, RawManifest
from habitat.fetch import service
from habitat.fetch.agent import MAX_ITERATIONS, run_agent
from habitat.fetch.connectors import ConnectorRequest
from habitat.fetch.coverage import source_coverage_gaps
from habitat.fetch.session import Receipts, current_archive, current_receipts


def requirements_prompt(req: FetchRequest) -> str:
    query, requirements = req.input.query, req.input.requirements
    return "\n".join([
        f"Question: {query.question}",
        f"Task: {query.task_type}",
        f"Species: {', '.join(query.species) or 'unspecified'}",
        f"Time range: {query.time_range.start} .. {query.time_range.end}",
        f"Region: {query.region}; bounding box: {requirements.bbox}",
        f"Requirement species: {requirements.species}; dates: {requirements.start} .. {requirements.end}",
        f"Required data kinds: {', '.join(requirements.data_kinds) or 'any supported'}",
        f"Preferred sources: {', '.join(requirements.source_ids) or 'choose suitable sources'}",
        f"Named package or study: {requirements.package or 'none; search for one'}",
        "Find suitable datasets, check access, download permitted matches, and summarize raw artifacts retrieved.",
    ])


def connector_request(req: FetchRequest) -> ConnectorRequest:
    requirements = req.input.requirements
    return ConnectorRequest(
        bbox=tuple(requirements.bbox) if requirements.bbox else None,
        start=date.fromisoformat(requirements.start[:10]) if requirements.start else None,
        end=date.fromisoformat(requirements.end[:10]) if requirements.end else None,
        item=requirements.package,
        access_scope=req.access_scope,
    )


def response_from(
    req: FetchRequest, artifacts: list[RawManifest], warnings: list[str], failure: FetchError | None, **extensions
) -> FetchResponse:
    warnings = list(warnings) + source_coverage_gaps(artifacts, req)

    found_kinds = {kind for artifact in artifacts for kind in data_kinds(artifact)}
    for missing in sorted(set(req.input.requirements.data_kinds) - found_kinds):
        warnings.append(f"No artifacts retrieved for required kind: {missing}")
    warnings = list(dict.fromkeys(warnings))

    # Completion means bytes were retrieved, never that scientific sufficiency was established.
    if artifacts:
        status = "partial" if failure or warnings else "ok"
    else:
        status = "error" if failure else "insufficient_data"
        failure = failure or FetchError(code="no_datasets", message="No matching datasets could be retrieved.")

    return FetchResponse(
        request_id=req.request_id, query_id=req.query_id, access_scope=req.access_scope, status=status,
        output=FetchResponseOutput(raw_artifacts=artifacts), warnings=warnings, error=failure,
        extensions={"coverage_verified": False, **extensions},
    )


def data_kinds(manifest: RawManifest) -> set[str]:
    from habitat.sources import get_source

    declared = manifest.extensions.properties.get("data_kind")
    if declared:
        return {declared}
    source = get_source(manifest.extensions.source_id)
    return set(source.data_kinds) if source else set()


def run_deterministic(req: FetchRequest) -> FetchResponse:
    """Non-LLM path. Named sources run their connector; otherwise search the catalog and download the matches."""
    receipts = Receipts()
    token = current_receipts.set(receipts)
    try:
        if req.input.requirements.source_ids:
            for source_id in req.input.requirements.source_ids:
                service.run_connector(source_id, connector_request(req))
        else:
            download_catalog_matches(req, receipts)
    finally:
        current_receipts.reset(token)

    return response_from(req, list(receipts.artifacts.values()), receipts.warnings, None)


def download_catalog_matches(req: FetchRequest, receipts: Receipts) -> None:
    species = req.input.requirements.species or req.input.query.species
    kinds = set(req.input.requirements.data_kinds)
    hits = service.search_catalog(req.input.query.question, species=species or None)
    if not hits and species:
        hits = service.search_catalog("", species=species)

    # Environmental fixtures have no species; do not discard rainfall with a species filter.
    if "rainfall_observations" in kinds:
        known = {hit.get("dataset_id") for hit in hits}
        hits.extend(hit for hit in service.search_catalog("rainfall") if hit.get("dataset_id") not in known)

    for hit in hits:
        if kinds and hit.get("data_kind") not in kinds:
            continue
        access = service.check_access(hit["dataset_id"])
        if access.get("status") != "available":
            receipts.warnings.append(f"Skipped {hit['dataset_id']}: {access.get('status')}")
            continue
        service.download_dataset(hit["dataset_id"])


async def run_with_agent(req: FetchRequest) -> FetchResponse:
    receipts = Receipts()
    token = current_receipts.set(receipts)
    failure = None
    summary = ""
    try:
        # The tool runner is synchronous; the thread gets a copy of this context, so tools record into `receipts`.
        agent = await asyncio.to_thread(run_agent, requirements_prompt(req))
        summary = agent.summary
        if agent.reached_limit:
            receipts.warnings.append(f"Agent stopped at the limit of {MAX_ITERATIONS} tool-loop iterations.")
            failure = FetchError(code="iteration_limit", message="Agent reached its tool-loop limit.", retryable=True)
        if agent.refused:
            failure = FetchError(code="agent_refused", message="The model declined the request.")
    except Exception as error:
        failure = FetchError(code="agent_failed", message=f"Agent failed ({type(error).__name__}).", retryable=True)
    finally:
        current_receipts.reset(token)

    return response_from(req, list(receipts.artifacts.values()), receipts.warnings, failure, agent_summary=summary)


def save_run(request: dict, response: dict) -> str:
    """Persist the request-scoped handoff; no scan of unrelated archive entries."""
    directory = settings().runs_dir / uuid4().hex
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "request.json").write_text(json.dumps(request, indent=2))
    (directory / "response.json").write_text(json.dumps(response, indent=2))
    artifacts = response.get("output", {}).get("raw_artifacts", [])
    (directory / "manifest.jsonl").write_text("".join(json.dumps(a) + "\n" for a in artifacts))
    return str(directory)


def run(req: FetchRequest | dict[str, Any], *, use_agent: bool = False, archive: Archive | None = None) -> FetchResponse:
    """Entry point for the Coordinator -> Fetch boundary."""
    request = req if isinstance(req, FetchRequest) else FetchRequest.model_validate(req)
    if request.access_scope != "public":
        return FetchResponse(
            request_id=request.request_id, query_id=request.query_id, access_scope=request.access_scope,
            status="error", error=FetchError(code="unsupported_scope", message="This fetcher only handles public data."),
        )

    token = current_archive.set(archive or Archive())
    try:
        response = asyncio.run(run_with_agent(request)) if use_agent else run_deterministic(request)
    finally:
        current_archive.reset(token)

    response.extensions["run_directory"] = save_run(request.model_dump(mode="json"), response.model_dump(mode="json"))
    return response

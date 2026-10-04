"""Stage handlers for the coordinator: habitat Fetch and Normalize, Recipe, and Analysis.

Each handler takes the coordinator stage request and returns a v1 envelope. A stage reads the output of
an earlier stage from `extensions.stage_outputs`. See ANALYSIS_INTEGRATION.md.
"""

from dataclasses import asdict, dataclass
from datetime import timedelta

from shapely.geometry import shape

from analysis.service import run as run_analysis
from analysis.store import ArtifactStore
from habitat.contracts import FetchRequest, FetchRequestInput, FetchRequirements, FetchResponse, TimeRange
from habitat.contracts import QuerySpec as FetchQuery
from habitat.fetch import run as fetch
from habitat.ingest import Workspace
from habitat.pipeline import NO_PUBLISH_STATUSES, ingest_and_publish
from workflow.recipe_handoff import RecipeArtifactReader, analysis_request, utc

USABLE_OUTCOMES = {"appended", "already_present"}


@dataclass(frozen=True)
class FetchSource:
    source_id: str
    package: str | None = None


def stage_handlers(
    *,
    workspace: Workspace,
    recipe_service,
    model_store: ArtifactStore,
    sources: list[FetchSource] | None = None,
    use_agent: bool = False,
    history_days: int = 0,
    use_ai: bool = False,
) -> dict:
    """Handlers for every coordinator stage.

    `sources` names the habitat connectors to run for each query. Without sources, the fetch agent reads the
    question. `history_days` widens the fetch before the query start, for recipes with a lookback window.
    """
    if not sources and not use_agent:
        raise ValueError("name the fetch sources or enable the fetch agent")

    def fetch_stage(request: dict) -> dict:
        fetches = []
        for fetch_request in fetch_requests(request, sources or [], history_days):
            response = fetch.run(fetch_request, use_agent=use_agent, archive=workspace.archive)
            fetches.append({"request": fetch_request.model_dump(mode="json"),
                            "response": response.model_dump(mode="json")})

        responses = [item["response"] for item in fetches]
        warnings = [warning for response in responses for warning in response["warnings"]]
        return envelope(request, fetch_status(responses), {"fetches": fetches}, warnings, fetch_error(responses))

    def normalize_stage(request: dict) -> dict:
        outcomes, published = [], []
        for item in request["extensions"]["stage_outputs"]["fetch"]["output"]["fetches"]:
            response = FetchResponse.model_validate(item["response"])
            if response.status in NO_PUBLISH_STATUSES:
                continue

            result = ingest_and_publish(FetchRequest.model_validate(item["request"]), response, workspace, use_ai)
            outcomes.extend(asdict(outcome) for outcome in result.outcomes)
            published.extend(result.published)

        quarantined = [outcome for outcome in outcomes if outcome["status"] not in USABLE_OUTCOMES]
        warnings = [f"{outcome['artifact_id']}/{outcome['version']} quarantined: {outcome['reason']}"
                    for outcome in quarantined]
        if len(quarantined) == len(outcomes):
            status = "insufficient_data"
        else:
            status = "partial" if quarantined else "ok"
        return envelope(request, status, {"outcomes": outcomes, "published": published}, warnings)

    def recipe_stage(request: dict) -> dict:
        response = recipe_service.run(recipe_request(request))
        if response["status"] != "pending":
            return response

        # The coordinator has no waiting state for a user answer, so a clarification stops the job.
        return {**response, "status": "error", "error": {
            "code": "CLARIFICATION_REQUIRED",
            "message": "Recipe needs an answer from the user; the coordinator cannot hold a clarification job",
            "retryable": False,
        }}

    def analysis_stage(request: dict) -> dict:
        recipe_response = request["extensions"]["stage_outputs"].get("recipe")
        if recipe_response is None:
            return run_analysis(request, store=model_store)

        handoff = analysis_request(request["input"]["query"], recipe_response,
                                   request_id=request["request_id"], boundaries=request["input"].get("boundaries"))
        reader = RecipeArtifactReader(recipe_service.store, request["access_scope"], model_store)
        return run_analysis(handoff, store=reader)

    return {"fetch": fetch_stage, "normalize": normalize_stage, "recipe": recipe_stage, "analysis": analysis_stage}


def fetch_requests(request: dict, sources: list[FetchSource], history_days: int) -> list[FetchRequest]:
    query = request["input"]["query"]
    start = (utc(query["time_range"]["start"]) - timedelta(days=history_days)).date().isoformat()
    end = utc(query["time_range"]["end"]).date().isoformat()
    fetch_query = FetchQuery(
        query_id=query["query_id"],
        question=query["question"],
        task_type=query["task_type"],
        species=query["species"],
        region=query["region"],
        time_range=TimeRange(start=start, end=end),
    )

    def build(suffix: str, source_ids: list[str], package: str | None) -> FetchRequest:
        return FetchRequest(
            request_id=f"{request['request_id']}:fetch:{suffix}",
            query_id=query["query_id"],
            access_scope=request["access_scope"],
            input=FetchRequestInput(query=fetch_query, requirements=FetchRequirements(
                species=query["species"], source_ids=source_ids, bbox=list(shape(query["region"]).bounds),
                start=start, end=end, package=package,
            )),
        )

    if not sources:
        return [build("agent", [], None)]

    return [build(source.source_id, [source.source_id], source.package) for source in sources]


def fetch_status(responses: list[dict]) -> str:
    statuses = [response["status"] for response in responses]
    if not any(status in {"ok", "partial"} for status in statuses):
        return "error" if "error" in statuses else "insufficient_data"

    return "ok" if all(status == "ok" for status in statuses) else "partial"


def fetch_error(responses: list[dict]) -> dict | None:
    if fetch_status(responses) != "error":
        return None

    errors = [response["error"] for response in responses if response["status"] == "error"]
    return {
        "code": "fetch_failed",
        "message": "; ".join(error["message"] for error in errors),
        "retryable": any(error["retryable"] for error in errors),
    }


def recipe_request(request: dict) -> dict:
    """The Recipe query has the forecast cutoff as a field and no comparison windows."""
    query = request["input"]["query"]
    forecast = query.get("forecast")
    return {
        "contract_version": "1.0",
        "request_id": request["request_id"],
        "query_id": request["query_id"],
        "access_scope": request["access_scope"],
        "input": {"query": {
            "query_id": query["query_id"],
            "question": query["question"],
            "task_type": query["task_type"],
            "access_scope": query["access_scope"],
            "time_range": query["time_range"],
            "region": query["region"],
            "species": query["species"],
            "forecast_cutoff": forecast["cutoff"] if forecast else None,
        }},
    }


def envelope(request: dict, status: str, output: dict, warnings: list[str], error: dict | None = None) -> dict:
    return {
        "contract_version": "1.0",
        "request_id": request["request_id"],
        "query_id": request["query_id"],
        "access_scope": request["access_scope"],
        "status": status,
        "output": output,
        "warnings": warnings,
        "error": error,
        "extensions": {},
    }

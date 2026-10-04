"""Durable in-process coordinator.

Stage state is queued, running, succeeded, or failed. User-facing status is
pending, complete, partial, or insufficient_data. Missing evidence is terminal.
"""

import uuid
from dataclasses import dataclass, field

from analysis.constants import SAMPLE_LIMITATION
from workflow.validate import QueryValidationError, normalize_request

PIPELINE = ("fetch", "normalize", "recipe", "analysis")


@dataclass
class StageRecord:
    name: str
    state: str = "queued"
    attempts: int = 0
    output: dict | None = None


@dataclass
class Job:
    job_id: str
    request: dict
    job_status: str = "queued"
    user_status: str = "pending"
    stages: list[StageRecord] = field(default_factory=list)
    error: dict | None = None
    final_output: dict | None = None


class Coordinator:
    def __init__(self, handlers: dict | None = None, *, max_attempts: int = 3, job_store=None):
        self.handlers = handlers or {}
        self.max_attempts = max_attempts
        if job_store is None:
            from workflow.jobs import MemoryJobStore

            job_store = MemoryJobStore()
        self.job_store = job_store

    def submit_query(self, request: dict) -> dict:
        # The first body for a request_id wins. A later submit with the same id
        # returns that job even when the question text differs.
        if isinstance(request, dict) and request.get("request_id"):
            existing = self.job_store.find_request(request["request_id"])
            if existing is not None:
                return self.view(existing)
        try:
            normalized = normalize_request(request)
        except QueryValidationError as exc:
            return self._rejected(str(exc))
        job = Job(job_id=f"job-{uuid.uuid4().hex[:12]}", request=normalized)
        plan = self._plan(normalized)
        if plan is None:
            job.job_status = "succeeded"
            job.user_status = "insufficient_data"
            job.final_output = {
                "schema_version": "1.0",
                "status": "insufficient_data",
                "findings": [],
                "report": "No feature table is available for this query.",
                "metrics": {},
                "missing": ["feature_artifact"],
                "limitations": ["Missing evidence is not retried as a transient failure.", SAMPLE_LIMITATION],
                "evidence": {},
            }
        elif plan == []:
            job.job_status = "succeeded"
            job.user_status = "complete"
            job.final_output = _discovery_result(normalized)
        else:
            job.stages = [StageRecord(name) for name in plan]
        if job.stages and job.job_status == "queued":
            self._run(job)
        self.job_store.save(job)
        return self.view(job)

    def get_result(self, job_id: str) -> dict:
        job = self.job_store.get(job_id)
        if job is None:
            return self._rejected("unknown job", code="job_not_found")
        return self.view(job)

    def resume(self, job_id: str) -> dict:
        job = self.job_store.get(job_id)
        if job is None:
            return self._rejected("unknown job", code="job_not_found")
        if job.job_status == "succeeded":
            return self.view(job)
        if job.error and not job.error.get("retryable", False):
            return self.view(job)
        self._run(job)
        self.job_store.save(job)
        return self.view(job)

    def view(self, job: Job) -> dict:
        return {
            "job_id": job.job_id,
            "job_status": job.job_status,
            "status": job.user_status,
            "result": _public_result(job),
            "error": job.error,
            "query": job.request["input"]["query"],
            "stages": [
                {"name": stage.name, "state": stage.state, "attempts": stage.attempts} for stage in job.stages
            ],
        }

    def _plan(self, request: dict) -> list[str] | None:
        query = request["input"]["query"]
        task = query["task_type"]
        if task == "discovery":
            if "fetch" in self.handlers and not request["input"].get("sources"):
                return ["fetch"]
            return []
        ready = "feature_artifact" in request["input"] and "recipe" in request["input"]
        if ready:
            return ["analysis"] if "analysis" in self.handlers else None
        if all(name in self.handlers for name in PIPELINE):
            return list(PIPELINE)
        return None

    def _run(self, job: Job) -> None:
        job.job_status = "running"
        job.user_status = "pending"
        for stage in job.stages:
            if stage.state == "succeeded":
                continue
            if stage.state == "failed" and job.error and not job.error.get("retryable", False):
                return
            stage.state = "running"
            try:
                response = self.handlers[stage.name](self._stage_request(job))
            except Exception as exc:
                message = str(exc).strip() or exc.__class__.__name__
                self._mark_failed(job, stage, message)
                return
            if not isinstance(response, dict) or "status" not in response:
                self._mark_failed(job, stage, "stage returned an invalid response", retryable=False)
                return
            if response["status"] == "error":
                message = response.get("error", {}).get("message", "stage failed")
                retryable = bool(response.get("error", {}).get("retryable"))
                self._mark_failed(job, stage, message, retryable=retryable)
                return
            stage.state = "succeeded"
            stage.output = response
            if response["status"] == "insufficient_data":
                job.job_status = "succeeded"
                job.user_status = "insufficient_data"
                job.error = None
                job.final_output = response
                return
        job.final_output = job.stages[-1].output if job.stages else job.final_output
        job.error = None
        job.job_status = "succeeded"
        status = (job.final_output or {}).get("status")
        if status == "partial":
            job.user_status = "partial"
        elif status == "insufficient_data":
            job.user_status = "insufficient_data"
        else:
            job.user_status = "complete"

    def _stage_request(self, job: Job) -> dict:
        from copy import deepcopy

        request = deepcopy(job.request)
        request["extensions"] = {
            "stage_outputs": {stage.name: stage.output for stage in job.stages if stage.output is not None}
        }
        return request

    def _mark_failed(self, job: Job, stage: StageRecord, message: str, retryable: bool = True) -> None:
        stage.attempts += 1
        stage.state = "failed"
        can_retry = retryable and stage.attempts < self.max_attempts
        job.job_status = "failed"
        job.user_status = "pending"
        job.error = {
            "code": "stage_failed",
            "message": message,
            "retryable": can_retry,
            "stage": stage.name,
        }

    def _rejected(self, message: str, code: str = "invalid_query") -> dict:
        return {
            "job_id": None,
            "job_status": "failed",
            "status": "error",
            "result": None,
            "error": {"code": code, "message": message, "retryable": False},
            "query": None,
            "stages": [],
        }


def _discovery_result(request: dict) -> dict:
    return {
        "schema_version": "1.0",
        "status": "complete",
        "findings": [],
        "report": "Discovery returns matching sources and coverage. No population or habitat finding is made from this request.",
        "metrics": {},
        "sources": list(request["input"].get("sources") or []),
        "limitations": ["No biological conclusion is drawn from a discovery request.", SAMPLE_LIMITATION],
        "evidence": {},
    }


def _public_result(job: Job):
    output = job.final_output
    if output is None:
        for stage in reversed(job.stages):
            if stage.output is not None:
                output = stage.output
                break
    if not isinstance(output, dict):
        return None
    inner = output.get("output")
    if isinstance(inner, dict) and "result" in inner:
        result = dict(inner["result"])
        if "model_artifact" in inner:
            result["model_artifact"] = inner["model_artifact"]
        return result
    return output

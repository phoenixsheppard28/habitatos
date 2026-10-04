"""Thin v1 Recipe boundary, including reserve reassessment and failure reports."""
from __future__ import annotations

from dataclasses import asdict

from pydantic import ValidationError

from .artifacts import cache_key
from .catalog import Candidate, discover, eligibility
from .errors import RecipeError
from .models import DatasetVersion, QuerySpec
from .validation import validate_recipe


class RecipeService:
    def __init__(self, *, catalog, planner, assessor, executor, store,
                 create_clarification_job=None, candidate_limit=100, max_plan_attempts=3):
        self.catalog, self.planner, self.assessor = catalog, planner, assessor
        self.executor, self.store = executor, store
        self.create_clarification_job = create_clarification_job
        self.candidate_limit, self.max_plan_attempts = candidate_limit, max_plan_attempts

    def run(self, request):
        if not isinstance(request, dict):
            return {"contract_version": "1.0", "request_id": None, "query_id": None,
                    "access_scope": None, "status": "error", "output": {}, "warnings": [],
                    "error": RecipeError("INVALID_REQUEST", "request must be an object").as_dict(), "extensions": {}}
        response = {"contract_version": "1.0", "request_id": request.get("request_id"),
            "query_id": request.get("query_id"), "access_scope": request.get("access_scope"),
            "status": "error", "output": {}, "warnings": [], "error": None, "extensions": {}}
        records, feedback = [], []
        try:
            if request.get("contract_version") != "1.0":
                raise RecipeError("UNSUPPORTED_CONTRACT", "only contract version 1.0 is supported")
            if not all(isinstance(request.get(k), str) and request[k] for k in ("request_id", "query_id", "access_scope")):
                raise RecipeError("INVALID_REQUEST", "request/query IDs and access scope are required")
            query = QuerySpec.model_validate(request.get("input", {}).get("query"))
            if query.query_id != request["query_id"] or query.access_scope != request["access_scope"]:
                raise RecipeError("INVALID_REQUEST", "envelope and query context differ")
            followup = request["input"].get("additional_information")
            context = self.store.context(query)
            if followup:
                if not isinstance(followup, dict) or not followup.get("need") or not followup.get("reason") or not followup.get("base_recipe_ref"):
                    raise RecipeError("INVALID_REQUEST", "follow-up needs need, reason and base_recipe_ref")
                parent = self.store.recipe(followup["base_recipe_ref"], query.access_scope)
                if parent["query_ref"] != query.query_id:
                    raise RecipeError("INVALID_REQUEST", "follow-up parent belongs to another query")
            requirements = self.planner.requirements(query, followup)
            if not 1 <= len(requirements) <= 16 or len({r.requirement_id for r in requirements}) != len(requirements):
                raise RecipeError("INVALID_PLAN", "planner requirement count/IDs are invalid")
            candidates = []
            if followup and context:
                # Reassess all eligible persisted candidates, including tangential
                # reserves, against the new requirements. Authorization is current.
                for record in context["candidates"]:
                    ds = DatasetVersion.model_validate(record["dataset"])
                    candidates.append(Candidate(ds, record["retrievals"], {r.requirement_id for r in requirements}))
            else:
                candidates, warnings = discover(self.catalog, query, requirements, self.candidate_limit)
                response["warnings"].extend(warnings)

            def assess_candidates(items):
                selected, satisfied = {}, set()
                for candidate in items:
                    ds = candidate.dataset
                    assessments, checks = [], []
                    for req in requirements:
                        if req.requirement_id not in candidate.requirements:
                            continue
                        check = eligibility(self.catalog, query, ds, req)
                        if check.status == "unresolved":
                            inspected = self.catalog.inspect(ds, query)
                            if inspected.key != ds.key:
                                raise RecipeError("CATALOG_CONFLICT", "inspection changed immutable dataset identity")
                            ds = inspected
                            check = eligibility(self.catalog, query, ds, req)
                        checks.append({"requirement_id": req.requirement_id, **asdict(check)})
                        if check.status != "eligible":
                            continue
                        assessment = self.assessor.assess(query, req, ds)
                        assessments.append({**asdict(assessment), "disposition": assessment.disposition})
                        if assessment.disposition == "selected":
                            selected[ds.key] = ds
                            satisfied.add(req.requirement_id)
                    records.append({"dataset": ds.model_dump(mode="json"), "retrievals": candidate.retrievals,
                                    "eligibility": checks, "assessments": assessments})
                return selected, satisfied

            selected, satisfied = assess_candidates(candidates)
            if followup and any(r.requirement_id not in satisfied for r in requirements):
                expanded, warnings = discover(self.catalog, query, requirements, self.candidate_limit)
                response["warnings"].extend(warnings)
                seen = {c.dataset.key for c in candidates}
                # Newly relevant requirement associations on existing candidates
                # were already assessed; avoid repeated provider calls.
                added, covered = assess_candidates([c for c in expanded if c.dataset.key not in seen])
                selected.update(added)
                satisfied.update(covered)
            self.store.save_context(query, records)
            missing = [r.description for r in requirements if r.required and r.requirement_id not in satisfied]
            if missing:
                response["status"] = "insufficient_data"
                response["output"] = {"unmet_requirements": missing}
                response["extensions"]["recipe_context"] = {"candidate_assessments": records}
                return response
            optional_missing = [r.description for r in requirements if not r.required and r.requirement_id not in satisfied]
            response["warnings"].extend("optional evidence unavailable: " + m for m in optional_missing)
            recipe = None
            for _ in range(self.max_plan_attempts):
                try:
                    decision = self.planner.plan(query, list(selected.values()), feedback=feedback, followup=followup)
                    if decision.clarification:
                        details = decision.clarification.model_dump(mode="json")
                        response["extensions"]["clarification"] = details
                        if self.create_clarification_job:
                            job_id = self.create_clarification_job(query, details)
                            if not isinstance(job_id, str) or not job_id:
                                raise RecipeError("COORDINATOR_UNAVAILABLE", "coordinator returned no durable clarification job ID")
                            response["status"], response["output"] = "pending", {"job_id": job_id}
                        else:
                            response["error"] = RecipeError("CLARIFICATION_REQUIRED", "coordinator must persist and resolve the clarification").as_dict()
                        return response
                    if decision.unmet_requirements:
                        response["status"], response["output"] = "insufficient_data", {"unmet_requirements": decision.unmet_requirements}
                        return response
                    recipe = decision.recipe
                    if followup:
                        if recipe.parent_recipe_ref != followup["base_recipe_ref"]:
                            raise RecipeError("INVALID_RECIPE", "derived recipe must reference its requested parent")
                        if {"recipe_id": recipe.recipe_id, "version": recipe.version} == recipe.parent_recipe_ref:
                            raise RecipeError("INVALID_RECIPE", "derived recipe must have a new identity/version")
                    inferred = validate_recipe(recipe, query, selected)
                    # Calculation lineage comes from the validated graph rather
                    # than the model's unsupported provenance assertions.
                    for c in recipe.output.columns:
                        c.derived_from = inferred[recipe.output.step][c.name].derived_from
                    break
                except RecipeError as exc:
                    if exc.code not in {"INVALID_RECIPE", "INVALID_PLAN", "UNSUPPORTED_OPERATION"}:
                        raise
                    feedback.append(str(exc))
            else:
                raise RecipeError("INVALID_RECIPE", "planner exhausted bounded validation attempts: " + "; ".join(feedback))
            # Recheck current authorization before cache lookup and execution.
            for ref in recipe.inputs.values():
                ds = selected[ref.key]
                if not self.catalog.authorize(ds, query) or not self.catalog.readable(ds):
                    raise RecipeError("ACCESS_DENIED", "input access changed before execution")
            self.store.save_recipe(recipe)
            key = cache_key(recipe, query, selected, self.executor.version)
            saved = self.store.lookup(key, query.access_scope)
            hit = saved is not None
            if not hit:
                rows, report = self.executor.execute(recipe, query, selected)
                report["column_quality"] = {c.name: {"null_count": sum(row[c.name] is None for row in rows),
                    "row_count": len(rows)} for c in recipe.output.columns}
                report["candidate_assessments"] = records
                report["warnings"] = response["warnings"]
                report["planner_validation_feedback"] = feedback
                saved = self.store.publish(key, recipe, rows, report)
            response["status"] = "partial" if optional_missing else "ok"
            response["output"] = {"recipe": recipe.model_dump(mode="json"), "feature_artifact": saved["artifact"]}
            response["extensions"]["recipe_context"] = {
                "cache_hit": hit, "description": recipe.output.description,
                "intended_use": recipe.output.intended_use, "row_keys": recipe.output.keys,
                "preparation_report": saved["report"],
                "source_datasets": [selected[r.key].model_dump(mode="json") for r in recipe.inputs.values()],
                "candidate_assessments": records,
                "supported_operations": ["select", "filter", "time_bucket", "aggregate", "join",
                                         "spatial_join", "asof_join", "window_aggregate"],
            }
            return response
        except (ValidationError, KeyError, TypeError, ValueError) as exc:
            response["error"] = RecipeError("INVALID_REQUEST", "request, provider data or artifact is malformed").as_dict()
        except RecipeError as exc:
            if exc.code == "INSUFFICIENT_DATA":
                response["status"], response["output"] = "insufficient_data", {"unmet_requirements": [str(exc)]}
            else:
                response["error"] = exc.as_dict()
        except Exception:
            response["error"] = RecipeError("INTERNAL_ERROR", "unexpected backend failure; inspect backend logs").as_dict()
        finally:
            if response["status"] == "error" and isinstance(response["access_scope"], str):
                try:
                    self.store.save_failure(response["request_id"], response["access_scope"],
                        {"error": response["error"], "candidate_assessments": records, "validation_feedback": feedback})
                except OSError:
                    response["warnings"].append("failure report could not be persisted")
        return response

"""Deterministic fixture demo, explicitly using simulated model decisions."""
from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path

from .artifacts import LocalArtifactStore
from .catalog import SearchPage
from .execution import FixtureExecutor
from .models import DatasetVersion
from .providers import Assessment, JsonPlanner, ROLES
from .service import RecipeService


class FixtureCatalog:
    def __init__(self, scenario):
        self.scenario = scenario
        self.datasets = {d.dataset_id: d for raw in scenario["datasets"] if (d := DatasetVersion.model_validate(raw))}

    def _page(self, requirement, method, query, limit):
        names = self.scenario["search"].get(requirement, {}).get(method, [])
        datasets = [self.datasets[n] for n in names if self.authorize(self.datasets[n], query)
                    and self.datasets[n].status == "ready"]
        return SearchPage(datasets[:limit], truncated=len(datasets) > limit)

    def search_metadata(self, filters, *, query, limit):
        supported = {"tags"}
        if set(filters) - supported:
            from .errors import RecipeError
            raise RecipeError("UNSUPPORTED_FILTER", "fixture catalog only supports its arbitrary tags field")
        return self._page(filters.get("tags"), "metadata", query, limit)

    def search_semantic(self, text, *, query, limit):
        # Fixed index responses, not a claimed embedding implementation.
        requirement = next((r["requirement_id"] for r in self.scenario["requirements"]
                            if r["semantic_text"] == text), None)
        return self._page(requirement, "semantic", query, limit)

    def authorize(self, dataset, query):
        return dataset.access_scope in {"public", query.access_scope}

    def readable(self, dataset):
        return dataset.dataset_id in self.scenario["rows"]

    def inspect(self, dataset, query):
        return self.datasets[dataset.dataset_id]

    def read(self, dataset, query):
        if not self.authorize(dataset, query):
            from .errors import RecipeError
            raise RecipeError("ACCESS_DENIED", "fixture access denied")
        return deepcopy(self.scenario["rows"][dataset.dataset_id])


class FixtureAssessor:
    def __init__(self, scenario):
        self.scenario, self.calls = scenario, []

    def assess(self, query, requirement, dataset):
        self.calls.append((requirement.requirement_id, dataset.key))
        level = self.scenario["labels"].get(dataset.dataset_id, 0)
        role = "primary_evidence" if level >= 2 else "supporting_context" if level == 1 else "irrelevant"
        return Assessment("fixture", "fixture-1", requirement.requirement_id, role, float(level),
                          {str(i): float(i == level) for i in range(4)},
                          {r: float(r == role) for r in ROLES}, 1, 1)


def fixture_service(scenario, root):
    catalog, assessor = FixtureCatalog(scenario), FixtureAssessor(scenario)

    def generate(*, instructions, context, schema):
        if schema.get("type") == "array":
            return deepcopy(scenario["requirements"])
        return {"recipe": deepcopy(scenario["recipe"])}

    service = RecipeService(catalog=catalog, planner=JsonPlanner(generate), assessor=assessor,
                            executor=FixtureExecutor(catalog.read), store=LocalArtifactStore(root))
    return service


def request_for(scenario, request_id="fixture-request"):
    query = scenario["query"]
    return {"contract_version": "1.0", "request_id": request_id,
            "query_id": query["query_id"], "access_scope": query["access_scope"],
            "input": {"query": deepcopy(query)}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("data/recipe-demo"))
    args = parser.parse_args()
    for scenario in json.loads(args.fixtures.read_text()):
        service = fixture_service(scenario, args.output)
        response = service.run(request_for(scenario))
        if response["status"] not in {"ok", "partial"}:
            raise SystemExit(json.dumps(response, indent=2))
        artifact = response["output"]["feature_artifact"]
        again = service.run(request_for(scenario, "fixture-repeat"))
        print(json.dumps({"scenario": scenario["name"], "status": response["status"],
              "rows": artifact["row_count"], "storage": artifact["storage"],
              "repeat_cache_hit": again["extensions"]["recipe_context"]["cache_hit"]}))


if __name__ == "__main__":
    main()

from copy import deepcopy
from datetime import datetime

import pytest

from recipe.artifacts import cache_key
from recipe.catalog import discover, eligibility
from recipe.compiler import SQLCompiler, TableBinding
from recipe.demo import FixtureCatalog, fixture_service, request_for
from recipe.errors import RecipeError
from recipe.execution import FixtureExecutor
from recipe.models import DatasetVersion, Filter, QuerySpec, RecipeSpec, Requirement
from recipe.providers import JevAssessor, JsonPlanner, RUBRIC, ROLES
from recipe.validation import validate_recipe


def parsed(scenario):
    return (RecipeSpec.model_validate(scenario["recipe"]), QuerySpec.model_validate(scenario["query"]),
            {d.key: d for raw in scenario["datasets"] if (d := DatasetVersion.model_validate(raw))})


def json_rows(rows):
    return [{k: v.isoformat() if isinstance(v, datetime) else v for k, v in row.items()} for row in rows]


@pytest.mark.parametrize("index", [0, 1])
def test_end_to_end_and_readable_handoff(scenarios, tmp_path, index):
    s = scenarios[index]
    service = fixture_service(s, tmp_path)
    response = service.run(request_for(s))
    assert response["status"] == "ok", response
    artifact = response["output"]["feature_artifact"]
    table = service.store.read_dataset(artifact["storage"], scope="public")
    rows = table.to_pylist()
    # Portable Parquet geometries are explicitly JSON-encoded WGS84 GeoJSON.
    import json
    for row in rows:
        if "geometry" in row:
            row["geometry"] = json.loads(row["geometry"])
    assert json_rows(rows) == s["expected"]
    assert table.schema.metadata[b"recipe.columns"]
    assert [c["name"] for c in artifact["columns"]] == [c["name"] for c in s["recipe"]["output"]["columns"]]
    context = response["extensions"]["recipe_context"]
    assert context["description"] and context["row_keys"]
    assert context["preparation_report"]["steps"]
    repeated = service.run(request_for(s, "repeat"))
    assert repeated["extensions"]["recipe_context"]["cache_hit"] is True
    assert repeated["output"]["feature_artifact"] == artifact


def test_candidate_union_deduplicates_without_pruning_semantic_only(scenarios):
    s = scenarios[0]
    catalog = FixtureCatalog(s)
    candidates, _ = discover(catalog, QuerySpec.model_validate(s["query"]),
                             [Requirement.model_validate(r) for r in s["requirements"]])
    assert {c.dataset.dataset_id for c in candidates} == {"habitat", "management", "water"}
    habitat = next(c for c in candidates if c.dataset.dataset_id == "habitat")
    assert {r["method"] for r in habitat.retrievals} == {"metadata", "semantic"}
    water = next(c for c in candidates if c.dataset.dataset_id == "water")
    assert water.requirements == {"habitat", "activity"}


def test_scope_controls_semantic_candidates_and_cache(scenarios, tmp_path):
    s = scenarios[0]
    s["datasets"][0]["access_scope"] = "private-other"
    service = fixture_service(s, tmp_path)
    response = service.run(request_for(s))
    assert response["status"] == "insufficient_data"
    assert not any(key[1][0] == "habitat" for key in service.assessor.calls)
    s["datasets"][0]["access_scope"] = "public"
    service = fixture_service(s, tmp_path)
    ready = service.run(request_for(s))
    with pytest.raises(RecipeError, match="not found"):
        service.store.resolve_artifact(ready["output"]["feature_artifact"]["storage"], scope="other")


def test_unknown_coverage_stays_unresolved(scenarios):
    s = scenarios[0]
    catalog = FixtureCatalog(s)
    ds = catalog.datasets["habitat"].model_copy(update={"coverage": catalog.datasets["habitat"].coverage.model_copy(update={"bbox": None})})
    check = eligibility(catalog, QuerySpec.model_validate(s["query"]), ds, Requirement.model_validate(s["requirements"][0]))
    assert check.status == "unresolved"
    assert "geographic coverage unknown" in check.reasons


def test_missing_keys_are_derived_before_join(scenarios):
    s = scenarios[0]
    assert all("month" not in row for rows in s["rows"].values() for row in rows)
    recipe, query, datasets = parsed(s)
    rows, _ = FixtureExecutor(FixtureCatalog(s).read).execute(recipe, query, datasets)
    assert json_rows(rows) == s["expected"]


def test_duplicate_join_keys_rejected_before_publication(scenarios, tmp_path):
    s = scenarios[0]
    # Join unaggregated activities on site only: several matches would multiply rows.
    s["recipe"]["steps"][-1].update(right="activities", keys={"site_id": "site_id"},
                                  cardinality="many_to_one", right_columns={"total_cost": "cost"})
    service = fixture_service(s, tmp_path)
    response = service.run(request_for(s))
    assert response["status"] == "error"
    assert response["error"]["code"] == "JOIN_VALIDATION_FAILED"
    assert not list(tmp_path.rglob("*.parquet"))


def test_spatial_ambiguity_is_stable_and_reported(scenarios):
    s = scenarios[1]
    s["rows"]["cells"].reverse()
    recipe, query, datasets = parsed(s)
    rows, report = FixtureExecutor(FixtureCatalog(s).read).execute(recipe, query, datasets)
    assert rows[0]["cell_id"] == "a"
    assert report["steps"]["cells"]["multiple_eligible_matches"] == 1


@pytest.mark.parametrize("value, expected, coverage", [(None, None, .5), (0, 0, 1)])
def test_missing_rainfall_and_observed_zero_are_distinct(scenarios, value, expected, coverage):
    s = scenarios[1]
    s["rows"]["rainfall"][1]["rainfall_mm"] = value
    recipe, query, datasets = parsed(s)
    rows, _ = FixtureExecutor(FixtureCatalog(s).read).execute(recipe, query, datasets)
    assert rows[0]["preceding_rainfall_mm"] == expected
    assert rows[0]["preceding_rainfall_mm_coverage"] == coverage


def test_overlapping_intervals_fail(scenarios):
    s = scenarios[1]
    s["rows"]["rainfall"][1]["interval_start"] = "2026-01-01T12:00:00Z"
    recipe, query, datasets = parsed(s)
    with pytest.raises(RecipeError, match="overlapping"):
        FixtureExecutor(FixtureCatalog(s).read).execute(recipe, query, datasets)


def test_future_measurements_excluded_and_history_retained(scenarios):
    s = scenarios[1]
    recipe, query, datasets = parsed(s)
    rows, _ = FixtureExecutor(FixtureCatalog(s).read).execute(recipe, query, datasets)
    assert rows[0]["preceding_rainfall_mm"] == 5  # excludes future 100, includes pre-query 0+5


def test_forecast_fails_closed_until_availability_contract_exists(scenarios, tmp_path):
    s = scenarios[1]
    s["query"].update(task_type="forecast", forecast_cutoff="2026-01-03T00:00:00Z")
    response = fixture_service(s, tmp_path).run(request_for(s))
    assert response["status"] == "insufficient_data"
    assert "availability" in response["output"]["unmet_requirements"][0]


def test_cache_keys_change_with_versions_parameters_and_scope(scenarios):
    recipe, query, datasets = parsed(scenarios[1])
    original = cache_key(recipe, query, datasets, "1")
    assert cache_key(recipe, query, datasets, "2") != original
    changed = deepcopy(datasets)
    ds = changed[("rainfall", "1")]
    changed[ds.key] = ds.model_copy(update={"mapping_version": "2"})
    assert cache_key(recipe, query, changed, "1") != original
    changed_recipe = recipe.model_copy(deep=True)
    changed_recipe.steps[-1].window_seconds = 86400
    assert cache_key(changed_recipe, query, datasets, "1") != original
    assert cache_key(recipe, query.model_copy(update={"access_scope": "tenant"}), datasets, "1") != original


def test_recipe_versions_are_immutable(scenarios, tmp_path):
    service = fixture_service(scenarios[0], tmp_path)
    recipe, _, _ = parsed(scenarios[0])
    service.store.save_recipe(recipe)
    with pytest.raises(RecipeError, match="different content"):
        service.store.save_recipe(recipe.model_copy(update={"rationale": "changed"}))


def test_planner_repairs_missing_column_with_bounded_feedback(scenarios, tmp_path):
    s = scenarios[0]
    service = fixture_service(s, tmp_path)
    attempts = []
    def generate(*, instructions, context, schema):
        if schema.get("type") == "array":
            return s["requirements"]
        attempts.append(context["validation_feedback"][:])
        bad = deepcopy(s["recipe"])
        if len(attempts) == 1:
            bad["steps"][0]["column"] = "nonexistent"
        return {"recipe": bad}
    service.planner = JsonPlanner(generate)
    response = service.run(request_for(s))
    assert response["status"] == "ok", response
    assert len(attempts) == 2
    assert "missing column" in attempts[1][0]


def test_requirement_filters_are_repaired_before_catalog_search(scenarios, tmp_path):
    from unittest.mock import Mock

    scenario = scenarios[0]
    service = fixture_service(scenario, tmp_path)
    searches = Mock(wraps=service.catalog.search_metadata)
    service.catalog.search_metadata = searches
    attempts = []

    def generate(*, instructions, context, schema):
        if schema.get("type") != "array":
            return {"recipe": scenario["recipe"]}
        assert searches.call_count == 0
        attempts.append(deepcopy(context))
        assert schema["$defs"]["Requirement"]["properties"]["filters"]["additionalProperties"] is False
        requirements = deepcopy(scenario["requirements"])
        if len(attempts) == 1:
            requirements[0]["filters"]["region_bbox"] = [0, 0, 2, 2]
        return requirements

    service.planner = JsonPlanner(generate, search_filters={"tags": "Catalog tags"})
    response = service.run(request_for(scenario))

    assert response["status"] == "ok", response
    assert len(attempts) == 2
    assert "region_bbox" in attempts[1]["validation_feedback"][0]
    assert searches.call_count == len(scenario["requirements"])


def test_requirement_repair_is_bounded_and_does_not_execute(scenarios, tmp_path):
    from unittest.mock import Mock

    scenario = scenarios[0]
    invalid = deepcopy(scenario["requirements"])
    invalid[0]["filters"]["access_scope"] = "public"
    generate = Mock(return_value=invalid)
    service = fixture_service(scenario, tmp_path)
    service.planner = JsonPlanner(generate, search_filters={"tags": "Catalog tags"})
    service.catalog.search_metadata = Mock()
    service.executor.execute = Mock()

    response = service.run(request_for(scenario))

    assert response["error"]["code"] == "INVALID_PLAN"
    assert generate.call_count == 3
    service.catalog.search_metadata.assert_not_called()
    service.executor.execute.assert_not_called()


def test_requirement_filter_values_use_catalog_validation(scenarios):
    from habitat.recipe_inputs import HabitatRecipeCatalog, SEARCH_FILTERS

    requirement = deepcopy(scenarios[0]["requirements"][0])
    requirement["filters"] = {"family": ["invented_family"]}
    repaired = deepcopy(requirement)
    repaired["filters"] = {"family": "animal_daily_movement"}
    responses = iter([[requirement], [repaired]])
    planner = JsonPlanner(lambda **kwargs: next(responses), search_filters=SEARCH_FILTERS,
                          validate_filters=HabitatRecipeCatalog.validate_filters)

    requirements = planner.requirements(QuerySpec.model_validate(scenarios[0]["query"]))

    assert requirements[0].filters == {"family": "animal_daily_movement"}


def test_invalid_operation_exhausts_planner_without_executing(scenarios, tmp_path):
    s = scenarios[0]
    s["recipe"]["steps"][0]["operation"] = "run_arbitrary_sql"
    service = fixture_service(s, tmp_path)
    response = service.run(request_for(s))
    assert response["error"]["code"] == "INVALID_RECIPE"
    assert "exhausted" in response["error"]["message"]
    assert not list(tmp_path.rglob("*.parquet"))


def test_clarification_uses_real_coordinator_job_id(scenarios, tmp_path):
    s = scenarios[0]
    service = fixture_service(s, tmp_path)
    def generate(*, instructions, context, schema):
        return s["requirements"] if schema.get("type") == "array" else {
            "clarification": {"question": "Which grain?", "reason": "Different questions need different grains"}}
    service.planner = JsonPlanner(generate)
    unavailable = service.run(request_for(s))
    assert unavailable["status"] == "error"
    assert unavailable["error"]["code"] == "CLARIFICATION_REQUIRED"
    service.create_clarification_job = lambda query, details: "durable-job-123"
    pending = service.run(request_for(s))
    assert pending["status"] == "pending"
    assert pending["output"]["job_id"] == "durable-job-123"


def test_optional_missing_returns_partial(scenarios, tmp_path):
    s = scenarios[0]
    s["requirements"].append({"requirement_id": "optional", "description": "Optional evidence",
        "semantic_text": "Optional evidence", "filters": {"tags": "optional"}, "required": False})
    response = fixture_service(s, tmp_path).run(request_for(s))
    assert response["status"] == "partial"
    assert "optional evidence unavailable" in response["warnings"][0]


def jev_response(level=3, role="primary_evidence"):
    return {"model": "jev-test-version", "answers": {
        "usefulness": {"type": "score", "score": level, "confidence": 1,
            "legend": {str(i): text for i, text in enumerate(RUBRIC)},
            "probabilities": {str(i): float(i == level) for i in range(4)}},
        "role": {"type": "choice", "choice": role, "confidence": 1,
            "probabilities": {r: float(r == role) for r in ROLES}}}}


def test_jev_wire_contract_and_conservative_policy(scenarios):
    s = scenarios[0]
    calls = []
    def transport(endpoint, payload, headers, timeout):
        calls.append(payload)
        return jev_response(level=1, role="supporting_context")
    assessor = JevAssessor("fixture-key", model="pinned-version", transport=transport)
    ds = DatasetVersion.model_validate(s["datasets"][0])
    result = assessor.assess(QuerySpec.model_validate(s["query"]), Requirement.model_validate(s["requirements"][0]), ds)
    assert result.disposition == "reserve"
    assert calls[0]["questions"]["usefulness"]["criteria"] == RUBRIC
    assert "storage" not in calls[0]["state"]["dataset"]
    assert result.model == "jev-test-version"


def test_jev_malformed_probabilities_are_rejected(scenarios):
    body = jev_response()
    body["answers"]["usefulness"]["probabilities"]["0"] = .5
    assessor = JevAssessor("fixture-key", model="pinned-version", transport=lambda *args: body)
    s = scenarios[0]
    with pytest.raises(RecipeError, match="invalid assessment"):
        assessor.assess(QuerySpec.model_validate(s["query"]), Requirement.model_validate(s["requirements"][0]),
                        DatasetVersion.model_validate(s["datasets"][0]))


def test_sql_compiler_binds_literals_and_pins_source_versions(scenarios):
    s = scenarios[0]
    recipe, query, datasets = parsed(s)
    bindings = {key: TableBinding("public", "normalized", {c.name: c.name for c in ds.columns})
                for key, ds in datasets.items()}
    recipe.steps.insert(0, Filter.model_validate({
        "id": "safe_filter", "operation": "filter", "input": "habitat", "predicates": [
            {"column": "site_id", "operator": "eq", "value": "'; DROP TABLE observations; --"}]}))
    recipe.steps[1].input = "safe_filter"
    plan = SQLCompiler(bindings).compile(recipe, query, datasets)
    assert "DROP TABLE" not in plan.sql
    assert any("DROP TABLE" in str(v) for v in plan.params.values())
    assert '"dataset_version"=' in plan.sql and '"access_scope"=' in plan.sql
    assert any("right key uniqueness" in check.name for check in plan.checks)


def test_stage4_reassesses_reserves_and_preserves_original(scenarios, tmp_path):
    s = scenarios[0]
    service = fixture_service(s, tmp_path)
    first = service.run(request_for(s))
    assert first["status"] == "ok"
    original = first["output"]["feature_artifact"]
    calls_before = len(service.assessor.calls)
    s["labels"]["water"] = 3
    s["requirements"].append({"requirement_id": "water", "description": "Water observations",
        "semantic_text": "Water observations", "filters": {"tags": "water"}})
    s["recipe"]["version"] = "2"
    s["recipe"]["parent_recipe_ref"] = {"recipe_id": "recipe-site", "version": "1"}
    s["recipe"]["inputs"]["water_source"] = {"dataset_id": "water", "version": "1"}
    s["recipe"]["steps"].extend([
        {"id": "water_month", "operation": "time_bucket", "input": "water_source", "column": "observed_at", "output": "month", "period": "month"},
        {"id": "water_summary", "operation": "aggregate", "input": "water_month", "group_by": ["site_id", "month"],
         "aggregations": [{"column": "water_level", "method": "mean", "output": "mean_water"}]},
        {"id": "enriched", "operation": "join", "left": "combined", "right": "water_summary",
         "keys": {"site_id": "site_id", "month": "month"}, "cardinality": "one_to_one", "right_columns": {"mean_water": "mean_water"}}])
    s["recipe"]["output"]["step"] = "enriched"
    s["recipe"]["output"]["columns"].append({"name": "mean_water", "type": "number", "unit": "m", "description": "Mean sampled water level"})
    request = request_for(s, "followup")
    request["input"]["additional_information"] = {"base_recipe_ref": {"recipe_id": "recipe-site", "version": "1"},
        "need": "Monthly water level", "reason": "Check environmental context"}
    response = service.run(request)
    assert response["status"] == "ok", response
    assert any(req == "water" and key[0] == "water" for req, key in service.assessor.calls[calls_before:])
    new_artifact = response["output"]["feature_artifact"]
    assert new_artifact["artifact_id"] != original["artifact_id"]
    assert service.store.read_dataset(original["storage"], scope="public").num_columns == 4
    assert service.store.read_dataset(new_artifact["storage"], scope="public").num_columns == 5


def asof_scenario(s):
    """Hand-specified observation-grain temporal alignment with availability."""
    activities = next(d for d in s["datasets"] if d["dataset_id"] == "management")
    activities["columns"].extend([
        {"name": "activity_id", "type": "string", "description": "Stable activity identifier"},
        {"name": "available_at", "type": "timestamp", "description": "When the record became available"}])
    for i, row in enumerate(s["rows"]["management"]):
        row["activity_id"] = str(i)
        row["available_at"] = "2026-01-25T00:00:00Z" if i == 1 else row["observed_at"]
    habitat = next(d for d in s["datasets"] if d["dataset_id"] == "habitat")
    s["recipe"]["steps"] = [{"id": "matched", "operation": "asof_join", "left": "habitat", "right": "activities",
        "keys": {"site_id": "site_id"}, "left_time": "observed_at", "right_time": "observed_at",
        "right_available_at": "available_at", "right_tie_break": "activity_id", "tolerance_seconds": 1728000,
        "right_columns": {"recent_cost": "cost"}}]
    s["recipe"]["output"].update(step="matched", keys=["site_id", "observed_at"],
        time_column="observed_at", row_grain="one site observation", columns=deepcopy(habitat["columns"])+[
            {"name": "recent_cost", "type": "number", "unit": "USD", "description": "Latest preceding available activity cost"}])
    return s


def test_asof_join_obeys_availability_tolerance_and_direction(scenarios):
    s = asof_scenario(scenarios[0])
    recipe, query, datasets = parsed(s)
    rows, report = FixtureExecutor(FixtureCatalog(s).read).execute(recipe, query, datasets)
    assert [r["recent_cost"] for r in rows] == [None, 100, None]
    assert report["steps"]["matched"]["unmatched_rows"] == 2


def test_malformed_requests_and_backend_failures_return_envelopes(scenarios, tmp_path):
    service = fixture_service(scenarios[0], tmp_path)
    assert service.run(None)["error"]["code"] == "INVALID_REQUEST"
    request = request_for(scenarios[0])
    request["input"]["query"]["time_range"]["start"] = "2026-01-01"
    assert service.run(request)["error"]["code"] == "INVALID_REQUEST"
    class BrokenExecutor:
        version = "broken"
        def execute(self, *args):
            raise RuntimeError("private backend details must not leak")
    service.executor = BrokenExecutor()
    response = service.run(request_for(scenarios[0]))
    assert response["error"]["code"] == "INTERNAL_ERROR"
    assert "private backend" not in str(response)
    assert not list(tmp_path.rglob("*.parquet"))


def test_empty_output_and_resource_limits_are_typed(scenarios, tmp_path):
    s = scenarios[0]
    service = fixture_service(s, tmp_path)
    service.executor.max_rows = 1
    assert service.run(request_for(s))["error"]["code"] == "RESOURCE_LIMIT"
    s["rows"]["habitat"] = []
    service = fixture_service(s, tmp_path)
    assert service.run(request_for(s))["status"] == "insufficient_data"


def test_aggregation_cannot_include_observations_after_query_end(scenarios):
    s = scenarios[0]
    s["query"]["time_range"]["end"] = "2026-01-15T23:59:59Z"
    recipe, query, datasets = parsed(s)
    rows, _ = FixtureExecutor(FixtureCatalog(s).read).execute(recipe, query, datasets)
    assert len(rows) == 1
    assert rows[0]["mean_canopy"] == 20  # Jan 20 observation is outside the query
    assert rows[0]["total_cost"] == 150


def species_scenario(s):
    s["query"]["species"] = ["antelope"]
    tracking = next(d for d in s["datasets"] if d["dataset_id"] == "tracking")
    tracking["coverage"]["species"] = ["antelope", "gazelle"]
    column = {"name": "species", "type": "string", "role": "species", "description": "Recorded species"}
    tracking["columns"].append(column)
    s["recipe"]["output"]["columns"].append(column)
    s["rows"]["tracking"][0]["species"] = "antelope"
    s["rows"]["tracking"].append({**s["rows"]["tracking"][0], "entity_id": "study-a:2", "species": "gazelle"})
    return s


def test_species_constraints_filter_rows_not_just_catalog(scenarios):
    s = species_scenario(scenarios[1])
    recipe, query, datasets = parsed(s)
    rows, _ = FixtureExecutor(FixtureCatalog(s).read).execute(recipe, query, datasets)
    assert len(rows) == 1 and rows[0]["species"] == "antelope"
    # A mixed-species descriptor without a species column cannot safely filter.
    s["datasets"][0]["columns"] = [c for c in s["datasets"][0]["columns"] if c["name"] != "species"]
    recipe, query, datasets = parsed(s)
    with pytest.raises(RecipeError, match="species column"):
        validate_recipe(recipe, query, datasets)


def test_vegetation_scopes_follow_renamed_filters_and_track_keys(scenarios):
    from vegetation_fixture import vegetation_scenario

    recipe, query, datasets = parsed(vegetation_scenario(scenarios[0]))
    bindings = {key: TableBinding("public", "observations", {c.name: c.name for c in dataset.columns})
                for key, dataset in datasets.items()}
    compiler = SQLCompiler(bindings)
    plan = compiler.compile(recipe, query, datasets, materialize=True)

    assert plan.cell_scopes == {"veg": ["fixes"]}
    assert plan.index_scopes == {"veg": ["ndvi"]}
    assert [stage.name for stage in plan.stages[:2]] == ["fixes", "veg"]
    assert plan.stages[1].input_context["selected_indices"] == ["ndvi"]
    assert compiler.compile(recipe, query, datasets).index_scopes == {}


def test_vegetation_scopes_do_not_remove_a_background_branch(scenarios):
    from recipe.models import Select
    from vegetation_fixture import vegetation_scenario

    recipe, query, datasets = parsed(vegetation_scenario(scenarios[0]))
    recipe.steps.insert(0, Select(id="background", operation="select", input="veg", columns={"index_name": "index_name"}))
    bindings = {key: TableBinding("public", "observations", {c.name: c.name for c in dataset.columns})
                for key, dataset in datasets.items()}
    plan = SQLCompiler(bindings).compile(recipe, query, datasets, materialize=True)

    assert plan.cell_scopes == {}
    assert plan.index_scopes == {}


def test_index_pushdown_preserves_different_filtered_branches(scenarios):
    from recipe.models import Select
    from vegetation_fixture import vegetation_scenario

    recipe, query, datasets = parsed(vegetation_scenario(scenarios[0]))
    recipe.steps.insert(0, Filter(id="evi_branch", operation="filter", input="veg",
                                 predicates=[{"column": "index_name", "operator": "eq", "value": "evi"}]))
    recipe.steps.insert(1, Select(id="evi_values", operation="select", input="evi_branch", columns={"evi": "index_value"}))
    bindings = {key: TableBinding("public", "observations", {c.name: c.name for c in dataset.columns})
                for key, dataset in datasets.items()}
    plan = SQLCompiler(bindings).compile(recipe, query, datasets, materialize=True)

    assert plan.cell_scopes == {}
    assert plan.index_scopes == {"veg": ["evi", "ndvi"]}


def test_residence_recipe_keeps_unmatched_fixes_and_composite_validity(scenarios):
    from vegetation_fixture import vegetation_scenario

    recipe, query, datasets = parsed(vegetation_scenario(scenarios[0]))
    recipe.steps[2].type = "inner"

    with pytest.raises(RecipeError, match="left joins"):
        validate_recipe(recipe, query, datasets)

    recipe.steps[2].type = "left"
    recipe.steps[-1].columns.pop("vegetation_valid_until")
    recipe.output.columns = [column for column in recipe.output.columns if column.name != "vegetation_valid_until"]

    with pytest.raises(RecipeError, match="observed_until"):
        validate_recipe(recipe, query, datasets)

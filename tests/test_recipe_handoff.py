from datetime import UTC, datetime

import pytest
from recipe.artifacts import LocalArtifactStore
from recipe.models import RecipeSpec

from analysis.store import ArtifactStore, StorageError
from habitat.recipe_inputs import DAILY_MOVEMENT, RAINFALL
from test_analysis_integration import movement_and_rain_recipe
from workflow.handlers import recipe_request
from workflow.recipe_handoff import RecipeArtifactReader, analysis_role, catalog_refs, feature_coverage

FAMILIES = {"movement": DAILY_MOVEMENT, "rain": RAINFALL}


def column(name, role=None, derived_from=None):
    return {"name": name, "type": "number", "nullable": True, "role": role,
            "derived_from": derived_from or [f"movement.{name}"]}


def test_the_recipe_time_column_is_the_only_event_time():
    assert analysis_role(column("day", "event_time"), "day", FAMILIES) == "event_time"
    assert analysis_role(column("observed_at", "event_time"), "day", FAMILIES) is None


def test_measurements_get_a_role_from_their_source_family():
    rain = column("rain_7d_mm", "measurement", ["rain.rainfall_mm"])
    summed_movement = column("km_per_month", "measurement", ["movement.daily_displacement_km"])
    displacement = column("daily_displacement_km", "daily_displacement")

    assert analysis_role(rain, "day", FAMILIES) == "rainfall"
    assert analysis_role(summed_movement, "day", FAMILIES) is None
    assert analysis_role(displacement, "day", FAMILIES) == "daily_displacement"
    assert analysis_role(column("available_at", "available_at"), "day", FAMILIES) is None


def test_a_derived_dataset_cites_its_catalog_version():
    movement = {"dataset_id": "fixes--daily-movement", "version": "2",
                "metadata": {"derived_from": {"dataset_id": "fixes", "version": "2"}}}
    fixes = {"dataset_id": "fixes", "version": "2", "metadata": {}}

    assert catalog_refs([movement, fixes]) == [{"dataset_id": "fixes", "version": "2"}]


def test_coverage_is_cut_to_the_query():
    query = {"time_range": {"start": "2011-03-01T00:00:00Z", "end": "2011-03-06T23:59:59Z"}}
    fixes = {"coverage": {"start": "2010-05-28T06:00:00Z", "end": "2011-03-04T00:00:00Z", "species": ["wildebeest"]}}
    rain = {"coverage": {"start": "2011-02-22T00:00:00Z", "end": "2011-03-07T00:00:00Z", "species": None}}

    assert feature_coverage(query, [fixes, rain]) == {
        "species": ["wildebeest"], "start": "2011-03-01T00:00:00Z", "end": "2011-03-06T23:59:59Z"}


def test_the_reader_resolves_recipe_tables_only_in_their_scope(tmp_path):
    inputs = {"movement": {"dataset_id": "m", "version": "1"}, "rain": {"dataset_id": "r", "version": "1"}}
    recipe = RecipeSpec.model_validate(movement_and_rain_recipe(inputs, ["entity_id", "day"]))
    store = LocalArtifactStore(tmp_path / "recipe")
    rows = [{"entity_id": "a", "day": datetime(2011, 3, 1, tzinfo=UTC)}]
    storage = store.publish("ab" * 32, recipe, rows, {})["artifact"]["storage"]

    public = RecipeArtifactReader(store, "public", ArtifactStore(tmp_path / "analysis"))
    private = RecipeArtifactReader(store, "partner-a", ArtifactStore(tmp_path / "analysis"))

    assert public.read_dataset(storage)["entity_id"].tolist() == ["a"]
    with pytest.raises(StorageError):
        private.read_dataset(storage)
    with pytest.raises(StorageError):
        public.read_dataset({**storage, "format": "json"})


def test_the_recipe_query_carries_the_forecast_cutoff():
    query = {"query_id": "q", "question": "next day?", "task_type": "forecast", "access_scope": "public",
             "time_range": {"start": "2011-03-01T00:00:00Z", "end": "2011-03-06T00:00:00Z"},
             "region": {"type": "Point", "coordinates": [0, 0]}, "species": ["x"], "comparison_windows": None,
             "forecast": {"cutoff": "2011-03-05T00:00:00Z", "horizon_days": 1, "target": "next_day_displacement"}}
    request = {"request_id": "r", "query_id": "q", "access_scope": "public", "input": {"query": query}}

    recipe_query = recipe_request(request)["input"]["query"]

    assert recipe_query["forecast_cutoff"] == "2011-03-05T00:00:00Z"
    assert "forecast" not in recipe_query and "comparison_windows" not in recipe_query


def test_workflow_preserves_residence_settings_and_satellite_source():
    from contracts.models import QuerySpec

    query = QuerySpec.model_validate({
        "query_id": "q", "question": "Bear residence time", "task_type": "historical", "access_scope": "public",
        "time_range": {"start": "2026-01-01T00:00:00Z", "end": "2026-02-01T00:00:00Z"},
        "region": {"type": "Point", "coordinates": [0, 0]}, "species": ["bear"],
        "analysis_method": "residence_time", "max_tracking_gap_hours": 4,
        "extensions": {"vegetation_source_id": "sentinel2"},
    }).model_dump(mode="json")
    request = {"request_id": "r", "query_id": "q", "access_scope": "public", "input": {"query": query}}

    recipe_query = recipe_request(request)["input"]["query"]

    assert recipe_query["analysis_method"] == "residence_time"
    assert recipe_query["max_tracking_gap_hours"] == 4
    assert recipe_query["extensions"] == {"vegetation_source_id": "sentinel2"}

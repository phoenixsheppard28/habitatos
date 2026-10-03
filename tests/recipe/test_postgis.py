"""Opt-in real PostgreSQL/PostGIS checks, using only isolated fixture tables."""
import os
import uuid

import pytest

from recipe.compiler import TableBinding
from recipe.demo import FixtureCatalog
from recipe.errors import RecipeError
from recipe.execution import FixtureExecutor, SupabaseExecutor
from recipe.models import DatasetVersion, QuerySpec, RecipeSpec


@pytest.fixture
def database(scenarios, request):
    dsn = os.environ.get("RECIPE_TEST_DSN")
    if not dsn:
        pytest.skip("set RECIPE_TEST_DSN to a disposable PostGIS database")
    variant = getattr(request, "param", None)
    if variant == "asof":
        from test_recipe import asof_scenario
        asof_scenario(scenarios[0])
    elif variant == "null_rainfall":
        scenarios[1]["rows"]["rainfall"][1]["rainfall_mm"] = None
    elif variant == "overlapping_intervals":
        scenarios[1]["rows"]["rainfall"][1]["interval_start"] = "2026-01-01T12:00:00Z"
    elif variant == "partial_month":
        scenarios[0]["query"]["time_range"]["end"] = "2026-01-15T23:59:59Z"
    elif variant == "species":
        from test_recipe import species_scenario
        species_scenario(scenarios[1])
    import psycopg
    from psycopg import sql
    from psycopg.types.json import Jsonb
    schema = "recipe_test_" + uuid.uuid4().hex
    bindings = {}
    types = {"string": "text", "integer": "bigint", "number": "double precision",
             "timestamp": "timestamptz", "geometry": "jsonb", "json": "jsonb", "boolean": "boolean"}
    with psycopg.connect(dsn) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        for scenario in scenarios:
            for raw in scenario["datasets"]:
                ds = DatasetVersion.model_validate(raw)
                columns = sql.SQL(", ").join(sql.SQL("{} {}").format(sql.Identifier(c.name), sql.SQL(types[c.type])) for c in ds.columns)
                connection.execute(sql.SQL("CREATE TABLE {}.{} ({}, dataset_id text, dataset_version text, access_scope text)").format(
                    sql.Identifier(schema), sql.Identifier(ds.dataset_id), columns))
                names = [c.name for c in ds.columns] + ["dataset_id", "dataset_version", "access_scope"]
                insert = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(
                    sql.Identifier(schema), sql.Identifier(ds.dataset_id),
                    sql.SQL(",").join(map(sql.Identifier, names)), sql.SQL(",").join(sql.Placeholder() for _ in names))
                for row in scenario["rows"][ds.dataset_id]:
                    values = [Jsonb(row[c.name]) if c.type in {"geometry", "json"} else row[c.name] for c in ds.columns]
                    connection.execute(insert, [*values, ds.dataset_id, ds.version, ds.access_scope])
                    # Other version and scope must not contaminate the recipe.
                    connection.execute(insert, [*values, ds.dataset_id, "unselected-version", "private-other"])
                bindings[ds.key] = TableBinding(schema, ds.dataset_id, {c.name: c.name for c in ds.columns})
    try:
        yield dsn, bindings, schema
    finally:
        with psycopg.connect(dsn) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.mark.parametrize("index", [0, 1])
def test_postgis_results_match_hand_checked_fixture(database, scenarios, index):
    import psycopg
    dsn, bindings, _ = database
    scenario = scenarios[index]
    recipe = RecipeSpec.model_validate(scenario["recipe"])
    query = QuerySpec.model_validate(scenario["query"])
    datasets = {d.key: d for raw in scenario["datasets"] if (d := DatasetVersion.model_validate(raw))}
    local_rows, _ = FixtureExecutor(FixtureCatalog(scenario).read).execute(recipe, query, datasets)
    backend = SupabaseExecutor(lambda: psycopg.connect(dsn), bindings, postgis_schema="public")
    try:
        sql_rows, report = backend.execute(recipe, query, datasets)
    except RecipeError as exc:
        if exc.__cause__:
            pytest.fail(f"fixture SQL failed: {exc.__cause__}")
        raise
    assert sql_rows == local_rows
    assert report["checks"]
    if index == 1:
        assert report["steps"]["cells: ambiguous matches"] == 1


def test_postgis_rejects_duplicate_join_keys(database, scenarios):
    import psycopg
    dsn, bindings, _ = database
    s = scenarios[0]
    s["recipe"]["steps"][-1].update(right="activities", keys={"site_id": "site_id"},
        cardinality="many_to_one", right_columns={"total_cost": "cost"})
    recipe, query = RecipeSpec.model_validate(s["recipe"]), QuerySpec.model_validate(s["query"])
    datasets = {d.key: d for raw in s["datasets"] if (d := DatasetVersion.model_validate(raw))}
    with pytest.raises(RecipeError, match="right key uniqueness"):
        SupabaseExecutor(lambda: psycopg.connect(dsn), bindings, postgis_schema="public").execute(recipe, query, datasets)


@pytest.mark.parametrize("database,index", [("asof", 0), ("null_rainfall", 1), ("partial_month", 0), ("species", 1)], indirect=["database"])
def test_postgis_temporal_variants(database, scenarios, index):
    import psycopg
    dsn, bindings, _ = database
    s = scenarios[index]
    recipe, query = RecipeSpec.model_validate(s["recipe"]), QuerySpec.model_validate(s["query"])
    datasets = {d.key: d for raw in s["datasets"] if (d := DatasetVersion.model_validate(raw))}
    expected, _ = FixtureExecutor(FixtureCatalog(s).read).execute(recipe, query, datasets)
    try:
        rows, _ = SupabaseExecutor(lambda: psycopg.connect(dsn), bindings, postgis_schema="public").execute(recipe, query, datasets)
    except RecipeError as exc:
        if exc.__cause__:
            pytest.fail(f"fixture SQL failed: {exc.__cause__}")
        raise
    assert rows == expected


@pytest.mark.parametrize("database", ["overlapping_intervals"], indirect=True)
def test_postgis_rejects_overlapping_intervals(database, scenarios):
    import psycopg
    dsn, bindings, _ = database
    s = scenarios[1]
    recipe, query = RecipeSpec.model_validate(s["recipe"]), QuerySpec.model_validate(s["query"])
    datasets = {d.key: d for raw in s["datasets"] if (d := DatasetVersion.model_validate(raw))}
    with pytest.raises(RecipeError, match="non-overlapping intervals"):
        SupabaseExecutor(lambda: psycopg.connect(dsn), bindings, postgis_schema="public").execute(recipe, query, datasets)

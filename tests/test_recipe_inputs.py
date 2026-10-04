from datetime import UTC, date, datetime

import httpx
import psycopg
import pytest
from recipe.artifacts import LocalArtifactStore
from recipe.errors import RecipeError
from recipe.models import QuerySpec
from recipe.providers import ROLES, Assessment, JsonPlanner
from recipe.service import RecipeService

from fixtures import movebank_package
from habitat.catalog.store import MemoryCatalog, PostgresCatalog
from habitat.contracts import Coverage, DatasetVersion, StorageRef, Tag, TaxonRef
from habitat.db import database_url
from habitat.ingest import Workspace
from habitat.pipeline import build_request, run
from habitat.recipe_inputs import (
    ANIMAL_LOCATIONS,
    RAINFALL,
    SEARCH_FILTERS,
    VEGETATION,
    HabitatRecipeCatalog,
    executor,
    to_recipe_dataset,
)
from test_chirps import gzipped_tif, server

BBOX = (36.8, -1.6, 37.1, -1.3)
REGION = {"type": "Polygon", "coordinates": [[[36.8, -1.6], [37.1, -1.6], [37.1, -1.3], [36.8, -1.3], [36.8, -1.6]]]}


def habitat_dataset(dataset_id, family, variables, scope="public", species=(), tags=()):
    return DatasetVersion(
        dataset_id=dataset_id,
        version=2,
        created_at=datetime(2026, 10, 3, tzinfo=UTC),
        access_scope=scope,
        family=family,
        source_id=dataset_id.split("--")[0],
        description=f"{dataset_id} on the 1 km grid",
        status="ready",
        storage=StorageRef(uri=f"postgres://{family}?series_id={dataset_id}&version=2", format="postgres"),
        row_grain="any",
        row_count=10,
        raw_artifact_refs=["item-1"],
        mapping_version="m1",
        coverage=Coverage(bbox=BBOX, start=datetime(2011, 1, 1, tzinfo=UTC), end=datetime(2011, 12, 31, tzinfo=UTC),
                          species=[name for _, name in species]),
        variables=variables,
        species=[TaxonRef(gbif_key=key, name=name) for key, name in species],
        tags=list(tags),
    )


def query(**changes):
    values = {
        "query_id": "q-1",
        "question": "How much rain fell before each wildebeest fix?",
        "task_type": "historical",
        "access_scope": "public",
        "time_range": {"start": "2011-03-01T00:00:00Z", "end": "2011-03-01T23:59:59Z"},
        "region": REGION,
        "species": ["Connochaetes taurinus"],
    }
    return QuerySpec.model_validate(values | changes)


@pytest.fixture
def memory_catalog():
    catalog = MemoryCatalog()
    catalog.register_dataset(habitat_dataset("chirps--chirps-v2--ease2-global-1km", "cell_observations",
                                             ["rainfall_mm"], tags=[Tag(key="biome", value="savanna")]))
    catalog.register_dataset(habitat_dataset("sentinel2--sentinel-2-l2a--ease2-global-1km", "cell_observations",
                                             ["ndvi", "mndwi"]))
    catalog.register_dataset(habitat_dataset("copernicus_dem--glo-30--ease2-global-1km", "cell_observations",
                                             ["elevation_m"]))
    catalog.register_dataset(habitat_dataset("movebank_repository--wildebeest--ease2-global-1km",
                                             ANIMAL_LOCATIONS, [], species=[(2441105, "Connochaetes taurinus")]))
    catalog.register_dataset(habitat_dataset("private--rain--ease2-global-1km", "cell_observations",
                                             ["rainfall_mm"], scope="partner-a"))
    return catalog


def test_each_family_maps_to_the_canonical_recipe_columns(memory_catalog):
    descriptors = {d.dataset_id: to_recipe_dataset(d) for d in memory_catalog.records}

    rain = descriptors["chirps--chirps-v2--ease2-global-1km"]
    assert rain.family == RAINFALL and rain.version == "2"
    assert {"dataset_id", "dataset_version", "source_record_id", "cell_id", "geometry", "interval_start",
            "interval_end", "rainfall_mm"} <= {c.name for c in rain.columns}
    assert {c.name: c.unit for c in rain.columns}["rainfall_mm"] == "mm"
    assert descriptors["sentinel2--sentinel-2-l2a--ease2-global-1km"].family == VEGETATION
    animals = descriptors["movebank_repository--wildebeest--ease2-global-1km"]
    assert {c.role: c.name for c in animals.columns}["species"] == "species"
    assert animals.coverage.species == ["Connochaetes taurinus"]
    assert descriptors["copernicus_dem--glo-30--ease2-global-1km"] is None


def test_metadata_search_filters_by_family_and_scope_and_fills_bindings(memory_catalog):
    catalog = HabitatRecipeCatalog(memory_catalog, allowed_scopes={"public"}, schema="habitat")

    page = catalog.search_metadata({"family": RAINFALL}, query=query(), limit=10)

    assert [d.dataset_id for d in page.datasets] == ["chirps--chirps-v2--ease2-global-1km"]
    [rain] = page.datasets
    assert catalog.readable(rain)
    assert catalog.bindings[rain.key].table == "recipe_rainfall_observations"
    assert catalog.bindings[rain.key].native_geometry_columns == {"geometry"}


def test_tags_and_source_filters(memory_catalog):
    catalog = HabitatRecipeCatalog(memory_catalog, allowed_scopes={"public"})

    tagged = catalog.search_metadata({"tags": {"biome": "savanna"}}, query=query(), limit=10)
    by_source = catalog.search_metadata({"source_id": "sentinel2"}, query=query(), limit=10)

    assert [d.family for d in tagged.datasets] == [RAINFALL]
    assert [d.family for d in by_source.datasets] == [VEGETATION]


def test_unsupported_filters_fail_explicitly(memory_catalog):
    catalog = HabitatRecipeCatalog(memory_catalog, allowed_scopes={"public"})

    with pytest.raises(RecipeError) as unknown_field:
        catalog.search_metadata({"habitat_type": "wetland"}, query=query(), limit=10)
    with pytest.raises(RecipeError) as unknown_family:
        catalog.search_metadata({"family": "soil_moisture"}, query=query(), limit=10)

    assert unknown_field.value.code == "UNSUPPORTED_FILTER"
    assert unknown_family.value.code == "UNSUPPORTED_FILTER"


def test_scope_comes_from_the_caller_not_from_the_query(memory_catalog):
    catalog = HabitatRecipeCatalog(memory_catalog, allowed_scopes={"public"})

    page = catalog.search_metadata({"family": RAINFALL}, query=query(access_scope="partner-a"), limit=10)
    [private] = [to_recipe_dataset(d) for d in memory_catalog.records if d.access_scope == "partner-a"]

    assert "private--rain--ease2-global-1km" not in {d.dataset_id for d in page.datasets}
    assert not catalog.authorize(private, query(access_scope="partner-a"))


def test_text_search_scores_word_overlap(memory_catalog):
    catalog = HabitatRecipeCatalog(memory_catalog, allowed_scopes={"public"})

    page = catalog.search_semantic("daily rainfall in savanna", query=query(), limit=10)

    assert page.datasets[0].family == RAINFALL
    assert page.scores[page.datasets[0].key] > 0


def test_planner_context_names_the_supported_filters():
    contexts = []

    def generate(*, instructions, context, schema):
        contexts.append(context)
        return [{"requirement_id": "rain", "description": "rain", "semantic_text": "rain"}]

    JsonPlanner(generate, search_filters=SEARCH_FILTERS).requirements(query())

    assert contexts[0]["search_filters"] == SEARCH_FILTERS


RAIN_DAYS = {date(2011, 2, day) for day in range(22, 29)} | {date(2011, 3, 1)}


def chirps_movebank_and_gbif(body: bytes):
    chirps = server(RAIN_DAYS, set(), body)

    def handle(request: httpx.Request) -> httpx.Response:
        if "chirps-v2.0." in request.url.path:
            return chirps(request)
        if request.url.host == "api.gbif.org":
            if request.url.path.endswith("/species/match"):
                return httpx.Response(200, json={"matchType": "EXACT", "rank": "SPECIES", "usageKey": 2441105,
                                                 "scientificName": "Connochaetes taurinus"})
            return httpx.Response(200, json={"results": []})
        return movebank_package.handler()(request)

    return handle


REQUIREMENTS = [
    {"requirement_id": "fixes", "description": "Wildebeest GPS fixes", "semantic_text": "wildebeest gps fixes",
     "filters": {"family": ANIMAL_LOCATIONS}, "required_columns": ["entity_id", "cell_id"], "species_specific": True},
    {"requirement_id": "rain", "description": "Daily rainfall per 1 km cell", "semantic_text": "daily rainfall",
     "filters": {"family": RAINFALL}, "expected_units": {"rainfall_mm": "mm"}, "lookback_seconds": 7 * 86400},
]


def rain_before_fix_recipe(animals, rainfall):
    return {
        "recipe_id": "rain-before-fix",
        "version": "1",
        "query_ref": "q-1",
        "access_scope": "public",
        "inputs": {"fixes": animals, "rain": rainfall},
        "steps": [
            {"id": "joined", "operation": "window_aggregate", "left": "fixes", "right": "rain",
             "right_columns": {}, "keys": {"cell_id": "cell_id"}, "left_time": "observed_at",
             "right_start": "interval_start", "right_end": "interval_end", "value_column": "rainfall_mm",
             "output": "rain_7d_mm", "window_seconds": 7 * 86400, "minimum_coverage": 0.8},
            {"id": "features", "operation": "select", "input": "joined",
             "columns": {"entity_id": "entity_id", "observed_at": "observed_at", "cell_id": "cell_id",
                         "rain_7d_mm": "rain_7d_mm", "rain_7d_mm_coverage": "rain_7d_mm_coverage"}},
        ],
        "output": {
            "step": "features", "row_grain": "one row per wildebeest fix", "keys": ["entity_id", "observed_at"],
            "description": "Rainfall in the 7 days before each fix, in the cell of the fix",
            "intended_use": "historical movement and rainfall comparison", "time_column": "observed_at",
            "columns": [
                {"name": "entity_id", "type": "string", "nullable": False, "description": "Animal id"},
                {"name": "observed_at", "type": "timestamp", "nullable": False, "description": "Fix time"},
                {"name": "cell_id", "type": "string", "description": "1 km cell of the fix"},
                {"name": "rain_7d_mm", "type": "number", "unit": "mm", "description": "Complete rain days in the 7 days before the fix"},
                {"name": "rain_7d_mm_coverage", "type": "number", "nullable": False,
                 "description": "Share of the 7 days with rainfall data"},
            ],
        },
    }


class SelectAll:
    def assess(self, query, requirement, dataset):
        return Assessment("test", "select-all", requirement.requirement_id, "primary_evidence", 3.0,
                          {str(level): float(level == 3) for level in range(4)},
                          {role: float(role == "primary_evidence") for role in ROLES}, 1, 1)


def recipe_service(database, tmp_path):
    schema = database.execute("SELECT current_schema()").fetchone()[0]
    catalog = HabitatRecipeCatalog(PostgresCatalog(database), allowed_scopes={"public"}, schema=schema)

    def generate(*, instructions, context, schema):
        if schema.get("type") == "array":
            return REQUIREMENTS
        refs = {d["family"]: {"dataset_id": d["dataset_id"], "version": d["version"]} for d in context["datasets"]}
        return {"recipe": rain_before_fix_recipe(refs[ANIMAL_LOCATIONS], refs[RAINFALL])}

    def connect():
        return psycopg.connect(database_url(), options=f"-c search_path={schema},extensions")

    return RecipeService(catalog=catalog, planner=JsonPlanner(generate, search_filters=SEARCH_FILTERS),
                         assessor=SelectAll(), executor=executor(connect, catalog),
                         store=LocalArtifactStore(tmp_path / "recipe"))


def request():
    return {"contract_version": "1.0", "request_id": "r-1", "query_id": "q-1", "access_scope": "public",
            "input": {"query": query().model_dump(mode="json")}}


def test_pipeline_output_is_recipe_input_end_to_end(database, grid, mock_http, tmp_path):
    mock_http(chirps_movebank_and_gbif(gzipped_tif(tmp_path)))
    workspace = Workspace(database, grid)
    rain = run(build_request("chirps", BBOX, date(2011, 2, 22), date(2011, 3, 1), None, None),
               use_agent=False, workspace=workspace)
    fixes = run(build_request("movebank_repository", None, None, None, movebank_package.PACKAGE_UUID, None),
                use_agent=False, workspace=workspace)
    assert (rain.status, fixes.status) == ("ok", "ok")
    service = recipe_service(database, tmp_path)

    first = service.run(request())
    again = service.run(request())

    assert first["status"] == "ok", (first["error"], first["warnings"], first["extensions"])
    rows = service.store.read_dataset(first["output"]["feature_artifact"]["storage"], scope="public").to_pylist()
    assert len(rows) == 4
    # Fixes are at 06:00 or later on 1 March. The complete rain days are 23 to 28 February: 6 of 7 days.
    assert [row["rain_7d_mm"] for row in rows] == pytest.approx([6 * 4.5] * 4)
    assert [row["rain_7d_mm_coverage"] for row in rows] == pytest.approx([6 / 7] * 4)
    assert len({row["entity_id"] for row in rows}) == 2
    assert first["extensions"]["recipe_context"]["cache_hit"] is False
    assert again["extensions"]["recipe_context"]["cache_hit"] is True


def test_a_pinned_version_does_not_see_later_batches(database, grid, mock_http, tmp_path):
    mock_http(chirps_movebank_and_gbif(gzipped_tif(tmp_path)))
    workspace = Workspace(database, grid)
    run(build_request("chirps", BBOX, date(2011, 2, 22), date(2011, 2, 24), None, None),
        use_agent=False, workspace=workspace)
    run(build_request("chirps", BBOX, date(2011, 2, 25), date(2011, 2, 26), None, None),
        use_agent=False, workspace=workspace)

    days = database.execute(
        """
        SELECT dataset_version, count(DISTINCT interval_start)
        FROM recipe_rainfall_observations GROUP BY dataset_version ORDER BY dataset_version::integer
        """
    ).fetchall()

    assert [count for _, count in days] == [3, 5]

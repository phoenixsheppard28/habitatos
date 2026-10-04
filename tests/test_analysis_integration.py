from datetime import date

import httpx
import psycopg
import pytest
from pyproj import Geod
from recipe.artifacts import LocalArtifactStore
from recipe.providers import JsonPlanner
from recipe.service import RecipeService

from analysis.store import ArtifactStore
from fixtures import movebank_package
from habitat.catalog.store import PostgresCatalog
from habitat.db import database_url
from habitat.ingest import Workspace
from habitat.pipeline import build_request, run
from habitat.recipe_inputs import DAILY_MOVEMENT, RAINFALL, SEARCH_FILTERS, HabitatRecipeCatalog, executor
from test_chirps import gzipped_tif, server
from test_recipe_inputs import REGION, SelectAll
from workflow.coordinator import Coordinator
from workflow.handlers import FetchSource, stage_handlers

RAIN_DAYS = {date(2011, 2, day) for day in range(22, 29)} | {date(2011, 3, day) for day in range(1, 7)}
WILDEBEEST = "Connochaetes taurinus"
MOVEMENT_DATASET = "movebank_repository--movebank-data-repository--ease2-global-1km--study-208413731"
WGS84 = Geod(ellps="WGS84")

REQUIREMENTS = [
    {"requirement_id": "movement", "description": "Daily movement of tracked wildebeest",
     "semantic_text": "wildebeest daily movement", "filters": {"family": DAILY_MOVEMENT},
     "required_columns": ["entity_id", "cell_id", "daily_displacement_km"], "species_specific": True},
    {"requirement_id": "rain", "description": "Daily rainfall per 1 km cell", "semantic_text": "daily rainfall",
     "filters": {"family": RAINFALL}, "expected_units": {"rainfall_mm": "mm"}, "lookback_seconds": 7 * 86400},
]

MOVEMENT_COLUMNS = {
    "entity_id": {"type": "string", "nullable": False, "description": "Animal id"},
    "species": {"type": "string", "description": "Scientific name"},
    "day": {"type": "timestamp", "nullable": False, "description": "UTC day"},
    "longitude": {"type": "number", "nullable": False, "unit": "degree", "description": "Last good fix longitude"},
    "latitude": {"type": "number", "nullable": False, "unit": "degree", "description": "Last good fix latitude"},
    "cell_id": {"type": "string", "description": "1 km cell of the last good fix"},
    "daily_displacement_km": {"type": "number", "unit": "km", "description": "Distance from the previous day"},
    "rain_7d_mm": {"type": "number", "unit": "mm", "description": "Rain in the 7 days before the day"},
}


def movement_and_rain_recipe(inputs: dict, columns: list[str]) -> dict:
    return {
        "recipe_id": "daily-movement-and-rain",
        "version": "-".join(columns),
        "query_ref": "q-movement",
        "access_scope": "public",
        "inputs": inputs,
        "steps": [
            {"id": "joined", "operation": "window_aggregate", "left": "movement", "right": "rain",
             "right_columns": {}, "keys": {"cell_id": "cell_id"}, "left_time": "day",
             "right_start": "interval_start", "right_end": "interval_end", "value_column": "rainfall_mm",
             "output": "rain_7d_mm", "window_seconds": 7 * 86400, "minimum_coverage": 0.8},
            {"id": "features", "operation": "select", "input": "joined",
             "columns": {name: name for name in columns}},
        ],
        "output": {
            "step": "features", "row_grain": "one row per wildebeest and UTC day", "keys": ["entity_id", "day"],
            "description": "Daily wildebeest movement with the rain of the previous 7 days in the cell",
            "intended_use": "historical movement and rainfall comparison", "time_column": "day",
            "columns": [{"name": name, **MOVEMENT_COLUMNS[name]} for name in columns],
        },
    }


def scripted_planner(columns: list[str]):
    def generate(*, instructions, context, schema):
        if schema.get("type") == "array":
            return REQUIREMENTS

        refs = {d["family"]: {"dataset_id": d["dataset_id"], "version": d["version"]} for d in context["datasets"]}
        inputs = {"movement": refs[DAILY_MOVEMENT], "rain": refs[RAINFALL]}
        return {"recipe": movement_and_rain_recipe(inputs, columns)}

    return JsonPlanner(generate, search_filters=SEARCH_FILTERS)


def chirps_and_daily_tracks(body: bytes):
    chirps = server(RAIN_DAYS, set(), body)
    tracks = movebank_package.handler(gps_csv=movebank_package.DAILY_GPS_CSV)

    def handle(request):
        if "chirps-v2.0." in request.url.path:
            return chirps(request)
        if request.url.host == "api.gbif.org":
            if request.url.path.endswith("/species/match"):
                return httpx.Response(200, json={"matchType": "EXACT", "rank": "SPECIES", "usageKey": 2441105,
                                                 "scientificName": WILDEBEEST})
            return httpx.Response(200, json={"results": []})
        return tracks(request)

    return handle


def coordinator(database, grid, tmp_path, columns: list[str]) -> Coordinator:
    schema = database.execute("SELECT current_schema()").fetchone()[0]
    catalog = HabitatRecipeCatalog(PostgresCatalog(database), allowed_scopes={"public"}, schema=schema)

    def connect():
        return psycopg.connect(database_url(), options=f"-c search_path={schema},extensions")

    recipe_service = RecipeService(catalog=catalog, planner=scripted_planner(columns), assessor=SelectAll(),
                                   executor=executor(connect, catalog), store=LocalArtifactStore(tmp_path / "recipe"))
    handlers = stage_handlers(
        workspace=Workspace(database, grid),
        recipe_service=recipe_service,
        model_store=ArtifactStore(tmp_path / "analysis"),
        sources=[FetchSource("chirps"), FetchSource("movebank_repository", movebank_package.PACKAGE_UUID)],
        history_days=7,
    )
    return Coordinator(handlers)


def query_request(**changes) -> dict:
    query = {
        "question": "How far did the tracked wildebeest move each day, and did rain matter?",
        "task_type": "historical",
        "species": [WILDEBEEST],
        "region": REGION,
        "time_range": {"start": "2011-03-01T00:00:00Z", "end": "2011-03-06T23:59:59Z"},
    } | changes
    return {"contract_version": "1.0", "request_id": "r-movement", "query_id": "q-movement",
            "access_scope": "public", "input": {"query": query}}


def kilometres(start, end) -> float:
    return WGS84.inv(*start, *end)[2] / 1000


def test_daily_movement_view_keeps_gaps_and_skips_outliers(database, grid, mock_http, tmp_path):
    mock_http(chirps_and_daily_tracks(gzipped_tif(tmp_path)))
    run(build_request("movebank_repository", None, None, None, movebank_package.PACKAGE_UUID, None),
        use_agent=False, workspace=Workspace(database, grid))

    rows = database.execute(
        """
        SELECT entity_id, (day AT TIME ZONE 'UTC')::date, fix_count, longitude, daily_displacement_km
        FROM recipe_animal_daily_movement ORDER BY entity_id, day
        """
    ).fetchall()

    naboisho = [row for row in rows if row[0].endswith("Naboisho")]
    olope = [row for row in rows if row[0].endswith("Olope")]
    assert [row[4] is None for row in olope] == [True, False, True, False, False]
    assert naboisho[0][4] is None
    # The outlier at 23:00 on 3 March is not the last fix of that day.
    assert (naboisho[2][2], naboisho[2][3]) == (2, pytest.approx(36.925))
    assert naboisho[1][4] == pytest.approx(kilometres((36.905, -1.45), (36.915, -1.45)), rel=1e-6)
    assert {row[1] for row in rows} == {date(2011, 3, day) for day in range(1, 7)}
    assert all(row[0].startswith("movebank:") for row in rows)
    assert database.execute("SELECT DISTINCT dataset_id FROM recipe_animal_daily_movement").fetchall() == [
        (MOVEMENT_DATASET + "--daily-movement",)]


def test_query_flows_from_fetch_to_analysis(database, grid, mock_http, tmp_path):
    mock_http(chirps_and_daily_tracks(gzipped_tif(tmp_path)))
    flow = coordinator(database, grid, tmp_path, list(MOVEMENT_COLUMNS))

    job = flow.submit_query(query_request())

    assert job["status"] == "complete", (job["error"], job["stages"])
    assert [stage["name"] for stage in job["stages"]] == ["fetch", "normalize", "recipe", "analysis"]
    result = job["result"]
    metrics = result["metrics"]
    assert (metrics["n_animals"], metrics["n_observations"]) == (2, 11)
    # First day of each animal and Olope's day after its gap on 3 March have no previous fix.
    assert (metrics["n_displacement_rows"], metrics["missing_displacement_rows"]) == (8, 3)
    assert metrics["gap_animal_days"] == 1
    assert metrics["displacement_unit"] == "km"
    naboisho_step = kilometres((36.905, -1.45), (36.915, -1.45))
    olope_step = kilometres((36.95, -1.50), (36.95, -1.49))
    assert metrics["displacement_km_total"] == pytest.approx(5 * naboisho_step + 3 * olope_step, rel=1e-6)
    assert result["evidence"]["recipe_id"] == "daily-movement-and-rain"
    cited = {ref["dataset_id"]: ref["version"] for ref in result["evidence"]["datasets"]}
    assert cited.keys() == {MOVEMENT_DATASET, "chirps--chirps-v2.0-daily-p05--ease2-global-1km"}
    assert cited[MOVEMENT_DATASET] == "1"
    # The fixture rain is the same in every cell and day, so the rainfall role is found but cannot split the days.
    analysis = flow.job_store.get(job["job_id"]).stages[-1].output
    assert any(warning.startswith("Rain in the 7 days before the day was present but fewer than 2 days")
               for warning in analysis["warnings"])


def test_a_recipe_without_displacement_is_insufficient_for_analysis(database, grid, mock_http, tmp_path):
    mock_http(chirps_and_daily_tracks(gzipped_tif(tmp_path)))
    columns = [name for name in MOVEMENT_COLUMNS if name != "daily_displacement_km"]

    job = coordinator(database, grid, tmp_path, columns).submit_query(query_request())

    assert job["status"] == "insufficient_data", (job["error"], job["stages"])
    assert [stage["state"] for stage in job["stages"]] == ["succeeded"] * 4
    assert job["result"]["code"] == "missing_roles"
    assert job["result"]["metrics"]["missing_roles"] == ["daily_displacement"]


def test_a_forecast_stops_at_recipe_as_insufficient(database, grid, mock_http, tmp_path):
    mock_http(chirps_and_daily_tracks(gzipped_tif(tmp_path)))
    forecast = {"cutoff": "2011-03-05T00:00:00Z", "horizon_days": 1, "target": "next_day_displacement"}

    job = coordinator(database, grid, tmp_path, list(MOVEMENT_COLUMNS)).submit_query(
        query_request(task_type="forecast", forecast=forecast))

    assert job["status"] == "insufficient_data"
    assert [stage["state"] for stage in job["stages"]] == ["succeeded", "succeeded", "succeeded", "queued"]

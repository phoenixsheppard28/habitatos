"""End-to-end run: how did Wyoming Sublette mule deer move in response to NDVI changes in spring 2019?

The coordinator runs Fetch, Normalize, Recipe and Analysis for one historical query. Claude plans the recipe;
`--scripted` uses a fixed recipe instead. The last section compares movement with NDVI change per 1 km cell,
which the Analysis lane does not calculate.

Run with `uv run python examples/sublette_mule_deer_ndvi.py`. HABITAT_DATABASE_URL selects the database.
"""

import argparse
import json
import logging
from pathlib import Path

import pandas as pd
import psycopg
from recipe.artifacts import LocalArtifactStore
from recipe.providers import ROLES, Assessment, JsonPlanner
from recipe.service import RecipeService

from analysis.store import ArtifactStore
from habitat.catalog.store import PostgresCatalog
from habitat.config import settings
from habitat.db import connect, database_url
from habitat.grid import default_grid
from habitat.ingest import Workspace
from habitat.llm import CATALOG_MODEL, client
from habitat.recipe_inputs import (
    DAILY_MOVEMENT,
    FAMILIES,
    SEARCH_FILTERS,
    VEGETATION,
    HabitatRecipeCatalog,
    executor,
)
from workflow.coordinator import Coordinator
from workflow.handlers import FetchSource, stage_handlers

MULE_DEER = "Odocoileus hemionus"
SUBLETTE_PACKAGE = "0e3a4577-b063-47d9-9d6b-edec949aa5fa"
WEST, SOUTH, EAST, NORTH = -110.75, 41.55, -108.70, 43.80
REGION = {"type": "Polygon", "coordinates": [[[WEST, SOUTH], [EAST, SOUTH], [EAST, NORTH], [WEST, NORTH],
                                              [WEST, SOUTH]]]}
SPRING_2019 = {"start": "2019-03-01T00:00:00Z", "end": "2019-06-30T23:59:59Z"}
QUESTION = "How did the tracked Sublette mule deer move in response to NDVI changes during spring 2019?"
COMPOSITE_DAYS = 16
MODIS_LOOKBACK_DAYS = 2 * COMPOSITE_DAYS
RUN_DIR = settings().data_dir / "runs" / "sublette-mule-deer-ndvi"

PLANNER_SYSTEM = (
    "You are the Recipe planner of Dora. Always answer with one call to the `answer` tool. "
    "Use only the dataset versions and operations in the context. The answer must satisfy the JSON schema exactly. "
    "In `select.columns` and `right_columns`, each key is the new output name and each value is the source column. "
    "The Analysis lane needs one row per animal and UTC day with a daily_displacement column, "
    "so use the animal_daily_movement family for movement. "
    "In required_columns and expected_units, use only these exact column names and unit spellings per family: "
    + json.dumps({family.name: {column.name: column.unit for column in family.columns}
                  for family in FAMILIES.values()})
)

FEATURE_COLUMNS = {
    "entity_id": {"type": "string", "nullable": False, "description": "Animal id"},
    "species": {"type": "string", "description": "Scientific name"},
    "day": {"type": "timestamp", "nullable": False, "description": "UTC day"},
    "longitude": {"type": "number", "nullable": False, "unit": "degree", "description": "Last good fix longitude"},
    "latitude": {"type": "number", "nullable": False, "unit": "degree", "description": "Last good fix latitude"},
    "cell_id": {"type": "string", "description": "1 km cell of the last good fix"},
    "daily_displacement_km": {"type": "number", "unit": "km", "description": "Distance from the previous day"},
    "ndvi": {"type": "number", "unit": "1", "description": "MODIS NDVI of the cell in the latest 16-day composite"},
    "ndvi_composite_start": {"type": "timestamp", "description": "Start of the matched MODIS composite"},
}


class AcceptAll:
    """The Jev assessor is not connected. Every candidate that passes the catalog checks is primary evidence."""

    def assess(self, query, requirement, dataset):
        return Assessment("local", "accept-all", requirement.requirement_id, "primary_evidence", 3.0,
                          {str(level): float(level == 3) for level in range(4)},
                          {role: float(role == "primary_evidence") for role in ROLES}, 1, 1)


def claude_generate(*, instructions, context, schema):
    """Structured output through a tool call. The tool input must be an object, so arrays are wrapped.

    Opus 5.5 rejects a forced tool_choice, so the system prompt asks for the call.
    """
    schema = dict(schema)
    definitions = schema.pop("$defs", {})
    tool = {
        "name": "answer",
        "description": "Return the answer.",
        "input_schema": {"type": "object", "properties": {"value": schema}, "required": ["value"],
                         "$defs": definitions},
    }
    message = client().messages.create(
        model=CATALOG_MODEL,
        max_tokens=16000,
        system=PLANNER_SYSTEM,
        tools=[tool],
        messages=[{"role": "user", "content": f"{instructions}\n\nContext:\n{json.dumps(context, default=str)}"}],
    )

    block = next((block for block in message.content if block.type == "tool_use"), None)
    if block is None:
        raise ValueError("the planner answered without the answer tool")
    return block.input["value"]


def scripted_generate(*, instructions, context, schema):
    if schema.get("type") == "array":
        return [
            {"requirement_id": "movement", "description": "Daily movement of tracked mule deer",
             "semantic_text": "mule deer daily movement", "filters": {"family": DAILY_MOVEMENT},
             "required_columns": ["entity_id", "cell_id", "daily_displacement_km"], "species_specific": True},
            {"requirement_id": "ndvi", "description": "NDVI per 1 km cell", "semantic_text": "ndvi vegetation",
             "filters": {"family": VEGETATION, "variables": ["ndvi"]},
             "lookback_seconds": MODIS_LOOKBACK_DAYS * 86400},
        ]

    refs = {d["family"]: {"dataset_id": d["dataset_id"], "version": d["version"]} for d in context["datasets"]}
    return {"recipe": movement_and_ndvi_recipe(refs[DAILY_MOVEMENT], refs[VEGETATION])}


def movement_and_ndvi_recipe(movement: dict, vegetation: dict) -> dict:
    return {
        "recipe_id": "daily-movement-and-ndvi",
        "version": "1",
        "query_ref": "q-sublette-ndvi",
        "access_scope": "public",
        "inputs": {"movement": movement, "vegetation": vegetation},
        "steps": [
            {"id": "ndvi", "operation": "filter", "input": "vegetation",
             "predicates": [{"column": "index_name", "operator": "eq", "value": "ndvi"}]},
            {"id": "joined", "operation": "asof_join", "left": "movement", "right": "ndvi",
             "right_columns": {"ndvi": "index_value", "ndvi_composite_start": "observed_at"},
             "keys": {"cell_id": "cell_id"}, "left_time": "day", "right_time": "observed_at",
             "right_tie_break": "source_record_id", "tolerance_seconds": MODIS_LOOKBACK_DAYS * 86400},
            {"id": "features", "operation": "select", "input": "joined",
             "columns": {name: name for name in FEATURE_COLUMNS}},
        ],
        "output": {
            "step": "features", "row_grain": "one row per mule deer and UTC day", "keys": ["entity_id", "day"],
            "description": "Daily mule deer movement with the NDVI of the latest MODIS composite in the cell",
            "intended_use": "historical movement and vegetation comparison", "time_column": "day",
            "columns": [{"name": name, **column} for name, column in FEATURE_COLUMNS.items()],
        },
    }


def build_coordinator(connection: psycopg.Connection, scripted: bool) -> tuple[Coordinator, RecipeService]:
    catalog = HabitatRecipeCatalog(PostgresCatalog(connection), allowed_scopes={"public"})
    planner = JsonPlanner(scripted_generate if scripted else claude_generate, search_filters=SEARCH_FILTERS)
    recipe_service = RecipeService(
        catalog=catalog, planner=planner, assessor=AcceptAll(),
        executor=executor(lambda: psycopg.connect(database_url()), catalog, max_rows=1_000_000,
                          statement_timeout_ms=300_000),
        store=LocalArtifactStore(RUN_DIR / "recipe"), max_plan_attempts=5,
    )
    handlers = stage_handlers(
        workspace=Workspace(connection, default_grid()),
        recipe_service=recipe_service,
        model_store=ArtifactStore(RUN_DIR / "analysis"),
        sources=[FetchSource("movebank_repository", SUBLETTE_PACKAGE), FetchSource("modis_mod13q1")],
        history_days=MODIS_LOOKBACK_DAYS,
    )
    return Coordinator(handlers), recipe_service


def query_request(request_id: str) -> dict:
    query = {"question": QUESTION, "task_type": "historical", "species": [MULE_DEER], "region": REGION,
             "time_range": SPRING_2019}
    return {"contract_version": "1.0", "request_id": request_id, "query_id": "q-sublette-ndvi",
            "access_scope": "public", "input": {"query": query}}


def features(job: dict, flow: Coordinator, recipe_service: RecipeService) -> pd.DataFrame:
    recipe_output = next(stage for stage in flow.job_store.get(job["job_id"]).stages if stage.name == "recipe").output
    storage = recipe_output["output"]["feature_artifact"]["storage"]
    return recipe_service.store.read_dataset(storage, scope="public").to_pandas()


def ndvi_change_by_cell(connection: psycopg.Connection, cell_ids: list[str]) -> pd.DataFrame:
    """NDVI of each cell per composite, with the change from the previous composite of the same cell."""
    rows = connection.execute(
        """
        SELECT cell_id, observed_at, avg(index_value) AS ndvi
        FROM recipe_vegetation_observations
        WHERE index_name = 'ndvi' AND cell_id = ANY(%s)
        GROUP BY cell_id, observed_at
        """,
        (cell_ids,),
    ).fetchall()
    frame = pd.DataFrame(rows, columns=["cell_id", "ndvi_composite_start", "cell_ndvi"]).sort_values(
        ["cell_id", "ndvi_composite_start"])

    frame["ndvi_change"] = frame.groupby("cell_id")["cell_ndvi"].diff()
    return frame


def regional_ndvi(connection: psycopg.Connection) -> pd.DataFrame:
    rows = connection.execute(
        """
        SELECT observed_at, percentile_cont(0.5) WITHIN GROUP (ORDER BY index_value), count(*)
        FROM recipe_vegetation_observations WHERE index_name = 'ndvi' GROUP BY observed_at ORDER BY observed_at
        """
    ).fetchall()
    return pd.DataFrame(rows, columns=["composite_start", "regional_median_ndvi", "cells"])


def ndvi_change_response(frame: pd.DataFrame, connection: psycopg.Connection) -> None:
    changes = ndvi_change_by_cell(connection, sorted(frame["cell_id"].dropna().unique()))
    days = frame.merge(changes[["cell_id", "ndvi_composite_start", "ndvi_change"]],
                       on=["cell_id", "ndvi_composite_start"], how="left")
    days["period"] = days["ndvi_composite_start"].dt.date

    print("\n== Per 16-day composite: deer movement, location and NDVI")
    per_period = days.groupby("period").agg(
        deer=("entity_id", "nunique"),
        median_km_per_day=("daily_displacement_km", "median"),
        median_latitude=("latitude", "median"),
        ndvi_at_deer=("ndvi", "median"),
        ndvi_change_at_deer=("ndvi_change", "median"),
    )
    print(per_period.round(3).to_string())

    print("\n== Regional median NDVI per composite (all cells in the area)")
    print(regional_ndvi(connection).round(3).to_string(index=False))

    paired = days.dropna(subset=["daily_displacement_km", "ndvi_change"])
    print(f"\n== Movement against NDVI change in the deer's cell ({len(paired)} deer-days)")
    print("Spearman rho, displacement vs NDVI change:",
          round(paired["daily_displacement_km"].corr(paired["ndvi_change"], method="spearman"), 3))
    print("Spearman rho, displacement vs NDVI level:",
          round(paired["daily_displacement_km"].corr(paired["ndvi"], method="spearman"), 3))
    bins = pd.cut(paired["ndvi_change"], [-1, -0.05, 0.0, 0.05, 0.10, 1],
                  labels=["falling > 0.05", "falling 0-0.05", "rising 0-0.05", "rising 0.05-0.10", "rising > 0.10"])
    print(paired.groupby(bins, observed=True)["daily_displacement_km"].agg(["count", "median", "mean"]).round(3)
          .to_string())

    print("\n== Long moves (> 5 km/day, the migration days) by NDVI change in the cell")
    paired = paired.assign(long_move=paired["daily_displacement_km"] > 5)
    print(paired.groupby(bins, observed=True)["long_move"].agg(["count", "mean"]).round(3)
          .rename(columns={"mean": "share_long_moves"}).to_string())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scripted", action="store_true", help="use the fixed recipe instead of Claude")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    with connect() as connection:
        flow, recipe_service = build_coordinator(connection, args.scripted)
        job = flow.submit_query(query_request("r-sublette-ndvi-scripted" if args.scripted else "r-sublette-ndvi"))

        print("job status:", job["status"])
        for stage in flow.job_store.get(job["job_id"]).stages:
            output = stage.output or {}
            print(f"  {stage.name}: {stage.state}, {output.get('status')}, warnings={output.get('warnings', [])[:3]}"
                  + (f", error={output['error']}" if output.get("error") else ""))
        if job["status"] not in {"complete", "partial"}:
            print(json.dumps(job, indent=2, default=str)[:4000])
            raise SystemExit(1)

        result = job["result"]
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        Path(RUN_DIR / "result.json").write_text(json.dumps(job, indent=2, default=str))
        print("\n== Analysis findings")
        for finding in result["findings"]:
            print("-", finding)
        print("\n== Limitations")
        for limitation in result["limitations"]:
            print("-", limitation)

        ndvi_change_response(features(job, flow, recipe_service), connection)


if __name__ == "__main__":
    main()

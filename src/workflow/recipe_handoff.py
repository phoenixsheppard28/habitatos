"""Recipe -> Analysis handoff: build the Analysis request from a Recipe response, and read Recipe tables.

Analysis roles come from the roles that Recipe validation infers, not from the planner's declared roles.
Measurements get an Analysis role from their lineage to a Stage 2 family. See ANALYSIS_INTEGRATION.md.
"""

from datetime import datetime, timezone

from recipe.errors import RecipeError
from recipe.validation import animal_sampling_grain

from analysis.store import ArtifactStore, StorageError
from habitat.recipe_inputs import RAINFALL, VEGETATION

SHARED_ROLES = frozenset({"entity_id", "longitude", "latitude", "cell_id", "species", "daily_displacement"})
MEASUREMENT_ROLES = {
    (RAINFALL, "rainfall_mm"): "rainfall",
    (VEGETATION, "index_value"): "vegetation_index",
    (VEGETATION, "observed_at"): "vegetation_valid_from",
    (VEGETATION, "observed_until"): "vegetation_valid_until",
}


def analysis_request(query: dict, recipe_response: dict, *, request_id: str, boundaries: list | None = None) -> dict:
    output = recipe_response["output"]
    recipe, artifact = output["recipe"], output["feature_artifact"]
    sources = {(dataset["dataset_id"], dataset["version"]): dataset
               for dataset in recipe_response["extensions"]["recipe_context"]["source_datasets"]}
    inputs = {alias: sources[(ref["dataset_id"], ref["version"])] for alias, ref in recipe["inputs"].items()}
    families = {alias: dataset["family"] for alias, dataset in inputs.items()}

    feature_artifact = {
        **artifact,
        "input_dataset_refs": catalog_refs(inputs.values()),
        "columns": [analysis_column(column, recipe["output"]["time_column"], families) for column in artifact["columns"]],
        "coverage": feature_coverage(query, inputs.values()),
        "sampling_grain": animal_sampling_grain(recipe, families),
    }
    composite_aliases = {alias for alias, dataset in inputs.items()
                         if dataset["metadata"].get("source_id") == "modis_mod13q1"}
    for column, original in zip(feature_artifact["columns"], artifact["columns"]):
        if column["role"] in {"vegetation_valid_from", "vegetation_valid_until"}:
            aliases = {source.split(".", 1)[0] for source in original.get("derived_from") or []}
            if not aliases <= composite_aliases:
                column["role"] = None
    return {
        "contract_version": "1.0",
        "request_id": request_id,
        "query_id": query["query_id"],
        "access_scope": query["access_scope"],
        "input": {
            "query": query,
            "recipe": {"recipe_id": recipe["recipe_id"], "version": recipe["version"],
                       "cutoff": None, "features_respect_cutoff": False},
            "feature_artifact": feature_artifact,
            "boundaries": boundaries or [],
        },
    }


def analysis_column(column: dict, time_column: str, families: dict[str, str]) -> dict:
    return {
        "name": column["name"],
        "type": column["type"],
        "nullable": column["nullable"],
        "unit": column.get("unit"),
        "role": analysis_role(column, time_column, families),
        "description": column.get("description"),
    }


def analysis_role(column: dict, time_column: str, families: dict[str, str]) -> str | None:
    """The output time column of the recipe is the only event time. Aggregated displacement is a measurement."""
    if column["name"] == time_column:
        return "event_time"

    role = column.get("role")
    if role in SHARED_ROLES:
        return role

    lineage = column.get("derived_from") or []
    if len(lineage) == 1:
        alias, source_column = lineage[0].split(".", 1)
        if role == "measurement" or column.get("type") == "timestamp":
            return MEASUREMENT_ROLES.get((families.get(alias), source_column))

    return None


def catalog_refs(datasets) -> list[dict]:
    """A derived Recipe dataset (daily movement) cites the catalog dataset version it comes from."""
    refs = []
    for dataset in datasets:
        ref = dataset["metadata"].get("derived_from") or {"dataset_id": dataset["dataset_id"],
                                                          "version": dataset["version"]}
        if ref not in refs:
            refs.append(ref)
    return refs


def feature_coverage(query: dict, datasets) -> dict | None:
    """Coverage of the sources, cut to the query interval. Recipe filters output rows to that interval."""
    datasets = list(datasets)
    coverage = {}
    species = sorted({name for dataset in datasets for name in dataset["coverage"].get("species") or []})
    if species:
        coverage["species"] = species

    starts = [dataset["coverage"].get("start") for dataset in datasets]
    ends = [dataset["coverage"].get("end") for dataset in datasets]
    if datasets and all(starts) and all(ends):
        start = max(min(map(utc, starts)), utc(query["time_range"]["start"]))
        end = min(max(map(utc, ends)), utc(query["time_range"]["end"]))
        coverage["start"], coverage["end"] = iso(start), iso(end)

    return coverage or None


def utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class RecipeArtifactReader:
    """The Analysis store interface over the scoped Recipe store.

    Tables resolve only inside `access_scope`. Forecast model files go to a separate Analysis store.
    """

    def __init__(self, recipe_store, access_scope: str, model_store: ArtifactStore):
        self.recipe_store = recipe_store
        self.access_scope = access_scope
        self.model_store = model_store

    def read_dataset(self, storage):
        storage = storage.model_dump() if hasattr(storage, "model_dump") else dict(storage)
        if storage.get("format") != "parquet":
            raise StorageError(f"unsupported table format: {storage.get('format')}")

        try:
            table = self.recipe_store.read_dataset(storage, scope=self.access_scope)
        except RecipeError as error:
            raise StorageError(str(error)) from error

        return table.to_pandas()

    def write_json(self, relative: str, payload: dict) -> str:
        return self.model_store.write_json(relative, payload)

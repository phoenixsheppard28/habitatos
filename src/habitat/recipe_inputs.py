"""Stage 2 side of the Recipe handoff: catalog callbacks, descriptors and trusted SQL bindings.

Recipe reads the views of migration 008. See RECIPE_INTEGRATION.md.
"""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import timedelta

import psycopg
from recipe.catalog import SearchPage
from recipe.compiler import TableBinding
from recipe.errors import RecipeError
from recipe.execution import SupabaseExecutor
from recipe.models import Column, Coverage, DatasetVersion, QuerySpec
from shapely.geometry import shape

from habitat.catalog.store import MemoryCatalog, PostgresCatalog
from habitat.contracts import DatasetVersion as HabitatDataset
from habitat.contracts import SearchFilters, Tag
from habitat.normalize.rows import ANIMAL_LOCATIONS

RAINFALL = "rainfall_observations"
VEGETATION = "vegetation_observations"
VEGETATION_INDICES = frozenset({"ndvi", "evi", "mndwi", "ndmi"})
POSTGIS_SCHEMA = "extensions"

SEARCH_FILTERS = {
    "family": f"one of {sorted([ANIMAL_LOCATIONS, RAINFALL, VEGETATION])}, or a list of them",
    "variables": f"list of measured variables: rainfall_mm or {sorted(VEGETATION_INDICES)}; any one matches",
    "source_id": "source id or list of them, for example chirps, sentinel2, modis_mod13q1, movebank",
    "tags": "object of catalog tag key to value; every pair must match, for example {\"biome\": \"savanna\"}",
}


def identity_columns() -> list[Column]:
    return [
        Column(name="dataset_id", type="string", nullable=False, description="Catalog dataset id (the series id)"),
        Column(name="dataset_version", type="string", nullable=False, description="Pinned catalog version"),
        Column(name="source_record_id", type="string", nullable=False,
               description="Source item id (a scene, a daily file or a tracking record)"),
    ]


def cell_columns() -> list[Column]:
    return [
        Column(name="cell_id", type="string", nullable=False, role="cell_id",
               description="EASE-Grid 2.0 global 1 km cell id; joins to animal_locations.cell_id"),
        Column(name="geometry", type="geometry", nullable=False, role="geometry",
               description="Cell polygon, WGS84"),
    ]


def quality_columns() -> list[Column]:
    return [
        Column(name="available_at", type="timestamp", nullable=False, role="available_at",
               description="When the source published the value; use it for point-in-time joins"),
        Column(name="quality_flag", type="string", nullable=False, description="Quality flag of the value"),
        Column(name="valid_fraction", type="number", nullable=False, unit="1",
               description="Share of valid source pixels in the cell, 0 to 1"),
    ]


@dataclass(frozen=True)
class RecipeFamily:
    name: str
    view: str
    row_grain: str
    columns: list[Column] = field(hash=False)
    native_geometry_columns: frozenset[str] = frozenset()


FAMILIES = {
    RAINFALL: RecipeFamily(
        name=RAINFALL,
        view="recipe_rainfall_observations",
        row_grain="one row per 1 km cell and accumulation interval",
        columns=[
            *identity_columns(),
            *cell_columns(),
            Column(name="interval_start", type="timestamp", nullable=False, role="interval_start",
                   description="Start of the accumulation interval, inclusive, UTC"),
            Column(name="interval_end", type="timestamp", nullable=False, role="interval_end",
                   description="End of the accumulation interval, exclusive, UTC"),
            Column(name="rainfall_mm", type="number", unit="mm", role="measurement",
                   description="Rainfall accumulated over the interval in the cell"),
            Column(name="product_status", type="string", nullable=False,
                   description="final or preliminary; final replaces preliminary for the same day"),
            *quality_columns(),
        ],
        native_geometry_columns=frozenset({"geometry"}),
    ),
    VEGETATION: RecipeFamily(
        name=VEGETATION,
        view="recipe_vegetation_observations",
        row_grain="one row per 1 km cell, spectral index and acquisition",
        columns=[
            *identity_columns(),
            *cell_columns(),
            Column(name="observed_at", type="timestamp", nullable=False, role="event_time",
                   description="Acquisition time, or start of a composite period, UTC"),
            Column(name="observed_until", type="timestamp", nullable=False,
                   description="Acquisition time, or end of a composite period, UTC"),
            Column(name="index_name", type="string", nullable=False,
                   description=f"Spectral index name, one of {sorted(VEGETATION_INDICES)}"),
            Column(name="index_value", type="number", unit="1", role="measurement",
                   description="Cell mean of the dimensionless spectral index"),
            Column(name="index_std", type="number", unit="1", description="Cell standard deviation of the index"),
            Column(name="pixel_count", type="integer", nullable=False, description="Valid source pixels in the cell"),
            *quality_columns(),
        ],
        native_geometry_columns=frozenset({"geometry"}),
    ),
    ANIMAL_LOCATIONS: RecipeFamily(
        name=ANIMAL_LOCATIONS,
        view="recipe_animal_locations",
        row_grain="one row per animal fix (animal, time and sensor)",
        columns=[
            *identity_columns(),
            Column(name="entity_id", type="string", nullable=False, role="entity_id",
                   description="Animal id, scoped by source and study, for example movebank:<study>:<animal>"),
            Column(name="species", type="string", role="species", description="Scientific name of the animal"),
            Column(name="observed_at", type="timestamp", nullable=False, role="event_time",
                   description="Fix time, UTC"),
            Column(name="longitude", type="number", nullable=False, unit="degree", role="longitude",
                   description="WGS84 longitude of the fix"),
            Column(name="latitude", type="number", nullable=False, unit="degree", role="latitude",
                   description="WGS84 latitude of the fix"),
            Column(name="cell_id", type="string", role="cell_id",
                   description="EASE-Grid 2.0 global 1 km cell that contains the fix"),
            Column(name="sensor_type", type="string", nullable=False, description="Movebank sensor type"),
            Column(name="available_at", type="timestamp", nullable=False, role="available_at",
                   description="When the source published the fix"),
            Column(name="quality_flag", type="string", nullable=False,
                   description="good, or the reason the fix is suspect"),
        ],
    ),
}


def recipe_family(dataset: HabitatDataset) -> str | None:
    """The Recipe family of a catalog dataset. None when the dataset has no Recipe view (for example elevation)."""
    if dataset.family == ANIMAL_LOCATIONS:
        return ANIMAL_LOCATIONS

    variables = set(dataset.variables)
    if dataset.family != "cell_observations" or not variables:
        return None

    if variables == {"rainfall_mm"}:
        return RAINFALL

    return VEGETATION if variables <= VEGETATION_INDICES else None


def to_recipe_dataset(dataset: HabitatDataset) -> DatasetVersion | None:
    family_name = recipe_family(dataset)
    if family_name is None:
        return None

    family = FAMILIES[family_name]
    version = str(dataset.version)
    coverage = dataset.coverage
    return DatasetVersion(
        dataset_id=dataset.dataset_id,
        version=version,
        access_scope=dataset.access_scope,
        status=dataset.status,
        family=family.name,
        description=dataset.description,
        row_grain=family.row_grain,
        columns=[column.model_copy() for column in family.columns],
        storage={"uri": f"postgres://{family.view}?dataset_id={dataset.dataset_id}&dataset_version={version}",
                 "format": "postgres"},
        mapping_version=dataset.mapping_version,
        validation_report_ref=dataset.validation_report_ref
        or f"postgres://ingest_batches?series_id={dataset.dataset_id}&version={version}",
        coverage=Coverage(start=coverage.start, end=coverage.end, bbox=coverage.bbox, species=coverage.species),
        # Stored rows before deduplication; the view can return fewer.
        row_count=None,
        metadata={
            "source_id": dataset.source_id,
            "variables": dataset.variables,
            "summary": dataset.summary,
            "species_gbif_keys": [taxon.gbif_key for taxon in dataset.species],
            "stored_row_count": dataset.row_count,
            "tags": [{"key": tag.key, "value": tag.value, "origin": tag.origin.value} for tag in dataset.tags],
        },
    )


def binding(dataset: DatasetVersion, schema: str) -> TableBinding:
    family = FAMILIES[dataset.family]
    return TableBinding(
        schema=schema,
        table=family.view,
        columns={column.name: column.name for column in family.columns},
        native_geometry_columns=family.native_geometry_columns,
    )


class HabitatRecipeCatalog:
    """The Recipe `Catalog` protocol over the habitat catalog.

    `allowed_scopes` must come from the authenticated caller, never from the request.
    `bindings` fills as datasets are served; pass the same dict to the executor.
    """

    def __init__(
        self,
        catalog: MemoryCatalog | PostgresCatalog,
        *,
        allowed_scopes: Iterable[str],
        schema: str = "public",
    ):
        self.catalog = catalog
        self.allowed_scopes = frozenset(allowed_scopes)
        self.schema = schema
        self.bindings: dict[tuple[str, str], TableBinding] = {}

    def search_metadata(self, filters: dict, *, query: QuerySpec, limit: int) -> SearchPage:
        unsupported = sorted(set(filters) - SEARCH_FILTERS.keys())
        if unsupported:
            raise RecipeError("UNSUPPORTED_FILTER", f"unsupported search filters {unsupported}; "
                              f"supported: {sorted(SEARCH_FILTERS)}")

        families = as_list(filters.get("family"))
        unknown = sorted(set(families) - FAMILIES.keys())
        if unknown:
            raise RecipeError("UNSUPPORTED_FILTER", f"unknown families {unknown}; known: {sorted(FAMILIES)}")

        source_ids = set(as_list(filters.get("source_id")))
        tags = filters.get("tags") or {}
        if not isinstance(tags, dict):
            raise RecipeError("UNSUPPORTED_FILTER", "tags must be an object of key to value")

        search = self.filters(query)
        search.variables = as_list(filters.get("variables")) or None
        search.tags_all = [Tag(key=key, value=str(value)) for key, value in tags.items()] or None
        datasets = [
            dataset for dataset in self.search(search)
            if (not families or dataset.family in families)
            and (not source_ids or dataset.metadata["source_id"] in source_ids)
        ]

        return SearchPage(datasets[:limit], truncated=len(datasets) > limit)

    def search_semantic(self, text: str, *, query: QuerySpec, limit: int) -> SearchPage:
        """Lexical stand-in: the catalog has no embedding index. Scores are the share of query words found."""
        words = set(re.findall(r"[a-z0-9]+", text.lower()))
        scored = []
        for dataset in self.search(self.filters(query)):
            found = words & set(re.findall(r"[a-z0-9]+", searchable_text(dataset)))
            if found:
                scored.append((len(found) / len(words), dataset))
        scored.sort(key=lambda pair: (-pair[0], pair[1].dataset_id))

        page = scored[:limit]
        return SearchPage([dataset for _, dataset in page], truncated=len(scored) > limit,
                          scores={dataset.key: score for score, dataset in page})

    def authorize(self, dataset: DatasetVersion, query: QuerySpec) -> bool:
        return dataset.access_scope in self.allowed_scopes

    def readable(self, dataset: DatasetVersion) -> bool:
        return dataset.key in self.bindings

    def inspect(self, dataset: DatasetVersion, query: QuerySpec) -> DatasetVersion:
        """The descriptor already holds all the catalog knows. Unknown coverage stays unknown."""
        return dataset

    def filters(self, query: QuerySpec) -> SearchFilters:
        # A preceding-window join needs history before the query start; Recipe checks the exact lookback.
        return SearchFilters(
            access_scope=sorted(self.allowed_scopes),
            region_wkt=shape(query.region).wkt,
            start=query.time_range.start - timedelta(days=366),
            end=query.time_range.end,
        )

    def search(self, filters: SearchFilters) -> list[DatasetVersion]:
        datasets = []
        for match in self.catalog.search_datasets(filters):
            dataset = to_recipe_dataset(match.dataset)
            if dataset is not None:
                self.bindings[dataset.key] = binding(dataset, self.schema)
                datasets.append(dataset)

        return datasets


def searchable_text(dataset: DatasetVersion) -> str:
    metadata = dataset.metadata
    tags = " ".join(f"{tag['key']} {tag['value']}" for tag in metadata["tags"])
    words = [dataset.family, dataset.description, metadata["summary"] or "", metadata["source_id"],
             " ".join(metadata["variables"]), " ".join(dataset.coverage.species or []), tags]
    return " ".join(words).replace("_", " ").lower()


def as_list(value) -> list:
    if value is None:
        return []

    return [value] if isinstance(value, str) else list(value)


def executor(
    connection_factory: Callable[[], psycopg.Connection],
    catalog: HabitatRecipeCatalog,
    *,
    postgis_schema: str = POSTGIS_SCHEMA,
    **limits,
) -> SupabaseExecutor:
    """A Recipe SQL executor over the bindings of `catalog`. Supabase installs PostGIS in `extensions`."""
    return SupabaseExecutor(connection_factory, catalog.bindings, postgis_schema=postgis_schema, **limits)

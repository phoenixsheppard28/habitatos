from datetime import UTC, datetime

import pandas as pd
import pytest

from conftest import make_manifest
from habitat.catalog.publish import publish_series_version
from habitat.catalog.store import MemoryCatalog, PostgresCatalog
from habitat.contracts import TimePrecision
from habitat.normalize.rows import SITE_OBSERVATIONS, series_id, to_cell_observations
from habitat.recipe_inputs import (
    FAMILIES,
    VEGETATION,
    WATER_QUALITY,
    WATER_QUALITY_SUFFIX,
    HabitatRecipeCatalog,
    binding,
    to_recipe_dataset,
    to_recipe_datasets,
)
from habitat.storage.series import SeriesStore
from test_ai_and_publish import fake_assistant
from test_recipe_inputs import habitat_dataset, query


def test_site_observations_map_to_the_station_view():
    dataset = to_recipe_dataset(habitat_dataset("wqp--wqp-wqx3-results--ease2-global-1km", SITE_OBSERVATIONS,
                                                ["lead", "ph"]))

    assert dataset.family == SITE_OBSERVATIONS
    assert binding(dataset, "public").table == "recipe_site_observations"
    columns = {column.name: column for column in dataset.columns}
    assert {"site_id", "parameter", "fraction", "value", "unit", "censored", "detection_limit", "cell_id",
            "available_at", "longitude", "latitude", "water_body_type"} <= set(columns)
    assert columns["available_at"].role == "available_at"


def test_a_sentinel2_series_also_gives_a_water_quality_dataset():
    datasets = to_recipe_datasets(habitat_dataset("sentinel2--sentinel-2-l2a--ease2-global-1km", "cell_observations",
                                                  ["ndvi", "mndwi", "ndti", "ndci"]))

    assert [d.family for d in datasets] == [VEGETATION, WATER_QUALITY]
    assert datasets[1].dataset_id == "sentinel2--sentinel-2-l2a--ease2-global-1km" + WATER_QUALITY_SUFFIX
    assert binding(datasets[1], "public").table == "recipe_water_quality_observations"


def test_a_lake_quality_series_is_only_a_water_quality_dataset():
    datasets = to_recipe_datasets(habitat_dataset("cgls_lwq--cgls-lwq300--ease2-global-1km", "cell_observations",
                                                  ["water_turbidity", "trophic_state_index"]))

    assert [d.family for d in datasets] == [WATER_QUALITY]


def test_search_filters_accept_the_new_families():
    catalog = MemoryCatalog()
    catalog.register_dataset(habitat_dataset("wqp--wqp-wqx3-results--ease2-global-1km", SITE_OBSERVATIONS, ["ph"]))
    recipe = HabitatRecipeCatalog(catalog, allowed_scopes={"public"})

    page = recipe.search_metadata({"family": SITE_OBSERVATIONS}, query=query(), limit=10)

    assert [d.family for d in page.datasets] == [SITE_OBSERVATIONS]
    assert {SITE_OBSERVATIONS, WATER_QUALITY} <= set(FAMILIES)


def test_the_question_parser_knows_the_new_family_and_variables():
    assistant, messages = fake_assistant([{
        "families": [SITE_OBSERVATIONS], "species_names": [], "start": None, "end": None, "variables": ["ndti"],
        "tags_any": [],
    }])

    filters = assistant.parse_question("turbidity of the Athi river")

    schema = messages.requests[0]["output_config"]["format"]["schema"]["properties"]
    assert SITE_OBSERVATIONS in schema["families"]["items"]["enum"]
    assert {"ndti", "ndci", "water_turbidity", "trophic_state_index"} <= set(schema["variables"]["items"]["enum"])
    assert filters.families == [SITE_OBSERVATIONS]


def test_the_water_quality_view_uses_the_suffixed_dataset_id(database, grid):
    store = SeriesStore(database, grid)
    scene = make_manifest("sentinel2", {}, datetime(2024, 3, 9, 8, 47, tzinfo=UTC), item_id="S2_LAKE",
                          product="sentinel-2-l2a", precision=TimePrecision.INSTANT)
    stats = pd.DataFrame({"cell_id": ["E1K-r1-c1", "E1K-r1-c1"], "variable": ["ndvi", "ndti"], "value": [0.5, -0.25],
                          "std": [0.0, 0.0], "valid_fraction": [0.9, 0.3], "pixel_count": [9000, 3000]})
    batch = to_cell_observations(stats, scene, grid, "s2-v2", "mean", {"ndvi": "index", "ndti": "index"}, 10)
    series = series_id(scene, grid)
    store.append_batch(series, scene, batch)
    publish_series_version(store, PostgresCatalog(database), grid, series, "sentinel2", "Sentinel-2", "public")

    rows = database.execute("SELECT dataset_id, variable, value FROM recipe_water_quality_observations").fetchall()
    vegetation = database.execute("SELECT index_name FROM recipe_vegetation_observations").fetchall()

    assert rows == [(series + WATER_QUALITY_SUFFIX, "ndti", pytest.approx(-0.25))]
    assert vegetation == [("ndvi",)]

from datetime import UTC, datetime

import pandas as pd
import pytest

from conftest import make_manifest
from habitat.catalog.publish import publish_series_version
from habitat.catalog.store import PostgresCatalog
from habitat.contracts import TimePrecision
from habitat.normalize.rows import series_id, to_cell_observations
from habitat.recipe_inputs import FAMILIES, HABITAT_INDICATORS, SEARCH_FILTERS, VEGETATION, to_recipe_dataset
from habitat.storage.series import SeriesStore
from test_recipe_inputs import habitat_dataset

CELLS = ("E1K-r7497-c20909", "E1K-r7497-c20910")


@pytest.mark.parametrize("dataset_id, variables, family", [
    ("landsat_c2_l2--landsat-c2-l2--ease2-global-1km", ["ndvi", "ndmi", "bare_soil_index"], HABITAT_INDICATORS),
    ("esa_cci_lc--esa-cci-lc--ease2-global-1km", ["landcover_fraction_tree", "landcover_fraction_bare"],
     HABITAT_INDICATORS),
    ("modis_mcd64a1--mcd64a1-061--ease2-global-1km", ["burned_fraction"], HABITAT_INDICATORS),
    ("vegetation_trend_derived--vegetation-trend--ease2-global-1km", ["restrend_slope"], HABITAT_INDICATORS),
    ("modis_mod13q1--mod13q1-061--ease2-global-1km", ["ndvi", "evi"], VEGETATION),
])
def test_habitat_degradation_sources_map_to_the_habitat_indicators_family(dataset_id, variables, family):
    descriptor = to_recipe_dataset(habitat_dataset(dataset_id, "cell_observations", variables))

    assert descriptor.family == family


def test_the_planner_knows_the_family():
    assert HABITAT_INDICATORS in SEARCH_FILTERS["family"]


def test_the_view_has_every_descriptor_column_and_the_published_rows(database, grid):
    store = SeriesStore(database, grid)
    year = datetime(2012, 1, 1, tzinfo=UTC)
    manifest = make_manifest(
        "esa_cci_lc", {}, year, datetime(2012, 12, 31, 23, 59, 59, tzinfo=UTC), item_id="ESACCI-2012",
        product="esa-cci-lc", precision=TimePrecision.COMPOSITE, available_at=datetime(2023, 1, 11, tzinfo=UTC),
    )
    stats = pd.DataFrame({"cell_id": list(CELLS), "variable": "landcover_fraction_tree", "value": [0.25, 0.5],
                          "std": None, "valid_fraction": 1.0, "pixel_count": 12})
    batch = to_cell_observations(stats, manifest, grid, "esa-cci-lc-v1", "fraction",
                                 {"landcover_fraction_tree": "fraction"}, 300.0)
    store.append_batch(series_id(manifest, grid), manifest, batch)
    publish_series_version(store, PostgresCatalog(database), grid, series_id(manifest, grid), source_id="esa_cci_lc",
                           description="ESA CCI land cover", access_scope="public")
    columns = [column.name for column in FAMILIES[HABITAT_INDICATORS].columns]

    rows = database.execute(f"SELECT {', '.join(columns)} FROM recipe_habitat_indicators ORDER BY cell_id").fetchall()

    assert [dict(zip(columns, row))["value"] for row in rows] == [0.25, 0.5]
    assert {dict(zip(columns, row))["indicator"] for row in rows} == {"landcover_fraction_tree"}
    assert {dict(zip(columns, row))["unit"] for row in rows} == {"fraction"}

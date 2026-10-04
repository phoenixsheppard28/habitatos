# Recipe integration

This file tells how the output of the habitat pipeline (Stage 2) becomes the input of the Recipe lane.
It also records the merge of the `kael` branch. The guide on the Recipe side is `MERGE_INTEGRATION.md`.

Branch: `psheppard/merge-kael`. Date: 2026-10-03.

## 1. Merge record

The merge followed the order in section "Recommended merge order" of `MERGE_INTEGRATION.md`.

1. Stage 2 (`psheppard/merge-1-and-2`) was already on `main`.
2. `origin/kael` was merged into this branch with `--no-ff`.
3. Two conflicts were resolved by hand:
   - `.gitignore`: the union of both files.
   - `README.md`: one status line names both lanes.
4. `src/recipe/requirements.txt` was removed. `pyproject.toml` now declares the dependencies and installs `recipe` next to `habitat`.
5. `tests/recipe/conftest.py` was removed. Its `scenarios` fixture moved to `tests/conftest.py`. Two files with the name `conftest` caused an import conflict.

Changes to the Recipe code:

| File | Change | Reason |
| --- | --- | --- |
| `src/recipe/providers.py` | `JsonPlanner` accepts `search_filters`. The requirement call puts the filters in the model context. | The planner must use the filter names of Stage 2. It must not guess them. |
| `tests/recipe/test_postgis.py` | `RECIPE_TEST_POSTGIS_SCHEMA` sets the PostGIS schema. The default stays `public`. | Supabase installs PostGIS in `extensions`. |

Dependency ranges: `pydantic>=2.10,<3`, `psycopg[binary]>=3.2,<4`, `shapely>=2.0,<3`, `pyarrow>=18`.
The Recipe file had `pyarrow<24`. The lock file has `pyarrow` 25.0.1, and all Recipe tests pass with it. Thus the upper limit was not kept.

## 2. How Recipe reads normalized data

Three parts connect the lanes:

| Part | File | Function |
| --- | --- | --- |
| Views | `migrations/008_recipe_inputs.sql` | Give one row set per catalog version, in the canonical columns of each family |
| Adapter | `src/habitat/recipe_inputs.py` | Maps catalog descriptors to Recipe `DatasetVersion`, makes the trusted `TableBinding`, and implements the Recipe `Catalog` protocol |
| Planner context | `JsonPlanner(..., search_filters=SEARCH_FILTERS)` | Tells the planner the supported metadata filters |

### 2.1 Families

| Habitat catalog dataset | Recipe family | View |
| --- | --- | --- |
| `cell_observations` with only `rainfall_mm` | `rainfall_observations` | `recipe_rainfall_observations` |
| `cell_observations` with only `ndvi`, `evi`, `mndwi`, `ndmi` | `vegetation_observations` | `recipe_vegetation_observations` |
| `animal_locations` | `animal_locations` | `recipe_animal_locations` |
| `site_observations` | `site_observations` | `recipe_site_observations` (migration 014) |
| `cell_observations` with `ndti`, `ndci`, `water_turbidity` or `trophic_state_index` | `water_quality_observations`, dataset id `<series>--water-quality` | `recipe_water_quality_observations` (migration 014) |
| Other variables, for example `elevation_m` | none | The adapter does not show the dataset to Recipe |

Each view has the minimum columns of the v1 contract in `README.md`. Each view also has `access_scope`, `available_at` and quality columns.
The `cell_id` column is in all three families. A recipe can join an animal fix to the rainfall or vegetation of its cell with no spatial join.

Water-quality rules (see `docs/ingestion/WATER_POLLUTION.md`):

- A Sentinel-2 series gives two Recipe datasets: the vegetation dataset and a water-quality dataset. The water-quality dataset id has the suffix `--water-quality`. The water-quality variables are not in the vegetation view.
- In `recipe_site_observations`, one row is current per site, parameter, fraction, sample time and sample depth. A final value replaces a preliminary value. Then a later batch replaces an earlier batch.
- A censored row has the limit in `value` and in `detection_limit`. `censored` is `left` or `right`. A censored row without a known limit has `value` null.
- NDTI and NDCI are relative indices. `water_turbidity` is in NTU. Do not mix a relative index with a concentration.
- A station describes the water at its own location. Join a station to an animal through the water body, not through the nearest river station.

### 2.2 Versions

Recipe pins a dataset as `(dataset_id, version)`. Its SQL filters a shared relation on `dataset_id`, `dataset_version` and `access_scope`.

- `dataset_id` is the series id of the habitat catalog.
- `dataset_version` is the catalog version, as text.
- A catalog version holds every batch with `added_in_version <= version` that is not superseded at that version.
- In each version, one value per cell, variable, source, UTC day of `time_start` and interval length is current. The rule is the same as in `current_cell_observations`.
- Two values with the same start and different ends are both current. An example is an annual value and a five-year trend. Filter on `time_end` when you need one of them.
- Only `ready` catalog versions are in the views. A series version that is not in the catalog is not visible.

### 2.3 Catalog callbacks

| Callback | Behavior |
| --- | --- |
| `search_metadata(filters)` | Supports `family`, `variables`, `source_id` and `tags`. Any other filter raises `UNSUPPORTED_FILTER`. |
| `search_semantic(text)` | Word overlap with the description, summary, tags, variables and species. The catalog has no embedding index. The score is the share of query words found. |
| `authorize(dataset)` | True when the dataset scope is in `allowed_scopes`. The caller sets `allowed_scopes` from the authenticated session. The `access_scope` of the request is not a credential. |
| `readable(dataset)` | True when the adapter made a binding for the dataset. |
| `inspect(dataset)` | Returns the descriptor. The catalog has no more knowledge. Unknown coverage stays unknown. |

Both searches apply the region of the query. They also apply the dates of the query, with 366 days of history before the start. Recipe then checks the exact lookback of each requirement.

### 2.4 Wiring

```python
import psycopg
from habitat.catalog.store import PostgresCatalog
from habitat.db import connect, database_url
from habitat.recipe_inputs import SEARCH_FILTERS, HabitatRecipeCatalog, executor
from recipe.providers import JsonPlanner
from recipe.service import RecipeService

catalog = HabitatRecipeCatalog(PostgresCatalog(connect()), allowed_scopes=caller_scopes)
service = RecipeService(
    catalog=catalog,
    planner=JsonPlanner(generate, search_filters=SEARCH_FILTERS),
    assessor=assessor,
    executor=executor(lambda: psycopg.connect(database_url()), catalog),
    store=store,
)
```

`generate`, `assessor` and `store` come from the Recipe and Stage 4 owners. See section 5.

## 3. Input problems found and fixed

| Problem | Effect without a fix | Fix |
| --- | --- | --- |
| In the old views, `dataset_version` is the version that added a batch. | A recipe pinned to version 4 gets only the rows added in version 4. | The 008 views expand each catalog version to all its rows. A test checks this. |
| The observation tables have no `access_scope` column. | The Recipe compiler cannot apply its scope filter. | The views take `access_scope` from the `datasets` table. |
| A Movebank series can hold more than one species. The wildebeest and black kite data are an example. | Recipe returns `insufficient_data` for a species query. | `recipe_animal_locations.species` has the role `species`. |
| Our catalog version is an integer. Recipe uses a string. | The binding filter does not match. | The views and the descriptors use the text form. |
| `animal_locations.cell_id` can be null. | None. Recipe validation rejects an output that declares the column non-null. | The descriptor says `nullable: true`. Recipes must keep it. |
| Vegetation values have the unit `index`. | A unit check against a standard unit fails. | The descriptor uses the unit `1` (dimensionless). |

Two facts are important for a recipe author:

- CHIRPS `available_at` is the `Last-Modified` time of the file. For old days, that time is years after the rainfall. Do not set `right_available_at` in a historical recipe. Use `right_available_at` only for a point-in-time (forecast) recipe.
- A MODIS composite has `observed_at` at the start of its 16-day period. `observed_until` is the end of the period.

## 4. Verification

| Check | Result |
| --- | --- |
| `uv sync`, then `uv run pytest` | 196 passed, 13 skipped. The skips are 5 live checks and 8 Recipe PostGIS tests with no `RECIPE_TEST_DSN`. |
| Recipe PostGIS tests on Supabase (`RECIPE_TEST_DSN=$HABITAT_DATABASE_URL`, `RECIPE_TEST_POSTGIS_SCHEMA=extensions`) | `uv run pytest tests/recipe`: 36 passed. Each test uses its own schema and drops it. |
| `uv run python -m recipe.demo --fixtures tests/recipe/fixtures/scenarios.json --output /tmp/recipe-merge-check` | Both scenarios `ok`. The second request of each is a cache hit. |
| `git diff --check` | No output |
| Fresh `git clone` of the branch, no `.env`: `uv sync`, then `uv run pytest` | 170 passed, 39 skipped. The skips are the database tests, the live checks and one test that needs a real scene. |
| `tests/test_recipe_inputs.py::test_pipeline_output_is_recipe_input_end_to_end` | The pipeline ingests mocked CHIRPS days and the Movebank fixture package into a throwaway schema and publishes them. `RecipeService` then runs with `JsonPlanner`, this adapter and `SupabaseExecutor`. Each of the 4 fixes gets 27.0 mm of rain in the 7 days before it, with a coverage of 6/7. The repeat request is a cache hit. |
| Read-only check on the live data | The 008 views were made in a throwaway schema over the live tables, then dropped. NDVI by month and cell for Athi-Kaputiei, February 2024: MODIS v3 and Sentinel-2 v4 each give 800 cell-months, NDVI 0.15 to 0.72. Sentinel-2 v4 gives 7,134 current rows of 9,534 stored rows, because the duplicate scenes of 2024-02-02 collapse. Wildebeest fixes by day, March 2011: 92 animal-days for 4 animals. |

## 5. Open items

1. Migration 008 is not on the live database. Apply it with `apply_migration` before Recipe reads live data.
2. The live data has no rainfall series. The wildebeest fixes (2010 to 2013) and the satellite data (2024) do not overlap in time. A live rainfall-and-movement recipe needs CHIRPS for 2010 to 2013 in the wildebeest area.
3. The live Movebank catalog has version 1 only. Series version 2 (the black kite data) is not published. Thus Recipe cannot see it.
4. `search_semantic` is a word match, not a semantic index. Stage 2 must build the index (`MERGE_INTEGRATION.md`, "Work to add and ownership").
5. Recipe and the application owner must supply the live LLM `generate` function, the Jev key and model, durable artifact storage, and the settings loader.
6. Stage 4 must supply the coordinator, `create_clarification_job` and the Analysis consumer.
7. `row_count` in a Recipe descriptor is null. The catalog counts stored rows before deduplication. The number is in `metadata.stored_row_count`.
8. Forecast recipes stay `insufficient_data` in Recipe. The `available_at` columns are ready for the point-in-time contract.

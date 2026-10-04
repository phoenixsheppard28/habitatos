# Analysis integration

This file tells how the output of the Recipe lane becomes the input of the Analysis lane.
It also records the merge of the `diego-branch` branch. The guide on the Analysis side is `src/analysis/DIEGO.md`.

Branch: `psheppard/merge-diego`. Date: 2026-10-03.

## 1. Merge record

1. `origin/diego-branch` was merged into this branch with `--no-ff`.
2. These conflicts were resolved by hand:
   - `pyproject.toml`: the build stays `hatchling` with the name `habitat`. The file adds `scikit-learn` and installs `analysis`, `workflow` and `contracts`.
   - `.gitignore`: the union of both files, with `.DS_Store`.
   - `tests/__init__.py` and `tests/simulation/__init__.py` were removed. The tests use `conftest.py` imports by base name. Thus the imports `from tests.<module>` became `from <module>`.
3. `contracts` is a Python package. The same folder also holds `grid.json` and `tag_vocabulary.json`.

## 2. How Analysis reads Recipe output

Four parts connect the lanes:

| Part | File | Function |
| --- | --- | --- |
| Daily movement view | `migrations/009_animal_daily_movement.sql` | Gives one row per animal and UTC day, with the daily displacement |
| Recipe adapter | `src/habitat/recipe_inputs.py` | Shows the daily movement of each animal dataset as the family `animal_daily_movement` |
| Handoff | `src/workflow/recipe_handoff.py` | Makes the Analysis request from the Recipe response. Reads Recipe tables for Analysis. |
| Coordinator stages | `src/workflow/handlers.py` | `stage_handlers(...)` gives the handlers `fetch`, `normalize`, `recipe` and `analysis` |

### 2.1 Daily movement

Analysis needs one row per animal per UTC day, with `daily_displacement` and a unit.
Recipe has no operation for path distance. Thus Stage 2 calculates the displacement in the view `recipe_animal_daily_movement`.

- The view uses only fixes with `quality_flag = 'ok'`.
- Each row keeps the last good fix of the UTC day.
- `daily_displacement_km` is the geodesic distance from the last good fix of the previous UTC day.
- When the previous day has no good fix, the value is NULL. A gap is never zero.
- `available_at` is the later value of the two days.
- The dataset id is the catalog dataset id with the suffix `--daily-movement`.
- `source_dataset_id`, `fix_count` and `last_fix_at` keep the lineage.

The adapter gives two descriptors for each animal dataset: the fixes and the daily movement.
The daily movement descriptor has `metadata.derived_from`, with the catalog dataset id and version.

### 2.2 Roles

The handoff takes the roles from Recipe validation, not from the planner.
Two changes in Recipe make this possible:

| File | Change |
| --- | --- |
| `src/recipe/service.py` | Each output column gets the role that validation infers |
| `src/recipe/validation.py` | `time_bucket`, `aggregate` and `window` outputs keep the lineage to the input column. A `window` output has the role `measurement`. |

| Recipe column | Analysis role |
| --- | --- |
| The `time_column` of the recipe output | `event_time` |
| Role `entity_id`, `longitude`, `latitude`, `cell_id`, `species` or `daily_displacement` | The same role |
| `measurement` from `rainfall_observations.rainfall_mm` | `rainfall` |
| `measurement` from `vegetation_observations.index_value` | `vegetation_index` |
| Other columns | No role |

### 2.3 Request fields

- `input_dataset_refs`: the catalog versions of the inputs. A daily movement input cites its source catalog version.
- `coverage`: the species of the inputs, and the time range of the inputs cut to the query interval.
- `recipe.cutoff` is null and `features_respect_cutoff` is false. Recipe does not prepare forecasts yet.

### 2.4 Artifacts

Recipe gives an artifact URI in the form `artifact://features-<sha>/1`.
`RecipeArtifactReader(recipe_store, access_scope, model_store)` gives the Analysis store interface:

- `read_dataset` reads Parquet tables from the Recipe store, only in the `access_scope` of the query.
- `write_json` writes model files to a separate Analysis store.

### 2.5 Coordinator stages

| Stage | Handler | Behavior |
| --- | --- | --- |
| Fetch | `fetch_stage` | Makes one fetch request per `FetchSource`, with `history_days` before the query start |
| Normalize | `normalize_stage` | Calls `ingest_and_publish` for each fetch response. The CLI `run` uses the same function. |
| Recipe | `recipe_stage` | Runs `RecipeService` on the query |
| Analysis | `analysis_stage` | Makes the Analysis request with `analysis_request` and runs Analysis |

The fetch outcomes `appended` and `already_present` are usable. Other outcomes stop the job.

### 2.6 Residence time and bounded vegetation inputs

Set `analysis_method` to `residence_time` for questions about time spent in greener places.
Use `animal_locations` with original timestamps, coordinates, animal identifiers, and matched NDVI values.
Daily movement summaries cannot supply residence time.
Use left joins to retain fixes without vegetation measurements.
MODIS outputs must include the start and end timestamps of each matched composite.

The handoff derives `sampling_grain` from the validated recipe.
Residence analysis rejects daily summaries and duplicate timestamps for the same animal.
Each valid interval assigns half its duration to each endpoint.
The default `max_tracking_gap_hours` is six hours.
Longer gaps, missing measurements, and expired composites contribute no time.
The method does not extrapolate before the first fix or after the last fix.
Results include animal-hours, greenness shares, excluded durations, and a bar chart.
The greenness threshold is the median vegetation value at unique fixes within usable intervals.
These results describe observed time allocation, not habitat preference.

Preparation restricts compatible vegetation joins to cells used by the selected animal tracks.
Preparation also applies safe index filters before materialization.
Background branches retain their required cells and indices.
The row limit remains 100,000 rows per stage.
Limit errors include the dataset version, region, dates, selected indices, and a lower bound for the row count.
Set `vegetation_source_id` to `sentinel2` or `modis_mod13q1` to enforce the selected satellite source.
Another download does not reduce the size of an existing shared series.

## 3. Verification

| Check | Result |
| --- | --- |
| `uv sync`, then `uv run pytest` | 309 passed, 14 skipped. The skips are 8 Recipe PostGIS tests with no `RECIPE_TEST_DSN`, 5 live checks and 1 test that needs a real scene. |
| Recipe PostGIS tests on Supabase | `uv run pytest tests/recipe`: 36 passed |
| `uv run python -m recipe.demo --fixtures tests/recipe/fixtures/scenarios.json --output /tmp/recipe-merge-check` | Both scenarios `ok`. The second request of each is a cache hit. |
| `git diff --check` | No output |
| `test_daily_movement_view_keeps_gaps_and_skips_outliers` | A missing day gives NULL. An outlier fix is not used. |
| `test_query_flows_from_fetch_to_analysis` | The job completes all four stages. Analysis gets 2 animals, 11 observations and 8 displacement rows, in km. 3 values are missing and 1 animal-day is a gap. The evidence cites the movement dataset version 1 and the CHIRPS series. |
| `test_a_recipe_without_displacement_is_insufficient_for_analysis` | Analysis returns `insufficient_data` with the missing role `daily_displacement` |
| `test_a_forecast_stops_at_recipe_as_insufficient` | The job stops at Recipe with `insufficient_data` |

## 4. Open items

1. Migration 009 is not on the live database. Apply it with `apply_migration` before Analysis reads live data.
2. Forecast queries stop at Recipe with `insufficient_data`. Recipe needs the point-in-time contract first.
3. The live LLM `generate` function, the Jev key and durable artifact stores are not connected.
4. `search_semantic` is a word match, not a semantic index.
5. The live data has no CHIRPS rainfall for 2010 to 2013. Thus a live movement-and-rainfall query has no rainfall values.

# New ingestion points

Status: the shared code changes are implemented. The topic shapes are proposals.

This folder describes how to add new data sources to the fetch agent and to the first normalization stage.
Each topic has a separate document:

| Document | Topic | Main shape |
| --- | --- | --- |
| [WATER_POINTS.md](WATER_POINTS.md) | Rivers, lakes, dams, boreholes, troughs and seasonal pans | `site_features` |
| [POPULATION.md](POPULATION.md) | Animal counts and population trends over time | `population_counts` |
| [EVENTS.md](EVENTS.md) | Sightings, fires, disease outbreaks, camera-trap detections | `point_events` |
| [HABITAT_DEGRADATION.md](HABITAT_DEGRADATION.md) | Loss of vegetation, land cover change, fragmentation | `cell_observations` |
| [WATER_POLLUTION.md](WATER_POLLUTION.md) | Water quality at stations and from satellites | `site_observations` |

This document gives the rules that all five documents use.

## The current pipeline

1. A connector in `src/habitat/fetch/connectors/` downloads one source item into the archive. The connector writes a `RawManifest`.
2. `src/habitat/sources.py` registers the connector, the normalizer and the storage format for each `source_id`.
3. `src/habitat/normalize/router.py` selects the normalizer by `source_id`. An unknown source goes to quarantine.
4. The normalizer returns a `NormalizedBatch`. `batch.family` is the name of the target table. `batch.references` holds the rows of the reference tables.
5. `src/habitat/storage/series.py` upserts the reference rows. Then it appends the batch to the table of that family. The series and batch tables give versions and supersession.
6. Migration 008 exposes each family to the Recipe lane as a `recipe_<family>` view.

There are two families now:

- `cell_observations`: one value per 1 km EASE-Grid 2.0 cell, variable and time interval.
- `animal_locations`: one GPS fix per animal and time, with `animal_entities` as the reference table.

## Shape rules

All new shapes obey these rules.

### Time

- Every row has `time_start`, `time_end`, `time_precision` and `available_at`.
- `time_precision` uses the existing `TimePrecision` values: `instant`, `day`, `composite` and `static`.
- A survey or a yearly value is `composite`. `time_start` and `time_end` give the interval.
- Some surveys give only one date. Store such a survey as `composite` with `time_start = time_end`. Flag the row `interval_unknown`.
- A static layer, for example a dam or a protected area, is `static`. `time_start` is the first date the source shows the feature. `time_end` is the last date, or `9999-12-31` when the feature still exists.
- `available_at` is the date when the source published the value. When the source gives no date, use the publication date of the dataset. Recipe uses `available_at` for point-in-time joins. Never set `available_at` to the download time.
- When no publication date is known, skip the item or raise `QuarantineError`. The STAC connector skips such a scene.

### Archives and `available_at`

- An archive often has a publication date that is much later than the values. Examples are FIRMS, CHIRPS, JRC and GEMStat.
- The rule does not change. `available_at` is the publication date, also for an archive.
- A backtest uses the `available_at` cutoff. Then the backtest sees only the data that was public at that time.
- A historical analysis needs a mode that ignores the `available_at` cutoff. Recipe does not have this mode yet.
- The Recipe lane owns this change. The ingestion lane does not change `available_at` to help a historical analysis.

### Space

- Coordinates are WGS84, EPSG:4326.
- A row with a point has `longitude`, `latitude` and `cell_id`.
- A row with an area has a `geometry` column. Recipe joins an area to cells by overlap, not by point lookup.
- Keep the source uncertainty of a location in `coordinate_uncertainty_m` when the source gives it.

### Provenance and versions

- Every row has `series_id`, `batch_key`, `dataset_id`, `mapping_version`, `source_id` and `quality_flag`.
- A row of a new family also has `source_record_id` and `attributes`. This rule applies to new families only.
- `cell_observations` keeps its columns. It has no `source_record_id` and no `attributes`.
- A derived cell value records its inputs in `ingest_batches.raw_manifest`, under `properties.inputs`.
- Each input in `properties.inputs` gives the dataset id, the dataset version, the mapping version and the variable.
- The new table has a foreign key to `ingest_batches (series_id, batch_key)`, as `cell_observations` has.
- `quality_flag` is `ok` or one specific reason. Do not drop a row because of low quality. Flag it.
- A new family can have a separate `censored` column for values below or above a limit. `quality_flag` stays one reason.
- Keep source fields that have no canonical column in `attributes` (JSON text).

### Taxa

- A row about a species has `taxon_name` and `gbif_taxon_key`.
- `ingest.resolve_batch_taxa` resolves the key with `habitat.catalog.taxa`. It fills only a missing key.
- When the key does not resolve, keep the row with `gbif_taxon_key = null` and flag it `taxon_unresolved`.

### Rights

- Put the license, the citation and `reuse_allowed` in `RawManifest.rights`.
- Some sources give a different license for each record, for example GBIF. A new family can then have a `license` column for each row. `RawManifest.rights` then gives the most restrictive license.
- A source that forbids redistribution gets `access_scope` other than `public`. The agent handoff then excludes it, as it does for Movebank account data.

### Quarantine

- Raise `QuarantineError` when a value can only be normalized with a guess. Examples: an unknown unit, an unknown count method, or missing coordinates.
- Never convert a unit without a documented factor.

## New shapes

Each new shape is one table, one PyArrow schema in `src/habitat/contracts.py`, one `family` constant in `src/habitat/normalize/rows.py`, and one `recipe_<family>` view.

A family can also have helper tables, for example `count_area_cells` for `population_counts`. A helper table holds derived links, such as the overlap of an area with grid cells. The upsert function of the reference table can fill the helper table.

| Family | One row is | Reference table | Defined in |
| --- | --- | --- | --- |
| `site_features` | One physical feature (a water point, a road, a fence, a park) with a validity interval | none | WATER_POINTS.md |
| `point_events` | One thing that happened at one place and time | none | EVENTS.md |
| `population_counts` | One count or estimate of one taxon in one area for one interval | `count_areas` | POPULATION.md |
| `site_observations` | One measurement at one monitoring station for one interval | `monitoring_sites` | WATER_POLLUTION.md |

Habitat degradation adds no new family. It adds new variables to `cell_observations`.

### Migration numbers

Each shape has a fixed migration number. Use these numbers, also when the build order changes:

| Migration | Content |
| --- | --- |
| `010_current_rows_time_end.sql` | Foundation: the current-row rule for `cell_observations` |
| `011_site_features.sql` | `site_features` |
| `012_point_events.sql` | `point_events` |
| `013_population_counts.sql` | `population_counts`, `count_areas`, `count_area_cells` |
| `014_site_observations.sql` | `site_observations`, `monitoring_sites` |
| `015_habitat_degradation.sql` | Habitat degradation, only when it needs a migration |

### Derived values

A feature is also a source of per-cell values. For example, `distance_to_water_m` is a `cell_observations` variable that the pipeline derives from `site_features`. A derived variable has its own `source_id` with the suffix `_derived` and its own `mapping_version`.

## Shared code changes

These six changes are implemented. Each topic document uses them.

1. **Kind values.** `SourceItem.kind` accepts `"raster"`, `"tabular"`, `"vector"` and `"derived"`. The type is `SourceItemKind` in `src/habitat/contracts.py`.
   - Use `"vector"` for GeoJSON, GeoPackage and Shapefile items.
   - Use `"derived"` for a run that computes values from other series.
2. **Reference rows.** `NormalizedBatch.references` is a `dict[str, pa.Table]`. The key is the name of the reference table.
   - `REFERENCE_UPSERTS` in `src/habitat/storage/series.py` gives the upsert function of each reference table.
   - `upsert_on_key(table_name, key_columns)` makes an upsert that replaces the other columns of an existing row.
   - `SeriesStore.append_batch` upserts each reference table before it copies the rows.
   - `append_batch` refuses a batch with an unregistered reference table.
   - `append_batch` inserts the grid cells of each table with a `cell_id` column. A family without `cell_id` is allowed.
   - The animal entities use the key `animal_entities`, with the schema `ANIMAL_ENTITIES_SCHEMA`.
   - `ingest.resolve_batch_taxa` fills a missing `gbif_taxon_key` in each table with `taxon_name` and `gbif_taxon_key`.
3. **Family summaries.** `FAMILY_SUMMARIES` in `src/habitat/storage/series.py` gives the summary function of each family.
   - `SeriesStore.summary` uses this registry. A family without a summary function causes a `ValueError`.
   - Use `version_parameters`, `VERSION_BATCHES` and `distinct_cell_ids` to write a summary function.
4. **Current rows.** Migration 010 changes the current-row rule of `cell_observations`.
   - The partition now also has the interval length, `time_end - time_start`.
   - Two values with the same start and different ends are then both current.
   - Two instant scenes of one UTC day still compete, as before.
5. **Warp and class fractions.** Two helpers prepare rasters for `aggregate_blocks`.
   - `iter_warped_blocks` in `src/habitat/normalize/raster_io.py` warps rasters onto one pixel grid in the grid CRS. Use it for EPSG:4326 rasters.
   - `class_fractions` in `src/habitat/normalize/zonal.py` makes one 0/1 layer per class. The cell mean of a layer is the class fraction.
   - An unmapped class code is a `QuarantineError`.
6. **Publication dates.** `published_at` in the STAC connector never uses the retrieval time.
   - The connector skips a scene without a publication date.
   - MODIS items have no `created` date. MODIS uses the production time from the item id.

Each new family must also do these steps:

1. Add the family to `ROW_GRAIN` in `src/habitat/catalog/publish.py`. Add it to the `families` values of `parse_question` in `src/habitat/catalog/ai.py`.
2. Add a summary function to `FAMILY_SUMMARIES`. Add an upsert function to `REFERENCE_UPSERTS` for each reference table.
3. Add the family to the `recipe_inputs.py` descriptors and to `RECIPE_INTEGRATION.md`.

## How to add one source

1. Write the connector in `src/habitat/fetch/connectors/<source>.py`. The connector writes raw files to the archive only.
2. Write the normalizer in `src/habitat/normalize/sources/<source>.py`.
3. Register the source in `src/habitat/sources.py` with its `data_kinds`.
4. Add the data kinds to the fetch agent tool descriptions in `src/habitat/fetch/tools.py`.
5. Add a section to `SOURCES.md`.
6. Add tests with a small recorded fixture file. Put one live test in `tests/test_live.py`.

## Build order

1. The shared code changes above. These are done.
2. Habitat degradation. It needs no new family, and most inputs are on Planetary Computer.
3. Water points. Its `site_features` family and the derived distance variables are inputs for the other topics.
4. Events and population. These two do not depend on each other.
5. Water pollution. It needs the water points to place stations on rivers and lakes.

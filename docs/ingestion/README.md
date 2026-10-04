# New ingestion points

Status: proposal. Nothing in this folder is implemented yet.

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
4. The normalizer returns a `NormalizedBatch`. `batch.family` is the name of the target table.
5. `src/habitat/storage/series.py` appends the batch to the table of that family. The series and batch tables give versions and supersession.
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
- A static layer, for example a dam or a protected area, is `static`. `time_start` is the first date the source shows the feature. `time_end` is the last date, or `9999-12-31` when the feature still exists.
- `available_at` is the date when the source published the value. When the source gives no date, use the publication date of the dataset. Recipe uses `available_at` for point-in-time joins. Never set `available_at` to the download time.

### Space

- Coordinates are WGS84, EPSG:4326.
- A row with a point has `longitude`, `latitude` and `cell_id`.
- A row with an area has a `geometry` column. Recipe joins an area to cells by overlap, not by point lookup.
- Keep the source uncertainty of a location in `coordinate_uncertainty_m` when the source gives it.

### Provenance and versions

- Every row has `series_id`, `batch_key`, `dataset_id`, `mapping_version`, `source_id`, `source_record_id` and `quality_flag`.
- The new table has a foreign key to `ingest_batches (series_id, batch_key)`, as `cell_observations` has.
- `quality_flag` is `ok` or one specific reason. Do not drop a row because of low quality. Flag it.
- Keep source fields that have no canonical column in `attributes` (JSON text).

### Taxa

- A row about a species has `taxon_name` and `gbif_taxon_key`.
- Resolve the key with `habitat.catalog.taxa`, as `resolve_entity_taxa` does for animal entities.
- When the key does not resolve, keep the row with `gbif_taxon_key = null` and flag it `taxon_unresolved`.

### Rights

- Put the license, the citation and `reuse_allowed` in `RawManifest.rights`.
- A source that forbids redistribution gets `access_scope` other than `public`. The agent handoff then excludes it, as it does for Movebank account data.

### Quarantine

- Raise `QuarantineError` when a value can only be normalized with a guess. Examples: an unknown unit, an unknown count method, or missing coordinates.
- Never convert a unit without a documented factor.

## New shapes

Each new shape is one table, one PyArrow schema in `src/habitat/contracts.py`, one `family` constant in `src/habitat/normalize/rows.py`, and one `recipe_<family>` view.

| Family | One row is | Reference table | Defined in |
| --- | --- | --- | --- |
| `site_features` | One physical feature (a water point, a road, a fence, a park) with a validity interval | none | WATER_POINTS.md |
| `point_events` | One thing that happened at one place and time | none | EVENTS.md |
| `population_counts` | One count or estimate of one taxon in one area for one interval | `count_areas` | POPULATION.md |
| `site_observations` | One measurement at one monitoring station for one interval | `monitoring_sites` | WATER_POLLUTION.md |

Habitat degradation adds no new family. It adds new variables to `cell_observations`.

A feature is also a source of per-cell values. For example, `distance_to_water_m` is a `cell_observations` variable that the pipeline derives from `site_features`. A derived variable has its own `source_id` with the suffix `_derived` and its own `mapping_version`.

## Code changes that every shape needs

1. `SourceItem.kind` accepts `"raster"` and `"tabular"`. Add `"vector"` for GeoJSON, GeoPackage and Shapefile items.
2. `NormalizedBatch.entities` carries only animal entities. Change it to a general field for reference rows, for example `references: dict[str, pa.Table]`. `SeriesStore.append_batch` then upserts each reference table before it copies the rows.
3. `SeriesStore.summary` has a special case for `animal_locations`. Give each family a summary function instead.
4. Add each new family to the catalog `family` values, to the `recipe_inputs.py` descriptors and to `RECIPE_INTEGRATION.md`.

Do these four changes once, before the first new shape. Each topic document assumes them.

## How to add one source

1. Write the connector in `src/habitat/fetch/connectors/<source>.py`. The connector writes raw files to the archive only.
2. Write the normalizer in `src/habitat/normalize/sources/<source>.py`.
3. Register the source in `src/habitat/sources.py` with its `data_kinds`.
4. Add the data kinds to the fetch agent tool descriptions in `src/habitat/fetch/tools.py`.
5. Add a section to `SOURCES.md`.
6. Add tests with a small recorded fixture file. Put one live test in `tests/test_live.py`.

## Build order

1. The shared code changes above.
2. Habitat degradation. It needs no new family, and most inputs are on Planetary Computer.
3. Water points. Its `site_features` family and the derived distance variables are inputs for the other topics.
4. Events and population. These two do not depend on each other.
5. Water pollution. It needs the water points to place stations on rivers and lakes.

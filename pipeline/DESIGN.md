# Pipeline design: satellite precompute, canonical tables, dataset search, appends

This document is the clean result of the design chats in `PIPELINE.MD`.
It covers the Fetch → Normalize → Recipe boundary for satellite and rainfall data.
The contract rules in `README.md` still apply. This document adds to them.

## 1. Scope

The MVP accepts three gridded sources:

| Source | Product | Native grid | Time model | What the source gives |
| --- | --- | --- | --- | --- |
| Sentinel-2 | L2A surface reflectance, COG through STAC (Earth Search or Planetary Computer) | 10 m / 20 m, UTM tiles | Instant (one overpass) | Bands only. We compute the indices. |
| CHIRPS | v2.0 daily rainfall, GeoTIFF | 0.05° (~5 km) | Interval (one UTC day) | Rainfall in mm. No indices. |
| MODIS | MOD13Q1 collection 061 (Terra), COG on Planetary Computer | 250 m, sinusoidal | Composite (16 days) | Ready-made NDVI and EVI. |

Assumption: "the other one in the first table" is MODIS MOD13Q1.
MODIS is the source in that table that gives indices directly.
Change this row if you meant Landsat.

Other sources (Landsat, ERA5, GBIF, Movebank and so on) use the same output shape later.
They are out of scope for this document.

## 2. Raw data stays the source of truth

- Fetch keeps the original bytes and writes one `RawManifest` per file.
- If a source gives a non-COG GeoTIFF, Normalize writes a COG as a derived copy. The original stays.
- The precomputed table is a derived, versioned dataset. We can rebuild it from the raw files at any time.
- We need the raw bands to add a new index, fix a cloud-mask bug, or sample an exact animal location.

Fetch must record time in the manifest. A GeoTIFF has location in the file, but time is often only in the STAC item or the filename.

Fetch also sets these manifest fields. The Normalize router uses them as a lookup key:

```json
"extensions": {
  "source_id": "sentinel2",
  "product": "sentinel-2-l2a",
  "source_item_id": "S2B_36KWD_20240304_0_L2A",
  "format": "cog",
  "kind": "raster",
  "time_start": "2024-03-04T08:21:14Z",
  "time_end": "2024-03-04T08:21:14Z",
  "processing_version": "05.10"
}
```

## 3. The analysis grid

Every source is aggregated onto one global grid before any lane writes a `cell_id`.

- Decision: one global 1 km grid. Do not build a grid per Sentinel-2 tile. Tiles use UTM zones, so per-tile cells do not align at zone boundaries.
- Recommendation: EASE-Grid 2.0 Global, 1 km. The cells are equal-area squares.
- Alternative: H3 resolution 8 (~0.74 km² hexagons). H3 has better library support in DuckDB and PostGIS.
- `cell_id` comes from the grid definition only. Rainfall rows and vegetation rows for the same place have the same `cell_id`.
- The grid definition goes into `contracts/` with a version.

## 4. What we precompute per source

Precompute only the values that each source gives directly or by simple band math.
Compute all other indices on demand from the COGs.

### 4.1 Sentinel-2 L2A

Steps for each scene:

1. Read B3, B4, B8, B11 and the SCL band. Resample B11 (20 m) and SCL (20 m) to 10 m with nearest neighbour.
2. Convert digital numbers to reflectance: `reflectance = (DN + BOA_ADD_OFFSET) / 10000`. Read the offset from the item metadata. Processing baseline 04.00 and later uses an offset of −1000. Older baselines use 0. Do not hard-code the offset.
3. Mask the pixels where SCL is 0, 1, 3, 8, 9, 10 or 11. These are nodata, saturated, cloud shadow, cloud, cirrus and snow.
4. Compute the indices on the valid pixels.
5. Aggregate each index to each grid cell.

| Variable | Formula |
| --- | --- |
| `ndvi` | (B8 − B4) / (B8 + B4) |
| `mndwi` | (B3 − B11) / (B3 + B11) |
| `ndmi` | (B8 − B11) / (B8 + B11) |

Statistics per cell, per index, per scene: `mean`, `std`, `valid_fraction`, `pixel_count`.
If `valid_fraction` is less than 0.5, `value` is null. It is not zero.

### 4.2 CHIRPS daily

1. Read the daily GeoTIFF. Mask nodata (−9999).
2. Give each 1 km cell the value of the CHIRPS pixel that contains the cell centre.
3. Write `rainfall_mm` with `stat = sum` and an interval of one UTC day.

CHIRPS pixels are about 5 km wide, so about 25 neighbouring cells have the same value.
Each row stores `source_resolution_m = 5566`, so Analysis does not count those cells as independent measurements.

CHIRPS publishes a preliminary product first and a final product later.
Section 7.4 explains how the final value replaces the preliminary value.

### 4.3 MODIS MOD13Q1

1. Read the `NDVI`, `EVI`, `pixel_reliability` and `composite_day_of_the_year` layers.
2. Apply the scale: `value = raw × 0.0001`. Mask the fill value −3000.
3. Keep only pixels where `pixel_reliability` is 0 (good) or 1 (marginal).
4. Aggregate to each grid cell with the same statistics as Sentinel-2. A 1 km cell holds about 16 MODIS pixels.

| Variable | Origin |
| --- | --- |
| `ndvi` | Given by MODIS |
| `evi` | Given by MODIS |

The time is the 16-day composite window, with `time_precision = composite`.
The Planetary Computer collection also holds Aqua items (MYD13Q1), with windows offset by 8 days. Fetch keeps Terra items only.
The pixel day inside the window is not the same for all pixels, so we do not use it as an instant.

### 4.4 Summary

| Source | Variables | Rows per cell per acquisition |
| --- | --- | --- |
| Sentinel-2 | ndvi, mndwi, ndmi | 3 |
| CHIRPS | rainfall_mm | 1 |
| MODIS | ndvi, evi | 2 |

The `source_id` column separates Sentinel-2 NDVI from MODIS NDVI.
The two values are not interchangeable, so Recipe must select the source explicitly.

### 4.5 Size estimate

These numbers are estimates. We did not measure them.

- One Sentinel-2 tile (~110 × 110 km) is about 12,000 cells.
- One acquisition gives about 36,000 rows. That is about 5–10 MB in PostgreSQL, with indexes.
- One year (~70 acquisitions per tile) is about 0.5 GB in PostgreSQL. The raw bands for the same year are tens of GB.
- If a table becomes too large, partition it by month with `pg_partman`. Supabase supplies this extension.

## 5. One output shape for every source

Every gridded source ends as rows in one long-format PostgreSQL table, `cell_observations`.
Animal tracking data ends in a second table, `animal_locations`.
The next lane writes SQL against these tables and their views. It does not read files.
CSV is only an export format for users, because CSV loses types, time zones and the difference between null and empty.

### 5.1 Common core (every canonical table)

| Column | Type | Meaning |
| --- | --- | --- |
| `cell_id` | string | Cell on the shared grid |
| `time_start` | timestamp UTC | Start of the period that the value covers |
| `time_end` | timestamp UTC | End of the period. Equal to `time_start` for an instant. |
| `time_precision` | string | `instant`, `day`, `composite` or `static` |
| `available_at` | timestamp UTC | When the value became public. Recipe uses it to prevent leakage. |
| `source_id` | string | `sentinel2`, `chirps`, `modis_mod13q1` |
| `source_item_id` | string | STAC item or file that produced the row |
| `processing_version` | string | Provider processing version, for example Sentinel-2 baseline `05.10` |
| `product_status` | string | `final` or `preliminary` |
| `mapping_version` | string | Version of the normalizer code |
| `dataset_id` | string | The series, for example `chirps--chirps-v2.0-daily-p05--ease2-global-1km` |
| `quality_flag` | string | `ok`, `low_valid_fraction`, `preliminary` |

### 5.2 Measurement columns (`cell_observations`)

| Column | Type | Meaning |
| --- | --- | --- |
| `variable` | string | `ndvi`, `mndwi`, `ndmi`, `evi`, `rainfall_mm` |
| `stat` | string | `mean` or `sum` |
| `value` | float, nullable | The measurement |
| `std` | float, nullable | Spread inside the cell |
| `unit` | string | `index` or `mm` |
| `valid_fraction` | float | Share of the cell with valid pixels |
| `pixel_count` | integer | Valid source pixels in the cell |
| `source_resolution_m` | float | Native pixel size of the source |

### 5.3 Example rows

| cell_id | time_start | time_end | precision | source_id | variable | stat | value |
| --- | --- | --- | --- | --- | --- | --- | --- |
| E1K-r4410-c21877 | 2024-03-04T08:21Z | 2024-03-04T08:21Z | instant | sentinel2 | ndvi | mean | 0.41 |
| E1K-r4410-c21877 | 2024-03-05T00:00Z | 2024-03-06T00:00Z | day | chirps | rainfall_mm | sum | 12.0 |
| E1K-r4410-c21877 | 2024-02-18T00:00Z | 2024-03-05T00:00Z | composite | modis_mod13q1 | evi | mean | 0.29 |

### 5.4 Compatibility with the README families

The README v1 contract names `rainfall_observations` and `vegetation_observations`.
Migration `003_cell_observations.sql` makes both as views of `current_cell_observations`.
Each view also has the cell polygon in the `geometry` column:

- `rainfall_observations`: `variable = 'rainfall_mm'`. Map `time_start` → `interval_start`, `time_end` → `interval_end`, `value` → `rainfall_mm`.
- `vegetation_observations`: the vegetation and water indices. Map `time_start` → `observed_at`, `variable` → `index_name`, `value` → `index_value`.

Thus the existing contract does not change, and Recipe can still use one generic join on `cell_id` and time.

### 5.5 Animal locations

Migration `004_animal_locations.sql` makes two tables:

- `animal_entities`: one row per animal. `entity_id` has the form `movebank:<study id>:<local name>`. The same local name in two studies is two animals.
- `animal_locations`: one row per fix, with `observed_at`, `longitude`, `latitude`, a PostGIS point, and the `cell_id` of the fix.

The `cell_id` column lets Recipe join a fix to `cell_observations` without a spatial query.
Movebank columns without a canonical column go into `attributes` (JSONB). The original file stays in the raw archive.
Movebank marks outliers with `visible = false`. The normalizer keeps these fixes and sets `quality_flag = 'marked_outlier'`.

The first source is the Movebank Data Repository. Its data packages are public, have a DOI and a license, and need no login.
The Movebank REST API needs an account. Add it later for studies that are not in the repository.

## 6. Dataset search with metadata filters

### 6.1 What the catalog stores per dataset

Normalize writes one catalog row per dataset when it calls `register_dataset`.
The README `DatasetVersion` fields stay. We add these search fields:

| Field | Type | How we fill it |
| --- | --- | --- |
| `footprint` | PostGIS geometry | Union of the covered cells, simplified |
| `time_range` | `tstzrange` | Minimum `time_start` to maximum `time_end` |
| `family` | string | `animal_locations`, `cell_observations`, `occurrences` |
| `source_id` | string | From the manifest |
| `variables` | string[] | Distinct `variable` values, for example `{ndvi, evi}` |
| `species` | taxon reference[] | GBIF backbone taxon keys plus the scientific names. Empty for satellite data. |
| `tags` | tag records | See 6.2 |
| `summary` | text | Short description that an LLM writes from the metadata |
| `summary_embedding` | vector, optional | For free-text search later |

### 6.2 Tags

Each tag is a record, not a bare string:

```json
{"key": "habitat", "value": "savanna", "origin": "ai", "model": "claude-haiku-4-5", "confidence": 0.82, "evidence": "71% of footprint in ecoregion 'Etosha Pan halophytics'"}
```

There are two tag origins:

1. Deterministic tags. Code computes them from the data: `country`, `ecoregion` (intersection with the RESOLVE Ecoregions layer), `biome`, `sensor_type`, `temporal_resolution`, `season_coverage`, `taxon_class`.
2. AI tags. An LLM proposes them from the descriptor, the deterministic tags and a sample of rows. Examples: `habitat=wetland`, `behaviour=migration`, `study_design=gps_collar`, `topic=drought`.

Rules for AI tags:

- The LLM must choose values from a controlled vocabulary in `contracts/tag_vocabulary.json`. Validation rejects any other value.
- An AI tag never replaces a deterministic tag with the same key.
- Search shows the tag origin, so a user can exclude AI tags.
- We generate tags when a dataset is published. We generate them again only when coverage changes materially (section 7.5).

### 6.3 Search function

The README adapter already has `search_datasets(filters)`. We extend the filters:

```python
search_datasets(
    access_scope: str,
    family: list[str] | None,
    region: GeoJSON | None,          # overlap with footprint
    start: datetime | None,          # overlap with time_range
    end: datetime | None,
    species: list[TaxonRef] | None,  # exact taxon match; no automatic genus expansion
    variables: list[str] | None,
    tags_all: list[Tag] | None,      # dataset must have every tag
    tags_any: list[Tag] | None,
    include_ai_tags: bool = True,
    text: str | None = None,         # matched against summary
    status: str = "ready",
) -> list[DatasetMatch]
```

Each `DatasetMatch` holds the `DatasetVersion` plus three overlap scores:

- `spatial_overlap`: share of the query region that the footprint covers.
- `temporal_overlap`: share of the query period that `time_range` covers.
- `matched_tags`: tags that matched, with their origin.

Results are ordered by `spatial_overlap × temporal_overlap`, then by tag matches.
If a dataset has unknown coverage, the result marks it as unknown. Search does not count it as covered.

The core SQL filter:

```sql
SELECT d.*
FROM datasets d
WHERE d.access_scope = ANY(:scopes)
  AND d.status = 'ready'
  AND ST_Intersects(d.footprint, ST_GeomFromGeoJSON(:region))
  AND d.time_range && tstzrange(:start, :end)
  AND (:species IS NULL OR d.species_keys && :species)
  AND (:variables IS NULL OR d.variables && :variables);
```

### 6.4 From a question to filters

Example question: "antelope tracking observations in northern Namibia during the 2019 drought".

1. An LLM converts the question into a filter JSON.
2. Code validates the JSON against the schema and the tag vocabulary.
3. Code resolves "antelope" through the GBIF backbone. "Antelope" is not one taxon, so the system asks the user to select species, or it lists the candidate taxa. It does not silently broaden the query.
4. Code runs `search_datasets`. The LLM never writes SQL.

```json
{
  "family": ["animal_locations"],
  "region": {"type": "Polygon", "coordinates": ["..."]},
  "start": "2019-01-01T00:00:00Z",
  "end": "2019-12-31T23:59:59Z",
  "species": [{"gbif_key": 2441057, "name": "Antidorcas marsupialis"}],
  "tags_any": [{"key": "topic", "value": "drought"}]
}
```

A second call with `family = ["cell_observations"]` and the same region and dates finds the rainfall and vegetation datasets.
Recipe joins the results.

## 7. Append new acquisitions to an existing dataset

### 7.1 One series per source product

A dataset is a growing series for one source product on one grid, for example `sentinel2-l2a/ease1km`.
New scenes go into the same series. They do not create a new dataset.

Do not mix product versions in one series.
MODIS collection 061 and a future collection 062 are two series.

### 7.2 Batches and idempotency

- Each ingest of one source file is one batch.
- Batch key: `(source_id, source_item_id, processing_version, product_status, mapping_version)`.
- Fetch checks the latest series version before it downloads. It does not download an item that the series already holds.
- If the batch key already exists, the job stops and does nothing. Retries are therefore safe.
- Each batch inserts new rows. The job never updates or deletes an observation row.
- One append is one transaction. A failed append leaves no rows.
- The writer locks the `series` row. Two writers of one series therefore never make the same version.

### 7.3 Versions

- An append creates a new version in `series_versions`.
- `ingest_batches` records for each batch the version that added it (`added_in_version`) and the version that replaced it (`superseded_in_version`).
- Version N holds each batch with `added_in_version <= N` that was not replaced at or before N.
- Old versions stay readable. A saved recipe pins a version with `current_cell_observations_at(series_id, version)`, so its results do not change.

### 7.4 Overlaps and replacements

The rule is: store every row, and select the current row in a view.

| Case | Example | Rule for the current view |
| --- | --- | --- |
| Tile overlap | Two Sentinel-2 tiles cover the same cell on the same day | Per `cell_id`, `variable` and UTC day, keep the row with the highest `valid_fraction` |
| Preliminary → final | CHIRPS preliminary, then CHIRPS final for the same day | Keep the final row. Mark the preliminary row as superseded. |
| Reprocessing | ESA reprocesses a scene with a new processing baseline | Keep the row with the newest `processing_version` |
| Normalizer bug fix | New cloud-mask code | Rebuild the affected batches with a new `mapping_version`. Publish a new dataset version. |

Superseded rows stay in storage with their `available_at`.
A forecast backtest with a cutoff date must see only the values that were available on that date.

### 7.5 Catalog update after an append

1. Expand `time_range` and `footprint` with the new batch.
2. Compute the deterministic tags again for the new area only.
3. Generate AI tags again only if the footprint grows by more than 10 % or the period reaches a new calendar quarter.
4. Write the new `dataset_version` and the updated coverage in one transaction.

### 7.6 Scheduled appends

A background job checks each followed series:

- Sentinel-2 and MODIS: search STAC for items newer than the latest `time_end` in the footprint.
- CHIRPS: download new preliminary days. Download final files when they are published, about three weeks after the end of the month.

The job uses the durable job table with `job_type = raster`.
Raster jobs run on their own worker, so they do not block tabular jobs.

## 8. Lane ownership

| Item | Lane |
| --- | --- |
| Manifest fields `source_id`, `source_item_id`, `format`, `kind`, time fields | 1. Fetch |
| Source router, per-source adapters, zonal statistics, `cell_observations`, `animal_locations`, batches and versions | 2. Normalize |
| Grid definition, column contract, tag vocabulary (`contracts/`) | 2. Normalize, agreed with all lanes |
| Catalog search fields, tagging, `search_datasets` | 2. Normalize (storage adapter) |
| Point sampling from COGs, time-window joins with `available_at` | 3. Recipe |

## 9. Open decisions

1. Grid: EASE-Grid 2.0 1 km or H3 resolution 8.
2. Confirm that the third source is MODIS MOD13Q1, not Landsat.
3. The threshold for `valid_fraction` (0.5 proposed).
4. The first version of the tag vocabulary.
5. The target species and study. These decide the region for the first scheduled appends.

## 10. Tables and views for the next lane

All objects are in the `public` schema of the Supabase project.

| Object | Use |
| --- | --- |
| `current_cell_observations` (view) | One current value per cell, variable, source and day |
| `rainfall_observations` (view) | README form of CHIRPS rainfall, with the cell polygon |
| `vegetation_observations` (view) | README form of NDVI, EVI, MNDWI and NDMI, with the cell polygon |
| `current_cell_observations_at(series_id, version, cutoff)` (function) | The current view at a pinned version, with only values public at `cutoff` |
| `current_animal_locations` (view) | Animal fixes after replacement by later batches |
| `animal_entities` (table) | Animal, taxon, GBIF key, sex, deployment dates |
| `grid_cells` (table) | Cell polygon and centre point for each `cell_id` |
| `latest_datasets` (view), `dataset_tags` (table) | The catalog |

`examples/queries.sql` has queries that join animal fixes to rainfall and to NDVI.

Access:

- Row-level security is on for every table. The `anon` and `authenticated` API roles cannot read the tables.
- Give a person or service the group role `habitat_reader` to read. Give the pipeline `habitat_writer` to append.
- Set `HABITAT_DATABASE_URL` in `.env`. Use the session pooler connection string (port 5432), because the writer uses `COPY` and session settings.

## 11. Code layout

The scaffold implements this design in `src/habitat/`. Run the tests with `uv run pytest`.

| Path | Content |
| --- | --- |
| `contracts/grid.json`, `contracts/tag_vocabulary.json` | Shared grid definition and tag vocabulary |
| `migrations/` | Roles, catalog, `cell_observations` and its views, `animal_locations`, indexes. All are applied to Supabase. |
| `src/habitat/contracts.py` | `RawManifest`, `DatasetVersion`, `SearchFilters`, `Tag`, and the Arrow schemas of a normalized batch |
| `src/habitat/db.py` | Connection from `HABITAT_DATABASE_URL` |
| `src/habitat/grid.py` | EASE-Grid 2.0 1 km cells and `cell_id` |
| `src/habitat/fetch/` | Raw archive, CHIRPS connector, STAC connector for Sentinel-2 and MODIS, Movebank Data Repository connector |
| `src/habitat/normalize/` | Index math, block-wise zonal statistics, per-source adapters, router |
| `src/habitat/storage/series.py` | Append-only batches and versions in PostgreSQL, `COPY` of rows, grid cells, animal entities |
| `src/habitat/catalog/` | Footprints, deterministic tags, Claude tags and question parsing, GBIF taxon resolution, `search_datasets`, publish |
| `src/habitat/ingest.py` | Command line: fetch, normalize, append and publish one source |
| `examples/athi_kaputiei.py`, `examples/queries.sql` | Real example: wildebeest fixes, CHIRPS, MODIS and Sentinel-2 on the Athi-Kaputiei Plains, Kenya |

Example:

```bash
uv run python -m habitat.ingest chirps --bbox 15.8,-19.2,16.6,-18.8 --start 2024-03-04 --end 2024-03-07
uv run python -m habitat.ingest modis_mod13q1 --bbox 15.8,-19.2,16.6,-18.8 --start 2024-03-01 --end 2024-03-31
uv run python -m habitat.ingest sentinel2 --bbox 16.0,-19.1,16.3,-18.9 --start 2024-03-09 --end 2024-03-09 --ai-tags
uv run python -m habitat.ingest movebank --package 5b6706c8-e7e5-46e4-82ba-da5a82324298
uv run python examples/athi_kaputiei.py
```

The database tests make a temporary schema, apply the migrations, and remove the schema after the test.
They are skipped when `HABITAT_DATABASE_URL` is not set.

The `--ai-tags` option calls Claude. It needs `ANTHROPIC_API_KEY` or an `ant auth login` profile.

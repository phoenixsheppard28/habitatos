# Integration surfaces: Fetch + Normalize lanes

This document is for agents that work on the other lanes (Recipe, Analysis, coordinator).
It describes what this part of the system accepts and what it produces.
For the design reasons, read `pipeline/DESIGN.md`. For the full product contract, read `README.md`.

Status at 2026-10-03, branch `psheppard/pipeline-1`.

## 1. What this part does

This part takes satellite data and animal tracking data from public sources.
It changes the data into typed PostgreSQL tables on one shared 1 km grid.
The next lane reads these tables with SQL. The next lane does not read raw files.

```text
CLI / run()  ->  Fetch (download + RawManifest)  ->  Normalize (router + per-source adapter)
             ->  Append (batch + series version)  ->  Publish (catalog row + tags)
             ->  PostgreSQL tables and views  ->  Recipe lane
```

| Source | `source_id` | Product | Output family | Variables |
| --- | --- | --- | --- | --- |
| Sentinel-2 L2A (STAC, COG) | `sentinel2` | `sentinel-2-l2a` | `cell_observations` | `ndvi`, `mndwi`, `ndmi` |
| MODIS Terra MOD13Q1 v061 (STAC, COG) | `modis_mod13q1` | `mod13q1-061` | `cell_observations` | `ndvi`, `evi` |
| CHIRPS v2.0 daily (GeoTIFF) | `chirps` | `chirps-v2.0-daily-p05` | `cell_observations` | `rainfall_mm` |
| Movebank Data Repository (CSV) | `movebank` | `movebank-data-repository` | `animal_locations` | GPS fixes |

## 2. Input surface

### 2.1 How to start an ingest

There is no `run(request) -> response` JSON adapter yet (see section 6).
Today there are two entry points.

Command line:

```bash
uv run python -m habitat.ingest chirps        --bbox W,S,E,N --start YYYY-MM-DD --end YYYY-MM-DD
uv run python -m habitat.ingest modis_mod13q1 --bbox W,S,E,N --start YYYY-MM-DD --end YYYY-MM-DD
uv run python -m habitat.ingest sentinel2     --bbox W,S,E,N --start YYYY-MM-DD --end YYYY-MM-DD [--ai-tags]
uv run python -m habitat.ingest movebank      --package <data-repository-item-uuid>
```

Python (`src/habitat/ingest.py`):

```python
from habitat.ingest import Workspace, run
from habitat.db import connect
from habitat.grid import default_grid

with connect() as connection:
    workspace = Workspace(Path("data/raw"), connection, default_grid())
    outcomes = run("chirps", workspace, bbox=(36.85, -1.60, 37.10, -1.35),
                   start=date(2011, 3, 1), end=date(2011, 4, 30))
```

| Parameter | Type | Meaning |
| --- | --- | --- |
| `source` | `sentinel2` \| `modis_mod13q1` \| `chirps` \| `movebank` | The source to ingest |
| `bbox` | `(west, south, east, north)`, WGS84 degrees | Area of interest. Required for satellite sources. For Movebank, it filters fixes. |
| `start`, `end` | `date`, inclusive | Required for satellite sources. Not used for Movebank. |
| `package` | string | Movebank Data Repository item UUID. Required for Movebank. |
| `use_ai` | bool | When true, Claude writes AI tags and a summary for the catalog row |

`run()` returns `list[IngestOutcome]`. Each outcome has the `RawManifest`, an `AppendResult`
(`series_id`, `version`, `batch_key`, `appended`), or a `quarantine_reason` string.

To find a Movebank package, call `habitat.fetch.movebank.search_data_packages("Connochaetes taurinus")`.
The function returns `uuid`, `title`, `taxon` and `study_id` for each package.

### 2.2 Idempotency

- Fetch asks the latest series version which items it holds. Fetch does not download those items again.
- The batch key is `source_id|source_item_id|processing_version|product_status|mapping_version`.
- A repeated run with the same inputs downloads nothing and appends nothing. It is safe to retry.

### 2.3 Fetch → Normalize handoff: `RawManifest`

Fetch writes one `RawManifest` per source item (`src/habitat/contracts.py`).
The manifest follows the README v1 shape. `extensions` is a typed `SourceItem`:

```json
"extensions": {
  "source_id": "sentinel2",
  "product": "sentinel-2-l2a",
  "source_item_id": "S2B_36KWD_20240304_0_L2A",
  "kind": "raster",
  "time_start": "2024-03-04T08:21:14Z",
  "time_end": "2024-03-04T08:21:14Z",
  "time_precision": "instant",
  "available_at": "2024-03-04T14:02:00Z",
  "processing_version": "05.10",
  "product_status": "final",
  "assets": {"B04": "data/raw/.../B04.tif"},
  "properties": {}
}
```

- The router selects a normalizer by `(extensions.source_id, storage.format)`.
- An unknown pair raises `QuarantineError`. The item is not ingested.
- Raw bytes stay on local disk under `data/raw/<artifact_id>/`. `storage.uri` is a local path today.
- Each `ingest_batches` row stores the full manifest in the `raw_manifest` JSONB column.

If your lane produces raw files for this part, write a `RawManifest` with these `extensions` fields.
Then add a normalizer to `NORMALIZERS` in `src/habitat/normalize/router.py`.

## 3. Output surface: PostgreSQL

All objects are in the `public` schema of the Supabase project.
Read them with SQL. Do not depend on Python classes or file paths.

### 3.1 Connection and access

- Set `HABITAT_DATABASE_URL` in `.env`. Use the session pooler string (port 5432).
- Grant the group role `habitat_reader` to read. Grant `habitat_writer` to append.
- Row-level security is on. The Supabase `anon` and `authenticated` roles cannot read the tables.
- The session time zone is UTC. All timestamps are `timestamptz`.

### 3.2 Objects to read

Read the views, not the base tables. The views apply the version, replacement and de-duplication rules.

| Object | Kind | Use |
| --- | --- | --- |
| `current_cell_observations` | view | One current value per cell, variable, source and UTC day |
| `rainfall_observations` | view | README form of CHIRPS rainfall, with the cell polygon |
| `vegetation_observations` | view | README form of NDVI, EVI, MNDWI and NDMI, with the cell polygon |
| `current_cell_observations_at(series_id, version, cutoff)` | function | The current view at a pinned version. Only values public at `cutoff`. |
| `current_animal_locations` | view | Animal fixes after replacement by later batches |
| `animal_entities` | table | One row per animal: taxon, GBIF key, sex, deployment dates |
| `grid_cells` | table | Polygon and centre point for each `cell_id` |
| `latest_datasets` | view | Catalog: the newest version of each dataset |
| `dataset_tags` | table | Catalog tags, with origin `deterministic` or `ai` |

Base tables: `cell_observations`, `animal_locations`, `ingest_batches`, `series`, `series_versions`, `datasets`.

### 3.3 The join key: `cell_id`

- Grid: EASE-Grid 2.0 Global, 1 km, `EPSG:6933`. Definition: `contracts/grid.json`.
- Format: `E1K-r{row}-c{col}`, for example `E1K-r4410-c21877`.
- Every gridded row has a `cell_id`. Every animal fix has the `cell_id` that contains the fix.
- Thus a fix joins to rainfall and vegetation on `cell_id` plus a time rule. No spatial query is necessary.
- Python helper: `habitat.grid.default_grid()` gives cell ids and polygons for points or a bbox.

### 3.4 `current_cell_observations` columns

Grain: one row per `cell_id`, `variable`, `source_id` and UTC day.

| Column | Type | Meaning |
| --- | --- | --- |
| `cell_id` | text | Cell on the shared grid |
| `time_start` | timestamptz | Start of the period that the value covers |
| `time_end` | timestamptz | End of the period. Equal to `time_start` for an instant. |
| `time_precision` | text | `instant` (Sentinel-2), `day` (CHIRPS), `composite` (MODIS 16-day) |
| `available_at` | timestamptz | When the value became public. Use it to prevent leakage in forecasts. |
| `source_id` | text | `sentinel2`, `chirps`, `modis_mod13q1` |
| `source_item_id` | text | STAC item or file that produced the row |
| `processing_version` | text | Provider processing version |
| `product_status` | text | `final` or `preliminary` |
| `dataset_id` | text | Series id, for example `chirps--chirps-v2.0-daily-p05--ease2-global-1km` |
| `dataset_version` | integer | Series version that added the row |
| `mapping_version` | text | Normalizer version, for example `sentinel2-l2a-v1` |
| `quality_flag` | text | `ok`, `low_valid_fraction`, `preliminary` |
| `variable` | text | `ndvi`, `mndwi`, `ndmi`, `evi`, `rainfall_mm` |
| `stat` | text | `mean` (indices) or `sum` (rainfall) |
| `value` | float, nullable | The measurement. Null means not reliable. Null is not zero. |
| `std` | float, nullable | Spread inside the cell |
| `unit` | text | `index` or `mm` |
| `valid_fraction` | float | Share of the cell with valid pixels. `value` is null below 0.5. |
| `pixel_count` | bigint | Valid source pixels in the cell |
| `source_resolution_m` | float | Native pixel size. CHIRPS is 5566 m, so about 25 cells share one value. |

Rules for consumers:

- Select the source explicitly. Sentinel-2 NDVI and MODIS NDVI are not interchangeable.
- Do not count CHIRPS cells as independent measurements. Use `source_resolution_m`.
- Use `time_start` and `time_end` as an interval. A MODIS value covers 16 days.

### 3.5 README views

| View | Columns |
| --- | --- |
| `rainfall_observations` | `dataset_id`, `dataset_version`, `source_record_id`, `cell_id`, `geometry`, `interval_start`, `interval_end`, `rainfall_mm`, `product_status`, `quality_flag`, `available_at`, `source_id` |
| `vegetation_observations` | `dataset_id`, `dataset_version`, `source_record_id`, `cell_id`, `geometry`, `observed_at`, `observed_until`, `index_name`, `index_value`, `index_std`, `valid_fraction`, `pixel_count`, `quality_flag`, `available_at`, `source_id` |

These views match the minimum columns of the README v1 contract.
`geometry` is the cell polygon in WGS84 (PostGIS). Use `ST_AsGeoJSON(geometry)` to get GeoJSON.

### 3.6 `current_animal_locations` columns

Grain: one row per animal fix (`entity_id`, `observed_at`, `sensor_type`).

| Column | Type | Meaning |
| --- | --- | --- |
| `entity_id` | text | `movebank:<study_id>:<local name>`. Equal local names in two studies are two animals. |
| `observed_at` | timestamptz | Fix time, UTC |
| `available_at` | timestamptz | Publication date of the data package |
| `longitude`, `latitude` | float | WGS84 |
| `geometry` | geometry(Point, 4326) | The fix as a PostGIS point |
| `cell_id` | text | Grid cell that contains the fix |
| `tag_id` | text, nullable | Tag identifier |
| `sensor_type` | text | For example `gps` |
| `quality_flag` | text | `ok` or `marked_outlier` (Movebank `visible = false`) |
| `source_record_id` | text | Movebank `event-id` |
| `dataset_id`, `dataset_version`, `mapping_version` | | Provenance |
| `attributes` | jsonb | Movebank columns without a canonical column |

Filter `quality_flag = 'ok'` unless you want the outliers.
Join `animal_entities` on `entity_id` for taxon, `gbif_taxon_key`, sex, life stage and deployment dates.

### 3.7 Example joins

`examples/queries.sql` has ready queries:

- Fixes per animal inside an area and a period.
- Rainfall sum in the 14 days before each daily position.
- The MODIS NDVI composite that covers each daily position.
- Sentinel-2 index summary for one day.

The core pattern:

```sql
SELECT p.entity_id, p.observed_at, v.index_value AS ndvi
FROM current_animal_locations p
JOIN vegetation_observations v
  ON v.cell_id = p.cell_id
 AND v.source_id = 'modis_mod13q1' AND v.index_name = 'ndvi'
 AND p.observed_at >= v.observed_at AND p.observed_at < v.observed_until
WHERE p.quality_flag = 'ok';
```

### 3.8 Reproducible reads

- A saved recipe must pin a version: `SELECT * FROM current_cell_observations_at('<series_id>', <version>, '<cutoff>')`.
- `version = NULL` gives the latest version. `cutoff = NULL` disables the leakage filter.
- Old versions stay readable. The pipeline never updates or deletes an observation row.
- There is no pinned-version function for animal locations yet. Filter on `dataset_version` for now.

## 4. Output surface: the catalog

Each append that adds rows publishes a new catalog version of the series.

### 4.1 `latest_datasets` / `datasets`

| Column | Meaning |
| --- | --- |
| `dataset_id` | The series id: `<source_id>--<product>--ease2-global-1km` |
| `version` | Series version. Immutable after publication. |
| `family` | `cell_observations` or `animal_locations` |
| `source_id`, `access_scope`, `status` | `status` is `ready` or `quarantined` |
| `footprint` | PostGIS MultiPolygon, union of covered cells |
| `time_range` | `tstzrange` from the first `time_start` to the last `time_end` |
| `variables` | For example `{ndvi,evi}` |
| `species_keys` | GBIF backbone taxon keys. Empty for satellite data. |
| `summary` | Short text from Claude, when AI tags are on |
| `descriptor` | The full `DatasetVersion` as JSONB |

`descriptor.storage.uri` has the form `postgres://<family>?series_id=<id>&version=<n>`.
Use it to read the pinned version from section 3.8.

### 4.2 Search API (Python)

```python
from habitat.catalog.store import PostgresCatalog
from habitat.catalog.taxa import resolve_taxon
from habitat.contracts import SearchFilters, Tag

matches = PostgresCatalog(connection).search_datasets(SearchFilters(
    access_scope=["public"],
    family=["animal_locations"],
    region_wkt="POLYGON((36.85 -1.6, 37.1 -1.6, 37.1 -1.35, 36.85 -1.35, 36.85 -1.6))",
    start=datetime(2011, 3, 1, tzinfo=UTC), end=datetime(2011, 4, 30, tzinfo=UTC),
    species=resolve_taxon("Connochaetes taurinus").taxa,
    tags_any=[Tag(key="topic", value="drought")],
))
```

- Each `DatasetMatch` has `dataset`, `spatial_overlap`, `temporal_overlap`, `matched_tags` and `score`.
- Results are sorted by `spatial_overlap × temporal_overlap`, then by the number of matched tags.
- An overlap of `None` means unknown coverage. Search keeps the dataset but does not count it as covered.
- Species match is exact on the GBIF key. Search does not expand a genus to its species.
- `habitat.catalog.taxa.resolve_taxon(name)` converts a name to GBIF keys.
- `CatalogAssistant().parse_question(text)` converts a question to `QuestionFilters` with Claude. Code validates the result. Claude never writes SQL.

### 4.3 Tags

- Deterministic keys: `sensor_type`, `temporal_resolution`, `variable`, `country`, `ecoregion`, `biome`, `year`, `taxon_class`.
- AI keys and allowed values: `contracts/tag_vocabulary.json` (`habitat`, `topic`, `behaviour`, `study_design`).
- Validation rejects an AI tag value that is not in the vocabulary.
- Set `include_ai_tags=False` to search with deterministic tags only.

## 5. Data in the live database now

Checked on 2026-10-03:

| Object | Rows |
| --- | --- |
| `cell_observations` (Sentinel-2 only) | 4,734 |
| `animal_locations` | 0 |
| `animal_entities` | 0 |
| `grid_cells` | 800 |
| `latest_datasets` | 1 (`sentinel2--sentinel-2-l2a--ease2-global-1km`, version 2) |

The demo area is the Athi-Kaputiei Plains, Kenya (bbox `36.85,-1.60,37.10,-1.35`).
To load wildebeest fixes, CHIRPS and MODIS for March–April 2011, run `uv run python examples/athi_kaputiei.py`.

## 6. Gaps against the README v1 contract

Other lanes must know these gaps before they integrate.

| README requirement | Status here |
| --- | --- |
| `run(request) -> response` adapter with `contract_version`, `request_id`, `status` | Not built. Entry points are the CLI and `ingest.run()`. |
| Input is a `QuerySpec` with GeoJSON region | Not accepted. Input is `bbox` + dates or a Movebank package UUID. |
| `DatasetVersion.columns` (name, type, unit, role) | Not filled. The column contract is in this document and in the migrations. |
| `ValidationReport` and `validation_report_ref` | Not built. `validation_report_ref` is null. |
| Quarantined records stay discoverable | Partly. A quarantined item gives a `quarantine_reason` in the outcome and a log line. It is not written to the database. |
| `resolve_artifact(storage)` / `read_dataset(storage, ...)` adapter | Not built. Read the views with SQL. |
| Portable Parquet export | Not built. |
| Raw artifacts in object storage | Not built. Raw files are on the local disk of the machine that ran the ingest. |
| Durable job table and scheduled appends | Not built. Each ingest is a manual run. |
| Coordinator → Fetch with "missing requirements" | Not built. The caller decides the source, area and dates. |

## 7. Open decisions

1. Grid: EASE-Grid 2.0 1 km (built) or H3 resolution 8.
2. The `valid_fraction` threshold (0.5 now).
3. The first version of the tag vocabulary.
4. The target species and study for the demo.
5. The shape of the JSON adapter for the README v1 handoff.

## 8. Files to read

| Path | Content |
| --- | --- |
| `migrations/001`–`005` | Roles, catalog, `cell_observations` and views, `animal_locations`, indexes |
| `src/habitat/contracts.py` | `RawManifest`, `SourceItem`, `DatasetVersion`, `SearchFilters`, Arrow schemas |
| `src/habitat/ingest.py` | Entry point: fetch, normalize, append, publish |
| `src/habitat/normalize/router.py` | Source/format → normalizer map |
| `src/habitat/storage/series.py` | Batches, versions, `COPY` of rows |
| `src/habitat/catalog/` | Search, tags, taxon resolution, publish |
| `examples/queries.sql` | Join examples for the Recipe lane |
| `pipeline/DESIGN.md` | Design reasons and per-source processing steps |

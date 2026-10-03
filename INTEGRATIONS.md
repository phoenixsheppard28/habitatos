# Integration surfaces: Fetch + Normalize lanes

This document is for agents that work on the other lanes (Recipe, Analysis, coordinator).
It describes what this part of the system accepts and what it produces.
For the data flow and the design reasons, read `PIPELINE.md`. For each source, read `SOURCES.md`.
For the full product contract, read `README.md`.

Status at 2026-10-03, branch `psheppard/merge-1-and-2`. The fetch lane and the processing lane are one package: `habitat`.

## 0. Repository layout and setup

```text
README.md          Product contract for all lanes
INTEGRATIONS.md    This document
PIPELINE.md        Data flow, archive, registry, agent, design reasons
SOURCES.md         One section per source: input, archived files, limits
.env               HABITAT_DATABASE_URL, ANTHROPIC_API_KEY, Movebank credentials (not in git)
.env.example       Template for .env
.mcp.json          Supabase MCP server for this project
pyproject.toml     The uv Python project (package `habitat`)
src/habitat/       Package code
tests/             Unit tests, database tests, live tests, fixtures
migrations/        SQL for Supabase, applied in number order (001–007)
contracts/         Grid definition and tag vocabulary
examples/          Real example script and SQL queries for the Recipe lane
data/              Raw archive and run reports (not in git)
```

Setup:

1. Install `uv`. Python 3.12 is pinned in `.python-version`.
2. Copy `.env.example` to `.env` at the repository root and fill in the values:
   - `HABITAT_DATABASE_URL`: the Supabase session pooler string, port 5432. The direct `db.<ref>.supabase.co` host is IPv6 only. It does not resolve on most networks.
   - `ANTHROPIC_API_KEY`: necessary for the fetch agent (`--question`) and for `--ai-tags`.
   - `MOVEBANK_USERNAME`, `MOVEBANK_PASSWORD`: optional, for authenticated Movebank study downloads.
3. Run all commands from the repository root:

```bash
uv sync
uv run pytest            # database tests use a temporary schema and remove it
```

Database tests are skipped when `HABITAT_DATABASE_URL` is not set.

## 1. What this part does

This part takes satellite data and animal tracking data from public sources.
It keeps the raw files in an archive and changes the data into typed PostgreSQL tables on one shared 1 km grid.
The next lane reads these tables with SQL. The next lane does not read raw files.

```text
FetchRequest -> Fetch (connector or Claude agent) -> Archive (files + raw_artifacts row)
             -> Normalize (registry normalizer) -> Append (batch + series version)
             -> Publish (catalog row + tags) -> PostgreSQL tables and views -> Recipe lane
```

| Source | `source_id` | Product | Output family | Variables |
| --- | --- | --- | --- | --- |
| Sentinel-2 L2A (STAC, clipped GeoTIFF) | `sentinel2` | `sentinel-2-l2a` | `cell_observations` | `ndvi`, `mndwi`, `ndmi` |
| MODIS Terra MOD13Q1 v061 (STAC, clipped GeoTIFF) | `modis_mod13q1` | `mod13q1-061` | `cell_observations` | `ndvi`, `evi` |
| CHIRPS v2.0 daily (`.tif.gz`) | `chirps` | `chirps-v2.0-daily-p05` | `cell_observations` | `rainfall_mm` |
| Movebank Data Repository (CSV) | `movebank_repository` | `movebank-data-repository` | `animal_locations` | GPS fixes |
| Movebank direct-read API (CSV) | `movebank_study` | `movebank-direct-read` | `animal_locations` | GPS fixes |
| Zenodo record file | `zenodo` | `zenodo-record-file` | none (quarantined) | none |
| Synthetic fixtures | `fixture` | `habitat-fixture` | none (quarantined) | none |

## 2. Input surface

### 2.1 How to start an ingest

Command line:

```bash
uv run habitat chirps              --bbox W,S,E,N --start YYYY-MM-DD --end YYYY-MM-DD
uv run habitat modis_mod13q1       --bbox W,S,E,N --start YYYY-MM-DD --end YYYY-MM-DD
uv run habitat sentinel2           --bbox W,S,E,N --start YYYY-MM-DD --end YYYY-MM-DD [--ai-tags]
uv run habitat movebank_repository --package <data-repository-item-uuid>
uv run habitat movebank_study      --package movebank:<study_id>
uv run habitat --question "Find rainfall and vegetation for bbox W,S,E,N from YYYY-MM-DD to YYYY-MM-DD"
```

Python, with the README v1 request and response (`src/habitat/pipeline.py`):

```python
from habitat.pipeline import build_request, run

request = build_request("chirps", (36.85, -1.60, 37.10, -1.35), date(2011, 3, 1), date(2011, 4, 30), None, None)
result = run(request, use_agent=False)          # or a FetchRequest that you build yourself
result.fetch                                    # FetchResponse: status, warnings, raw_artifacts
result.outcomes                                 # one ArtifactOutcome per artifact
result.published                                # series ids with a new catalog version
```

| `FetchRequirements` field | Meaning |
| --- | --- |
| `source_ids` | Sources of the deterministic path. Empty: search the catalog. |
| `bbox` | `[west, south, east, north]`, WGS84 degrees. Required for satellite sources. For Movebank, it filters fixes. |
| `start`, `end` | Inclusive `YYYY-MM-DD`. Required for satellite sources. |
| `package` | Movebank package UUID, `movebank:<study_id>`, `zenodo:<id>` or a fixture id |

`run(request, use_agent=True)` gives the question to the Claude fetch agent instead.
Each `ArtifactOutcome` has `artifact_id`, `version`, `source_id`, `status` (`appended`, `already_present`, `quarantined`), `reason` and `series_id`.

### 2.2 Idempotency

- Connectors ask the latest series version which items it holds. They do not download those items again.
- The archive caches by `source_key`. A second request for the same item uses the archived files after a checksum check.
- The batch key is `source_id|source_item_id|processing_version|product_status|mapping_version`.
- A repeated run with the same inputs downloads nothing and appends nothing. It is safe to retry.

### 2.3 Fetch → Normalize handoff: `RawManifest`

Fetch writes one `RawManifest` per source item (`src/habitat/contracts.py`).
The manifest follows the README v1 shape. `extensions` is a typed `SourceItem`:

```json
"storage": {"uri": "artifact://S2B_MSIL2A_20240217T074009_R092_T37MBU_20240217T113157/1", "format": "geotiff"},
"checksum": "sha256:…,sha256:…",
"extensions": {
  "source_id": "sentinel2",
  "product": "sentinel-2-l2a",
  "source_item_id": "S2B_MSIL2A_20240217T074009_R092_T37MBU_20240217T113157",
  "source_key": "sentinel2:S2B_MSIL2A_…:36.80000,-1.60000,37.10000,-1.30000",
  "kind": "raster",
  "time_start": "2024-02-17T07:40:09Z",
  "time_end": "2024-02-17T07:40:09Z",
  "time_precision": "instant",
  "available_at": "2024-02-17T11:31:57Z",
  "processing_version": "05.10",
  "product_status": "final",
  "assets": {"green": "B03.tif", "red": "B04.tif", "nir": "B08.tif", "swir16": "B11.tif", "scl": "SCL.tif"},
  "properties": {"boa_add_offset": -1000.0, "requested_bbox": [36.8, -1.6, 37.1, -1.3]}
}
```

- `storage.uri` is always `artifact://<artifact_id>/<version>`. Resolve it only through `habitat.archive` (`Archive.resolve(manifest)` or `LocalArtifactStore.open(manifest, asset)`).
- The `raw_artifacts` table holds every manifest. Raw bytes stay in the archive under `data/raw/<artifact_id>/<version>/`.
- The router selects the normalizer from the source registry (`src/habitat/sources.py`). An unknown source, a source without a normalizer, or a wrong storage format gives a quarantine reason.
- Each `ingest_batches` row stores the full manifest in the `raw_manifest` JSONB column.

If your lane produces raw files for this part, add a `Source` entry with a connector and a normalizer to `SOURCES`.

## 3. Output surface: PostgreSQL

All objects are in the `public` schema of the Supabase project.
Read them with SQL. Do not depend on Python classes or file paths.

### 3.1 Connection and access

- Set `HABITAT_DATABASE_URL` in `.env`. Use the session pooler string (port 5432). See section 0.
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
| `available_at` | timestamptz | When the value became public. Use it to prevent leakage in forecasts. Sentinel-2: `s2:generation_time`. MODIS: production time. CHIRPS: file `Last-Modified`. |
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
| `source_record_id` | text | Movebank `event-id`. A public preview has no event id: `synthetic:<hash>`. |
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

| Dataset (`series_id`) | Family | Version | Rows | Period |
| --- | --- | --- | --- | --- |
| `sentinel2--sentinel-2-l2a--ease2-global-1km` | `cell_observations` | 4 | 9,534 | 2024-02-02 to 2024-03-03 (4 items, 3 dates) |
| `modis_mod13q1--mod13q1-061--ease2-global-1km` | `cell_observations` | 3 | 4,800 | 2024-01-17 to 2024-03-04 (3 composites) |
| `movebank--movebank-data-repository--ease2-global-1km` | `animal_locations` | 1 | 279,082 | 2010-05-25 to 2013-01-15 |

- Satellite data: the Athi-Kaputiei Plains, Kenya (bbox `36.85,-1.60,37.10,-1.35`), about 800 cells.
- Tracking data: 36 white-bearded wildebeest (*Connochaetes taurinus*, GBIF key 2441105) at three sites: Mara, Athi-Kaputiei Plains and Amboseli Basin. Source: Movebank Data Repository, doi:10.5441/001/1.h0t27719, CC0.
- `grid_cells` has 6,129 cells.

The satellite data (2024) and the tracking data (2010–2013) do not overlap in time yet.
To load CHIRPS and MODIS for March–April 2011, run `uv run python examples/athi_kaputiei.py`.
Then the joins in `examples/queries.sql` return rows.

### 5.1 Verified behaviour

These checks ran against the live database with real data:

| Check | Result |
| --- | --- |
| Same Sentinel-2 item again | No download, no append. 2.6 s. |
| New Sentinel-2 date | Same series, next version |
| Two processing runs of one acquisition (2024-02-02) | Both stored. The current view has one row per cell, variable and day. |
| MODIS after Sentinel-2 | Same table, separate series and catalog row. The Sentinel-2 versions did not change. |
| Pinned version 1 after later appends | Returns only the 2,337 rows of version 1 |
| `as_of` cutoff 2024-02-20 | Returns the 02-02 and 02-17 items. It excludes 03-03. |
| Sentinel-2 NDVI against MODIS NDVI per cell | Correlation 0.71 over 423 cells |
| Same Movebank package again | No download, no append. 2 s. |
| Duplicate keys in `current_cell_observations` | 0 |

### 5.2 Data corrections

- The first Sentinel-2 ingests set `available_at` to the download time. The code now uses `s2:generation_time`.
  A one-off SQL update corrected the 9,534 stored Sentinel-2 rows and their `raw_manifest`. No other observation row was ever changed.
- Migration `006` adds `source_item_id` as the last tie-breaker of the current view. It was applied with the pipeline connection, so the Supabase migration history does not show it.
- Migration `007_raw_artifacts` adds the manifest table of the archive. It is in the Supabase migration history.
- The Movebank series in the live database has the id `movebank--movebank-data-repository--ease2-global-1km`, from before the merge. The source is now `movebank_repository`. A new ingest of that package writes to `movebank_repository--movebank-data-repository--ease2-global-1km`.

## 6. Gaps against the README v1 contract

Other lanes must know these gaps before they integrate.

| README requirement | Status here |
| --- | --- |
| `run(request) -> response` adapter with `contract_version`, `request_id`, `status` | Built: `habitat.fetch.run.run` (fetch only) and `habitat.pipeline.run` (fetch to publish). |
| Input is a `QuerySpec` with GeoJSON region | Partly. The deterministic path reads `requirements.bbox`. The agent reads the question text. GeoJSON regions are not read. |
| `DatasetVersion.columns` (name, type, unit, role) | Not filled. The column contract is in this document and in the migrations. |
| `ValidationReport` and `validation_report_ref` | Not built. `validation_report_ref` is null. |
| Quarantined records stay discoverable | Partly. The raw file and its manifest stay in the archive and `raw_artifacts`. The reason is in the outcome. No quarantine row is written. |
| `resolve_artifact(storage)` adapter | Built for raw files: `habitat.archive.Archive.resolve`. Normalized data: read the views with SQL. |
| Portable Parquet export | Not built. |
| Raw artifacts in object storage | Not built. `ArtifactStore` is the interface; only `LocalArtifactStore` exists. Raw files are on the disk of the machine that ran the ingest. |
| Durable job table and scheduled appends | Not built. Each ingest is a manual run. |
| Coordinator → Fetch with "missing requirements" | Partly. The agent asks for a missing area or date in its summary. There is no structured "missing requirements" field. |

## 7. Open decisions

1. Grid: EASE-Grid 2.0 1 km (built) or H3 resolution 8.
2. The `valid_fraction` threshold (0.5 now).
3. The first version of the tag vocabulary.
4. The target species and study for the demo.
5. Whether a request with a larger area must extend a scene that the series already holds (see `MERGE_NOTES.md`).

## 8. Files to read

| Path | Content |
| --- | --- |
| `migrations/001`–`007` | Roles, catalog, `cell_observations` and views, `animal_locations`, indexes, tie-breaker, `raw_artifacts` |
| `src/habitat/contracts.py` | `FetchRequest`, `FetchResponse`, `RawManifest`, `SourceItem`, `DatasetVersion`, `SearchFilters`, Arrow schemas |
| `src/habitat/pipeline.py` | Entry point: fetch, archive, normalize, append, publish |
| `src/habitat/sources.py` | Source registry: connector, normalizer, storage format |
| `src/habitat/archive/` | Artifact store, `raw_artifacts` index, path safety |
| `src/habitat/fetch/` | Connectors, Claude fetch agent, tools, receipts |
| `src/habitat/storage/series.py` | Batches, versions, `COPY` of rows |
| `src/habitat/catalog/` | Search, tags, taxon resolution, publish |
| `examples/athi_kaputiei.py` | Real example: tracking, rainfall and vegetation for one area |
| `examples/queries.sql` | Join examples for the Recipe lane |

## 9. Movebank: two sources, one animal id

The merge keeps two Movebank sources. They are different products:

| Source | Access | Content |
| --- | --- | --- |
| `movebank_repository` | Public. No account. | Published data packages with a DOI, a license and a citation |
| `movebank_study` | Movebank account for full data. Some studies also need the owner's permission or a license acceptance. | All studies that the account can download, including data after publication |

Rules:

1. Both sources write CSV with `properties.study_id`. Thus `entity_id = movebank:<study_id>:<local name>` is the same for one animal from either source.
2. Use the repository first when it has the study. It gives a stable DOI, a license and a citation.
3. The two sources are two series in `animal_locations`: `movebank_repository--movebank-data-repository--…` and `movebank_study--movebank-direct-read--…`.
4. A fix from both sources has the same `event-id` when the direct-read export includes it. `current_animal_locations` keeps one row per `entity_id`, `observed_at` and `sensor_type`.
5. Credentials stay in `.env`. They are never written to a manifest.
6. Authenticated study downloads have `access_scope = "movebank-account"`. The public agent handoff excludes them.

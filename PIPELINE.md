# Habitat pipeline: fetch, archive, normalize, publish

This document describes the `habitat` package. It replaces `DESIGN.md`, `PIPELINE.MD` and `FETCH_INTEGRATION.md`.
The contract rules in `README.md` still apply. This document adds to them.
For the tables and views that other lanes read, see `INTEGRATIONS.md`. For each source, see `SOURCES.md`.

## 1. Data flow

```text
FetchRequest
  -> fetch (deterministic connector, or the Claude fetch agent)
  -> archive (raw files on disk, one manifest per item in the raw_artifacts table)
  -> normalize (one normalizer per source, from the source registry)
  -> append (one batch per item, one new series version per append)
  -> publish (one catalog row per series version, with tags)
  -> PostgreSQL tables and views -> Recipe lane
```

| Step | Module | Output |
| --- | --- | --- |
| Fetch | `habitat.fetch.run`, `habitat.fetch.connectors.*` | `FetchResponse` with a list of `RawManifest` |
| Archive | `habitat.archive` | Files at `data/raw/<artifact_id>/<version>/`, a row in `raw_artifacts` |
| Normalize | `habitat.normalize.router`, `habitat.normalize.sources.*` | `NormalizedBatch` (Arrow table) |
| Append | `habitat.storage.series` | Rows in `cell_observations` or `animal_locations` |
| Publish | `habitat.catalog.publish` | Row in `datasets`, rows in `dataset_tags` |
| Orchestration | `habitat.pipeline` | `PipelineResult` and the `habitat` command |

## 2. Setup

1. Install `uv`. The project pins Python 3.12 in `.python-version`.
2. Copy `.env.example` to `.env` at the repository root. Fill in the values.
3. Run `uv sync`.
4. Run `uv run pytest`.

| Variable | Use |
| --- | --- |
| `HABITAT_DATABASE_URL` | Supabase session pooler string, port 5432. The writer uses `COPY` and session settings. |
| `HABITAT_DATA_DIR` | Optional. Root of the raw archive and run reports. The default is `<repo>/data`. |
| `ANTHROPIC_API_KEY` | The fetch agent (`--question`) and the catalog tags (`--ai-tags`) |
| `MOVEBANK_USERNAME`, `MOVEBANK_PASSWORD` | Optional. Authenticated Movebank direct-read downloads |

`habitat.config.settings()` reads `.env` once, on first access. It does not read `.env` at import.
A value in the process environment wins over the value in `.env`.
A test can change a value with `habitat.config.configure(data_dir=tmp_path)`.

## 3. Command line

The deterministic path runs one connector. It needs no LLM.

```bash
uv run habitat sentinel2 --bbox 36.8,-1.6,37.1,-1.3 --start 2024-02-15 --end 2024-02-20
uv run habitat modis_mod13q1 --bbox 36.8,-1.6,37.1,-1.3 --start 2024-02-01 --end 2024-02-28
uv run habitat chirps --bbox 36.8,-1.6,37.1,-1.3 --start 2024-02-17 --end 2024-02-18
uv run habitat movebank_repository --package 5b6706c8-e7e5-46e4-82ba-da5a82324298
uv run habitat movebank_study --package movebank:2911040
uv run habitat sentinel2 --bbox 16.0,-19.1,16.3,-18.9 --start 2024-03-09 --end 2024-03-09 --ai-tags
```

The agent path gives a question to the Claude fetch agent:

```bash
uv run habitat --question "Find rainfall and vegetation for bbox 36.8,-1.6,37.1,-1.3 from 2024-02-15 to 2024-02-20"
```

- Coordinates are west, south, east, north in WGS84 degrees. Dates are inclusive `YYYY-MM-DD` days.
- The command prints the fetch status, the warnings, one line per artifact, and each published series.
- The command exits with code 2 when the fetch status is `insufficient_data` or `error`.
- `examples/athi_kaputiei.py` runs four sources for one area in Kenya.

From Python:

```python
from habitat.pipeline import build_request, run

request = build_request("chirps", (36.8, -1.6, 37.1, -1.3), date(2024, 2, 17), date(2024, 2, 18), None, None)
result = run(request, use_agent=False)
```

## 4. Contracts

All models are in `habitat.contracts`. The contract version is `1.0`.

### 4.1 Request and response

- `FetchRequest` holds a `QuerySpec` and `FetchRequirements`.
- `FetchRequirements.source_ids` names the sources of the deterministic path. When it is empty, the deterministic path searches the catalog.
- `FetchRequirements.package` names one dataset of a source: a package UUID, a study id, a Zenodo record or a fixture id.
- `FetchResponse.output.raw_artifacts` always exists. It can be empty.

| Status | Meaning for the consumer |
| --- | --- |
| `ok` | Retrieval finished within the limits. It is not proof that the data answers the question. |
| `partial` | Some files exist. Read the warnings: gaps, previews, truncation, or an agent failure after a download. |
| `insufficient_data` | No usable retrieval. This is not evidence that an animal is absent. |
| `error` | Read `error.code`, `error.message` and `error.retryable`. |
| `pending` | Reserved. No code makes it now. |

`extensions.coverage_verified` is always `false`. The fetcher compares declared bounds only. It does not check pixels or tracks.
The pipeline never publishes after `insufficient_data` or `error`.

### 4.2 RawManifest

Each connector makes one `RawManifest` per source item: one scene, one rainfall day, or one tabular file.

- `storage.uri` is always `artifact://<artifact_id>/<version>`. It is never a file path. The model rejects other values.
- `checksum` is `sha256:<hex>` per file, sorted by file name, joined by `,`.
- `extensions` is a typed `SourceItem`.

| `SourceItem` field | Meaning |
| --- | --- |
| `source_id` | Key in the source registry |
| `product`, `source_item_id` | Provider product and item, for example the STAC item id |
| `source_key` | The archive cache key. One key gives one archived artifact. |
| `kind` | `raster` or `tabular` |
| `time_start`, `time_end`, `time_precision` | The period that the item covers |
| `available_at` | When the value became public. Recipe uses it to prevent leakage. |
| `processing_version`, `product_status` | Provider version, and `final` or `preliminary` |
| `assets` | Canonical name to file name in the artifact folder, for example `{"red": "B04.tif"}` |
| `properties` | Source facts, for example `boa_add_offset`, `study_id`, `requested_bbox` |

## 5. Archive

### 5.1 Rules

- Raw files are the source of truth. The normalized tables are derived and can be rebuilt.
- Postgres keeps manifests only. Raw data never goes into Postgres.
- `LocalArtifactStore` keeps the layout `data/raw/<artifact_id>/<version>/<file>`. One artifact can hold more than one file.
- `ArtifactStore` is a protocol. Another store, for example object storage, can replace the local store later.

### 5.2 Write and read

1. A connector downloads into `data/staging/`.
2. `Archive.put` takes a lock, selects the next free version, and writes each file to `<name>.part`.
3. The store renames each `.part` file to its final name.
4. The connector builds the manifest and calls `Archive.record`. This writes the row to `raw_artifacts`.
5. Before normalize, the pipeline calls `Archive.resolve`. This compares the checksum of the files with the manifest.

- A folder that holds a `.part` file is not complete. The cache does not use it.
- A changed byte gives `ChecksumMismatch`. The pipeline quarantines the item.
- A corrupt cached artifact is downloaded again as a new version. The `raw_artifacts` row then points to the new version.

### 5.3 Safety

- `safe_name(value)` keeps only the base name of a remote file name. It rejects an empty name, `.`, `..` and names that end with `.part`.
- Every remote file name, every `artifact_id` and every version goes through `safe_name`.
- After a path join, the store resolves the path and requires it inside the archive root. If not, it raises `ValueError`.
- `habitat.fetch.http.download` goes only to the HTTPS hosts of the connector. This applies to every redirect too.
- The STAC connector reads only assets from the Planetary Computer storage hosts.
- Each download has a byte limit. The download stops and deletes the partial file when the limit is exceeded.

### 5.4 Cache and idempotency

- `Archive.cached(source_key)` returns the archived manifest when its files are complete and unchanged.
- The `raw_artifacts` table has a unique index on `source_key`.
- Before a download, the deterministic connectors ask the series whether it already holds the item (`habitat.archive.index.ingested`).
- The pipeline asks the same question again before normalize. A repeated run downloads nothing and appends nothing.

## 6. Source registry

`habitat.sources.SOURCES` has one `Source` entry per `source_id`:

| Field | Meaning |
| --- | --- |
| `product`, `description`, `data_kinds` | Catalog text and search keys |
| `fetch` | The connector |
| `normalizer` | The normalizer, or `None` |
| `storage_format` | The only `storage.format` that the normalizer accepts |

| `source_id` | Normalizer | Storage format |
| --- | --- | --- |
| `sentinel2` | `normalize_sentinel2` | `geotiff` |
| `modis_mod13q1` | `normalize_modis` | `geotiff` |
| `chirps` | `normalize_chirps` | `tif.gz` |
| `movebank_repository` | `normalize_movebank` | `csv` |
| `movebank_study` | `normalize_movebank_study` | `csv` |
| `zenodo` | none | any |
| `fixture` | none | `csv` |

The router quarantines an item when:

- the `source_id` is not in the registry,
- the source has no normalizer (`zenodo`, `fixture`),
- the storage format is not the format of the source,
- the normalizer cannot read the item without a guess.

A quarantined item stays in the archive. The pipeline reports the reason in its outcome. It writes no rows.

## 7. Fetch agent

- The agent uses the tool runner of the Anthropic SDK (`client.beta.messages.tool_runner`).
- The model is `habitat.llm.FETCH_MODEL` (`claude-sonnet-5-5`). The catalog assistant uses `CATALOG_MODEL` (`claude-opus-5-5`).
- The system prompt is `FETCH_INSTRUCTIONS` in `habitat.fetch.agent`.
- The tools are in `habitat.fetch.tools`: `search_catalog`, `inspect_source`, `check_access`, `download_dataset`, `fetch_environment`, `list_downloaded_files`.
- The tool loop stops after `MAX_ITERATIONS` (20) requests. A run that reaches the limit returns `partial` with a warning.
- The response holds only the artifacts that the tools of this request recorded. Text from the agent is never an artifact.
- Downloads before an agent failure stay in the response. The status is then `partial`.
- Authenticated Movebank downloads have `access_scope = "movebank-account"`. They stay out of the public handoff.
- On the agent path the request has no bbox. The pipeline then uses the `requested_bbox` that the connector wrote into the manifest.

## 8. Analysis grid

- One global grid: EASE-Grid 2.0 Global, 1 km, `EPSG:6933`. The definition is `contracts/grid.json`.
- `cell_id` has the form `E1K-r{row}-c{col}`. It comes from the grid definition only.
- Rainfall rows, vegetation rows and animal fixes for the same place have the same `cell_id`.
- Do not use a grid per Sentinel-2 tile. Tiles use UTM zones, so cells per tile do not align at zone boundaries.

## 9. What we precompute per source

Precompute only the values that a source gives directly or by simple band math. Compute other indices from the raw files on demand.

### 9.1 Sentinel-2 L2A

The connector reads only the AOI window of each remote COG. It writes a clipped GeoTIFF per band: B03, B04, B08, B11, SCL.

1. Resample B11 (20 m) and SCL (20 m) onto the B04 pixels with nearest neighbour.
2. Convert digital numbers to reflectance: `(DN + BOA_ADD_OFFSET) / 10000`. Baseline 04.00 and later uses an offset of −1000. Older baselines use 0. The connector writes the offset into `properties.boa_add_offset`.
3. Mask the pixels where SCL is 0, 1, 3, 8, 9, 10 or 11: nodata, saturated, cloud shadow, cloud, cirrus and snow.
4. Compute the indices on the valid pixels and aggregate each index to each grid cell.

| Variable | Formula |
| --- | --- |
| `ndvi` | (B8 − B4) / (B8 + B4) |
| `mndwi` | (B3 − B11) / (B3 + B11) |
| `ndmi` | (B8 − B11) / (B8 + B11) |

Statistics per cell, index and scene: `mean`, `std`, `valid_fraction`, `pixel_count`.
If `valid_fraction` is less than 0.5, `value` is null. It is not zero.
`available_at` is `s2:generation_time` from the STAC item.

### 9.2 MODIS MOD13Q1

1. The connector keeps Terra items (`MOD13Q1.`) only. The collection also holds Aqua items with windows offset by 8 days.
2. The connector clips NDVI, EVI and pixel reliability to the AOI.
3. Apply the scale `raw × 0.0001`. Mask the fill value −3000.
4. Keep only pixels with `pixel_reliability` 0 (good) or 1 (marginal).
5. Aggregate to each grid cell with the same statistics as Sentinel-2.

The time is the 16-day composite window, with `time_precision = composite`.

### 9.3 CHIRPS daily

1. The connector downloads the final file when it exists. Otherwise it downloads the preliminary file.
2. The archive keeps the `.tif.gz` file as downloaded. The normalizer reads it through `/vsigzip/`.
3. Each 1 km cell gets the value of the CHIRPS pixel that contains the cell centre. Nodata (−9999) is masked.
4. The row is `rainfall_mm`, `stat = sum`, for one UTC day.

CHIRPS pixels are about 5.5 km wide, so about 25 cells share one value. Each row stores `source_resolution_m = 5566`.
CHIRPS is quasi-global, so the normalizer needs an AOI.

### 9.4 Movebank

- `movebank_repository` reads published Data Repository packages. A CSV is a location file when its header has `timestamp`, `location-long` and `location-lat`. The reference file goes into the same artifact.
- `movebank_study` reads one study from the direct-read API. Without credentials, it reads a small public preview.
- Both sources write `properties.study_id`. Thus `entity_id = movebank:<study_id>:<local name>` is the same for one animal from either source.
- The direct-read CSV uses underscores in column names. The adapter changes them to the repository names. The preview has no event ids, so the adapter makes a deterministic `synthetic:<hash>` id.
- Movebank marks outliers with `visible = false`. The normalizer keeps these fixes with `quality_flag = 'marked_outlier'`.

## 10. One output shape for every source

Gridded sources write rows to `cell_observations`. Animal tracking data goes to `animal_locations`.
The next lane writes SQL against these tables and their views. It does not read files.

### 10.1 Common core

| Column | Meaning |
| --- | --- |
| `cell_id` | Cell on the shared grid |
| `time_start`, `time_end`, `time_precision` | The period that the value covers: `instant`, `day`, `composite` or `static` |
| `available_at` | When the value became public |
| `source_id`, `source_item_id` | Source and item that produced the row |
| `processing_version`, `product_status` | Provider version, `final` or `preliminary` |
| `mapping_version` | Version of the normalizer code |
| `dataset_id` | The series, for example `chirps--chirps-v2.0-daily-p05--ease2-global-1km` |
| `quality_flag` | `ok`, `low_valid_fraction`, `preliminary`, `marked_outlier` |

### 10.2 Measurement columns of `cell_observations`

| Column | Meaning |
| --- | --- |
| `variable` | `ndvi`, `mndwi`, `ndmi`, `evi`, `rainfall_mm` |
| `stat` | `mean` or `sum` |
| `value`, `std` | The measurement and the spread inside the cell. Both can be null. |
| `unit` | `index` or `mm` |
| `valid_fraction`, `pixel_count` | Share of the cell with valid pixels, and their count |
| `source_resolution_m` | Native pixel size of the source |

The `source_id` column separates Sentinel-2 NDVI from MODIS NDVI. The two values are not interchangeable.

## 11. Catalog and search

- Publish writes one `DatasetVersion` per new series version: footprint, time range, variables, species and tags.
- Deterministic tags come from code: `sensor_type`, `temporal_resolution`, `study_design`, `variable`, `year`, and region layers.
- AI tags come from Claude. The value must be in `contracts/tag_vocabulary.json`. An AI tag never replaces a deterministic tag with the same key.
- Claude writes tags again only when the footprint grows by more than 10 % or the period reaches a new calendar quarter.
- `search_datasets(filters)` filters by scope, family, region, period, species, variables and tags. It sorts by `spatial_overlap × temporal_overlap`, then by matched tags.
- Species match is exact on the GBIF backbone key. "Antelope" is not one taxon, so the user must select species.
- `CatalogAssistant.parse_question` turns a question into filters. Code validates the result. Claude never writes SQL.

## 12. Appends and versions

- A dataset is a growing series for one source product on one grid. New items go into the same series.
- One item is one batch. The batch key is `source_id|source_item_id|processing_version|product_status|mapping_version`.
- A batch that exists already does nothing. Retries are safe.
- One append is one transaction. The writer locks the `series` row.
- An append makes a new version. Version N holds each batch with `added_in_version <= N` that no version up to N replaced.
- The rows stay. Views select the current row:

| Case | Rule of the current view |
| --- | --- |
| Two tiles cover a cell on one day | Keep the row with the highest `valid_fraction` |
| CHIRPS preliminary, then final | Keep the final row |
| Reprocessing with a new baseline | Keep the newest `processing_version` |
| Normalizer fix | Rebuild with a new `mapping_version`; the new batch supersedes the old batch |

`current_cell_observations_at(series_id, version, cutoff)` returns the current view at a pinned version, with only the values public at `cutoff`.

## 13. Tests

| Command | Content |
| --- | --- |
| `uv run pytest` | Unit tests and database tests |
| `HABITAT_LIVE_TESTS=1 uv run pytest -m live -s` | Real providers and a real Claude call |

- Unit tests make no network calls. An autouse fixture makes every HTTP call fail. Tests use `httpx.MockTransport`.
- The agent tests use the real SDK tool runner against a fake Messages API (`tests/fake_claude.py`).
- Each test writes to its own `tmp_path`. The project `data/` folder is read only.
- Database tests make a throwaway schema, apply all migrations, and drop the schema. They skip when `HABITAT_DATABASE_URL` is not set.
- Live tests write only to the throwaway schema, never to the live tables.

## 14. Code layout

| Path | Content |
| --- | --- |
| `src/habitat/config.py` | `Settings` from `.env` |
| `src/habitat/contracts.py` | Request, response, manifest, catalog models and Arrow schemas |
| `src/habitat/llm.py` | Anthropic client and model names |
| `src/habitat/sources.py` | Source registry |
| `src/habitat/pipeline.py` | End-to-end run and the `habitat` command |
| `src/habitat/archive/` | `ArtifactStore`, `LocalArtifactStore`, the `raw_artifacts` index, path safety |
| `src/habitat/fetch/connectors/` | `stac`, `chirps`, `movebank_repository`, `movebank_study`, `zenodo`, `fixture` |
| `src/habitat/fetch/` | Agent, tools, service, session receipts, catalog search, coverage checks, HTTP helper |
| `src/habitat/normalize/` | Index math, block-wise zonal statistics, per-source normalizers, router |
| `src/habitat/storage/series.py` | Append-only batches and versions, `COPY` of rows |
| `src/habitat/catalog/` | Footprints, tags, Claude tags and question parsing, GBIF taxa, search, publish |
| `migrations/` | `001`–`007`. `007_raw_artifacts.sql` adds the manifest table. |
| `contracts/` | Grid definition and tag vocabulary |
| `examples/` | Athi-Kaputiei example and join queries |

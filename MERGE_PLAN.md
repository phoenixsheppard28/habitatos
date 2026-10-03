# Merge plan: fetch_pipeline + processing_pipeline → one `habitat` package

This document is the task specification for one agent. Do the phases in order.
Do not start a phase until the tests of the previous phase pass.

## 1. Goal

Merge `fetch_pipeline/` (package `fetch`) and `processing_pipeline/` (package `habitat`) into one package.
The data flow is: fetch gets raw data → archive keeps the raw files → processing normalizes and publishes.

The goal is complete when all items in [section 9](#9-definition-of-done) are true.

## 2. Decisions (already made)

| Topic | Decision |
| --- | --- |
| Package name | `habitat`. The `fetch` package becomes `habitat.fetch`. |
| Tooling | `uv`, Python 3.12, hatchling. Remove the setuptools/pip setup. |
| LLM provider | Anthropic SDK only. Remove `openai-agents` and `openai`. Remove all OpenRouter code and env variables. |
| API key | `ANTHROPIC_API_KEY` from the root `.env`. The key exists there now. |
| Models | Fetch agent: `claude-sonnet-5-5`. Catalog assistant (`catalog/ai.py`): keep `claude-opus-5-5`. Put both constants in `habitat/llm.py`. |
| Contract | One `RawManifest`. Extensions are a typed `SourceItem`. Keep `FetchRequest` / `FetchResponse` from `fetch/models.py`. |
| Archive | Use the logic of `fetch/archive.py` (`artifact://id/version`, sha256, cache by `source_key`, lock). Put it behind an `ArtifactStore` interface. Implement only `LocalArtifactStore` now. |
| Archive index | Replace `artifact_index.json` with a Postgres table `raw_artifacts`. |
| Raster size | Clip Sentinel-2 and MODIS to the AOI bbox at fetch time. Use a windowed COG read. Do not download full tiles. |
| Raw data in Postgres | Never. Postgres keeps manifests only. Rasters stay in the archive. |
| S2 / MODIS / CHIRPS connector | Start from `processing_pipeline/src/habitat/fetch/stac.py` and `chirps.py`. Add the request validation, bounds, and limits from `fetch_pipeline/src/fetch/connectors/environment.py`. |
| Movebank | Keep both. They are different sources. Data Repository packages → `source_id="movebank_repository"`. Direct-read API by study → `source_id="movebank_study"`. |
| Source IDs | `sentinel2`, `modis_mod13q1`, `chirps`, `movebank_repository`, `movebank_study`, `zenodo`, `fixture`. Remove `sentinel-2` and `modis`. |

If a decision is not possible, stop. Write the reason in `MERGE_NOTES.md`, then continue with the next independent phase.

## 3. Current state

### 3.1 Duplicated code

| Concern | `fetch_pipeline/src/fetch/` | `processing_pipeline/src/habitat/` |
| --- | --- | --- |
| Manifest models | `models.py` (dict extensions, string dates) | `contracts.py` (typed `SourceItem`, datetimes) |
| Raw archive | `archive.py` (full) | `fetch/archive.py` (minimal `RawArchive`) |
| Sentinel-2 / MODIS | `connectors/environment.py` (one artifact per band) | `fetch/stac.py` (one manifest per scene) |
| CHIRPS | `connectors/environment.py` (final only) | `fetch/chirps.py` (final and preliminary) |
| Dedup | `source_key` / checksum in a JSON index | `already_ingested(...)` against Postgres series |
| Env loading | `load_dotenv()` in the CLI; `paths.py` reads `HABITAT_DATA_DIR` at import | `db.py` loads `.env` |
| Source metadata | `PRODUCTS` in `environment.py` | `PRODUCTS`, `DESCRIPTIONS` in `ingest.py`; `NORMALIZERS` in `normalize/router.py` |

### 3.2 The main gap

`normalize.router.normalize()` requires `manifest.extensions` to be a `SourceItem`.
Fetch connectors return a plain dict without `source_id`, `time_start`, `time_end`, `time_precision`, `available_at`, `processing_version`, `product_status`, or `assets`.
Fetch also splits one Sentinel-2 scene into seven artifacts.
Each connector must therefore emit one manifest per scene or file, with a complete `SourceItem`.

### 3.3 Known defects to fix during the merge

1. **Path traversal.** `processing_pipeline/src/habitat/fetch/movebank.py` uses `bitstream["name"]` from the remote API as a filename. `RawArchive.download` writes to `root / artifact_id / filename` without a containment check.
2. **Import-time config.** `fetch/paths.py` reads `HABITAT_DATA_DIR` at import. A value in `.env` has no effect.
3. **Missing archive.** `fetch_pipeline/data/` does not exist. Do not search for it. The fixture tests must create their own data.

## 4. Target layout

```text
habitatos/
  pyproject.toml
  uv.lock
  .python-version          # 3.12
  .env                     # gitignored
  .env.example
  README.md                # product specification; keep
  PIPELINE.md              # merged from DESIGN.md, PIPELINE.MD, FETCH_INTEGRATION.md
  SOURCES.md               # from fetch_pipeline/src/fetch/SOURCES.md
  contracts/               # grid.json, tag_vocabulary.json
  migrations/              # 001..006 + 007_raw_artifacts.sql
  examples/
  data/                    # gitignored runtime data
  tests/
    fixtures/              # from fetch_pipeline/tests/fetch/fixtures
    ...
  src/habitat/
    __init__.py
    config.py              # Settings: database URL, data dir, API keys, Movebank credentials
    contracts.py           # RawManifest, SourceItem, FetchRequest, FetchResponse, ...
    llm.py                 # Anthropic client factory + model constants
    sources.py             # Source registry
    db.py
    grid.py
    pipeline.py            # end-to-end orchestration + CLI
    archive/
      __init__.py
      store.py             # ArtifactStore protocol, LocalArtifactStore
      index.py             # raw_artifacts table access
      paths.py             # safe name and containment helpers
    fetch/
      __init__.py
      agent.py             # Anthropic tool runner
      tools.py             # @beta_tool wrappers
      service.py
      session.py
      catalog.py
      coverage.py
      http.py              # one httpx-based helper module
      connectors/
        stac.py            # sentinel2, modis_mod13q1
        chirps.py
        movebank_repository.py
        movebank_study.py
        zenodo.py
        fixture.py
    normalize/  storage/  catalog/   # unchanged structure from processing_pipeline
```

Delete `fetch_pipeline/`, `processing_pipeline/`, and `FETCH_INTEGRATION.md` at the end.
Move `processing_pipeline/data/` to `data/` with `mv`. Git ignores this folder, so `git mv` does not apply. Do not delete raw data.

## 5. Shared infrastructure specification

### 5.1 `config.py`

- Define one `Settings` object. Load the root `.env` once, on first access, not at import.
- Fields: `database_url`, `data_dir` (default `<repo>/data`), `anthropic_api_key`, `movebank_username`, `movebank_password`.
- All modules get configuration from `Settings`. Remove other `load_dotenv` calls.
- Tests must be able to set `data_dir` to `tmp_path`.

### 5.2 `contracts.py`

- Start from `processing_pipeline/src/habitat/contracts.py`.
- Add `FetchRequest`, `FetchResponse`, `QuerySpec`, `FetchRequirements`, `FetchError`, `TimeRange` from `fetch_pipeline/src/fetch/models.py`.
- `RawManifest.storage.uri` is always `artifact://<artifact_id>/<version>`. Never a filesystem path.
- `SourceItem.assets` maps canonical names to filenames relative to the artifact directory. Example: `{"red": "B04.tif"}`.
- Add `source_key: str` to `SourceItem`. The archive cache uses this value.
- Keep `contract_version = "1.0"`.

### 5.3 `archive/`

- `ArtifactStore` protocol: `put(artifact_id, version, files) -> RawManifest storage`, `open(manifest, asset) -> Path`, `exists(artifact_id, version)`.
- `LocalArtifactStore` keeps the layout `data/raw/<artifact_id>/<version>/<filename>`.
- One artifact can hold more than one file. A Sentinel-2 scene is one artifact with five files.
- Write each file to `<name>.part` first, then rename.
- `paths.safe_name(value)`: keep only `Path(value).name`. Reject an empty value, `.`, and `..`. Apply to every remote filename and every `artifact_id`.
- After you join a path, resolve it and require `is_relative_to(archive_root)`. Raise `ValueError` if not.
- Checksum: `sha256:<hex>` per file. Manifest `checksum` holds the files in a stable order, joined by `,`.
- Normalizers resolve asset paths only through the store. GDAL paths such as `/vsigzip/` are built in the normalizer, not stored in the manifest.

### 5.4 `migrations/007_raw_artifacts.sql`

Columns: `artifact_id`, `version`, `source_id`, `source_item_id`, `source_key`, `processing_version`, `product_status`, `checksum`, `storage_uri`, `access_scope`, `retrieved_at`, `manifest jsonb`.
Primary key: `(artifact_id, version)`. Unique index on `source_key`.
Follow the roles and grants pattern of `001_roles.sql` and the other migrations.

`archive/index.py` replaces `artifact_index.json`:
- `find_by_source_key(source_key) -> RawManifest | None`
- `record(manifest)`
- `ingested(...)`: keep the existing `already_ingested` check from `ingest.py`. Fetch connectors call it before a download.

Apply the migration to the live Supabase database in this order:

1. Run the database tests. The test fixture applies 007 to a throwaway schema. All tests must pass.
2. Apply `007_raw_artifacts.sql` to the live database with the Supabase MCP tool `apply_migration`. Use the name `007_raw_artifacts`.
3. Verify with `list_migrations` and `list_tables`. Run `get_advisors` for security, and fix any new finding on `raw_artifacts`.
4. Record the result in `MERGE_NOTES.md`.

Migration 007 only adds a table. It must not change or drop the objects from migrations 001–006.

### 5.5 `sources.py`

One registry entry per `source_id`:

```python
@dataclass(frozen=True)
class Source:
    source_id: str
    product: str
    description: str
    data_kinds: frozenset[str]
    fetch: Connector
    normalizer: Normalizer | None
    storage_format: str
```

- `normalize/router.py`, `ingest.py`, and the agent tools read from this registry.
- `zenodo` and `fixture` have `normalizer=None`. The pipeline quarantines them with a clear reason.

### 5.6 `llm.py` and the fetch agent

- `llm.py`: `client() -> anthropic.Anthropic`, `FETCH_MODEL = "claude-sonnet-5-5"`, `CATALOG_MODEL = "claude-opus-5-5"`.
- `catalog/ai.py` uses `llm.client()` and `CATALOG_MODEL`.
- `fetch/tools.py`: change `@function_tool` to `@beta_tool` from the Anthropic SDK. Keep names, arguments, and docstrings.
- `fetch/agent.py`: use `client.beta.messages.tool_runner(...)`. Keep `FETCH_INSTRUCTIONS` as the system prompt. Update the line about Movebank IDs to the new source IDs.
- `run.run_with_agent` keeps its signature and its `FetchResponse` output. `session.py` receipts stay unchanged.
- Set a maximum number of tool-loop iterations. A run that reaches the limit returns `status="partial"` with a warning.
- Before you write the agent code, read the current Anthropic SDK docs for `tool_runner` and `beta_tool`. Do not guess the API.

### 5.7 Connectors

All connectors return `list[RawManifest]` with a complete `SourceItem`, or a typed error.

| Connector | Base | Required changes |
| --- | --- | --- |
| `stac.py` | processing `fetch/stac.py` | Add `validate_request` and limits from `environment.py`. Read a window for the AOI bbox from each remote COG. Write a clipped GeoTIFF. Keep `boa_add_offset`, `published_at`, `modis_processing_version`. Keep the Terra-only filter. |
| `chirps.py` | processing `fetch/chirps.py` | Store the `.tif.gz` as downloaded. Keep the final/preliminary logic. Add the date limit from `environment.py`. |
| `movebank_repository.py` | processing `fetch/movebank.py` | Use `safe_name`. Store files through the archive. |
| `movebank_study.py` | fetch `connectors/movebank.py` | Emit `SourceItem` with `kind="tabular"`. Keep `movebank_download_mode` in `properties`. Keep the `access_scope` rule for authenticated downloads. Emit CSV only. |
| `zenodo.py` | fetch `connectors/zenodo.py` | Use `safe_name`. `SourceItem.kind="tabular"`. |
| `fixture.py` | fetch `connectors/fixture.py` | Emit `SourceItem`. |

The normalizer for `movebank_study` must accept the CSV shape of the direct-read API. If the shape is the same as the repository CSV, reuse `normalize_movebank`. If not, write an adapter and test it.

### 5.8 `pipeline.py`

```text
run(request: FetchRequest, use_agent: bool) -> PipelineResult
  1. response = fetch.run(request, use_agent)
  2. for each manifest in response.output.raw_artifacts:
       verify checksum through the archive
       skip if raw_artifacts says the item is already ingested
       normalize → append to series (existing ingest_manifest)
  3. publish each changed series (existing publish_series_version)
  4. return fetch status, warnings, outcomes per artifact
```

- Keep the deterministic CLI from `ingest.py` (`source`, `--bbox`, `--start`, `--end`, `--package`). It builds a `FetchRequest` and calls `run(..., use_agent=False)`.
- Add a CLI flag `--question` that runs the agent path.
- `insufficient_data` and `error` statuses never produce a publish.

## 6. Phases

1. **Scaffold.** Create the root `pyproject.toml` with the union of dependencies, minus `openai` and `openai-agents`. Move `processing_pipeline` content to the root layout. `uv sync`. All existing processing tests pass.
2. **Contracts and config.** Add `config.py`, merge `contracts.py`. Fix all imports.
3. **Archive.** Build `archive/`, migration 007, `index.py`. Fix the path traversal defect.
4. **Registry.** Build `sources.py`. Change `normalize/router.py` and `ingest.py` to read from the registry.
5. **Connectors.** Port all connectors as in 5.7. Delete `habitat/fetch/archive.py` (old `RawArchive`).
6. **Agent.** Port `agent.py`, `tools.py`, `run.py`, `session.py`, `service.py`, `catalog.py`, `coverage.py` to Anthropic.
7. **Pipeline.** Build `pipeline.py` and the CLI.
8. **Docs and cleanup.** Merge docs into `PIPELINE.md` and `SOURCES.md` in STE (see `CLAUDE.md`). Update `README.md` links and `INTEGRATIONS.md`. Delete the old folders. Update `.env.example`.

Commit after each phase on branch `psheppard/merge-1-and-2`. Do not push. Do not open a PR.

## 7. Testing

### 7.1 Rules

- Run tests with `uv run pytest`.
- Unit tests make no network calls. Use `httpx.MockTransport` or recorded fixtures.
- Live tests have the marker `@pytest.mark.live`. They run only when `HABITAT_LIVE_TESTS=1`.
- Database tests use the existing throwaway-schema fixture in `tests/conftest.py`. They skip when `HABITAT_DATABASE_URL` is not set.
- LLM tests use a fake Anthropic client by default. One live agent test has the marker `live`.
- You can use the existing files in `data/raw/` as real input: Sentinel-2 scenes, MODIS tiles, and Movebank packages. Examples: compare a clipped output with the full tile, or run a normalizer on a real scene. Read these files only. Write all test output to `tmp_path`. A test that needs `data/raw/` must skip when the files are not there, because `data/` is gitignored.
- Move all tests from `fetch_pipeline/tests/fetch/` and `processing_pipeline/tests/`. Update imports. Do not delete a test unless its subject is deleted. Write the reason in `MERGE_NOTES.md`.

### 7.2 Tests to add

| Area | Test |
| --- | --- |
| config | `.env` value for `data_dir` takes effect. A test can override `data_dir` without import-order problems. |
| contracts | Each connector output passes `RawManifest.model_validate` with a `SourceItem`. `storage.uri` starts with `artifact://`. |
| archive: safety | A remote name `../../evil`, `/etc/passwd`, `..`, and `""` are rejected or reduced to a safe base name. Nothing is written outside `data_dir`. Same for `artifact_id`. |
| archive: integrity | A changed byte after archive gives a checksum mismatch on resolve. A `.part` file from an interrupted download is not treated as complete. |
| archive: cache | The same `source_key` twice → one download, second call returns the cached manifest. |
| archive: index | `raw_artifacts` round trip (DB test). Duplicate `source_key` is rejected. |
| registry | Every `source_id` has a connector. Every source with a normalizer has a matching `storage_format`. No old IDs (`sentinel-2`, `modis`) remain: `grep` in `src/` and `tests/`. |
| stac | The clipped output bounds are inside the requested bbox plus one pixel. The clipped file is a valid GeoTIFF that `rasterio` opens. Use a small local COG fixture made in the test. |
| stac | MODIS search keeps only `MOD13Q1.` items. S2 `boa_add_offset` is correct for baselines before and after 04.00. |
| chirps | Final file wins over preliminary. Preliminary is used when final is missing. Neither present → no manifest. |
| movebank_study | Output CSV goes through the normalizer without quarantine. Authenticated downloads are not in the public handoff. |
| agent | With a fake client that asks for `search_catalog` then `download_dataset`, `run_with_agent` returns `ok` with one artifact. The loop limit returns `partial`. No `openai` or `agents` import remains: `grep`. |
| pipeline | Fixture request → fetch → normalize → series append → publish, on the throwaway schema. Run twice: the second run appends nothing. |
| pipeline | `insufficient_data` response → no publish. Unknown `source_id` → quarantine with a reason. |

### 7.3 Live checks (run once, after phase 7)

Run with `HABITAT_LIVE_TESTS=1`. Record command, result, and duration in `MERGE_NOTES.md`.

1. `sentinel2`, bbox `36.8,-1.6,37.1,-1.3`, `2024-02-15` to `2024-02-20`. Expect at least one scene. Each band file is clipped: check that the archive size is far below the 3.4 GB of full tiles.
2. `modis_mod13q1`, same bbox, `2024-02-01` to `2024-02-28`.
3. `chirps`, same bbox, `2024-02-17` to `2024-02-18`.
4. Agent path: `--question "Find rainfall and vegetation for bbox 36.8,-1.6,37.1,-1.3 from 2024-02-15 to 2024-02-20"`. Expect `ok` or `partial` and real artifacts.

Do not write to the live Supabase database in these checks. Use the throwaway schema.

## 8. Constraints

- Do not commit `.env`, `data/`, or any API key.
- Apply only migration 007 to the live database, as described in 5.4. Do not change migrations 001–006.
- You can read the files under `data/raw/` for tests and checks. Do not delete or change them.
- Follow `CLAUDE.md`: few comments, blank lines between sections in a function body, no more than one blank line.
- Do not add a dependency that is not in this document without a note in `MERGE_NOTES.md`.

## 9. Definition of done

- [ ] `fetch_pipeline/` and `processing_pipeline/` do not exist.
- [ ] One `pyproject.toml` at the root. `uv sync` works on a clean checkout.
- [ ] `uv run pytest` passes. Skips occur only for `live` and missing database URL.
- [ ] With `HABITAT_DATABASE_URL` set, all database tests pass.
- [ ] `grep -rn "openrouter\|OPENROUTER\|openai\|from agents" src tests` returns nothing.
- [ ] `grep -rn "RawArchive\|artifact_index.json" src tests` returns nothing.
- [ ] Every connector emits a `RawManifest` with a `SourceItem` and an `artifact://` URI.
- [ ] Path traversal tests pass.
- [ ] Live checks 1–4 recorded in `MERGE_NOTES.md`.
- [ ] `PIPELINE.md` and `SOURCES.md` describe the merged system. `FETCH_INTEGRATION.md` is deleted.
- [ ] Migration 007 is applied to the live Supabase database and shows in `list_migrations`.
- [ ] `MERGE_NOTES.md` lists open items.

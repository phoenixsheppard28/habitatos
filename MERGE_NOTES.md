# Merge notes

This file records the results, the deviations, and the open items of the merge in `MERGE_PLAN.md`.
Branch: `psheppard/merge-1-and-2`. Date: 2026-10-03.

## 1. Migration 007 on the live database

- The database tests applied 007 to a throwaway schema first. All tests passed (81 passed at that time).
- `apply_migration` with the name `007_raw_artifacts` succeeded.
- `list_migrations` shows `007_raw_artifacts` (version `20261003232000`).
- `get_advisors` (security) returns no findings, before and after the migration.
- `list_tables` was denied by the permission classifier of the agent session. This check is not done (see section 6).
- Finding: the live migration history has no entry for `006_deterministic_current_rows`. The live view `current_cell_observations` and the function `current_cell_observations_at` contain the 006 tie-breaker (`source_item_id DESC`). Thus 006 was applied as plain SQL. The merge did not change this.

## 2. Live checks (section 7.3 of the plan)

Command: `HABITAT_LIVE_TESTS=1 uv run pytest -s -q tests/test_live.py -k <name>`.
All checks wrote to a throwaway schema, not to the live tables. Each test has its own empty archive in `tmp_path`.

| Check | Request | Result | Duration |
| --- | --- | --- | --- |
| 1. `sentinel2` | bbox `36.8,-1.6,37.1,-1.3`, 2024-02-15 to 2024-02-20 | `partial`. One scene (`S2B_MSIL2A_20240217T074009_R092_T37MBU_20240217T113157`), appended and published. Archive 58.4 MB, against 3.4 GB of full tiles. The warnings say that one instant does not cover a 6-day range. | 46.8 s |
| 2. `modis_mod13q1` | same bbox, 2024-02-01 to 2024-02-28 | `ok`. Three Terra composites (A2024017, A2024033, A2024049), appended and published. Archive 0.2 MB. | 10.6 s |
| 3. `chirps` | same bbox, 2024-02-17 to 2024-02-18 | `ok`. Two final daily files, appended and published. Archive 7.8 MB. | 4.6 s |
| 4. Agent | `--question "Find rainfall and vegetation for bbox 36.8,-1.6,37.1,-1.3 from 2024-02-15 to 2024-02-20"` | `partial`. Six CHIRPS days and one MODIS composite, all appended; two series published. Warning: the agent asked for one MODIS scene of two. | 42.7 s |

The first agent run found a defect: on the agent path the request has no bbox, so the pipeline quarantined the CHIRPS days ("an area of interest is required"). The fix: each area connector writes `properties.requested_bbox`, and the pipeline uses it when the request has no bbox. The run above is after the fix.

Coverage checks first ran per file, so two correct CHIRPS days gave `partial`. They now run per source over the union of its files.

## 3. Deviations from the plan

- Phases 4 and 5 are one commit. The registry imports the connectors, and the connectors fix the storage formats that the registry checks. Neither phase builds alone.
- `FetchRequirements` has two new optional fields: `source_ids` and `package`. The deterministic CLI needs them to name one source and one dataset.
- `raw_artifacts` has a second read policy for `habitat_writer`. Readers see only rows with `access_scope = 'public'`.
- `PostgresArtifactIndex.replace` updates the row for a source key. The archive uses it when a cached artifact is corrupt and is downloaded again as a new version. Thus migration 007 grants `UPDATE` to `habitat_writer`.
- `MemoryArtifactIndex` exists next to `PostgresArtifactIndex`. Unit tests and fetch-only runs without a database use it.
- `ArtifactStore` has five more methods than the plan: `verify`, `next_version`, `list_files`, `lock` and `staging`. `Archive` needs them.
- Normalizers take the store: `normalizer(manifest, store, grid, aoi)`. They call `store.open(manifest, asset)`. The CHIRPS normalizer builds the `/vsigzip/` path.
- Storage formats: CHIRPS `tif.gz`; Sentinel-2 and MODIS `geotiff` (clipped GeoTIFF, not COG).
- The pipeline test in section 7.2 ("fixture request → … → publish") uses `movebank_repository` with a recorded-shape package served by `httpx.MockTransport` (`tests/fixtures/movebank_package.py`). Reason: the plan also says that `fixture` has no normalizer, so a `fixture` request can never append. A separate test shows that `fixture` data is quarantined and never published.
- `fetch_environment` (agent tool) changed its docstring: the source names are now `sentinel2`, `modis_mod13q1`, `chirps`, and rasters are clipped. The old docstring named the removed ids.
- The fetch agent sends `output_config={"effort": "medium"}` and server-side refusal fallbacks (`fallbacks="default"`, beta `server-side-fallback-2026-07-01`). A refusal gives the error code `agent_refused`.
- `catalog/taxa.py` uses the shared HTTP helper, so unit tests can block GBIF calls.
- Animal entities keep `source_id="movebank"` for both Movebank sources. Both sources use the entity id `movebank:<study>:<animal>`.
- `movebank_study` emits CSV only. The public preview JSON becomes a CSV with the direct-read column names. The preview has no event ids; the normalizer adapter makes a deterministic id (`synthetic:<hash>`).
- No new dependencies. `httpx2` (used in `tests/fake_claude.py`) comes with `anthropic` 1.x.

## 4. Tests moved, replaced or deleted

| Old test | New place |
| --- | --- |
| `processing_pipeline/tests/*` | `tests/` (imports updated) |
| `processing_pipeline/tests/test_fetch.py` | Deleted. Its subject `habitat.fetch.stac` moved. Both tests are in `tests/test_stac.py`. |
| `fetch_pipeline/tests/fetch/test_archive.py` | `tests/test_archive.py`, `tests/test_fixture_source.py` |
| `fetch_pipeline/tests/fetch/test_catalog.py` | `tests/test_fixture_source.py` |
| `fetch_pipeline/tests/fetch/test_environment.py` | `tests/test_stac.py`, `tests/test_chirps.py`, `tests/test_service.py` |
| `fetch_pipeline/tests/fetch/test_http_util.py` | `tests/test_http.py` (urllib → httpx) |
| `fetch_pipeline/tests/fetch/test_movebank.py` | `tests/test_movebank_study.py`, `tests/test_service.py` |
| `fetch_pipeline/tests/fetch/test_run.py` | `tests/test_run.py` (fake Messages API instead of a patched `Runner`) |
| `fetch_pipeline/tests/fetch/test_zenodo.py` | `tests/test_zenodo.py`, `tests/test_service.py` |
| `fetch_pipeline/tests/fetch/fixtures/*` | `tests/fixtures/` |

One test assertion changed meaning: the old CHIRPS test checked that the manifest bbox is the global CHIRPS bbox. The new manifest keeps that bbox too; the test now checks the gzip bytes and the normalized value.

## 5. Data

- `processing_pipeline/data/` moved to `data/` with `mv`. No raw file was deleted or changed.
- Raw files from before the merge stay in the old layout `data/raw/<item>/<file>`. The new layout is `data/raw/<artifact_id>/<version>/<file>`. The `raw_artifacts` table does not know the old files. Tests only read them.
- The old Sentinel-2 duplicates of 2024-02-02 (about 0.8 GB) are still in `data/raw/`. The plan says not to delete raw data.

## 6. Open items

Checked and closed: the content links of the wildebeest package (`5b6706c8-…`) answer HTTP 200 on `datarepository.movebank.org` with no redirect, so the host check of `movebank_repository` lets them through.


1. Run `list_tables` on the live project and confirm that `raw_artifacts` exists with RLS on. The agent session could not run it.
2. Decide whether to register `006_deterministic_current_rows` in the live migration history. The objects are there; only the history entry is missing.
3. The live Movebank series has the old id `movebank--movebank-data-repository--ease2-global-1km`. New ingests of that package write to `movebank_repository--movebank-data-repository--ease2-global-1km`. Decide to re-ingest, or to rename the old series in a migration.
4. A Sentinel-2 or MODIS source key holds the bbox, because the clipped file depends on it. The series check (`ingested`) uses the item id and the processing version only. Thus a second request with a larger bbox for an ingested scene downloads nothing. Decide if a larger area must extend an ingested scene.
5. A cached CHIRPS day keeps the `requested_bbox` of its first request. On the agent path a later request for another area uses that first area. The deterministic path is not affected, because it uses the request bbox.
6. `movebank_study` downloads go through `http.client` with redirects on. They do not check the redirect host like file downloads do.
7. The old raw files in `data/raw/<item>/` are not in the archive index. Import them, or delete them after a decision.
8. `ArtifactStore` has only a local implementation. Object storage is not built.
9. Quarantined items get no database row. The reason is only in the outcome and the log.

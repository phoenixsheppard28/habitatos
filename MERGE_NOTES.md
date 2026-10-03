# Merge notes

This file records the results, deviations, and open items of the merge in `MERGE_PLAN.md`.

## Migration 007 on the live database

- 2026-10-03: The database tests applied 007 to a throwaway schema. All tests passed (81 passed).
- 2026-10-03: `apply_migration` with the name `007_raw_artifacts` succeeded.
- `list_migrations` shows `007_raw_artifacts` (version `20261003232000`).
- `get_advisors` (security) returns no findings, before and after the migration.
- `list_tables` was denied by the permission classifier of the agent session. The check is not done. Open item: run `list_tables` and confirm `raw_artifacts`.
- Finding: the live migration history has no entry for `006_deterministic_current_rows`. The view `current_cell_observations` and the function `current_cell_observations_at` on the live database do contain the 006 tie-breaker (`source_item_id DESC`). Thus 006 was applied as plain SQL, not as a migration. The merge did not change this.

## Deviations from the plan

- `FetchRequirements` has two new optional fields: `source_ids` and `package`. The deterministic CLI needs them to build a `FetchRequest` for one source or one Movebank package.
- `raw_artifacts` has a second read policy for `habitat_writer`. Readers see only rows with `access_scope = 'public'`.
- `PostgresArtifactIndex.replace` updates the row for a source key. The archive uses it when a cached artifact is corrupt and is downloaded again as a new version.
- `MemoryArtifactIndex` exists next to `PostgresArtifactIndex`. Unit tests and runs without a database use it.

## Open items
- Phases 4 and 5 are one commit. The registry imports the connectors, and the connectors fix the storage formats that the registry checks. Neither phase builds alone.
- Normalizers take the store: `normalizer(manifest, store, grid, aoi)`. They call `store.open(manifest, asset)`. The CHIRPS normalizer builds the `/vsigzip/` path.
- CHIRPS storage format is `tif.gz`. Sentinel-2 and MODIS storage format is `geotiff` (clipped GeoTIFF, not COG).
- A Sentinel-2 or MODIS source key holds the bbox, because the clipped file depends on it. The series dedup check (`ingested`) uses the item id and processing version only. Thus a second request with a different bbox for an ingested scene downloads nothing. Open item: decide if a larger AOI must extend an ingested scene.
- `movebank_study` emits CSV only. The public preview JSON becomes a CSV with the direct-read column names. The preview has no event ids; the normalizer adapter makes a deterministic id (`synthetic:<hash>`).
- Animal entities keep `source_id="movebank"` for both Movebank sources. Both sources use the same entity id `movebank:<study>:<animal>`, so a repository package and a study download describe the same animal.
- `tests/test_fetch.py` is deleted. Its subject (`habitat.fetch.stac`) moved; its two `published_at` tests are in `tests/test_stac.py`.
- Raw files from before the merge stay in the old layout `data/raw/<item>/<file>`. The new layout is `data/raw/<artifact_id>/<version>/<file>`. The archive index does not know the old files. Tests read them only.

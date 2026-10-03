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

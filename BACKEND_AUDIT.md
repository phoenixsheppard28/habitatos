Backend audit — October 3, 2026

The backend audit covers `pipeline/src/habitat` and `fetch_pipeline/src/fetch`. The final combined suite passes 143 tests on Python 3.12 and Python 3.14, with no skipped tests, against a disposable PostgreSQL 16 / PostGIS 3.5 database. Measured statement coverage is 87%, including subprocess CLI execution. This is substantial verification, not a claim of perfection or complete coverage.

The most consequential defect was production ingestion under `habitat_writer`: PostgreSQL forbids direct `COPY FROM` into tables with row-level security for that role. Bulk loads now copy into a temporary staging table and insert into the destination through its policies and constraints. Both raster observations and animal locations are tested as the writer role; reader queries work and reader writes are denied. The writer needs database TEMPORARY privileges, which PostgreSQL grants by default.

Other repaired behavior:

- Fresh migrations create the required `extensions` schema.
- Catalog publication retries after data was committed by a previous run whose publication failed, including runs with no new downloads.
- Ingestion rejects unknown sources, missing source requirements, malformed bounding boxes, and reversed dates before database or network work.
- Memory catalog records preserve published snapshots. Empty optional filters and one-sided time filters behave consistently in memory and PostgreSQL.
- Interrupted archive downloads remove temporary files and preserve earlier successful files. Archive paths reject traversal; malformed optional HTTP dates fall back to unknown.
- HTTP service failures for CHIRPS propagate instead of being treated as missing data. Internally created archive and taxonomy clients close after use.
- Animal normalization preserves numeric-looking identifiers and leading zeroes. Invalid or duplicated identifiers, malformed coordinates/timestamps, and locations outside the grid are quarantined. Missing taxonomy and timezone-bearing deployment dates are handled correctly.
- Taxonomy HTTP outages leave animals unresolved without discarding valid tracking data.
- Fully cloudy raster cells remain null observations. Out-of-grid pixels are excluded, disjoint areas produce empty batches, and mixed or unknown raster coordinate systems are quarantined.
- Environmental download byte budgets count rejected payloads. Empty STAC provider lists no longer crash discovery.
- Saved fetch responses retain the run directory returned to the caller. Malformed query-region bounds produce a coverage warning instead of an indexing error.
- Agent handoffs exclude artifacts whose access scope does not match the request.
- Authenticated Movebank timestamps preserve explicit timezone offsets. Repository packages without original files produce no downloads.
- Zenodo downloads reject restricted records and unavailable or oversized requested files; requesting a missing filename never silently downloads another file.
- Built wheels include contracts, migrations, and demo CSVs, and work outside the source checkout.

Verification performed:

- All six SQL migrations applied to a fresh database.
- 143 combined tests passed on each of Python 3.12 and 3.14, including real PostGIS persistence, search, versioning, publication, and role tests.
- Concurrent duplicate writers create one batch and version. Failed writes roll back rows, batches, versions, grid cells, and series creation. Equal-quality observations use deterministic ordering.
- Provider response tests cover CHIRPS final/preliminary/missing files, STAC manifest metadata, Movebank repository downloads and search, GBIF taxonomy modes, and Zenodo download failures and selection.
- Command-line tests exercise deterministic fetch, bounded environmental discovery, receipt persistence, and nonzero failure exits.
- Both packages built successfully and passed installed-wheel smoke tests outside the checkout.
- Critical Ruff checks (`E9,F63,F7,F82`), `pip check`, and `git diff --check` passed.
- `.github/workflows/backend.yml` repeats migration, static, test, and wheel checks on Python 3.12 and 3.14. The workflow has been authored; no remote GitHub Actions run was dispatched.

To repeat the suite, install the packages and test tools:

```sh
python -m pip install -e ./pipeline -e ./fetch_pipeline pytest pytest-cov ruff
```

Set `HABITAT_DATABASE_URL` to a disposable PostGIS database, bootstrap the roles and extension schema with `001_roles.sql`, and ensure PostGIS is installed in `extensions`. The CI workflow contains the complete fresh-database bootstrap. Then run:

```sh
python -m pytest --cov=habitat --cov=fetch --cov-report=term-missing -q
python -m pip wheel --no-deps --wheel-dir /tmp/habitatos-wheels ./pipeline ./fetch_pipeline
python scripts/check_backend_wheels.py /tmp/habitatos-wheels
```

Without a database URL the existing database fixture skips integration tests; that reduced run is not equivalent to this audit.

Remaining verification limits: live authenticated Movebank calls, live provider downloads, and paid AI calls were not exercised. Provider behavior and AI responses were tested with controlled responses. Production database state, provider availability, large-scale performance, and all possible input combinations are outside this run. One upstream Planetary Computer warning remains: its SAS model uses a deprecated Pydantic configuration style. It does not fail the tests. Python 3.11 support advertised by the fetch package alone was not separately tested; the combined backend requires Python 3.12 or newer.

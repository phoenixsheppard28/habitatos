# Recipe merge and integration guide

This guide covers integrating the Recipe implementation on `kael` with Stage 2 Normalize/storage and Stage 4 Analysis/workflow. It distinguishes working code from the adapters and runtime configuration still to add. It does not deploy a service or load secrets.

## Current implementation

Recipe lives in `src/recipe/`; tests and safe examples live in `tests/recipe/`. The implementation includes typed recipes, candidate union/eligibility, a Jev HTTP client, an injectable LLM planner, SQL/PostGIS compilation, PostgreSQL execution, local Parquet persistence, cache checks, the Analysis handoff, and follow-up requests.

The fixture demo uses simulated catalog, LLM, and Jev results. Recorded verification before integration: **28 local tests and 8 real PostgreSQL/PostGIS tests passed**. Those results do not establish that live provider calls or the merged shared adapters work.

Current limits:

- No application bootstrap or environment-variable loader has been added.
- No live LLM provider/client has been selected or connected.
- Jev's request/response shape is tested with simulated responses; a live authenticated call still needs verification.
- Stage 2 catalog search, semantic indexing, descriptor mappings, and physical table bindings are not connected.
- Persistence is local development storage, with a single worker per query assumed for context updates.
- Strict forecast preparation returns `insufficient_data` until availability and per-row feature-time contracts exist.
- Nearest-distance joins, arbitrary arithmetic, raw raster sampling, movement-path features, and exact per-output-row source-record lineage remain unimplemented.

The first merged vertical slice should be a historical query with actual normalized data. Forecasting is a separate increment.

## Work to add and ownership

| Work | Owner | Integration point / completion condition |
| --- | --- | --- |
| Shared contract mapping | Stage 2 with Recipe and Analysis | Map shared `QuerySpec`/`DatasetVersion` models into lane-local models, or adopt shared models without weakening validation |
| Metadata and semantic search | Stage 2 | Implement `CallbackCatalog` callbacks returning `SearchPage`; Stage 2 builds and maintains the semantic index |
| Search capabilities | Stage 2 + Recipe | Expose supported metadata filters to the planner; arbitrary tag formats remain supported through mappings, and unsupported filters fail explicitly |
| Trusted authorization | Shared auth/storage owner | Catalog callbacks use authenticated caller context; query `access_scope` is not an authorization credential |
| Database binding construction | Stage 2 storage | Supply trusted `TableBinding` objects for immutable dataset versions and verify actual physical field names |
| Live LLM client | Recipe / application owner | Implement the structured-output `generate` callable and normalize provider errors |
| Live Jev configuration/evaluation | Recipe | Configure a pinned model, verify a live call, and evaluate usefulness labels on held-out domain examples |
| Production artifact persistence | Stage 2 storage + Recipe | Replace `LocalArtifactStore` with durable recipe/context/report storage and a shared Parquet resolver |
| Coordinator entrypoint/jobs | Stage 4 | Invoke `RecipeService.run`, persist stage state, resume failures, and create real clarification jobs |
| Analysis consumption | Stage 4 | Read the table through shared storage and consume schema, grain, quality, and provenance |
| Additional evidence loop | Stage 4 + Recipe | Send a parent-linked follow-up request and consume the new artifact without replacing the original |
| Point-in-time forecasting | Stage 2 + Recipe + Analysis | Define availability/feature-time contracts and test leakage prevention before enabling forecasts |

## Environment configuration

**Only `RECIPE_TEST_DSN` is currently read by code**, in the opt-in database tests. Names below are a proposed application configuration convention. Add a settings loader and explicitly wire its values into constructors; setting these variables alone will not activate the service.

| Proposed variable | Required when | Usage |
| --- | --- | --- |
| `RECIPE_DATABASE_URL` | Supabase SQL execution | Backend PostgreSQL connection string for the selected direct/pooler endpoint; this contains credentials |
| `RECIPE_POSTGIS_SCHEMA` | Supabase SQL execution | Actual schema containing PostGIS functions; compiler default is `extensions`, test container uses `public` |
| `RECIPE_JEV_API_KEY` | Live Jev assessment | Passed to `JevAssessor`; backend secret |
| `RECIPE_JEV_MODEL` | Live Jev assessment | Explicit model/version identifier passed to `JevAssessor` |
| `RECIPE_LLM_PROVIDER` | Live planning | Selected provider, interpreted by the new LLM client factory |
| `RECIPE_LLM_MODEL` | Live planning | Explicit planner model/version |
| `RECIPE_LLM_API_KEY` | Provider requiring a key | Application-level name mapped to the chosen client's credential mechanism |
| `RECIPE_ARTIFACT_ROOT` | Local development storage | Passed to `LocalArtifactStore`; suggested local default `data/recipe` |
| `RECIPE_CANDIDATE_LIMIT` | Optional | Candidates per requirement per search route; service default `100`, favor broad recall |
| `RECIPE_MAX_PLAN_ATTEMPTS` | Optional | Bounded recipe-repair attempts; service default `3` |
| `RECIPE_MAX_ROWS` | Optional | Intermediate/output row limit; executor default `100000` |
| `RECIPE_STATEMENT_TIMEOUT_MS` | Optional | PostgreSQL per-statement timeout; executor default `30000` |
| `RECIPE_PROVIDER_TIMEOUT_SECONDS` | Optional | Jev/client network timeout; Jev default `10`; configure LLM timeout in its client too |
| `RECIPE_TEST_DSN` | Real SQL integration tests only | Disposable PostGIS database; tests create and drop isolated fixture schemas |

If Stage 2 uses Supabase's HTTP catalog/Storage APIs, it may also need a project URL and backend API credential. Reuse the application's agreed names for those settings; the SQL executor itself requires a PostgreSQL connection, not a Supabase HTTP API key. Embedding-model credentials and index configuration belong to Stage 2.

Local placeholder example for the future loader:

```dotenv
RECIPE_DATABASE_URL=<backend-postgres-connection-string>
RECIPE_POSTGIS_SCHEMA=<actual-postgis-schema>
RECIPE_JEV_API_KEY=<backend-secret>
RECIPE_JEV_MODEL=<explicit-model-version>
RECIPE_LLM_PROVIDER=<chosen-provider>
RECIPE_LLM_MODEL=<chosen-model>
RECIPE_LLM_API_KEY=<backend-secret-if-required>
RECIPE_ARTIFACT_ROOT=data/recipe
RECIPE_CANDIDATE_LIMIT=100
RECIPE_MAX_PLAN_ATTEMPTS=3
RECIPE_MAX_ROWS=100000
RECIPE_STATEMENT_TIMEOUT_MS=30000
RECIPE_PROVIDER_TIMEOUT_SECONDS=10
```

Store local values in ignored `.env` or the deployment's secret store. Add a placeholder-only `.env.example` when the settings loader is implemented. Do not pass credentials or physical database bindings into model context. Validate missing configuration at startup with messages naming the missing variable without printing its value.

## Connect the live planner and Jev

`JsonPlanner` expects this callable:

```python
def generate(*, instructions, context, schema):
    # Use the selected provider's structured-output API.
    # Keep trusted instructions separate from untrusted dataset context.
    # Return parsed JSON matching the supplied schema.
    ...
```

The two calls have different schema shapes: evidence discovery returns a list of `Requirement`; recipe planning returns `PlanningDecision`, containing exactly one of a recipe, clarification, or unmet requirements. Check whether the chosen provider accepts the generated JSON schemas, including the root array and discriminated operation union. If its schema subset requires an envelope or conversion, implement that translation inside the client adapter and still validate the final result locally.

The adapter must handle timeout, rate-limit, refusal, malformed JSON, and provider-unavailable outcomes. Convert failures into `RecipeError` codes with appropriate retryability; otherwise unexpected SDK exceptions fall back to the service's generic internal error. Provider retry scheduling belongs to the coordinator. The existing three planning attempts repair invalid proposals; they are not a complete network-retry policy.

Record planner provider/model, prompt/schema version, and generation settings in execution provenance. That metadata is not yet supplied by the generic callable. Keep input context bounded and preserve validated query constraints. Expose actual catalog filter capabilities so the LLM does not guess Stage 2 field names.

Construct the Jev assessor with `JevAssessor(api_key, model=...)`. It uses [TypeSafe's documented API](https://docs.typesafe.ai/api), sends metadata rather than tables, and validates probability distributions. Run a real request before enabling it in the live pipeline.

Keep the current broad usefulness policy during integration. Measure useful-dataset recall, inappropriate inclusions, uncertainty handling, calibration, latency, and cost before changing thresholds. Tangential candidates remain available for Stage 4 reassessment. Add a bounded metadata-inspection path for Jev's `insufficient_metadata` role: currently technical unknowns trigger inspection, but a Jev-only inspection disposition is saved without a second inspection call.

## Connect Stage 2 and Supabase

Implement the exact callback signatures in `catalog.py`:

```text
search_metadata(filters, *, query, limit) -> SearchPage
search_semantic(text, *, query, limit) -> SearchPage
authorize(dataset, query) -> bool
readable(dataset) -> bool
inspect(dataset, query) -> DatasetVersion
```

Descriptors must map identity/version, status, scope, columns, storage, mapping/validation references, and row grain. Preserve arbitrary producer tags under `metadata`. Keep unknown coverage explicit; provide time/location roles so Recipe can align records. Use `species` roles for mixed-species tables that need row filtering. Inspection may enrich metadata but must not silently change the immutable dataset identity.

The compiler works with `TableBinding` entries keyed by `(dataset_id, version)`. Build those from trusted Stage 2 storage metadata, not LLM output. Map logical columns to physical columns and configure dataset-ID, version, and scope columns on shared tables. Use `pinned_relation=True` only for a physically isolated immutable version. Identify native PostGIS columns separately from JSON GeoJSON columns.

Confirm the connection method, permissions, and actual PostGIS schema using the [Supabase connection guide](https://supabase.com/docs/guides/database/connecting-to-postgres) and [PostGIS guide](https://supabase.com/docs/guides/database/extensions/postgis). Provision the backend connection without giving the planner arbitrary database access. The executor performs reads in a repeatable-read transaction and does not install tables or migrations.

An application factory can wire the components as follows; this is a wiring example, not a shipped entrypoint:

```python
import os
import psycopg
from recipe.execution import SupabaseExecutor
from recipe.providers import JevAssessor, JsonPlanner
from recipe.service import RecipeService

def build_service(catalog, trusted_bindings, shared_store,
                  generate, create_clarification_job):
    def connect():
        return psycopg.connect(
            os.environ["RECIPE_DATABASE_URL"],
            sslmode="require",
            connect_timeout=10,
            prepare_threshold=None,
        )

    return RecipeService(
        catalog=catalog,
        planner=JsonPlanner(generate),
        assessor=JevAssessor(
            os.environ["RECIPE_JEV_API_KEY"],
            model=os.environ["RECIPE_JEV_MODEL"],
        ),
        executor=SupabaseExecutor(
            connect, trusted_bindings,
            postgis_schema=os.environ["RECIPE_POSTGIS_SCHEMA"],
        ),
        store=shared_store,
        create_clarification_job=create_clarification_job,
    )
```

Wire configurable limits/timeouts through the constructors when implementing the settings loader. The production store must support `context`, `save_context`, `recipe`, `save_recipe`, `lookup`, `publish`, and `save_failure`; Analysis also needs shared artifact resolution/reading. Add atomic ready publication, immutable versions, scoped reads, checksums, and query-level concurrency control. Pending/failed writes cannot become cache hits.

## Connect Stage 4

Use the shared `run(request) -> response` envelope and adapt the coordinator's validated query. Resolve relative dates before dispatch. A caller-supplied `datasets` list is not automatically consumed by the current service; candidates come through its catalog adapter. Agree on seeded-candidate handling if the coordinator expects to send a shortlist, rather than silently ignoring the field.

Analysis reads `output.feature_artifact` and `output.recipe`, plus `extensions.recipe_context` for purpose, row keys, descriptions, preparation counts, null counts, candidate assessments, and source references. Read tables through the resolver, including JSON-encoded geometry metadata; do not reconstruct private local paths. A primary artifact is the current contract. Multiple primary outputs require an agreed extension.

Follow-up requests carry the same query context and `input.additional_information` containing `base_recipe_ref`, `need`, and `reason`, with grain/alignment requirements as additional context. The planner must return a new identity/version and the requested `parent_recipe_ref`. Stage 4 should reference the returned artifact rather than assume the old table was mutated.

Clarification uses `create_clarification_job(query, details) -> real_job_id`. The coordinator persists the job and presents the question; without that callback the service returns `CLARIFICATION_REQUIRED`, not a fabricated pending job. Map errors into bounded retries, preserve upstream IDs, and distinguish missing evidence from a transient failure.

## Recommended merge order

1. Fetch the latest branches and verify `kael` contains the Recipe commit(s). Start from a clean working tree and create an integration branch from the current integration base; do not use a merge to overwrite another contributor's work.
2. Merge Stage 2's shared contracts/storage work first. Agree on descriptor mapping, catalog callbacks, table bindings, and artifact access before making broad changes to Recipe internals.
3. Merge `kael` into that integration branch. Keep the Recipe lane's implementation and tests together. Resolve shared README status, ignore rules, dependency declarations, and contract conflicts deliberately.
4. Merge Stage 4's coordinator/Analysis work. Add the service factory and handoff adapters in their agreed owned paths; ensure live and fixture providers are explicitly selected.
5. Consolidate `src/recipe/requirements.txt` into the repository's chosen package/dependency configuration. Preserve compatible dependency ranges and a test import path until package installation replaces `PYTHONPATH=src`. Run a clean dependency install.
6. Run the checks below, inspect the complete integration diff, and only then merge the integration branch into the shared base through the team's PR workflow. Deploy/publish separately after configuration is ready.

Example Recipe merge commands, after Stage 2 is integrated and with `main` as the agreed base:

```bash
git fetch origin
git switch -c integration/recipe origin/main
git merge --no-ff origin/kael
```

If Stage 2 has not landed on `main`, merge its actual branch into the integration branch before the final command. If an integration branch already exists, switch to it rather than recreate it. Use the repository's actual integration base if it is not `main`.

For contract conflicts, adapt at boundaries rather than maintaining two divergent production schemas. Preserve immutable IDs/version semantics, scope checks, null meaning, UTC/WGS84 conventions, deterministic join rules, and the shared statuses. Optional `extensions` metadata should remain additive. Required field/type/meaning changes need a contract-version decision with the other owners.

## Post-merge verification

### 1. Offline baseline

Before live credentials are involved:

```bash
python -m venv .venv
.venv/bin/python -m pip install -r src/recipe/requirements.txt pytest
PYTHONPATH=src .venv/bin/python -m pytest tests/recipe -q
PYTHONPATH=src .venv/bin/python -m recipe.demo --fixtures tests/recipe/fixtures/scenarios.json --output data/recipe-merge-check
git diff --check
```

Expected baseline: 28 local tests pass; 8 database tests skip unless `RECIPE_TEST_DSN` is set. Both demo scenarios publish readable Parquet and report repeat cache hits. Use a fresh output directory if fixture recipes changed; an immutable-version conflict is not permission to overwrite an old recipe.

### 2. Real database compiler checks

Set `RECIPE_TEST_DSN` to a disposable PostGIS database, then run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/recipe/test_postgis.py -q
```

Expected baseline: all 8 database tests pass. They verify actual SQL results, source version/scope filtering, cardinality failures, spatial ambiguity, interval overlaps, availability-aware temporal matching, query-date boundaries, and species filtering. The test suite expects PostGIS functions in `public`; separately verify the deployed executor's configured schema.

### 3. Stage 2 adapter tests

- Map real published descriptors from at least two dataset families with different metadata/tag representations.
- Confirm metadata-only and semantic-only matches survive the union and duplicate versions are assessed once per requirement.
- Confirm unknown metadata remains unresolved and unsupported filters produce a typed response.
- Check authenticated access in both search routes, inspection, execution, cache hits, and artifact reads; exclude quarantined inputs.
- Check actual physical table mappings, native versus JSON geometry, mixed-species data, and required lookback history.
- Verify semantic-index and embedding-model compatibility through Stage 2's search service; Recipe should not build a second index.

### 4. Live provider smoke tests

- Run a small labeled query/dataset assessment through Jev and inspect model version, role, probabilities, and reserve disposition.
- Run both LLM call shapes and validate the returned JSON locally. Include a query needing derived keys rather than a shared ID.
- Exercise malformed output, refusal, timeout, rate limit, invalid-operation repair, and exhausted attempts using controlled transport failures.
- Verify secrets and physical bindings never enter model context or user-facing error messages.
- Keep tests of model quality separate from deterministic assertions: live scores/plans need not be text-identical between calls, but every accepted result must satisfy the same contracts.

### 5. Stage 4 vertical slice and failures

- Submit a historical query through the real coordinator; verify the returned grain and joined values against a small hand-checked dataset.
- Read the artifact using the shared resolver and confirm types, units, null semantics, geometry encoding, and versioned provenance.
- Repeat with the same pinned recipe/inputs and confirm a cache hit. Change input/mapping version, recipe parameters, execution version, or access scope and confirm stale artifacts are not reused.
- Ask Stage 4 for additional evidence found initially in the tangential reserve. Verify Jev reassessment, current eligibility checks, parent lineage, and preservation of the first artifact.
- Test `ok`, optional-evidence `partial`, required-evidence `insufficient_data`, clarification `pending` with a real job ID, and technical `error` with retryability.
- Simulate interrupted work, retry/resume through the coordinator, and simultaneous requests against the production store; failed/incomplete artifacts cannot become ready cache entries.
- Confirm strict forecast requests remain insufficient until the availability contract and associated tests are implemented.

## Merge completion versus deployment readiness

Code can merge once shared interfaces agree and offline/database/handoff checks pass. Live deployment additionally requires the settings loader, secrets, real provider/catalog adapters, backend authorization, durable artifact storage, coordinator jobs, and a successful live historical vertical slice. Keep remaining operation families and forecasting as explicit follow-up work rather than implying that merging enables every query type.

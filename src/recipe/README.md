# Recipe lane

This is a working local implementation of the Recipe pipeline and a PostgreSQL/PostGIS backend for Supabase. Shared contracts, catalog schemas, semantic indexing, and workflow jobs remain owned by the other lanes. No live credentials or production tables are required for the fixture demo.

## Run

From the repository root:

```bash
uv sync
uv run pytest tests/recipe -q
uv run python -m recipe.demo --fixtures tests/recipe/fixtures/scenarios.json
```

The repository `pyproject.toml` declares the dependencies and installs `recipe` next to `habitat`. The Stage 2 adapter for this lane is `habitat.recipe_inputs`; see [`RECIPE_INTEGRATION.md`](../../RECIPE_INTEGRATION.md).

The demo uses **simulated planner and suitability decisions**, not live LLM/Jev inference. It prepares two distinct grains: site-month habitat/management observations and animal-observation rainfall context. It writes scoped Parquet artifacts under ignored `data/recipe-demo/` and demonstrates repeat-request cache hits. Fixture expected values are in `tests/recipe/fixtures/scenarios.json`.

Real PostgreSQL integration tests are opt-in. Set `RECIPE_TEST_DSN` to a **disposable PostGIS test database** and run `tests/recipe/test_postgis.py`. Set `RECIPE_TEST_POSTGIS_SCHEMA=extensions` when the database is Supabase. Tests create and remove isolated fixture schemas. They do not target a live Supabase project by default.

## Components

| Module | Responsibility |
| --- | --- |
| `models.py` | Strict lane-local query/dataset adapters and typed recipe operation graph |
| `catalog.py` | Broad candidate union, retrieval provenance, deterministic eligibility, Stage 2 callback boundary |
| `providers.py` | Injectable JSON-schema LLM planner and real Jev HTTP assessor |
| `validation.py` | Graph, columns/types/units, operation parameters, grain declaration, and output schema validation |
| `compiler.py` | Closed operation-to-parameterized SQL/PostGIS compiler and runtime preflight checks |
| `execution.py` | Fixture execution, output validation, transactional PostgreSQL backend |
| `artifacts.py` | Development recipe/context/report persistence, scoped cache, Parquet publication/resolution |
| `service.py` | v1 envelope, bounded planning repairs, reserve reassessment, handoff and typed failures |

## Stage 2 integration

`CallbackCatalog` accepts metadata/semantic search, authorization, readability, and inspection callbacks. Map the producer's metadata into `DatasetVersion`; preserve arbitrary tags under `metadata`. No global tag vocabulary is imposed. Search callbacks must reject unsupported filters explicitly. Each search returns a `SearchPage`, including truncation and optional semantic scores keyed by `(dataset_id, version)`. Stage 2 builds/maintains the semantic index.

Authorization callbacks must use trusted authenticated caller context, not merely compare a client-supplied `access_scope` string. Both discovery routes, eligibility, and cache reuse enforce the callback. Unknown coverage/meaning is inspected once and remains unresolved if inspection cannot establish it.

Time and location roles map onto explicit normalized columns. Source rows are restricted to the query region and dates, plus operation-declared lookback history. Event timestamps are timezone-aware; interval inputs use complete start/end pairs. Deriving day/month/year keys currently uses UTC. Geometry is WGS84 GeoJSON at the JSON boundary; Parquet stores it as a JSON string with typed descriptor metadata. Raster sampling and identity crosswalk discovery are not implemented.

Use the `species` column role when an input mixes species so execution can enforce the query on actual rows. A multi-species input without that mapping returns insufficient data for species-constrained queries. A single-species descriptor can establish scope without an extra row-level field.

The local store is a development adapter, not the shared production storage service. Replace it through the same persistence methods when Stage 2 is available. Recipe-level and pinned-dataset provenance is saved; exact per-output-row source-record lineage is an integration follow-up, particularly for aggregations. Local context updates assume a single worker per query; distributed locking and durable jobs belong to shared storage/workflow integration.

## Supabase execution

Configure `SupabaseExecutor(connection_factory, bindings, postgis_schema=...)` with a backend PostgreSQL connection factory and trusted `TableBinding` entries keyed by dataset/version. The connection can use Supabase's direct or session-pooler endpoint. The [official connection guide](https://supabase.com/docs/guides/database/connecting-to-postgres) explains endpoint selection.

Bindings map logical column names to physical columns. Shared tables must have dataset ID, version, and scope columns; the compiler binds all three restrictions. Set `pinned_relation=True` only for a trusted physically isolated immutable dataset version. The planner cannot supply physical table names or bindings. PostgreSQL permissions and the authorization adapter must independently restrict access.

Confirm PostGIS is enabled and supply its actual schema (`extensions` by default; disposable PostGIS tests use `public`). Native PostGIS columns can be declared in `native_geometry_columns`; otherwise geometry input columns must contain GeoJSON. See [Supabase's PostGIS guide](https://supabase.com/docs/guides/database/extensions/postgis).

The executor uses a read-only repeatable-read transaction so checks and execution see the same snapshot, per-statement timeouts, and intermediate/output row limits. It does not install migrations or expose an arbitrary-SQL RPC. Credential loading and production connection provisioning remain backend configuration tasks.

## Operations and recipe format

The planner produces a JSON object validated by `RecipeSpec.model_validate`. Obtain its machine schema with `RecipeSpec.model_json_schema()`. Recipe steps reference input aliases or prior step IDs; forward references and ID collisions fail validation. Every output column must have a description, type, unit where applicable, and declared null behavior. Output keys must be non-null and unique at execution.

Supported operation names:

- `select`: projection/renaming (`columns` maps output name to source name).
- `filter`: typed scalar predicates; literal values become SQL parameters.
- `time_bucket`: derive a UTC day/month/year timestamp key.
- `aggregate`: grouped sum/mean/min/max/count, excluding nulls; all-null sums stay null.
- `join`: left/inner equality join with explicit one-to-one or many-to-one cardinality; duplicate keys fail.
- `spatial_join`: point-to-polygon containment, including boundaries; choose a stable unique right-side tie-break value and report ambiguity.
- `asof_join`: preceding matching observation within a maximum tolerance, with optional availability constraints and stable tie-breaks.
- `window_aggregate`: sum complete preceding measurement intervals, enforce non-overlap, expose coverage, and null totals below the declared coverage requirement.

Unsupported new operations require typed models, validation, SQL compilation, reference execution, and meaningful tests before enabling them. There is no generated-code escape hatch. Nearest-distance matching, arbitrary arithmetic expressions, vegetation-specific recipes, and sampled-path movement distance are not yet registered.

Strict **forecast preparation currently returns `insufficient_data`** until Stage 2 and Analysis agree on historical availability and per-row feature-time contracts. Historical window/as-of operations already exclude future observations relative to each row; that alone does not prove a leakage-free historical forecast backtest.

## Providers

`JsonPlanner(generate)` accepts the team's structured-output LLM callable:

```python
def generate(*, instructions, context, schema):
    # Send trusted instructions, untrusted context and the JSON schema to
    # the selected provider; return parsed JSON, never SQL or executable code.
    ...
```

The LLM provider/model is intentionally not selected. Supply a production callable to enable live query interpretation and recipe planning. Local parsing, bounded repairs, and deterministic validation remain mandatory regardless of provider.

`JevAssessor(api_key, model="<explicit-version>")` implements TypeSafe's documented endpoint and validates typed answer distributions. It sends query/requirement context and dataset metadata, not row payloads, SQL bindings, or storage credentials. Configure keys outside source control. See the [official API reference](https://docs.typesafe.ai/api).

The initial disposition policy is conservative: potentially/directly useful candidates advance, tangential/uncertain candidates remain reserves, insufficient metadata stays unresolved, and only unanimous unrelated scores with an irrelevant role are excluded. This policy is deliberately broad pending domain-labeled evaluation; it is not a validated optimal Jev threshold. Record actual provider/model versions and rubric versions.

## Analysis handoff and additional requests

`RecipeService.run(request)` accepts the README's v1 envelope with `input.query`. Catalog discovery supplies dataset descriptors through the injected adapter. This implementation does not ingest caller-supplied rows or private database handles.

A successful response contains `output.recipe` and `output.feature_artifact`, plus `extensions.recipe_context` with table purpose, row keys, column descriptors, preparation counts, null counts, selected sources, candidate assessments/reserves, and supported operations. Read Parquet through `resolve_artifact`/`read_dataset`; do not infer local file paths. `artifact://` URIs are resolver conventions, not network endpoints.

Stage 4 sends another v1 request with the same validated query and:

```json
{
  "additional_information": {
    "base_recipe_ref": {"recipe_id": "recipe-site", "version": "1"},
    "need": "Monthly water-level observations aligned to these sites",
    "reason": "Compare another environmental factor with canopy change",
    "required_grain": "one site per month"
  }
}
```

Place that object under `input`, alongside `query`. Saved candidates are rechecked and rescored against new requirements; missing coverage triggers a broader catalog search. The planner returns a new recipe with `parent_recipe_ref`. Original recipes/artifacts remain immutable. Standalone `derive_recipe`/`execute_recipe` transport endpoints are not exposed yet; the implemented external boundary is `run` plus this follow-up request.

`ok`, `partial`, `insufficient_data`, `pending`, and `error` follow the shared envelope. Optional missing evidence yields partial; required missing evidence, empty output, or strict forecasting without availability guarantees yields insufficient data. Invalid joins and unsupported operations cannot publish artifacts. Technical provider failures retain retryability for the coordinator; Recipe does not perform unbounded retries.

Clarification requires an injected `create_clarification_job` callback returning a durable coordinator job ID. Without it, return `CLARIFICATION_REQUIRED` with clarification details under `extensions`, rather than fabricate a pending job. Durable scheduling, retries, and the user conversation remain Stage 4 responsibilities.

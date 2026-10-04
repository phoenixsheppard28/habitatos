# Habitat Watch

The map workspace lives in [`web/`](web/README.md). It uses TypeScript, Leaflet, Esri World Topographic Map tiles, and Vite.
The workspace reads published observations through the Python API. The assistant connects to retrieval, Recipe, and Analysis.

## Start the complete app

Install Docker with Docker Compose support.
Run this command from the repository root:

```sh
docker compose up --build -d --wait
```

Open http://localhost:8080.
Compose starts PostGIS and GLiClass, applies database migrations, and starts the API with the built frontend.
The backend includes Fetch, Normalize, Recipe, and Analysis.
GLiClass assigns dataset tags locally with confidence scores.
The first classifier start downloads the pinned, Apache-2.0 model weights.
Database records, downloaded files, and analysis artifacts persist in Docker volumes.
The first start creates an empty catalog.
Use the assistant or the pipeline command to retrieve data.

Set `ANTHROPIC_API_KEY` in the root `.env` file to enable the assistant.
Movebank study retrieval also requires `MOVEBANK_USERNAME` and `MOVEBANK_PASSWORD`.
Compose reads these settings at runtime.

For frontend development, keep the stack active and run:

```sh
pnpm --dir web install
pnpm --dir web run dev
```

Open http://localhost:5173.
Vite forwards `/api` requests to the container at http://127.0.0.1:8080.
See [`docs/docker.md`](docs/docker.md) for configuration, pipeline commands, migrations, and service checks.

A self-service ecological data platform that answers questions about historical patterns, forecasts possible changes, and retrieves relevant datasets on demand.

**Status:** The Fetch and Normalize lanes are implemented as one Python package, `habitat`, in [`src/habitat/`](src/habitat/). See [`MERGE_PLAN.md`](MERGE_PLAN.md) for the package layout and the data flow, [`SOURCES.md`](SOURCES.md) for each source, and [`migrations/`](migrations/) for the tables. The Recipe lane is in [`src/recipe/`](src/recipe/README.md). [`RECIPE_INTEGRATION.md`](RECIPE_INTEGRATION.md) tells how the normalized tables become Recipe inputs. The Analysis lane is in [`src/analysis/`](src/analysis/) ([`DIEGO.md`](DIEGO.md)), and the coordinator is in [`src/workflow/`](src/workflow/). [`ANALYSIS_INTEGRATION.md`](ANALYSIS_INTEGRATION.md) tells how one query flows from Fetch to Analysis.

## The product

Ask a question about a species, habitat, region, and time period. Habitat Watch finds relevant records, combines animal tracking with satellite imagery and environmental observations, and returns evidence-backed analysis with maps and timelines.

The product works with public data from the first use. Contractor partnerships, proprietary records, and a collector marketplace are not launch dependencies.

Initial users are conservation groups, researchers, and land managers who want answers without assembling geospatial datasets themselves.

## Three core capabilities

- **Analyze history:** "Identify a notable pattern in tracked antelope movement in this region over the past year." Quantify patterns, cite the supporting records, and distinguish sampled animals from population-wide conclusions.
- **Forecast:** "If rainfall continues declining here, where might these tracked antelopes move over the next month?" Produce conditional forecasts with uncertainty, assumptions, and a stated horizon. Fit and evaluate models on appropriate movement and environmental data; a language model explains results rather than inventing trajectories. Report insufficient evidence when necessary.
- **Find new data:** "Find movement records and rainfall data for these antelopes and this region." A retrieval agent searches supported catalogs, APIs, and repositories, checks coverage and permissions, and imports usable records.

These are example queries, not claims that a matching antelope dataset or a validated forecast is currently available.

## Core workflow

1. Ask a question and specify or clarify the species, region, dates, and forecast horizon.
2. Check the local database for relevant, sufficiently fresh records.
3. Retrieve missing coverage from supported online sources; distinguish unavailable data from absent wildlife.
4. Validate and normalize permitted records, then match them by location and time.
5. Run the relevant analysis or forecast and return findings, source links, coverage, and uncertainty.
6. Save reusable data and analysis provenance for subsequent queries.

Satellite indicators support inspection decisions; they do not establish species recovery, restoration success, or the cause of a change.

## Retrieval and cache

Think of our database as a cache and online repositories as external storage. Reuse suitable cached records; fetch missing or outdated data. Store permitted raw records alongside normalized data, indexed by source, study, species, geography, observation dates, version, and retrieval time. Track units, quality, licenses, attribution, and freshness rules; deduplicate repeat downloads.

The agent discovers and selects data; connectors perform downloads, and analysis code performs calculations. Validate schema, timestamps, coordinates, and sampling coverage before using records. A search snippet or paper mentioning a dataset is not a substitute for the underlying measurements.

Cache records only where source terms allow retention and reuse. Otherwise save permitted metadata and access references. Respect restricted access and sensitive locations; keep private customer records isolated. New queries enrich the cache only when they retrieve new usable data, rather than duplicating existing records.

## Architecture and execution

The four agent roles are Fetch, Normalize, Recipe, and Analysis. Agents choose sources, propose mappings, and select methods. Typed specifications, bounded tools, and deterministic application code execute their decisions. Every handoff must pass machine validation; an agent's self-assessment is not an acceptance check.

```text
User question -> validated QuerySpec -> catalog / evidence-gap check
                                             |
                         missing data -> Fetch -> raw archive + manifest
                                             |
                                         Normalize
                                             |
                          validated canonical dataset versions
                                             |
                                           Recipe
                                             |
                           validate + execute -> feature dataset
                                             |
                                          Analysis
                                             |
                              evidence report + optional model
```

Skip stages when suitable artifacts already exist. Known sources reuse normalization mappings; matching requests reuse recipes and feature datasets. Historical questions generally use calculations rather than model training.

**Hot path:** save raw data immediately, then use cached validated records or normalize the minimum required fields before answering. **Background path:** finish normalization, retry failed jobs, enrich the catalog, and evaluate proposed mapping/recipe improvements. Scheduling is an application responsibility; do not depend on an LLM remembering to perform background work. Return an explicit pending or insufficient-data response when required evidence is not ready.

Initial stack: Python for connectors and analysis; PostgreSQL for the catalog, canonical observations, and JSONB staging attributes; local files in development and object storage later for original files, raster imagery, and versioned Parquet feature datasets. A durable job table and worker provide orchestration. Raster measurements stay as files until relevant values are extracted. Do not pass whole imagery files or millions of records into LLM context.

## Four contributor lanes

Claim one lane by replacing `Unclaimed` with your name and branch. Each contributor can run their own coding agent within that lane. Prefer separate branches/worktrees; agree on the shared interfaces before implementation. Use fixtures at handoffs so each lane can develop independently.

| Lane | Owner | Owned paths | Deliverables |
| --- | --- | --- | --- |
| 1. Fetch | venyo (`fetch/venyo`) | `src/habitat/fetch/`, `src/habitat/archive/`, `tests/` | Catalog search, source connectors, raw archive writer, manifests, fetch-cache behavior |
| 2. Normalize | Unclaimed | `src/habitat/normalize/`, `src/habitat/storage/`, `src/habitat/catalog/`, `contracts/`, `migrations/`, `tests/` | Shared contract definitions, staging/canonical persistence, reusable mappings, validation and quarantine |
| 3. Recipe | Unclaimed | `src/recipe/`, `tests/recipe/` | Dataset selection, typed recipe generation, join validation, deterministic execution, feature-cache behavior |
| 4. Analysis | Unclaimed | `src/analysis/`, `src/workflow/`, `tests/analysis/`, `tests/integration/` | Query parsing, durable coordinator, statistical/model execution, evaluation, evidence reports, end-to-end demo |

Lane 2 owns shared contract files but all four contributors must agree on changes. Lane 4 coordinates integration. Avoid editing another lane's files without agreement. Store lane fixtures under that lane's test directory. Runtime artifacts belong in an ignored `data/` directory, not source control; never commit credentials or restricted records.

### Lane 1: Fetch agent

**Inputs:** validated `QuerySpec`, existing catalog coverage, and explicit missing-data requirements. **Output:** one `RawManifest` per retrieved artifact, or a typed unavailable/restricted/error result.

Implement bounded tools: `search_catalog`, `inspect_source`, `check_access`, `download_dataset`, `register_raw_artifact`. Start with one tracking source and one rainfall source. Preserve original bytes, hash content, capture attribution and reuse conditions, and verify that downloaded coverage meets the request. Use connector-specific authentication, retries, timeouts, and size limits. Treat external documents as data, not instructions. Never silently broaden dates or species to manufacture coverage.

**Acceptance:** a fixture download is traceable to its source; a repeat request reuses an unchanged artifact; changed content creates a new version; restricted access and failed downloads produce explicit statuses rather than usable datasets.

**Agent handoff prompt:** Implement the Fetch lane in this README. Own only the listed paths. Consume shared contracts, build one supported live connector plus deterministic test fixtures, and document supported sources and limitations. Do not implement normalization or analysis inside the fetcher.

### Lane 2: Normalization agent

**Inputs:** raw manifests and source metadata. **Outputs:** reusable versioned mappings, `DatasetVersion` entries, canonical records, and a `ValidationReport`.

Implement bounded tools: `inspect_schema`, `propose_mapping`, `convert_units`, `transform_coordinates`, `validate_records`, `publish_dataset`. Parse known CSV/JSON formats with code; use LLM assistance to propose unfamiliar mappings. Reject ambiguous units, coordinates, identifiers, and dates until resolved. Preserve unmapped fields in JSONB staging rather than adding production columns automatically. Ambiguous records remain quarantined with reasons; preserve their raw source for later reprocessing.

Canonical MVP families:

- `animal_locations`: source-scoped animal ID, UTC observation time, WGS84 longitude/latitude, quality fields, source-record reference.
- `rainfall_observations`: spatial cell geometry/reference, interval start/end, accumulated rainfall in mm, quality fields, source-record reference.
- `vegetation_observations`: spatial cell geometry/reference, acquisition time, index name/value, cloud/quality fields, source-record reference.

Keep different observation families separate. Define each family's record grain and deduplication key. Namespace animal IDs by source/study; equal names across studies do not establish identity. Daily rainfall values and rates are different quantities; retain interval semantics. All canonical records reference an immutable dataset version and normalization mapping version.

**Acceptance:** two fixtures with different source field names map into the same canonical family; unit/time conversions are checked; rerunning ingestion does not duplicate records; rejected records remain discoverable; published records link to their originals.

**Agent handoff prompt:** Implement the Normalize lane and shared contracts in this README. Publish contract definitions first so other contributors can use fixtures. Build canonical movement and rainfall schemas, reusable mappings, persistence, and validation. Keep original artifacts intact and avoid guessing unresolved source meanings.

### Lane 3: Recipe agent

**Inputs:** `QuerySpec` and compatible, validated `DatasetVersion` entries. **Outputs:** a versioned `RecipeSpec`, validation results, and a `FeatureArtifact` when execution succeeds.

Implement bounded tools: `find_datasets`, `check_compatibility`, `build_recipe`, `validate_recipe`, `execute_recipe`, `lookup_feature_cache`. Start with approved operations for filtering, daily movement aggregation, spatial point-to-cell matching, and preceding-window rainfall aggregation. Compile the typed recipe into parameterized SQL or tested analysis code; arbitrary generated SQL is outside the initial MVP.

Define the output grain before joining. Aggregate each source to that grain and check join cardinality to avoid multiplying rows. Resolve overlapping spatial cells and multiple eligible observations with explicit, stable rules. Predictions may only use inputs available at the forecast cutoff. Record measurement age and missingness instead of silently filling values. Individual-based movement features must specify how daily locations and distances are computed.

Always save the recipe. Cache expensive/repeated feature datasets and pin training inputs. Key the cache by source and mapping versions, recipe version, filters, feature definitions, execution-code version, and access scope. New source observations can use the same recipe; changed joins or features create a new recipe version. Preserve old versions. Improvements must pass validation before becoming active.

**Acceptance:** fixture inputs produce expected joined rows and numeric features; duplicate/overlapping matches are handled explicitly; forecast features exclude future observations; identical versions hit the cache; a changed input version misses it; unsupported joins return an error.

**Agent handoff prompt:** Implement the Recipe lane in this README. Consume canonical fixtures, generate a validated typed recipe, execute only supported operations, and save recipe and feature provenance. Demonstrate deterministic joins and cache invalidation without relying on an LLM to calculate results.

### Lane 4: Analysis agent and coordinator

**Inputs:** user question, validated query context, `FeatureArtifact`, and compatible saved models. **Outputs:** `AnalysisSpec`, `AnalysisResult`, and an optional `ModelArtifact`.

Implement bounded tools: `summarize_data`, `run_statistical_method`, `fit_baseline`, `fit_supported_model`, `evaluate_model`, `save_model`, `render_report`. Start with movement summaries and one narrow prediction target such as next-day displacement. Do not claim that this target predicts an entire migration route. Choose the demo species and dates based on actual accessible coverage.

The coordinator validates the query, resolves relative dates once, checks the catalog, dispatches missing stages, records durable job state, and resumes after failure. Make stage writes idempotent and bounded retries explicit. Historical analyses use deterministic calculations. Forecast evaluation uses held-out later periods and a simple baseline; report error metrics, sample coverage, assumptions, and uncertainty. Do not present unevaluated model output as a validated forecast. Species disappearance/extinction questions require suitable population and observation-effort evidence, not merely a decline in tracking points.

**Acceptance:** one end-to-end fixture query returns cited findings; an insufficient-data query returns an honest status; a repeated query reuses compatible artifacts; a failed stage can resume; any demonstrated forecast includes temporal evaluation and baseline comparison.

**Agent handoff prompt:** Implement the Analysis and Workflow lane in this README. Coordinate the other three contracts using fixtures, implement a historical report first, and add a bounded forecast only when evidence supports evaluation. Save result/model provenance and clearly report pending, failed, and insufficient-data states.

## Shared contracts to agree before coding

The loose v1 handoff below is the minimum integration agreement. The contract inventory that follows describes fuller metadata to add as needed; it does not require every contributor to implement every future feature before integration. Use typed models and generated JSON schemas in `contracts/`. Each artifact includes `schema_version`, an immutable artifact/version ID, creation time, and access scope. References use IDs plus storage locations, not large inline payloads.

### Loose v1 handoff: fixed boundaries, flexible internals

Each lane exposes `run(request) -> response` through a thin adapter. It may be a Python function initially and an HTTP endpoint or worker later. Requests and responses must be JSON-serializable; do not hand off private Python classes, database connections, or hardcoded machine paths. This contract specifies exchanged values, not transport or deployment.

Every request contains `contract_version: "1.0"`, `request_id`, `query_id`, `access_scope`, and `input`. Every response echoes those first four fields and adds:

```json
{
  "contract_version": "1.0",
  "request_id": "request-001",
  "query_id": "query-001",
  "access_scope": "public",
  "status": "ok",
  "output": {},
  "warnings": [],
  "error": null,
  "extensions": {}
}
```

Response status is `ok`, `partial`, `pending`, `insufficient_data`, or `error`. `output` always exists, even when empty. An error is `{ "code": "...", "message": "...", "retryable": false }`; pending responses include `output.job_id`. `partial` exposes individually ready artifacts and explains excluded data in warnings. It never means all records are safe to consume. These response statuses are distinct from the coordinator's internal job states.

Use `extensions` for optional lane-specific metadata. Consumers ignore unfamiliar optional fields but reject unsupported major contract versions. Treat published IDs/versions as immutable. Use UTC ISO-8601 timestamps, WGS84 coordinates, and explicit units; `null` means unknown, not zero. Check the carried access scope when reading any artifact; it is not itself an authorization credential.

| Boundary | Request `input` | Successful response `output` | Consumer guarantee |
| --- | --- | --- | --- |
| Coordinator -> Fetch | `query` plus `requirements` describing missing species/region/dates/data kinds | `raw_artifacts: [RawManifest]` | Original permitted bytes are resolvable; coverage claims distinguish known from unknown |
| Fetch -> Normalize | `raw_artifacts: [RawManifest]` | `datasets: [DatasetVersion]`, `validation_reports` | Ready datasets are discoverable and readable without knowing the fetcher's implementation |
| Catalog + query -> Recipe | `query`, `datasets: [DatasetVersion]` | `recipe`, `feature_artifact` | Saved recipe pins inputs; feature artifact has a declared row grain and typed columns |
| Recipe -> Analysis | `query`, `recipe`, `feature_artifact` | `result`, optional `model_artifact` | Result identifies the feature/recipe versions and includes evidence and limitations |

Here `query` is the same `QuerySpec` across all stages, containing `query_id`, `question`, `task_type` (`historical`, `forecast`, or `discovery`), `species` (list), `region` (GeoJSON geometry), and `time_range` (`start`, `end`). Extra forecast/scenario fields are optional for historical/discovery requests. The coordinator resolves relative dates before dispatch.

**Fetch -> Normalize: preserve bytes and provide a manifest.** The source format is allowed to vary; the manifest format is not. Minimum raw-artifact fields:

```json
{
  "artifact_id": "raw-001",
  "version": "1",
  "created_at": "2026-10-03T16:00:00Z",
  "access_scope": "public",
  "source": {"name": "fixture", "url": "https://example.org/study", "study_id": "study-1"},
  "storage": {"uri": "artifact://raw-001/1", "format": "csv"},
  "checksum": "sha256:<computed-content-hash>",
  "retrieved_at": "2026-10-03T16:00:00Z",
  "coverage": {"species": ["example-species"], "bbox": null, "start": null, "end": null},
  "rights": {"license": "fixture-only", "retention_allowed": true, "reuse_allowed": true, "attribution": "Demo fixture"},
  "extensions": {}
}
```

This is an illustrative manifest, not permission to reuse a real dataset. Unknown rights are `null`, never assumed granted. Unknown coverage is explicit. Multiple files produce multiple manifests; archive-member selection must be supplied in metadata when needed. Fetch does not promise canonical columns.

**Normalize -> Recipe: publish searchable descriptors, not just anonymous tables.** Each `DatasetVersion` must include `dataset_id`, `version`, `created_at`, `access_scope`, `family`, `description`, `status` (`ready` or `quarantined`), `storage`, `row_grain`, `columns`, `coverage`, `row_count`, `raw_artifact_refs`, `mapping_version`, and `validation_report_ref`.

- `columns` is a list of `{ "name": "observed_at", "type": "timestamp", "nullable": false, "unit": null, "role": "event_time" }`. Standard types are `string`, `integer`, `number`, `boolean`, `timestamp`, `geometry`, and `json`; standard roles include `entity_id`, `event_time`, `interval_start`, `interval_end`, `longitude`, `latitude`, `geometry`, and `measurement`. New roles may be added without renaming existing ones.
- `coverage` uses `species`, `bbox` (`[west, south, east, north]` or `null`), `start`, and `end`; full geometry and sampling summaries are optional extensions. Descriptors identify study/source scope for entities. Column units and measurement descriptions resolve meaning beyond field names.
- The storage adapter exposes `register_dataset(descriptor)` and `search_datasets(filters) -> [DatasetVersion]`. Minimum filters: `access_scope`, `family`, `species`, `bbox`, `start`, `end`, and `status`. Search applies scope checks and overlap filters; unknown coverage is marked as such, not counted as verified coverage. Recipe performs the final suitability checks.

For v1, agree on these minimum canonical column names:

| Family | Minimum columns |
| --- | --- |
| `animal_locations` | `dataset_id`, `dataset_version`, `source_record_id`, `entity_id`, `observed_at`, `longitude`, `latitude` |
| `rainfall_observations` | `dataset_id`, `dataset_version`, `source_record_id`, `cell_id`, `geometry`, `interval_start`, `interval_end`, `rainfall_mm` |
| `vegetation_observations` | `dataset_id`, `dataset_version`, `source_record_id`, `cell_id`, `geometry`, `observed_at`, `index_name`, `index_value` |

Additional columns are welcome. `entity_id` is source/study-scoped, and `geometry` is WGS84 GeoJSON at the JSON boundary. Never emit guessed values merely to satisfy required columns: quarantine incompatible records or report the missing requirement.

**Recipe -> Analysis: publish a feature artifact.** Minimum fields: `artifact_id`, `version`, `created_at`, `access_scope`, `recipe_ref`, `input_dataset_refs`, `storage`, `row_grain`, `columns`, `row_count`, and `validation_report_ref`. Feature names may vary; the descriptor must state their meaning, units, and roles. The analysis stage returns insufficient data if its selected method requires absent features.

**Common artifact access:** all consumers call `resolve_artifact(storage)` for an original file or `read_dataset(storage, columns, filters)` for tabular rows through the storage adapter. `artifact://...` is a project resolver convention to implement, not a real network protocol. The adapter hides local paths, object storage, and SQL-table details. Agree on a portable Parquet export for ready tables/features as the v1 fallback; lanes can store them differently internally. Agree on coordinate, geometry, and timestamp encoding when exporting. No downstream lane should infer table names or open another contributor's private filesystem layout.

**Freedom inside each lane:** contributors choose prompts, agent frameworks, source parsers, internal schemas, additional features, and model methods. Only exchanged fields, canonical MVP columns, artifact readability, and status semantics are fixed. Replaceable adapters keep those choices from becoming integration dependencies.

**Before merging:** each producer supplies a small safe example response and readable artifact; the next lane must consume it without producer-specific code. Check one successful handoff, one missing/invalid-data response, and preservation of IDs/provenance. Additive optional fields are compatible; changes to required names, types, or meanings need agreement and a contract-version change. Begin with movement and rainfall fixtures so all four branches can progress before live-source access is ready.

| Contract | Required contents |
| --- | --- |
| `QuerySpec` | Original question, task type, species/study constraints, region geometry, absolute UTC dates, optional forecast cutoff/horizon/scenario, access scope |
| `RawManifest` | Source/study identifiers, source URL, raw storage reference, checksum/version, retrieval time, observed coverage, format, license/attribution/access and retention metadata |
| `DatasetVersion` | Canonical family, raw references, mapping version, schema, units/coordinate system, spatial/time coverage, row count, validation status, storage reference |
| `ValidationReport` | Checks performed, accepted/rejected counts, error reasons, coverage gaps, readiness for the requested task |
| `RecipeSpec` | Query reference, pinned source versions, output grain, filters, approved operations/parameters, features, missingness/tie-break rules, recipe and execution versions |
| `FeatureArtifact` | Recipe reference, input versions, cache key, storage reference, schema, row count, quality/coverage report |
| `AnalysisSpec` | Feature reference, method/version, target if applicable, forecast horizon, temporal split, baseline, parameters, random seed |
| `AnalysisResult` | Status, findings/metrics, evidence references, limitations, artifact versions, optional model reference |
| `ModelArtifact` | Model storage reference, training-feature version, input schema, method/dependency versions, parameters, seed, evaluation, intended use |

A minimal coordinator API is `submit_query(QuerySpec) -> job_id` and `get_result(job_id) -> status + result/artifact references`. Track stage state as `queued`, `running`, `succeeded`, or `failed`; user-facing results additionally distinguish `pending`, `complete`, and `insufficient_data`. Persist errors and upstream references. Do not treat missing evidence as a transient failure to retry indefinitely.

## Integration order and demo definition

1. All contributors agree on contracts; Lane 2 publishes initial schemas. Lane 4 defines one fixture query with absolute dates.
2. Build each lane against local fixtures, then connect Fetch -> Normalize -> Recipe -> Analysis.
3. Add one permitted live retrieval route. Show first-query retrieval and a repeat-query cache hit with no duplicate records.
4. Demonstrate a historical movement finding linked to rainfall/vegetation evidence, with attribution and coverage limitations.
5. Demonstrate a narrowly defined, evaluated forecast if coverage allows; otherwise return insufficient data with the missing requirements.

The integration is done when the same pinned inputs and recipe reproduce the same feature dataset, results trace to their sources, failed jobs can resume, and incompatible/new inputs cannot reuse stale cached artifacts. No live credentials should be required for fixture-based checks.

## Hackathon MVP

- One region, one species, and a licensed tracking study with sufficient coverage.
- Movement records, satellite vegetation/water indicators, and rainfall.
- Natural-language historical analysis with a map and timeline.
- One live catalog/API retrieval path with a demonstrated cache miss followed by a cache hit.
- One conditional forecast, evaluated on a withheld later time period against a simple baseline; mark it experimental or omit unsupported forecasting when data are inadequate.
- Source citations and an exportable evidence summary.

Use preprocessed imagery for a reliable demo. Clearly label sample observations. Clouds, spatial resolution, tracking gaps, and mismatched time periods may limit analysis; surface those limits.

## Optional animal movement layer: Movebank

[Movebank](https://www.movebank.org/cms/movebank-content/about-movebank) is a research platform for animal tracking and animal-borne sensor data, hosted by the Max Planck Institute of Animal Behavior. Records can include timestamped locations, species, individual animals, tags, and deployment metadata.

Combine permitted tracks with habitat boundaries, satellite imagery, and rainfall to investigate:

- Which wetlands tracked animals repeatedly visit.
- Whether habitat use changes during dry periods or as visible water extent changes.
- Where movement routes overlap roads or development.
- Whether tracked animals use a site before and after restoration.

For a movement demo, select one accessible study with geography and observation dates that overlap the imagery. Habitat-only queries remain usable without tracking data; movement questions require relevant tracks.

Researchers retain ownership and control access by study. Download permitted records as files or through the API, preserving study identifiers, attribution, and license terms. Restricted studies require permission; public visibility does not establish rights for every commercial use. See [data access](https://www.movebank.org/cms/movebank-content/access-data) and [terms of use](https://www.movebank.org/cms/movebank-content/general-movebank-terms-of-use).

Tracks describe sampled animals, not a complete wildlife census, and cannot by themselves establish restoration effectiveness. Movebank already offers environmental annotation through Env-DATA and analysis through MoveApps. Our differentiation remains site management records, repeated field evidence, and outcomes rather than simply combining tracks with weather.

## Data and moat

Public records provide immediate utility. A growing cache improves retrieval speed and coverage, but copying public data alone does not create an exclusive moat. Stronger differentiation comes from reliable normalization, evaluated analyses, and permissioned field histories linking site conditions, management actions, and outcomes.

Record location, time, collection method, units, quality, and provenance. Keep customer analysis separate from permission to use records for cross-site benchmarks or model improvement. More data is useful when it is consistent and comparable.

## Contractor integration later

Add CSV imports for historical surveys, planting, maintenance, and costs; team accounts; standardized field tasks; and integrations with sensors, cameras, and survey tools. Match imported records to the same site timeline.

Contractors gain easier reporting and project comparisons. With sufficient comparable data and agreed reuse rights, the product can develop restoration benchmarks. Paid field verification can follow demonstrated customer demand.

## Business model to validate

Offer a free limited analysis and paid deeper queries, forecasts, recurring monitoring, and report exports. Later, add organization plans and commissioned field collection. Validate pricing against retrieval, storage, and processing costs.

Validate whether users find defensible answers, save research/reporting time, return with new questions, and pay for continued use before expanding sources or adding hardware.

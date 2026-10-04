# Recipe agent implementation scope

Status: implementation scope with an initial implementation in `src/recipe/`; see `src/recipe/README.md` for executable fixtures, integration adapters, and current limitations. This document refines Lane 3 in `README.md`; shared contract changes remain proposals for agreement with Normalize and Analysis.

## Goal

Turn a validated user query and normalized datasets into a trustworthy, analysis-ready base dataset. Reduce the modeling agent's work by handling dataset selection, spatial and temporal alignment, common deterministic features, validation, and provenance. Preserve access to relevant source observations so modeling can iterate beyond the initial feature set.

Deliver a useful default table rather than an exhaustive join of every available dataset. The modeling agent owns the prediction target, train/validation splits, feature selection, learned preprocessing, and model evaluation. Recipe owns reproducible data preparation and executes supported feature requests from Analysis.

## Ownership and dependencies

- Implementation: `src/recipe/` and `tests/recipe/`.
- Normalize owns `contracts/`, canonical schemas, storage adapters, and migrations. Coordinate contract additions with that contributor rather than implementing a second storage system.
- Analysis owns query parsing, orchestration, statistical/model execution, and evaluation.
- Fetch owns external source discovery and downloads. Recipe searches the normalized catalog; it reports missing requirements to the coordinator rather than downloading data itself.

Use Python, typed specifications, the shared storage adapter, and portable Parquet artifacts. Supabase PostgreSQL supplies the catalog and canonical observations. Use PostGIS for supported spatial operations after confirming the extension is enabled; execution runs through a backend storage adapter with scoped database access. Agent framework and LLM provider remain undecided; the executor must work with fixture recipes without an LLM.

## Query-driven architecture

Use an LLM planner to interpret the original question alongside the coordinator's validated `QuerySpec`, identify required evidence, inspect candidate dataset descriptions/schemas, and propose a typed `RecipeSpec`. Do not independently change resolved dates, region, access scope, or other query constraints; return unresolved ambiguity to the coordinator.

The architecture supports different query types and normalized dataset families without hardcoded movement/rainfall feature lists. Flexibility depends on available data, metadata, and supported operations. An LLM can propose a plan for an unfamiliar question; it cannot make unavailable evidence or unsupported calculations valid.

```text
Question + QuerySpec
    -> evidence requirements
    -> catalog search + bounded schema/sample inspection
    -> dataset suitability assessment
    -> typed recipe proposal
    -> deterministic validation and execution
    -> base artifact + source references + preparation report
```

Dataset descriptions and samples are untrusted data, not agent instructions. Bound catalog candidates, sample sizes, planning retries, execution cost, and output size. Supply schemas and summaries to the planner rather than entire datasets.

## Dataset suitability

Separate semantic relevance from technical eligibility. The LLM assesses whether a dataset measures the phenomenon needed by the query, what role it could play, and what limitations its description implies. Record the supporting metadata, structured assessment, and uncertainties for each inclusion or exclusion. Any narrative explanation comes from the planner and must cite the supplied metadata.

Deterministic checks establish access permissions, ready/validated status, readable storage, schema/types, units, entity namespaces, geographic/time coverage, and whether a supported join exists. A relevance score or classifier label cannot override these checks. Unknown meaning or coverage remains explicit and may require clarification or return insufficient data.

### Jev suitability assessor

Evaluate Jev by TypeSafe AI as the initial semantic suitability provider behind a replaceable adapter. Its structured decision primitives fit candidate assessment; the generative LLM remains responsible for interpreting complex requirements and proposing recipes. This is a proposed provider choice pending evaluation, not a claim that Jev outperforms an LLM on ecological datasets.

After deterministic eligibility checks, supply each candidate's query requirements, description, column meanings/units, coverage metadata, and bounded inspection summaries as state. Ask small independent questions:

- `Score`: relevance to the requested phenomenon, using an explicit ordered rubric from unrelated through directly useful.
- `Choice`: primary evidence, supporting context, irrelevant, or unknown, with defined category boundaries.
- `Noul`: whether the supplied metadata establishes a specific required measurement or temporal interpretation. Missing metadata must remain unknown in application logic; a probability is not proof of compatibility.

Compose these results in code to rank candidates. Use configurable, evaluated inclusion/review thresholds; uncertain candidates go to planner inspection rather than automatic rejection. Do not request free-text explanations from Jev or treat a low score as permission to discard all evidence for a query. Evaluate candidate sets jointly in the planner for complementary coverage and redundant products.

Initial assessment policy favors recall. Use an ordered usefulness rubric (unrelated, tangential, potentially useful, directly useful) and an evidence-role classification (primary evidence, supporting context, irrelevant, insufficient metadata). Assess each candidate against the relevant evidence requirements. Pass potentially/directly useful candidates to the planner, retain tangential candidates as reserves, inspect insufficient metadata, and exclude only clearly irrelevant candidates. Tune numeric thresholds on labeled examples rather than assuming a fixed probability cutoff.

Persist the reserve candidates and their assessments with the query context. When Stage 4 requests additional information, interpret its new evidence requirements and reassess relevant reserve candidates with Jev against that request. Prior assessments are not permanent labels. Apply current access/readiness checks before reuse, then validate and execute a derived recipe for any selected inputs. Expand catalog search if reserves cannot meet the request. Preserve the original recipe/artifact and link the new assessment and derived artifact to the Stage 4 request.

Compare Jev with an LLM assessor on labeled query/dataset pairs before making it the default. Measure useful-dataset recall, incompatible inclusions, uncertainty handling, probability calibration, latency, and total cost. Include unfamiliar dataset families and ambiguous descriptions. Select thresholds on development examples and evaluate on held-out examples. Vendor calibration claims do not establish calibration on this task.

Persist provider/model version, assessment rubric, supplied metadata references, scores/probabilities, and decision policy alongside pinned dataset versions. Provider failures return an explicit assessment failure or use a configured LLM fallback; never fabricate a score. Assessments do not alter deterministic eligibility rules.

Reference: [TypeSafe AI introduction](https://docs.typesafe.ai/introduction), which describes Choice, Score, Noul and composing atomic judgments in code.

## Initial execution capabilities

Implement a domain-neutral registry of typed, versioned operations:

1. Column selection, renaming, and typed filters.
2. Grouped aggregations with explicit keys, units, and null behavior.
3. Equality joins with declared keys, join type, and expected cardinality.
4. Temporal/as-of matching with direction, tolerance, availability constraints, and stable tie-break rules.
5. Spatial point-to-cell matching with explicit coordinate systems and overlap rules.
6. Preceding-window aggregations and approved deterministic derived calculations.
7. Quality/coverage summaries and artifact export.

Each operation defines its input/output requirements, parameters, validation, and deterministic implementation. Domain-specific calculations such as sampled movement distance are optional registered extensions. New dataset families can reuse existing operations; genuinely new calculations require implementing and testing a new operation. Arbitrary generated SQL/Python is outside the initial implementation.

The initial implementation does not train models, learn imputations, perform automated feature search, or process arbitrary raster imagery. Unsupported requests identify the missing data or capability explicitly.

## Recipe compilation and missing join keys

The planner emits a JSON-serializable typed operation graph, not executable SQL. Each step names a registered operation/version, input references, parameters, and expected output semantics. The recipe declares immutable dataset references, output grain/keys, column metadata, missingness rules, temporal constraints, and optional parent recipe reference.

A deterministic compiler validates the graph and maps supported operations into parameterized PostgreSQL/PostGIS queries for Supabase. Resolve table/column identifiers through trusted storage descriptors and an allowed operation registry; bind literal values as parameters. Execute through the backend database adapter, rather than exposing arbitrary SQL execution to the agent or browser. Save compiler/code version, recipe, and execution report with the artifact. Backend transport and credentials integrate with Stage 2 storage; they are not LLM inputs.

Assume supported normalized datasets expose at least time and location semantics, with exact field representations deferred to Stage 2 integration. Validate this assumption for each input; do not invent missing timestamps or geometries. Shared equality keys are optional. When keys do not exist, the planner can propose explicit preparation steps:

- Derive time buckets such as day/month using declared timezone and interval rules.
- Assign observations to an existing spatial cell or region using geometry containment/intersection rules.
- Aggregate each source to a common spatial/time grain before an equality join on derived keys.
- Use a supported direct spatial/temporal match, including nearest or preceding observations with bounded distance/time tolerances and stable tie-break rules.
- Use a validated identity crosswalk where actual entity equivalence is needed.

Derived keys and matching predicates are part of the saved recipe, with lineage back to original time/location fields. Spatial proximity does not establish entity identity or scientific relevance. Expose match distance, time difference, ambiguity, and unmatched coverage as appropriate. Planner retries may propose a supported alternative when validation rejects a join; keep retries bounded. If no defensible match exists, retain separate useful source references and report the missing linkage rather than forcing a join.

## Query-specific base dataset

The planner chooses a useful output grain from the query and evidence: an entity-day, site-month, individual event, or another supported key. There is no universal daily-animal table. Declare row keys, time semantics, column meanings/units, intended analysis, and quality requirements before executing joins.

Produce one primary `FeatureArtifact` for the current handoff. Attach references to other relevant normalized tables when flattening would lose detail or create ambiguous relationships. Queries needing multiple primary outputs require an agreed contract extension rather than silently changing v1.

Prepare the common calculations necessary to make the table usable and keep useful available columns where justified by the query. Preserve source access so Analysis can request additional features. Do not automatically materialize every plausible feature. The modeling agent owns predictive feature selection and evaluates variations.

Movement/rainfall remains one fixture example: animal-day observations aligned with preceding environmental measurements. A second example should use a different grain and join path, such as site-month habitat observations aligned with management activity. These examples verify reusable behavior rather than define supported query categories.

## Dataset selection and joins

Search ready descriptors using query requirements and supported catalog filters, then assess shortlisted candidates. Catalog search is not limited to named movement/rainfall families. Extending canonical families and search metadata requires agreement with Normalize.

### Candidate discovery and assessment flow

Defer the exact metadata/tag contract until integration with the Stage 2 Normalize flow. Recipe must accept different tag vocabularies and representations through a catalog adapter rather than hardcoding tag names or a single metadata layout. Preserve producer metadata and map available fields into search/inspection capabilities; advertise unsupported filters explicitly. Do not treat absent fields as verified facts or silently ignore a query constraint. Existing agreed dataset identity, access, and artifact contracts still apply.

Descriptions, column meanings, family, species/entity scope, geographic/time coverage, validation status, and access scope are useful metadata when available, rather than a newly imposed tag schema. Tags and embeddings are discovery aids, not proof of suitability. Final field mappings will be agreed with Normalize during integration. Stage 2 Normalize owns building and maintaining the semantic index; Recipe consumes it through the catalog search adapter.

The LLM converts the validated query into typed metadata filters and semantic search text for each evidence requirement. Validate filter fields and values against the catalog interface; execute searches through code rather than generated SQL. Resolve dates and preserve query constraints through the coordinator's `QuerySpec`.

```text
Query -> LLM-generated search requirements
    -> metadata/tag matches UNION semantic-search matches
    -> deduplicate by (dataset_id, version)
    -> deterministic candidate eligibility checks
    -> Jev usefulness categories/scores
    -> LLM recipe planning across useful candidates
    -> deterministic recipe/join validation and execution
```

1. Search metadata/tags and semantic descriptions/column meanings separately for each evidence requirement. Combine results by union, not intersection: a dataset can be discovered by either route. Semantic search can find related terminology or a dataset missing the expected tag. Apply access-scope and ready-status restrictions to both search paths.
2. Deduplicate immutable dataset versions while preserving retrieval reasons, matched tags, semantic scores, and the evidence requirements each candidate might satisfy. Prefer broad retrieval and underpruning so Jev can assess more potentially useful options. Avoid aggressive semantic-score cutoffs or exact-tag requirements; expand uncertain relevance candidates rather than excluding them early. Hard access/readiness restrictions still apply. Bound results per requirement with configurable generous limits so one family cannot crowd out supporting evidence; record truncation and allow bounded search expansion when coverage is inadequate.
3. Check every unique candidate with deterministic rules: current authorization, validation/readiness, readable storage, required structural metadata, types/units, entity scope, and relevant spatial/time coverage. A semantic match cannot bypass these checks. Distinguish eligible, ineligible, and unresolved candidates. Unknown coverage or meaning requires bounded inspection or clarification rather than automatic rejection or presumed eligibility.
4. Send eligible candidates' query context, tags, descriptions, schemas, and coverage summaries to Jev for usefulness assessment. Do not send entire tables. Route unresolved candidates through inspection first; keep exclusions and their reasons available in the preparation report.
5. The planner chooses a complementary set of useful datasets and proposes a recipe. Ranking alone does not establish that datasets can be combined.

Compatibility has two stages. Before Jev, perform candidate-level structural and coverage checks; do not require a finalized join. After recipe planning, validate the actual join keys, identity crosswalks, grain, cardinality, spatial/temporal matching rules, and forecast availability constraints. Verify row counts and uniqueness again during execution. No recipe executes solely because its datasets received favorable Jev scores.

Pin selected dataset versions and retain reasons for exclusions. Namespace entity identities by source/study unless an explicit validated crosswalk establishes equivalence. Resolve overlapping products, boundary matches, and competing observations with recorded priorities and stable tie-break rules.

Reduce inputs to the required grain when appropriate; do not aggregate away detail needed by the analysis. Every join declares expected cardinality and allowed unmatched behavior. Verify key uniqueness and row-count effects. Use left joins when preserving the primary observation population is intended; other join types must be justified in the recipe. Intentional one-to-many outputs require their own declared grain and bounded size. Unexplained row multiplication fails validation.

Windows specify endpoints, interval inclusion, lookback history, aggregation semantics, and minimum coverage. Fetch sufficient source history for the requested calculations while keeping output rows within query constraints. Distinguish absent values from observed zero, and never silently prorate intervals, fill values, or infer unit conversions.

## Analysis handoff

Preserve the existing v1 `query`, `recipe`, and `feature_artifact` handoff. The base table is the initial `FeatureArtifact`; it is ready for ordinary summaries and a first modeling baseline.

### Table description and preparation report

Hand Analysis a readable artifact reference plus a structured description, not just an anonymous table or a narrative summary. Include:

| Item | Required meaning |
| --- | --- |
| Artifact identity/access | Artifact ID/version, storage reference, format, access scope, row count, and validation-report reference |
| Table description | What the table represents, intended use, output grain and row keys, time/location semantics, and supported coverage |
| Column definitions | Names, types, meanings, units, null behavior, and recipe-step references for derived calculations |
| Preparation report | Selected/excluded datasets and reasons, filters, join/matching rules, matched/unmatched counts, ambiguity, missingness, coverage gaps, and limitations |
| Provenance | Immutable recipe and input/mapping versions, execution-code version, and source-record lineage references |
| Available context | Relevant readable source references, supported transformations, and a reference to persisted reserve-candidate assessments |

Use existing `FeatureArtifact` fields for identity, storage, grain, schema, and provenance. Place additive descriptions and preparation context under `extensions.recipe_context` until shared contract owners agree otherwise. Large reports and reserve lists may be referenced rather than embedded. Preserve access checks on all referenced artifacts. A narrative summary can accompany structured metadata but cannot replace it or invent column meanings.

Example description: "One row per site per month. Rainfall is the monthly total for the cell containing the site. Months without complete rainfall coverage have null totals." Analysis must verify that the supplied grain, features, and quality support its chosen method before modeling.

Propose additive metadata under `extensions.recipe_context`:

- Selected `DatasetVersion` descriptors and access-controlled storage references for relevant normalized source tables.
- Source coverage, column descriptions, limitations, and excluded-dataset reasons.
- Supported transformation operations, parameters, and limits.
- The initial recipe reference and how to request a derived recipe.

Pass references and metadata, not whole source tables in agent context. Analysis reads source observations through the shared adapter when needed. Existing consumers may ignore the extension and still consume the base artifact.

### Feature iteration

Expose a Python adapter initially:

```text
run(request) -> existing v1 response
derive_recipe(base_recipe_ref, transform_request) -> validated RecipeSpec or typed error
execute_recipe(recipe_ref) -> FeatureArtifact or typed error
```

The exact iteration request schema needs agreement with Analysis and the shared-contract owner. Requests include the query/access context, pinned base recipe reference, requested approved operations, and explicit feature time/cutoff rules. Iteration can select additional permitted columns, change supported window parameters, add approved calculations, or propose a new supported grain/join plan. Revalidate the entire derived recipe and its query semantics; unsupported operations return a typed error. Resource limits apply to every variation.

Stage 4 additional-information requests identify the base artifact/recipe, what evidence or columns are needed, why they are needed, and the required grain, time/location alignment, and coverage. Requests may use natural-language requirements interpreted by the planner or structured transformations; neither bypasses validation. Reassess tangential reserves against the new requirements, expand catalog search when needed, and return a new versioned artifact or explicit unmet requirements. Keep original artifacts intact and record request/parent lineage.

Example: Analysis requests preceding 3-, 7-, and 30-day rainfall totals. Recipe checks source history, validates temporal rules, and creates a versioned derived artifact. Analysis evaluates whether those columns help. Recipe does not choose the winning model or infer success from training fit.

Do not make access to the modeling agent a dependency of the initial run. A standalone query produces a query-specific base dataset when inputs are sufficient.

## Failure handling and response statuses

Use the existing v1 response envelope, always including `output`, warnings, and the typed error field. Do not introduce competing top-level status values.

| Outcome | Response | Behavior |
| --- | --- | --- |
| Ready | `ok` | Publish the artifact only after required evidence and output checks pass; warnings may describe remaining limitations |
| Usable subset | `partial` | Identify exactly which artifacts/columns are ready and which optional evidence was excluded; Analysis decides whether its method can use that subset |
| Missing required evidence/linkage | `insufficient_data` | Report unmet requirements, inspected candidates, and what additional evidence or linkage would resolve them; no claimed-ready artifact for the unmet objective |
| Consequential query ambiguity | `pending` | Coordinator persists a clarification job; include its `job_id` and additive clarification details for user resolution |
| Technical/unsupported-operation failure | `error` | Return a stable code, actionable message, and `retryable` flag; do not publish an unvalidated result |
| Active asynchronous work | `pending` | Return the durable job ID and unfinished stage; coordinator handles polling/resumption |

For clarification, the coordinator owns job creation and the user conversation. Recipe reports an internal clarification outcome containing the unresolved choice, why it matters, and suggested options; the coordinator translates it to the shared pending envelope with a real job ID. Never invent a job ID. Historical versus forecast intent, consequential grain choices, and unsupported assumptions may need clarification; routine implementation choices do not.

Use stable error codes such as `INVALID_RECIPE`, `UNSUPPORTED_OPERATION`, `JOIN_VALIDATION_FAILED`, `ARTIFACT_UNREADABLE`, and `PROVIDER_UNAVAILABLE`. Retry transient provider/database failures with bounded attempts through the coordinator. Do not repeatedly retry missing evidence, authorization denial, invalid recipes, or unresolved scientific meaning as transient failures. A planner may repair a rejected recipe within bounded attempts, with rejection reasons recorded.

Preserve failure/validation reports and upstream references for diagnosis and resumption. Failed executions cannot populate the ready-artifact cache. A useful independently validated subset may be returned explicitly as partial, but pending or failed tables must not be presented as ready. Recovery and new Stage 4 requests use the same eligibility, recipe-validation, and output-validation path.

## Temporal safety

Forecast requests carry a forecast cutoff and per-row feature times. Every predictor must respect observation interval completion and actual availability time when known, as well as the global cutoff. Retrieval time alone does not establish historical availability.

If availability metadata is absent, distinguish a retrospective historical analysis from a forecast backtest. Mark availability unverified and return insufficient data when strict point-in-time guarantees are required. Do not present uncertain availability as a leakage-free forecast dataset.

Recipe computes causal deterministic transformations. Analysis computes labels separately, splits by time, fits preprocessing only on training data, and evaluates feature variants on validation data while preserving the final test set.

## Validation, persistence, and caching

Validate recipes before executing: supported contract/operation versions, readable scoped inputs, required columns and units, output grain, spatial rules, temporal windows, and forecast eligibility.

Validate outputs: unique row keys, expected join counts, coverage, null semantics, numeric constraints, and traceability to source versions. Partial artifacts identify exactly which columns/inputs are usable; required missing evidence returns `insufficient_data`. Use the README's existing status envelope and typed error format.

Save every validated recipe immutably. Cache materialized artifacts by input and mapping versions, recipe operations/parameters, query filters, feature-time/cutoff rules, execution-code version, and access scope. Identical requests reuse artifacts; changed versions, windows, or scope cannot reuse stale results. Publish only after output validation succeeds.

## Implementation sequence

1. Agree on generic recipe schemas, grain/cardinality declarations, feature-time rules, storage access, and two fixture queries with different grains and dataset families. Publish safe handoff examples.
2. Build the operation registry and deterministic validator/executor against hand-authored typed recipes. Start with filtering, grouped aggregation, and equality joins; add temporal and spatial operations next.
3. Implement catalog inspection, hard eligibility checks, and the replaceable semantic suitability assessor.
4. Add LLM requirement interpretation and typed recipe planning early; exercise both fixture query types through the same planner interface. Reject invalid proposals through the deterministic validator.
5. Add Parquet export, saved recipes, preparation reports, provenance, and cache invalidation through shared adapters.
6. Add derived-recipe requests and demonstrate that Analysis can consume base and varied artifacts without Recipe-specific filesystem knowledge.

## Acceptance checks

- Fixtures for at least two distinct query types produce hand-checked rows and numeric values at different grains using the same planner/executor interfaces.
- Semantic relevance is recorded separately from hard eligibility; a highly ranked incompatible dataset cannot be executed.
- Metadata-only and semantic-only matches both enter the candidate union; duplicate dataset/version hits are assessed once, with both retrieval reasons preserved.
- Both discovery paths enforce access and readiness restrictions; unknown metadata produces an unresolved state and exact join incompatibilities fail recipe validation after planning.
- Planner tests cover invalid columns, unsupported operations, unresolved query meaning, unavailable evidence, and bounded retry exhaustion.
- Duplicate records, conflicting timestamps, cell-boundary matches, and overlapping products follow explicit rules without row multiplication.
- Missing rainfall remains null with coverage metadata; zero rainfall remains zero.
- Window boundaries and pre-query history behave correctly; future or not-yet-available predictors cannot enter strict forecast artifacts.
- Identical pinned requests hit cache; changed source/mapping versions, parameters, code versions, or access scope miss it.
- Unsupported operations and inadequate evidence produce typed responses.
- Every artifact resolves to its recipe, inputs, quality report, and readable Parquet data.
- Analysis can interpret the table grain, each column's meaning/units/null behavior, join limitations, and preparation counts from the handoff without reverse-engineering SQL.
- Ready, partial, insufficient-data, clarification, and technical-failure fixtures map to the shared envelope; pending responses use real coordinator job IDs and failed results never enter the ready cache.
- Analysis consumes the initial handoff, requests a supported transformation variation, and receives a new reproducible artifact while the original stays intact.

## Decisions to settle at integration

Agree on generic operation schemas, query-specific column descriptors, grain/cardinality declarations, temporal availability metadata, suitability-assessment evidence, resource limits, recipe persistence APIs, and the additive iteration contract. Select the planner provider/framework independently of the deterministic executor. Domain-specific defaults belong in versioned operation configuration rather than a universal feature schema.

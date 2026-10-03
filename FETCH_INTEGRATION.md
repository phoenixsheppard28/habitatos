# Fetch → raw-data ingestion integration

The retrieval component lives entirely in **`fetch_pipeline/`**. Its Python import
name remains **`fetch`**. It produces permitted raw files and a manifest per file;
the next layer owns parsing, validation, normalization, spatial/temporal alignment,
and publishing canonical datasets. Fetch does not produce maps or scientific
conclusions.

The integration boundary is:

```text
Coordinator sends question + species/region/dates + missing data requirements
    → Fetch returns status + warnings + output.raw_artifacts
    → Ingestion resolves each artifact:// reference in the shared archive
    → Ingestion validates and parses the original file into its own output storage
```

Use `output.raw_artifacts` as the machine-readable handoff. The natural-language
summary and terminal output are for inspection; the measurements stay in files.

## 1. Layout and setup

```text
habitatos/
├── FETCH_INTEGRATION.md              # this handoff contract
├── README.md                        # broader product specification
└── fetch_pipeline/
    ├── pyproject.toml               # dependencies and CLI entry points
    ├── README.md
    ├── .env.example
    ├── .env                         # local credentials; ignored
    ├── src/fetch/                   # Python implementation
    │   ├── models.py                # typed contracts
    │   ├── run.py                   # coordinator adapter
    │   ├── service.py               # deterministic source operations
    │   ├── archive.py               # storage and checksum helpers
    │   ├── connectors/
    │   └── SOURCES.md               # source details and limits
    ├── tests/fetch/                 # tests and synthetic CSV fixtures
    └── data/                        # local runtime output; ignored
        ├── raw/<artifact_id>/<version>/<filename>
        ├── manifests/<artifact_id>.json
        ├── artifact_index.json
        └── runs/<run_id>/
            ├── request.json
            ├── response.json
            ├── manifest.jsonl
            └── events.jsonl        # environmental CLI only
```

From the repository root:

```bash
source .venv/bin/activate
python -m pip install -e "./fetch_pipeline[dev]"
python -m fetch run                  # offline fixture smoke test
python -m pytest -c fetch_pipeline/pyproject.toml fetch_pipeline/tests
```

For a new checkout, create a Python 3.11+ virtual environment first. Copy
`fetch_pipeline/.env.example` to `fetch_pipeline/.env` if needed. Natural-language
agent runs require `OPENROUTER_API_KEY`; deterministic fixture and public
environmental downloads do not. The archive currently uses `fcntl` locking, so
macOS/Linux are supported.

The default archive root is `fetch_pipeline/data/`, independent of the working
directory. Override it through the shell **before starting/importing Python**:

```bash
export HABITAT_DATA_DIR=/absolute/shared/habitat-data
```

Both stages must use the same archive root or share/mount that directory. Setting
this variable only in `.env` is not sufficient: archive paths initialize before
CLI dotenv loading. No database, HTTP server, or object-storage transport is
implemented yet. This component is currently intended for an editable install;
the fixture demo relies on the included tests directory.

## 2. Input: coordinator request

Use `fetch.run.run(request, use_agent=True)` for the contract adapter. It accepts
a JSON-compatible dictionary or `FetchRequest`. `use_agent=False` (the default)
is the deterministic **fixture-only** path, not live discovery.

```json
{
  "contract_version": "1.0",
  "request_id": "fetch-request-001",
  "query_id": "conservation-query-001",
  "access_scope": "public",
  "input": {
    "query": {
      "query_id": "conservation-query-001",
      "question": "Find movement and rainfall records for the specified region and dates.",
      "task_type": "discovery",
      "species": ["Connochaetes taurinus"],
      "region": {"name": "study area", "bbox": [36, -2, 37, -1]},
      "time_range": {"start": "2025-01-01", "end": "2025-01-16"}
    },
    "requirements": {
      "species": ["Connochaetes taurinus"],
      "data_kinds": ["animal_locations", "rainfall_observations"],
      "bbox": [36, -2, 37, -1],
      "start": "2025-01-01",
      "end": "2025-01-16"
    }
  }
}
```

This illustrates the contract, not a claim that the example wildlife dataset is
available. Keep the two `query_id` values consistent. Use a new `request_id` per
request; run directories receive separate random IDs.

| Field | Meaning |
| --- | --- |
| `contract_version` | Currently only `"1.0"` is accepted. |
| `request_id`, `query_id` | Caller-owned identifiers echoed in the response. |
| `access_scope` | Public handoffs only through `run`; other scopes return an error. |
| `query.question` | Natural-language retrieval request. |
| `query.task_type` | `discovery`, `historical`, or `forecast`; all only drive retrieval here. |
| `query.species` | Requested common/scientific names, not validated taxonomy. |
| `query.region` | Optional descriptive object; use `bbox` for structured geography. |
| `query.time_range` | Optional ISO date/timestamp strings. Resolve relative dates upstream. |
| `requirements` | Missing species, kinds, bounds, and dates the coordinator wants fetched. |

Bounding boxes are `[west, south, east, north]` in WGS84 degrees. Environmental
retrieval requires concrete bounds and inclusive `YYYY-MM-DD` dates. Split
antimeridian-crossing areas upstream. Supported kinds include `animal_locations`,
`rainfall_observations`, `vegetation_observations`, and `surface_reflectance`.
These are routing hints; validate the actual file schema at ingestion.

```python
from fetch.run import run

response = run(request, use_agent=True)  # request is the dictionary above
payload = response.model_dump(mode="json")
raw_artifacts = payload["output"]["raw_artifacts"]
```

The synchronous wrapper runs its own event loop. Inside an existing async service,
use `await fetch.run.run_with_agent(FetchRequest.model_validate(request))` instead;
that lower-level coroutine returns the response but does not write a run report.
Call `save_run(request, response.model_dump(mode="json"))` if persistence is needed.
Malformed model inputs raise Pydantic validation errors before a response exists.

## 3. Output: response and statuses

The contract response always includes `output.raw_artifacts`, even if it is empty:

```json
{
  "contract_version": "1.0",
  "request_id": "fetch-request-001",
  "query_id": "conservation-query-001",
  "access_scope": "public",
  "status": "insufficient_data",
  "output": {"raw_artifacts": []},
  "warnings": [],
  "error": null,
  "extensions": {"agent_summary": "...", "coverage_verified": false}
}
```

| Status | Consumer behavior |
| --- | --- |
| `ok` | Consider listed files for ingestion; still validate each file and coverage. |
| `partial` | Process eligible listed files; retain warnings and unresolved requirements. |
| `insufficient_data` | Do not interpret the result as evidence of animal absence. No adequate retrieval was established. |
| `error` | Inspect `error.code`, `message`, and `retryable`; do not infer successful retrieval. |
| `pending` | Reserved by the contract; no background worker currently emits it. |

An agent failure after a download can return `partial` with both artifacts and an
error. `ok` is a retrieval status, not proof that the data answers the scientific
question. `agent_summary` is explanatory text, never the ingestion interface.
It can currently be empty. `coverage_verified: false` means the fetcher has not
proved complete coverage or scientific suitability; missing extension fields are
also not proof of verification.

**Other entry points use different envelopes:**

- `python -m fetch agent "..."` and `python -m fetch run --agent ...` print the
  contract response above.
- `python -m fetch download <dataset_id>` prints top-level `raw_artifacts` and
  `run_directory` on success, or a source error dictionary on failure.
- `python -m fetch environment ...` prints top-level `raw_artifacts`, `status`,
  `warnings`, `downloaded_bytes`, and `run_directory`; its saved `response.json`
  also contains per-file `outcomes`. Discovery-only status is `discovered` and
  does not indicate downloaded files.
- `fetch.service.download_dataset(id)` returns one `RawManifest` or an error
  dictionary; it archives the file but does not create a run directory itself.
- `fetch.connectors.environment.fetch_environment(...)` returns a dictionary
  with a top-level `raw_artifacts` list; the CLI owns run-report persistence.

Use the contract adapter in your coordinator, or explicitly adapt one of these
other envelopes. Do not assume every CLI command returns the contract envelope.

## 4. RawManifest: one entry per file

The exact model is in
[`fetch_pipeline/src/fetch/models.py`](fetch_pipeline/src/fetch/models.py).

| Field | Type / interpretation |
| --- | --- |
| `artifact_id`, `version` | Strings identifying the archived artifact. |
| `created_at`, `retrieved_at` | ISO-8601 UTC timestamps; retrieval time is not observation time. |
| `access_scope` | Scope of this file. Check it separately from the response's scope. |
| `source.name`, `source.url`, `source.study_id` | Provider, source/landing URL, optional provider identifier. |
| `storage.uri` | `artifact://<artifact_id>/<version>`; points to a directory under the archive root. |
| `storage.format` | File format such as `csv`, `json`, `tif`, `tif.gz`, or `zip`. |
| `checksum` | `sha256:<hex>` of the archived file's actual bytes. |
| `coverage.species` | Provider/connector-reported species, possibly empty. |
| `coverage.bbox`, `coverage.start`, `coverage.end` | Actual reported/observed coverage, or null; not automatically the requested coverage. |
| `rights.license`, `retention_allowed`, `reuse_allowed`, `attribution` | Source terms; null means unknown, not permission. |
| `extensions.filename`, `extensions.bytes` | Filename inside the artifact directory and file size; older manifests may omit these. |
| `extensions.data_kind` | Optional routing hint; not guaranteed on every connector or older cached artifact. |
| Other `extensions` | Provider-specific metadata, quality information, time semantics, and limitations. |

Consumers should ignore unfamiliar optional extensions. `RawManifest` currently
has no independent schema-version field; version compatibility is indicated by the
outer `contract_version`. Artifact `version` is an artifact identifier component,
not the contract version. Changed content currently receives a **new artifact ID**
with version `"1"`; do not assume numeric versions increment under the same ID.

## 5. Resolve files and hand off to ingestion

Prefer the per-run manifest or `response.output.raw_artifacts`, not a recursive
scan of all `data/raw/` files. The global archive can include unrelated requests,
older files, or account-scoped records.

This example is executable against the offline fixture response and resolves
both current and older manifests. It validates the archive reference and bytes;
format, rights, and observation validation remain the ingestion layer's job.

```python
from pathlib import Path
from fetch import paths
from fetch.archive import sha256_file
from fetch.models import RawManifest


def resolve_for_ingestion(record: dict, allowed_scope: str = "public") -> Path:
    manifest = RawManifest.model_validate(record)
    if manifest.access_scope != allowed_scope:
        raise ValueError("Artifact access scope does not match consumer")
    expected_uri = f"artifact://{manifest.artifact_id}/{manifest.version}"
    if manifest.storage.uri != expected_uri:
        raise ValueError("Invalid artifact storage reference")
    root = paths.RAW_ROOT.resolve()
    folder = (root / manifest.artifact_id / manifest.version).resolve()
    if not folder.is_relative_to(root):
        raise ValueError("Artifact path escapes the configured archive")
    files = [p for p in folder.iterdir() if p.is_file()]
    if len(files) != 1:
        raise ValueError("Expected exactly one archived file")
    file = files[0].resolve()
    if not file.is_relative_to(root):
        raise ValueError("File escapes the configured archive")
    if sha256_file(file) != manifest.checksum:
        raise ValueError("Raw file checksum mismatch")
    return file


# response is the FetchResponse from run(...)
for record in response.model_dump(mode="json")["output"]["raw_artifacts"]:
    file = resolve_for_ingestion(record)
    # Your next layer: validate rights and file schema, then dispatch a parser.
    print(record["artifact_id"], record["storage"]["format"], file)
```

Recommended normalization request, following the repository's existing boundary:

```python
normalize_request = {
    "contract_version": response.contract_version,
    "request_id": "normalize-request-001",  # new stage request ID
    "query_id": response.query_id,
    "access_scope": response.access_scope,
    "input": {
        "raw_artifacts": [a.model_dump(mode="json") for a in response.output.raw_artifacts]
    },
}
```

This is a proposed call into the **next layer**; no normalization implementation is
included here. Pass file references and manifests, not entire CSVs/rasters in JSON
or LLM context. Across machines, copy/mount the archive with its relative layout,
or implement an artifact resolver backed by object storage. `artifact://` is an
application reference, not an HTTP URL or a globally resolvable download link.

## 6. Source-specific ingestion behavior

| Output | What ingestion must handle |
| --- | --- |
| Movebank public preview JSON | Nested individuals/locations; only a small sample. Check `movebank_download_mode == public_preview` and do not publish it as full movement tracks. |
| Movebank authenticated CSV | Preserve animal IDs, timestamps, coordinates, and quality flags. Conservatively marked `movebank-account`; excluded from public agent receipts. |
| Sentinel-2 TIFFs | Separate reflectance bands and SCL classification. Native grids can differ; mask/resample and compute indices downstream. |
| MODIS TIFFs | Separate NDVI, EVI, VI Quality, and pixel reliability. Preserve scale 0.0001 for NDVI/EVI and 16-day composite semantics. |
| CHIRPS `.tif.gz` | Decompress in ingestion staging. Global daily rainfall in mm; interval end is exclusive when flagged in metadata. |
| Zenodo file | Arbitrary research format; inspect schema and reuse terms rather than treating every CSV as tracks. |
| Fixtures | Synthetic records, for integration testing only. |

Preserve the archived original. Write transformed datasets separately and link
back to `artifact_id`, `version`, and `checksum`. Deduplicate ingestion by artifact
identity plus checksum and your parser/mapping version; a cache hit can appear in
multiple run manifests. The fetch cache verifies local integrity but does not
currently revalidate upstream freshness.

For environmental CLI runs, `events.jsonl` and `manifest.jsonl` are appended as
files complete. Read only complete newline-terminated records and deduplicate on
restarts. Ingest `downloaded`/`cached` artifacts, not `discovered`, `downloading`,
`failed`, or `blocked` events. Agent run reports are written after the agent ends;
there is no durable scheduling or exactly-once event delivery guarantee.

## 7. Known limitations relevant to integration

- Unauthenticated Movebank search still uses a two-study demo index. The
  investigation confirmed the missing Kenya wildebeest study, but live repository
  discovery was **not implemented** before this consolidation. Empty search results
  must not be interpreted as proof that no dataset exists.
- An agent can return an empty summary and no files. Treat this as an unsuccessful
  retrieval requiring follow-up, not as a completed scientific answer.
- General web search, geocoding, normalization, map rendering, and scientific
  sufficiency checks are not implemented.
- Environmental retrieval is bounded and may return only a few scenes/days. Read
  warnings and request limits; do not silently present these as complete coverage.
- The archive and credentials were moved intact from root `data/` and `.env` into
  `fetch_pipeline/`. Existing `artifact://` references resolve under the new data
  root. Historical absolute paths in reports were preserved and may show the old
  location; resolve through the manifest rather than those diagnostic paths.

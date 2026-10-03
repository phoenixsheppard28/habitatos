# Fetch lane: run it and hand off the files

This implements retrieval only. It does not normalize rasters, compute vegetation
indices, infer movement, or prove that a question has sufficient evidence.

## Quick start

```bash
# Run from the repository root after installing the component.
source .venv/bin/activate
python -m pip install -e "./fetch_pipeline[dev]"

# Movebank first: a known study, no LLM needed.
python -m fetch download movebank:2911040

# Search/select with the existing OpenRouter agent (.env needs OPENROUTER_API_KEY).
python -m fetch agent "Find public Galapagos albatross tracking on Movebank. Download it and report whether it is only a preview."

# Discover satellite assets without transferring imagery.
python -m fetch environment --bbox 36 -2 37 -1 \
  --start 2025-01-01 --end 2025-01-16 --discover-only

# Fetch environmental context. This example is Kenya, not the albatross study.
python -m fetch environment --bbox 36 -2 37 -1 \
  --start 2025-01-01 --end 2025-01-16 \
  --sources modis chirps --max-items 1 --max-days 3 --max-mb 128

# The agent can invoke the same environmental downloader.
python -m fetch agent "Fetch MODIS Terra and CHIRPS for WGS84 bbox [36,-2,37,-1], January 1–16, 2025. Report truncation and coverage gaps."

# Fixture-only integration path, without network or model credentials.
python -m fetch run
```

Coordinates are **west south east north**, in WGS84 degrees. Dates are inclusive
`YYYY-MM-DD`. Resolve these from the actual study/query before downloading; the
agent must ask when they are missing. A separate wildlife study and environmental
example do not demonstrate overlapping evidence.

Set `HABITAT_DATA_DIR=/absolute/output/path` in the shell before starting Python to
change the destination. No new Python dependencies are required.

## Sources and limits

| Source | Retrieval | Important limitations |
| --- | --- | --- |
| Movebank | `movebank:<study_id>`; public JSON preview, or authenticated CSV | Without credentials, search is a two-study demo index, not a global catalog. Preview is at most one event per individual and cannot establish movement. |
| Planetary Computer Sentinel-2 | `sentinel-2-l2a`; B02/B03/B04/B08/B11/B12/SCL | Full scene assets, not bbox-clipped downloads. Reflectance bands, not computed indices. Cloud-cover filtering does not guarantee cloud-free pixels. |
| Planetary Computer MODIS | `modis-13Q1-061`; NDVI/EVI/VI Quality/pixel reliability | Terra MOD13Q1 only. Historical entries can have empty platform fields, so product IDs are checked. 16-day composites are not daily observations. |
| UCSB CHIRPS | v2 daily p05 `.tif.gz` originals | Full 50°S–50°N grid, 0.05°, accumulated rainfall in mm. Northern Ontario can be outside coverage. Missing/unpublished dates are reported as failures. |
| Zenodo | `zenodo:<record_id>`; optional agent fallback search | Existing connector selects one file per record, with a 10 MiB cap; it is not an exhaustive repository export. |
| Fixtures | `fixture-movement-001`, `fixture-rainfall-001` | Synthetic antelope/rainfall data; explicitly request fixtures for demos. |

Movebank credentials (`MOVEBANK_USERNAME` / `MOVEBANK_PASSWORD`) permit an attempt,
not guaranteed study access. If a license gate is returned, review and accept it
on Movebank, then retry. The fetcher does not automatically accept study terms.
Authenticated downloads are conservatively tagged `movebank-account` and excluded
from public agent handoffs; use their local manifests for appropriately scoped
integration. Downloaded public previews retain the provider's JSON fields (JSON is
serialized locally); full CSV bytes are preserved. Observed bounds describe returned
events, not the entire study.

Environmental defaults are one scene per imagery product, three CHIRPS days,
512 MiB per file and 2 GiB total successful payload bytes per tool call. Increase
`--max-items` (up to 100), `--max-days` (up to 366), `--max-file-mb`, and `--max-mb`
when ready for volume. Catalog pagination is bounded to ten pages. These limits
are per invocation, not an overall agent-session budget. Downloads stream to disk;
transient errors retry up to three times. Failed attempts can use additional
bandwidth. Transfers are sequential. There is no durable background worker yet.

## Handoff

The default data root is `fetch_pipeline/data/` relative to the repository.
Paths below are relative to the component folder. Every CLI run writes
`data/runs/<unique-id>/`:

- `request.json`: exact request and limits.
- `response.json`: results, warnings, actual artifact references, and status.
- `manifest.jsonl`: only artifacts retrieved or reused for this request.
- `events.jsonl`: environmental discovery, downloading, downloaded, cached,
  blocked, and failed events, appended while retrieval runs.

The raw archive is shared across runs:
`data/raw/<artifact_id>/<version>/<filename>`, with manifests under
`data/manifests/` and an index at `data/artifact_index.json`.
`artifact://<id>/<version>` resolves to that directory via
`fetch.archive.resolve_artifact_path(manifest)`. `extensions.filename` identifies
the file; `extensions.bytes` gives its size. Manifests include SHA-256, source URL,
actual bounds/dates, rights, and provider metadata. Satellite asset metadata keeps
scale/offset/quality and CRS information when supplied. MODIS NDVI/EVI scaling is
0.0001; ingestion should read retained metadata rather than assume every product
uses that scale. CHIRPS interval ends are exclusive; decompress gzip at ingestion.

Repeated downloads reuse intact cached files; corruption triggers retrieval.
New checksums create distinct immutable artifacts. Live source caches do not
currently check for upstream revisions, so this is not a freshness guarantee.
Archive writes use a process lock and atomic JSON replacement on macOS/Linux.
A failed transfer never registers a usable artifact. Per-source failures do not
stop other environmental sources. Inspect warnings even when files exist.

`ok` means retrieval completed within selected limits, not scientific sufficiency.
`partial` includes gaps, previews, or truncation. A `discovered` entry is only
metadata. `coverage_verified: false` means there has been no pixel/track validation
or proof of complete temporal/spatial coverage. Actual file bounds are never replaced
with requested bounds. Provider license values and links are retained; unknown
reuse/retention permission stays null.

The contract adapter `fetch.run.run(request, use_agent=True)` collects actual tool
receipts, including cache hits. It never reruns fixtures to fabricate agent results.
Successful downloads survive a later agent failure as a partial response.

## What to add after this works

Choose an accessible study with enough repeated GPS positions and known dates.
Use its actual bounds and dates for a small environmental run, then hand the manifest
to ingestion. Expand limits only after that handoff works. General web discovery,
ArcGIS, FIRMS, population surveys, and automatic spatial/time alignment remain
future connectors/workflows; they are not implemented by this change.

## Provider references

- [Movebank API](https://github.com/movebank/movebank-api-doc/blob/master/movebank-api.md)
- [Sentinel-2 STAC collection](https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-2-l2a)
- [MODIS STAC collection](https://planetarycomputer.microsoft.com/api/stac/v1/collections/modis-13Q1-061)
- [Planetary Computer signing](https://planetarycomputer.microsoft.com/docs/concepts/sas/)
- [CHIRPS download directory](https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_daily/tifs/p05/)
- [CHIRPS product documentation](https://chc.ucsb.edu/data/chirps)

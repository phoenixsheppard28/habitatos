# Habitat Watch Fetch

Self-contained retrieval component for the Habitat Watch ingestion pipeline.
Python package name: `fetch`. All source, tests, fixtures, package configuration,
source documentation, and local runtime data live in this folder.

From the repository root:

```bash
source .venv/bin/activate
python -m pip install -e "./fetch_pipeline[dev]"
python -m fetch run
python -m pytest -c fetch_pipeline/pyproject.toml fetch_pipeline/tests
```

For a fresh environment, first run `python3 -m venv .venv`. The shared virtual
environment stays at the repository root and is not part of this portable component.
The existing local `.env` and `data/` were moved here intact and remain gitignored.
On a new checkout, copy `.env.example` to `.env` here and supply any needed keys.
Never commit `.env` or downloaded data.

- [Integration contract and examples](../FETCH_INTEGRATION.md)
- [Sources, CLI commands, and limits](src/fetch/SOURCES.md)
- [Typed request, response, and manifest models](src/fetch/models.py)

Default outputs: `fetch_pipeline/data/`. Set `HABITAT_DATA_DIR` in the process
**before importing** `fetch` to use a shared or external archive root. Existing
`artifact://` references remain valid after moving the archive. Historical reports
may contain old absolute `run_directory` paths; these diagnostic paths are not
artifact identifiers and were preserved as originally recorded.

Current limitations include the two-study unauthenticated Movebank demo search,
no general web discovery, and no normalization or map rendering. The investigated
wildebeest discovery fix has not been implemented; this consolidation preserves
the current implementation and documents that gap explicitly.

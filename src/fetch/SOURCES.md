# Fetch lane — supported sources (MVP)

## Fixture catalog (always available)

| Dataset ID | Kind | File | Notes |
| --- | --- | --- | --- |
| `fixture-movement-001` | `animal_locations` | `tests/fetch/fixtures/sample_tracks.csv` | Demo antelope tracks |
| `fixture-rainfall-001` | `rainfall_observations` | `tests/fetch/fixtures/sample_rainfall.csv` | Demo rainfall grid |

## Live sources (not wired yet)

- **Movebank** — planned tracking connector; requires study-specific access per [Movebank data access](https://www.movebank.org/cms/movebank-content/access-data).

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # add OPENROUTER_API_KEY

# OpenRouter agent (interactive)
python -m fetch agent "Find demo antelope movement fixtures and download them"

# Contract-style run without LLM (CI-friendly)
python -m fetch run --question "Demo fixtures"

# With OpenRouter agent loop
python -m fetch run --agent --question "Download movement and rainfall fixtures"
```

Artifacts land in `data/raw/` with manifests in `data/manifests/` (gitignored).

# Habitat Watch web workspace

The workspace uses TypeScript, Leaflet, and Vite.
The Python API reads published observations from the configured PostgreSQL database.
Maps use OpenStreetMap tiles with contributor attribution.

## Start development

Run these commands from the repository root:

```sh
uv sync
pnpm --dir web install
```

Configure `HABITAT_DATABASE_URL` in the root `.env` file.
Configure `ANTHROPIC_API_KEY` to enable the assistant.
Movebank study retrieval also uses `MOVEBANK_USERNAME` and `MOVEBANK_PASSWORD`.

Start the backend in one terminal:

```sh
uv run habitat-web
```

Start the frontend in another terminal:

```sh
pnpm --dir web run dev
```

Open http://localhost:5173.
Vite forwards `/api` requests to http://127.0.0.1:8000.
The backend also serves the built workspace from `web/dist`.

## Build and verify

```sh
pnpm --dir web run build
pnpm --dir web test
pnpm --dir web run format:check
uv run pytest tests/test_web_api.py
```

Use `pnpm --dir web run format` to format the frontend source.

## Workspace data

Select a dataset from the public catalog.
The catalog contains ready dataset versions and excludes development fixtures.
The workspace displays loading, empty, and connection error states without substitute records.

The timeline uses months with actual observations.
The timeline filters the map, table, chart, overview, and export.
The table supports filtering and pagination.
The provenance view contains dataset identifiers, versions, coverage, source links, licenses, and attribution.

Movement records contain the last good GPS fix per animal and UTC day.
Daily displacement connects fixes on consecutive days only.
Missing displacement values stay null.

Satellite maps display the latest measurement per cell through the selected month.
NDVI is the default map variable when NDVI is available.
Other variables appear in the table and monthly summaries.
Monthly summaries separate environmental variables.

The API limits map, table, and export responses to 20,000 records.
The workspace identifies truncated responses.
Database summaries cover every observation in the selected version.
The backend caches eight immutable dataset snapshots.
Each request checks the public catalog before it uses a cached snapshot.

## Assistant

The assistant sends questions and recent conversation messages to the Python API.
The API calls the configured model and exposes three bounded tools:

- Read database summaries and source citations.
- Prepare features through Recipe, then execute historical Analysis.
- Retrieve, normalize, and publish data through registered source connectors.

Retrieval requires an explicit source, region, date range, and any required study or package identifier.
Environmental retrieval accepts at most one year and 25 square degrees per request.
Successful publication refreshes the catalog.
Analysis returns an insufficient-data status when compatible evidence is unavailable.
The assistant does not generate unsupported forecasts.

## Local imports and exports

Import WGS84 GeoJSON or CSV with `latitude`/`longitude` or `lat`/`lon` columns.
Files must contain 1–10,000 features and must not exceed 5 MB.
Imports are browser map layers.
Imports are not sent to the backend.
Database analysis and exports use the selected published dataset.

Export downloads the loaded observations through the selected month as GeoJSON.
Exports include the dataset version, observation grain, source citations, and truncation status.

## Source modules

| Module | Responsibility |
| --- | --- |
| `index.html` | Workspace structure and accessible controls |
| `src/workspace/main.ts` | Initialization, imports, and exports |
| `src/workspace/types.ts` | API and workspace types |
| `src/workspace/api.ts` | HTTP requests and error handling |
| `src/workspace/store.ts` | Catalog, selection, timeline, and request cancellation |
| `src/workspace/map.ts` | Leaflet observations, coverage, and local layers |
| `src/workspace/views.ts` | Tables, charts, overview, and provenance |
| `src/workspace/sidebar.ts` | Dataset navigation and object search |
| `src/workspace/timeline.ts` | Timeline controls and playback |
| `src/workspace/assistant.ts` | Conversation requests and responses |
| `src/workspace/panels.ts` | Sidebar controls and assistant positioning |
| `src/workspace/imports.ts` | CSV parsing and geographic validation |
| `src/workspace.css` | Desktop and mobile layouts |
| `../src/habitat/web.py` | Catalog, observation endpoints, and static serving |
| `../src/habitat/web_assistant.py` | Model calls and integration with retrieval, Recipe, and Analysis |

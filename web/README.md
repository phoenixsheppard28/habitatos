# Habitat Watch web workspace

This directory contains the main Leaflet UI and its Vite configuration.
Make all UI changes in this directory.
The UI uses standard OpenStreetMap tiles and a Databricks-style sidebar for data, visualizations, and analysis objects.
Start the frontend without GeoServer to explore the synthetic overlays, tables, charts, and summaries.

The React/OpenLayers service implementation is in `src/`. Its catalog and adapters support public, read-only WMS, WMTS, and WFS endpoints.
The main Leaflet UI uses local synthetic fixtures. The main UI does not consume GeoServer services.
The local bootstrap uses GeoServer's authenticated REST API to publish fixtures.

## Start GeoServer and the samples

Docker Desktop or another Docker Compose implementation is required.

```sh
cd web
cp .env.geoserver.example .env.geoserver
docker compose --env-file .env.geoserver up -d
docker compose --env-file .env.geoserver logs -f geoserver-init
```

The one-shot `geoserver-init` service waits for GeoServer, creates the `habitat` workspace and PostGIS store, publishes four fixture tables, applies styles, and enables time dimensions. It is safe to run again. GeoServer is available at http://localhost:8080/geoserver.

To reset only this local sample stack, stop it normally with `docker compose --env-file .env.geoserver down`. Add `--volumes` only when you intentionally want to delete the local GeoServer configuration and sample database.

## Start the frontend

The frontend installs and builds without Python:

```sh
cd web
npm install
npm run dev
```

Open http://localhost:5173. Vite serves `index.html` and builds the workspace into `dist/`.
The `/geoserver` proxy supports development of the separate service adapters.

Useful checks:

```sh
npm test
npm run build
docker compose --env-file .env.geoserver config
```

## Workspace objects

The Databricks-style sidebar contains three sections:

- **Data:** movement, rainfall, vegetation, study boundary, and local imports.
- **Visualizations:** movement map, location table, and monthly distance chart.
- **Analysis:** movement overview and a comparison of movement with generated rainfall.

Select an object to open the object. Use the search field to filter objects.
Use the timeline to select the last month for the map, table, chart, and analysis.
The map uses Leaflet 1.9.4 and standard OpenStreetMap tiles with contributor attribution.

Open Habitat assistant from the sidebar. Drag the assistant header, or use arrow keys when the header has focus.
Try “show rainfall”, “hide rainfall”, “show vegetation”, “show August”, or “zoom to the tracks”.
Press Enter to send a question. Press Shift+Enter to insert a new line.

Add GeoJSON in WGS84 or CSV with `latitude`/`longitude` or `lat`/`lon` columns.
Files may contain up to 10,000 features and must not exceed 5 MB.
Imports remain in the browser session. Select an imported feature to inspect attributes.
Export downloads the synthetic movement locations through the selected month.

## Architecture

- `index.html` defines the main workspace and object sidebar.
- `src/sampleMap.js` provides OpenStreetMap, synthetic overlays, point inspection, and the timeline.
- `src/workspaceObjects.js` provides navigation, search, tables, charts, and calculated summaries.
- `src/interactions.js` provides assistant commands, file imports, and exports.
- `src/workspace.css` defines desktop and mobile layouts.
- `src/catalog/` defines the versioned layer contract and asynchronous catalog provider. Catalog entries contain data and capabilities, never OpenLayers instances.
- `src/map/adapters/` owns creation, time updates, service errors, and disposal for WMS, WMTS, bounded WFS, and temporary local vectors.
- `src/App.tsx` contains the separate React/OpenLayers service workspace.
- `geoserver/` contains sample PostGIS data, styles, and the trusted local publishing bootstrap.
- `docs/layer-compatibility.md` records the supported standards path and known gaps.

## Publishing boundary

The frontend may receive public service URLs, layer names, bounds, coordinate systems, time metadata, attribution, and supported actions. It must not receive GeoServer administrator credentials or call the REST configuration API.

In production, the Python pipeline will hand an approved dataset to trusted backend publishing tooling. That backend will write or register spatial data, configure GeoServer through authenticated server-side calls, and update the catalog API. The browser will then read the catalog and public map services. Local file imports remain browser-only and are not uploaded or published.

## Current limits

The main UI uses deterministic assistant commands and synthetic records.
Local imports are map layers. The movement table, chart, and summaries use the built-in sample tracks.
The rainfall grid is static. Monthly rainfall values in the point records support the comparison table.
The service adapters limit WFS requests to the current map extent and a feature-count cap.

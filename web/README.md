# Habitat Watch web workspace

This directory replaces the static `preview/` with a React, TypeScript, Vite, and OpenLayers app. The original preview remains available until the React behavior has been accepted.

The browser consumes public, read-only WMS, WMTS, and WFS endpoints. The local bootstrap uses GeoServer's authenticated REST API to publish fixtures, but those credentials never enter the frontend bundle. The sample movement and environmental layers are synthetic and are labelled as samples in the catalog and UI.

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

Open http://localhost:5173. Vite proxies `/geoserver` to the local service so WMS, WMTS, and WFS requests are same-origin during development. For a separately hosted service, copy `.env.example` to `.env.local` and set `VITE_GEOSERVER_URL` to its public read-only GeoServer URL.

Useful checks:

```sh
npm test
npm run build
docker compose --env-file .env.geoserver config
```

## Architecture

- `src/catalog/` defines the versioned layer contract and asynchronous catalog provider. Catalog entries contain data and capabilities, never OpenLayers instances.
- `src/map/adapters/` owns creation, time updates, service errors, and disposal for WMS, WMTS, bounded WFS, and temporary local vectors.
- `src/App.tsx` connects catalog state to the layer panel, OpenLayers map, timeline, feature inspector, imports, exports, and deterministic assistant.
- `geoserver/` contains sample PostGIS data, styles, and the trusted local publishing bootstrap.
- `docs/layer-compatibility.md` records the supported standards path and known gaps.

## Publishing boundary

The frontend may receive public service URLs, layer names, bounds, coordinate systems, time metadata, attribution, and supported actions. It must not receive GeoServer administrator credentials or call the REST configuration API.

In production, the Python pipeline will hand an approved dataset to trusted backend publishing tooling. That backend will write or register spatial data, configure GeoServer through authenticated server-side calls, and update the catalog API. The browser will then read the catalog and public map services. Local file imports remain browser-only and are not uploaded or published.

## Current limits

The assistant remains deterministic and sample-oriented. WFS is intentionally bounded by the current map extent and a feature-count cap. The sample WMS time dimension uses full timestamp values; GeoWebCache time behavior depends on its dimension configuration and may create separate cache keys. Production deployments need authentication and authorization at the service or reverse-proxy layer for any non-public data.

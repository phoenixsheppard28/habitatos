# Habitat Watch UI preview

Run from the repository root:

```sh
python3 -m http.server 8765 --bind 127.0.0.1 --directory preview
```

Open http://127.0.0.1:8765.

The ArcGIS-inspired workspace contains a map, collapsible layers, timeline, and floating assistant. Drag the assistant by its header, or focus the header and use arrow keys (Shift moves farther). Minimize or close it to reveal the map, and reopen it from the assistant tool.

Try “show rainfall”, “hide rainfall”, “show vegetation”, “show August”, “zoom to the tracks”, and “give me more detail about movement”. Enter sends a question; Shift+Enter inserts a newline. Follow-up buttons use the same command flow.

The + button accepts GeoJSON in WGS84 or CSV with `latitude`/`longitude` or `lat`/`lon` columns, up to 5 MB and 10,000 features. Files stay in the browser and become toggleable layers. Click an imported feature to inspect its attributes. Export downloads the synthetic movement locations through the selected month.

This is a frontend prototype. Chat uses deterministic sample commands, not an LLM or backend API. Movement, rainfall, and vegetation are synthetic illustrations. The rainfall backdrop is static; rainfall in point details varies by sample month. Uploaded records are displayed but not analyzed. Map tiles, Leaflet, and fonts need an internet connection. Imported data and conversation state last for the current page session.

Offline checks passed for JavaScript syntax, DOM element references, map commands, movement calculations, CSV quoting, and chat bounds. Browser rendering was not verified because local browser access was declined.

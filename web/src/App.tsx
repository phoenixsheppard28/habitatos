import { useEffect, useMemo, useRef, useState } from 'react';
import OlMap from 'ol/Map';
import View from 'ol/View';
import { defaults as defaultControls } from 'ol/control/defaults';
import ScaleLine from 'ol/control/ScaleLine';
import { extend as extendExtent, type Extent } from 'ol/extent';
import type Feature from 'ol/Feature';
import type Layer from 'ol/layer/Layer';
import { SampleCatalogProvider, type CatalogDocument, type LayerDefinition } from './catalog';
import {
  createLayer,
  featureAttributes,
  featuresToGeoJson,
  isTimeFiltered,
  layerExtent,
  loadedFeatures,
  setLayerTimeWindow,
  watchLoadStatus,
  type LoadStatus,
} from './map/layers';
import { monthsInRange, spanningRange } from './map/time';

const catalogProvider = new SampleCatalogProvider();
const FIT_PADDING = [48, 48, 120, 48];
const PLAY_INTERVAL_MS = 900;
const TOAST_DURATION_MS = 3000;

type CatalogState =
  | { kind: 'loading' }
  | { kind: 'ready'; catalog: CatalogDocument }
  | { kind: 'error'; message: string };

interface LayerUiState {
  visible: boolean;
  opacity: number;
  status?: LoadStatus;
  supported: boolean;
}

interface InspectedFeature {
  layerTitle: string;
  attributes: [string, string][];
}

const statusText: Record<LoadStatus, string> = {
  loading: 'Loading…',
  ready: 'Loaded',
  error: 'Could not load data',
};

export function App() {
  const [catalogState, setCatalogState] = useState<CatalogState>({ kind: 'loading' });

  useEffect(() => {
    const controller = new AbortController();

    catalogProvider
      .getCatalog(controller.signal)
      .then((catalog) => setCatalogState({ kind: 'ready', catalog }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setCatalogState({ kind: 'error', message: error instanceof Error ? error.message : String(error) });
      });

    return () => controller.abort();
  }, []);

  if (catalogState.kind === 'loading') return <p className="screen-message">Loading layer catalog…</p>;
  if (catalogState.kind === 'error') {
    return (
      <p className="screen-message" role="alert">
        Could not load the layer catalog: {catalogState.message}
      </p>
    );
  }
  return <Workspace catalog={catalogState.catalog} />;
}

function studyExtent(definitions: LayerDefinition[]): Extent {
  return definitions.map(layerExtent).reduce((total, extent) => extendExtent(total, extent));
}

function downloadText(fileName: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: 'application/geo+json' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = fileName;
  link.click();
  URL.revokeObjectURL(url);
}

function Workspace({ catalog }: { catalog: CatalogDocument }) {
  const definitions = useMemo(() => [...catalog.layers].sort((a, b) => a.order - b.order), [catalog]);
  const months = useMemo(() => {
    const range = spanningRange(definitions.filter(isTimeFiltered).map((definition) => definition.timeRange!));
    return range ? monthsInRange(range) : [];
  }, [definitions]);
  const hasSampleData = definitions.some((definition) => definition.sample);

  const [monthIndex, setMonthIndex] = useState(() => Math.max(months.length - 1, 0));
  const [playing, setPlaying] = useState(false);
  const [layersOpen, setLayersOpen] = useState(true);
  const [layerUi, setLayerUi] = useState<Record<string, LayerUiState>>(() =>
    Object.fromEntries(
      definitions.map((definition) => [
        definition.id,
        { visible: definition.visible, opacity: definition.opacity, supported: true },
      ]),
    ),
  );
  const [inspected, setInspected] = useState<InspectedFeature>();
  const [toast, setToast] = useState<string>();

  const mapElement = useRef<HTMLDivElement>(null);
  const mapRef = useRef<OlMap>(undefined);
  const olLayers = useRef(new Map<string, Layer>());

  const selectedMonth = months[monthIndex];
  const visibleCount = Object.values(layerUi).filter((state) => state.visible && state.supported).length;

  function updateLayerUi(id: string, change: Partial<LayerUiState>) {
    setLayerUi((current) => {
      const previous = current[id];
      const changed = Object.entries(change).some(([key, value]) => previous[key as keyof LayerUiState] !== value);
      return changed ? { ...current, [id]: { ...previous, ...change } } : current;
    });
  }

  useEffect(() => {
    const definitionsById = new Map(definitions.map((definition) => [definition.id, definition]));
    const inspectableIds = new Set(
      definitions.filter((definition) => definition.actions.includes('inspect')).map((definition) => definition.id),
    );
    const created = new Map<string, Layer>();
    const stopWatching: (() => void)[] = [];

    for (const definition of definitions) {
      const layer = createLayer(definition);
      if (!layer) {
        updateLayerUi(definition.id, { supported: false });
        continue;
      }
      created.set(definition.id, layer);
      stopWatching.push(watchLoadStatus(layer, (status) => updateLayerUi(definition.id, { status })));
    }

    const map = new OlMap({
      target: mapElement.current!,
      layers: [...created.values()],
      controls: defaultControls().extend([new ScaleLine()]),
      view: new View(),
    });
    map.getView().fit(studyExtent(definitions), { padding: FIT_PADDING });

    map.on('singleclick', (event) => {
      let found: InspectedFeature | undefined;
      map.forEachFeatureAtPixel(
        event.pixel,
        (feature, layer) => {
          const definition = definitionsById.get(layer.get('catalogId'));
          found = { layerTitle: definition?.title ?? 'Feature', attributes: featureAttributes(feature as Feature) };
          return true;
        },
        { layerFilter: (layer) => inspectableIds.has(layer.get('catalogId')), hitTolerance: 4 },
      );
      setInspected(found);
    });

    mapRef.current = map;
    olLayers.current = created;

    return () => {
      stopWatching.forEach((stop) => stop());
      map.setTarget(undefined);
      mapRef.current = undefined;
    };
  }, [definitions]);

  useEffect(() => {
    for (const [id, layer] of olLayers.current) {
      layer.setVisible(layerUi[id].visible);
      layer.setOpacity(layerUi[id].opacity);
    }
  }, [layerUi]);

  useEffect(() => {
    if (!selectedMonth) return;

    for (const definition of definitions.filter(isTimeFiltered)) {
      const layer = olLayers.current.get(definition.id);
      if (layer) setLayerTimeWindow(layer, definition, selectedMonth);
    }
  }, [definitions, selectedMonth]);

  useEffect(() => {
    if (!playing) return;
    if (monthIndex >= months.length - 1) {
      setPlaying(false);
      return;
    }

    const timer = window.setTimeout(() => setMonthIndex(monthIndex + 1), PLAY_INTERVAL_MS);
    return () => window.clearTimeout(timer);
  }, [playing, monthIndex, months.length]);

  useEffect(() => {
    if (!toast) return;

    const timer = window.setTimeout(() => setToast(undefined), TOAST_DURATION_MS);
    return () => window.clearTimeout(timer);
  }, [toast]);

  function zoomTo(extent: Extent) {
    mapRef.current?.getView().fit(extent, { padding: FIT_PADDING, duration: 300 });
  }

  function togglePlay() {
    if (!playing && monthIndex >= months.length - 1) setMonthIndex(0);
    setPlaying(!playing);
  }

  function exportVisibleFeatures() {
    const definition = definitions.find(
      (candidate) => candidate.actions.includes('export') && layerUi[candidate.id].visible,
    );
    const layer = definition && olLayers.current.get(definition.id);
    const features = layer ? loadedFeatures(layer) : [];

    if (!definition || features.length === 0) {
      setToast('No loaded features to export. Show an exportable layer first.');
      return;
    }

    const period = selectedMonth ? selectedMonth.start.slice(0, 7) : 'all';
    downloadText(`${definition.id}-${period}.geojson`, featuresToGeoJson(features));
    setToast(`Exported ${features.length} features from ${definition.title}.`);
  }

  return (
    <>
      <header className="service">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            HW
          </span>
          <strong>Habitat Watch</strong>
        </div>
        <div className="breadcrumb">
          Workspace <span className="separator">/</span> <b>Map</b>
        </div>
        <div className="header-right">
          {hasSampleData && <span className="demo">SAMPLE DATA</span>}
          <button className="plain-button" type="button" onClick={exportVisibleFeatures}>
            Export data
          </button>
        </div>
      </header>

      <main className="workspace">
        <nav className="rail" aria-label="Workspace tools">
          <button
            type="button"
            className={layersOpen ? 'active' : undefined}
            aria-expanded={layersOpen}
            aria-controls="layer-panel"
            onClick={() => setLayersOpen(!layersOpen)}
          >
            Layers
          </button>
          <button type="button" onClick={() => zoomTo(studyExtent(definitions))}>
            Extent
          </button>
        </nav>

        {layersOpen && (
          <aside className="layers" id="layer-panel" aria-label="Map layers">
            <div className="panel-title">
              <h2>Layers</h2>
              <span className="note">Catalog v{catalog.version}</span>
            </div>
            <ul className="layer-list">
              {[...definitions].reverse().map((definition) => {
                const state = layerUi[definition.id];
                return (
                  <li className="layer" key={definition.id}>
                    <label>
                      <input
                        type="checkbox"
                        checked={state.visible}
                        disabled={!state.supported}
                        onChange={(event) => updateLayerUi(definition.id, { visible: event.target.checked })}
                      />
                      <span>
                        <span className="layer-name">
                          {definition.title}
                          {definition.sample && <span className="sample-tag">Sample</span>}
                        </span>
                        <span className="layer-description">{definition.description}</span>
                      </span>
                    </label>
                    <div className="layer-controls">
                      <input
                        type="range"
                        min={0}
                        max={1}
                        step={0.05}
                        value={state.opacity}
                        disabled={!state.supported}
                        aria-label={`Opacity of ${definition.title}`}
                        onChange={(event) => updateLayerUi(definition.id, { opacity: Number(event.target.value) })}
                      />
                      {definition.actions.includes('zoom') && (
                        <button type="button" className="text-button" onClick={() => zoomTo(layerExtent(definition))}>
                          Zoom
                        </button>
                      )}
                    </div>
                    {!state.supported && <p className="layer-status error">This source type is not supported yet</p>}
                    {state.visible && state.status && (
                      <p className={`layer-status ${state.status}`}>{statusText[state.status]}</p>
                    )}
                  </li>
                );
              })}
            </ul>
            <p className="layer-footer">Generated {new Date(catalog.generatedAt).toLocaleDateString('en-GB', { dateStyle: 'medium', timeZone: 'UTC' })}</p>
          </aside>
        )}

        <section className="stage" aria-label="Geographic workspace">
          <div ref={mapElement} className="map" role="application" aria-label="Map" />

          <div className="map-status">
            {visibleCount} {visibleCount === 1 ? 'layer' : 'layers'} visible
          </div>

          {inspected && (
            <div className="inspect" role="dialog" aria-label={`${inspected.layerTitle} attributes`}>
              <h2>{inspected.layerTitle}</h2>
              <dl>
                {inspected.attributes.map(([name, value]) => (
                  <div key={name}>
                    <dt>{name}</dt>
                    <dd>{value}</dd>
                  </div>
                ))}
              </dl>
              <button className="text-button" type="button" onClick={() => setInspected(undefined)}>
                Close
              </button>
            </div>
          )}

          {selectedMonth && (
            <div className="timebar">
              <button className="play" type="button" aria-pressed={playing} onClick={togglePlay}>
                {playing ? 'Pause' : 'Play'}
              </button>
              <div className="when">{selectedMonth.label}</div>
              <div className="slider">
                <span>{months[0].label}</span>
                <input
                  type="range"
                  min={0}
                  max={months.length - 1}
                  value={monthIndex}
                  aria-label="Month in view"
                  aria-valuetext={selectedMonth.label}
                  onChange={(event) => setMonthIndex(Number(event.target.value))}
                />
                <span>{months[months.length - 1].label}</span>
              </div>
            </div>
          )}

          {toast && (
            <div className="toast" role="status">
              {toast}
            </div>
          )}
        </section>
      </main>
    </>
  );
}

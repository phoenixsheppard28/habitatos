import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent, type FormEvent, type PointerEvent as ReactPointerEvent } from 'react';
import OlMap from 'ol/Map';
import View from 'ol/View';
import GeoJSON from 'ol/format/GeoJSON';
import type VectorLayer from 'ol/layer/Vector';
import type VectorSource from 'ol/source/Vector';
import { defaults as defaultControls, ScaleLine } from 'ol/control';
import { fromLonLat, transformExtent } from 'ol/proj';
import type { FeatureCollection, GeoJsonObject } from 'geojson';
import { SampleCatalogProvider, type LayerDefinition } from './catalog';
import { createLayerAdapter, LocalVectorAdapter, type LayerAdapter } from './map/adapters';

const provider = new SampleCatalogProvider();
const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

interface LayerState extends LayerDefinition { error?: string }
interface Inspection { title: string; properties: Array<[string, string]> }
interface Exchange { question?: string; answer: string; actions?: string[] }

const icons: Record<string, string> = { layers: '◫', map: '⌘', extent: '⌗', assistant: '◌', add: '+', export: '⇩' };

function parseCsv(text: string): FeatureCollection {
  const rows: string[][] = [];
  let row: string[] = [], cell = '', quoted = false;
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (char === '"') {
      if (quoted && text[i + 1] === '"') { cell += '"'; i++; } else quoted = !quoted;
    } else if (char === ',' && !quoted) { row.push(cell); cell = ''; }
    else if ((char === '\n' || char === '\r') && !quoted) {
      if (char === '\r' && text[i + 1] === '\n') i++;
      row.push(cell); if (row.some((value) => value.trim())) rows.push(row); row = []; cell = '';
    } else cell += char;
  }
  if (quoted) throw new Error('CSV has an unclosed quoted field.');
  row.push(cell); if (row.some((value) => value.trim())) rows.push(row);
  const header = (rows.shift() ?? []).map((value) => value.replace(/^\uFEFF/, '').trim().toLowerCase());
  const latIndex = header.findIndex((value) => ['lat', 'latitude'].includes(value));
  const lonIndex = header.findIndex((value) => ['lon', 'lng', 'longitude'].includes(value));
  if (latIndex < 0 || lonIndex < 0) throw new Error('CSV needs latitude and longitude columns (or lat and lon).');
  const features = rows.map((values, index) => {
    const latitude = Number(values[latIndex]), longitude = Number(values[lonIndex]);
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude) || Math.abs(latitude) > 90 || Math.abs(longitude) > 180) {
      throw new Error(`Invalid coordinates in CSV row ${index + 2}.`);
    }
    return {
      type: 'Feature' as const,
      geometry: { type: 'Point' as const, coordinates: [longitude, latitude] },
      properties: Object.fromEntries(header.map((key, column) => [key, values[column] ?? ''])),
    };
  });
  if (!features.length) throw new Error('CSV contains no data rows.');
  return { type: 'FeatureCollection', features };
}

function normalizeGeoJson(data: unknown): GeoJsonObject {
  if (!data || typeof data !== 'object' || !('type' in data)) throw new Error('Choose a valid GeoJSON file.');
  const type = String((data as { type: unknown }).type);
  if (!['FeatureCollection', 'Feature', 'Point', 'MultiPoint', 'LineString', 'MultiLineString', 'Polygon', 'MultiPolygon', 'GeometryCollection'].includes(type)) {
    throw new Error('Choose a valid GeoJSON file.');
  }
  return data as GeoJsonObject;
}

function DownloadButton({ onClick }: { onClick: () => void }) {
  return <button className="plain-button" onClick={onClick}><span aria-hidden="true">{icons.export}</span> Export data</button>;
}

export function App() {
  const mapElement = useRef<HTMLDivElement>(null);
  const mapRef = useRef<OlMap>(undefined);
  const adapters = useRef(new Map<string, LayerAdapter>());
  const [layers, setLayers] = useState<LayerState[]>([]);
  const [catalogError, setCatalogError] = useState('');
  const [panelOpen, setPanelOpen] = useState(true);
  const [assistantOpen, setAssistantOpen] = useState(true);
  const [assistantCollapsed, setAssistantCollapsed] = useState(false);
  const [inspection, setInspection] = useState<Inspection>();
  const [month, setMonth] = useState(11);
  const [playing, setPlaying] = useState(false);
  const [question, setQuestion] = useState('');
  const [toast, setToast] = useState('');
  const [exchanges, setExchanges] = useState<Exchange[]>([{
    question: 'Where did the tracked pronghorn move during the dry months last year?',
    answer: 'In these synthetic sample records, all four tagged pronghorn move north through 2025. Use the timeline with the rainfall layer to explore the service-backed demonstration.',
    actions: ['Show dry months', 'Give me more detail about movement', 'Hide rainfall'],
  }]);
  const fileInput = useRef<HTMLInputElement>(null);
  const assistant = useRef<HTMLElement>(null);
  const drag = useRef<{ x: number; y: number } | undefined>(undefined);

  const selectedDate = `2025-${String(month + 1).padStart(2, '0')}-15T23:59:59Z`;
  const showToast = useCallback((message: string) => {
    setToast(message);
    window.setTimeout(() => setToast((current) => current === message ? '' : current), 3500);
  }, []);

  const reportLayerError = useCallback((id: string, message: string) => {
    setLayers((current) => current.map((layer) => layer.id === id ? { ...layer, error: message } : layer));
  }, []);

  useEffect(() => {
    if (!mapElement.current) return;
    const map = new OlMap({
      target: mapElement.current,
      layers: [],
      controls: defaultControls({ attribution: true }).extend([new ScaleLine({ units: 'metric' })]),
      view: new View({ center: fromLonLat([-109.77, 42.78]), zoom: 10.2, minZoom: 3, maxZoom: 18 }),
    });
    mapRef.current = map;
    const abort = new AbortController();
    provider.getCatalog(abort.signal).then((catalog) => {
      const created: LayerAdapter[] = [];
      for (const definition of catalog.layers.sort((a, b) => a.order - b.order)) {
        try {
          const adapter = createLayerAdapter(definition, { onError: reportLayerError });
          adapters.current.set(definition.id, adapter);
          created.push(adapter);
          map.addLayer(adapter.layer);
        } catch (error) {
          reportLayerError(definition.id, error instanceof Error ? error.message : 'Layer setup failed.');
        }
      }
      setLayers(catalog.layers);
    }).catch((error: Error) => {
      if (error.name !== 'AbortError') setCatalogError(`Could not load the layer catalog: ${error.message}`);
    });
    map.on('singleclick', (event) => {
      const feature = map.forEachFeatureAtPixel(event.pixel, (candidate, layer) => layer?.getVisible() && candidate);
      if (!feature) { setInspection(undefined); return; }
      const properties = Object.entries(feature.getProperties())
        .filter(([key]) => key !== 'geometry')
        .slice(0, 10)
        .map(([key, value]) => [key.replaceAll('_', ' '), typeof value === 'object' ? JSON.stringify(value) : String(value)] as [string, string]);
      setInspection({ title: String(feature.get('animal_id') ?? feature.get('name') ?? 'Selected feature'), properties });
    });
    return () => {
      abort.abort();
      adapters.current.forEach((adapter) => adapter.dispose());
      adapters.current.clear();
      map.setTarget(undefined);
    };
  }, [reportLayerError]);

  useEffect(() => { adapters.current.forEach((adapter) => adapter.setTime(selectedDate)); }, [selectedDate]);
  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => setMonth((current) => {
      if (current >= 11) { setPlaying(false); return 11; }
      return current + 1;
    }), window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 900 : 550);
    return () => window.clearInterval(timer);
  }, [playing]);

  const visibleCount = layers.filter((layer) => layer.visible).length;
  const updateLayer = (id: string, change: Partial<LayerState>) => {
    setLayers((current) => current.map((layer) => layer.id === id ? { ...layer, ...change } : layer));
    const adapter = adapters.current.get(id);
    if (!adapter) return;
    if (change.visible !== undefined) adapter.layer.setVisible(change.visible);
    if (change.opacity !== undefined) adapter.layer.setOpacity(change.opacity);
  };
  const setLayerVisible = (id: string, visible: boolean) => updateLayer(id, { visible });
  const moveLayer = (id: string, direction: -1 | 1) => {
    setLayers((current) => {
      const data = [...current];
      const index = data.findIndex((layer) => layer.id === id), target = index + direction;
      if (index < 0 || target < 0 || target >= data.length || data[target].sourceType === 'osm') return current;
      [data[index], data[target]] = [data[target], data[index]];
      data.forEach((layer, order) => { layer.order = order * 10; adapters.current.get(layer.id)?.layer.setZIndex(layer.order); });
      return data;
    });
  };
  const zoomTo = (layer: LayerDefinition) => {
    mapRef.current?.getView().fit(transformExtent(layer.bounds, 'EPSG:4326', 'EPSG:3857'), { padding: [70, 70, 120, 70], maxZoom: 12, duration: 350 });
  };
  const switchBasemap = () => {
    const baseMaps = layers.filter((layer) => layer.sourceType === 'osm');
    const active = baseMaps.findIndex((layer) => layer.visible);
    baseMaps.forEach((layer, index) => setLayerVisible(layer.id, index === (active + 1) % baseMaps.length));
    showToast(`Basemap: ${baseMaps[(active + 1) % baseMaps.length]?.title}`);
  };

  const runQuestion = (text: string) => {
    const value = text.toLowerCase();
    let answer = 'This demo assistant can change sample layers, dates, and extent. Connecting live analysis is a later milestone.';
    let actions = ['Show dry months', 'Zoom to tracks'];
    if (value.includes('rain')) {
      const hide = /hide|remove|off/.test(value); setLayerVisible('rainfall-wms', !hide);
      answer = hide ? 'Rainfall hidden.' : 'Showing the synthetic rainfall WMS layer published by the local GeoServer.';
    } else if (value.includes('dry')) {
      setMonth(8); setLayerVisible('rainfall-wms', true); setPlaying(false);
      answer = 'Showing September 2025 with the synthetic rainfall service visible. This demonstrates map control, not an ecological conclusion.';
    } else if (/zoom|extent|track/.test(value)) {
      const layer = layers.find((item) => item.id === 'movement-features'); if (layer) zoomTo(layer);
      answer = 'Centered the map on the sample movement extent. Turn on Inspectable movement and select a point to read WFS attributes.';
    } else if (/detail|movement|distance/.test(value)) {
      answer = 'The demo contains 48 synthetic positions for four tagged pronghorn, one per month in 2025. Straight connections are illustrative and do not establish the path traveled or the cause of movement.';
    } else if (/add|upload|import/.test(value)) {
      answer = 'Use Add data to load GeoJSON or CSV with latitude and longitude columns. Imports stay in this browser session and are never published to GeoServer.';
      actions = [];
    }
    setExchanges((current) => [...current, { question: text, answer, actions }]);
  };
  const submitQuestion = (event: FormEvent) => { event.preventDefault(); const text = question.trim(); if (!text) return; runQuestion(text); setQuestion(''); };

  const importFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]; if (!file) return;
    try {
      if (file.size > 5 * 1024 * 1024) throw new Error('Choose a file smaller than 5 MB.');
      const text = await file.text();
      const data = file.name.toLowerCase().endsWith('.csv') ? parseCsv(text) : normalizeGeoJson(JSON.parse(text));
      const count = data.type === 'FeatureCollection' ? (data as FeatureCollection).features.length : 1;
      if (count > 10_000) throw new Error('Choose a file with no more than 10,000 features.');
      const id = `local-${crypto.randomUUID()}`;
      const definition: LayerDefinition = {
        id, title: file.name, description: `${count} temporary browser feature${count === 1 ? '' : 's'}`,
        sourceType: file.name.toLowerCase().endsWith('.csv') ? 'csv' : 'geojson', serviceUrl: undefined,
        bounds: [-180, -90, 180, 90], crs: 'EPSG:4326', attribution: 'Local browser import',
        visible: true, opacity: 1, order: 1000 + layers.length, actions: ['inspect', 'zoom', 'export', 'remove'], sample: false,
      };
      const adapter = new LocalVectorAdapter(definition, data, { onError: reportLayerError });
      adapters.current.set(id, adapter); mapRef.current?.addLayer(adapter.layer); setLayers((current) => [...current, definition]);
      const extent = (adapter.layer as VectorLayer<VectorSource>).getSource()?.getExtent();
      if (extent) mapRef.current?.getView().fit(extent, { padding: [60, 60, 60, 60], maxZoom: 13, duration: 300 });
      showToast(`${file.name} added temporarily`);
      setExchanges((current) => [...current, { answer: `Added ${count} feature${count === 1 ? '' : 's'} from ${file.name}. The file remains local to this browser session.` }]);
    } catch (error) { showToast(error instanceof Error ? error.message : 'Could not import the file.'); }
    finally { event.target.value = ''; }
  };
  const removeLayer = (id: string) => {
    const adapter = adapters.current.get(id); if (adapter) { mapRef.current?.removeLayer(adapter.layer); adapter.dispose(); adapters.current.delete(id); }
    setLayers((current) => current.filter((layer) => layer.id !== id));
  };
  const exportData = () => {
    const features = [...adapters.current.values()].flatMap((adapter) => {
      if (!adapter.actions.has('export') || !adapter.layer.getVisible()) return [];
      return ((adapter.layer as VectorLayer<VectorSource>).getSource()?.getFeatures() ?? []);
    });
    if (!features.length) { showToast('No loaded, exportable features are visible.'); return; }
    const object = new GeoJSON().writeFeaturesObject(features, { featureProjection: 'EPSG:3857', dataProjection: 'EPSG:4326' });
    const url = URL.createObjectURL(new Blob([JSON.stringify(object, null, 2)], { type: 'application/geo+json' }));
    const link = document.createElement('a'); link.href = url; link.download = 'habitat-watch-map.geojson'; link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    showToast(`Exported ${features.length} loaded features`);
  };
  const beginDrag = (event: ReactPointerEvent<HTMLElement>) => {
    if ((event.target as HTMLElement).closest('button')) return;
    const rect = assistant.current?.getBoundingClientRect(); if (!rect) return;
    drag.current = { x: event.clientX - rect.left, y: event.clientY - rect.top }; event.currentTarget.setPointerCapture(event.pointerId);
  };
  const dragAssistant = (event: ReactPointerEvent<HTMLElement>) => {
    if (!drag.current || !assistant.current) return;
    const parent = assistant.current.parentElement!.getBoundingClientRect();
    const x = Math.max(0, Math.min(event.clientX - parent.left - drag.current.x, parent.width - assistant.current.offsetWidth));
    const y = Math.max(0, Math.min(event.clientY - parent.top - drag.current.y, parent.height - assistant.current.offsetHeight));
    Object.assign(assistant.current.style, { left: `${x}px`, top: `${y}px`, right: 'auto' });
  };
  const orderedLayers = useMemo(() => layers.filter((layer) => layer.sourceType !== 'osm').sort((a, b) => b.order - a.order), [layers]);

  return <div className="app-shell">
    <header className="service"><div className="brand"><span className="brand-mark">⌁</span><strong>Habitat Watch</strong></div><div className="breadcrumb">Workspace <span>/</span> <b>Sublette County</b></div><div className="header-right"><span className="demo">SAMPLE SERVICES</span><DownloadButton onClick={exportData} /></div></header>
    <main className="workspace">
      <nav className="rail" aria-label="Workspace tools">
        <button className={panelOpen ? 'active' : ''} onClick={() => setPanelOpen((open) => !open)}><b>{icons.layers}</b><span>Layers</span></button>
        <button onClick={switchBasemap}><b>{icons.map}</b><span>Basemap</span></button>
        <button onClick={() => { const layer = layers.find((item) => item.id === 'study-boundary'); if (layer) zoomTo(layer); }}><b>{icons.extent}</b><span>Extent</span></button>
        <button onClick={() => setAssistantOpen(true)}><b>{icons.assistant}</b><span>Assistant</span></button>
        <button className="bottom" onClick={() => fileInput.current?.click()}><b>{icons.add}</b><span>Add data</span></button>
      </nav>
      {panelOpen && <aside className="layers-panel" aria-label="Map layers">
        <div className="panel-title"><h2>Layers</h2><button className="icon-button" onClick={() => setPanelOpen(false)} aria-label="Close layers">×</button></div>
        <div className="layer-context"><span className="eyebrow">CURRENT EXPLORATION</span><h1>Pronghorn movement</h1><p>Sublette County, Wyoming<br />January – December 2025</p></div>
        {catalogError && <p className="service-error">{catalogError}</p>}
        <div className="layer-list">{orderedLayers.map((layer, index) => <article className="layer-row" key={layer.id}>
          <div className="layer-main"><input type="checkbox" checked={layer.visible} onChange={(event) => updateLayer(layer.id, { visible: event.target.checked })} aria-label={`Show ${layer.title}`} /><div><strong>{layer.title}</strong><small>{layer.description}</small><span className="layer-kind">{layer.sourceType.toUpperCase()} {layer.sample && '· SAMPLE'}</span></div></div>
          {layer.error && <p className="layer-error" role="alert">{layer.error}</p>}
          <label className="opacity"><span>Opacity</span><input type="range" min="0" max="1" step="0.05" value={layer.opacity} onChange={(event) => updateLayer(layer.id, { opacity: Number(event.target.value) })} /></label>
          <div className="layer-actions"><button onClick={() => zoomTo(layer)}>Zoom</button><button disabled={index === 0} onClick={() => moveLayer(layer.id, 1)}>Up</button><button disabled={index === orderedLayers.length - 1} onClick={() => moveLayer(layer.id, -1)}>Down</button>{layer.actions.includes('remove') && <button onClick={() => removeLayer(layer.id)}>Remove</button>}</div>
        </article>)}</div>
        <button className="plain-button add-data" onClick={() => fileInput.current?.click()}>+ Add your data</button>
        <div className="layer-footer"><strong>Public, read-only services</strong><p>GeoServer administration stays outside the browser. Local imports are temporary.</p></div>
      </aside>}
      <section className="stage" aria-label="Geographic workspace">
        <div ref={mapElement} className="map" role="application" aria-label="OpenLayers map of sample pronghorn movement" />
        <div className="map-heading"><strong>Sublette County, Wyoming</strong><p>OpenLayers · GeoServer · synthetic 2025 samples</p></div>
        <div className="map-status"><i className={layers.some((layer) => layer.error) ? 'warn' : ''} />{visibleCount} layers visible</div>
        {inspection && <aside className="inspection"><button onClick={() => setInspection(undefined)} aria-label="Close feature inspection">×</button><h2>{inspection.title}</h2><dl>{inspection.properties.map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl></aside>}
        <div className="timebar"><button className="play" onClick={() => { if (month === 11 && !playing) setMonth(0); setPlaying((value) => !value); }} aria-pressed={playing}>{playing ? 'Pause' : 'Play'}</button><div className="when">15 {months[month]} 2025</div><div className="slider"><span>Jan</span><input type="range" min="0" max="11" value={month} onChange={(event) => { setMonth(Number(event.target.value)); setPlaying(false); }} aria-label="Month in view" /><span>Dec</span></div></div>
        {assistantOpen ? <aside ref={assistant} className={`assistant ${assistantCollapsed ? 'collapsed' : ''}`} aria-label="Map assistant">
          <header className="assistant-header" onPointerDown={beginDrag} onPointerMove={dragAssistant} onPointerUp={() => { drag.current = undefined; }}><span className="assistant-mark">⌁</span><div><strong>Habitat assistant</strong><p>Your map, in conversation</p></div><span className="grip">⠿</span><button onClick={() => setAssistantCollapsed((value) => !value)} aria-label={assistantCollapsed ? 'Expand assistant' : 'Minimize assistant'}>−</button><button onClick={() => setAssistantOpen(false)} aria-label="Close assistant">×</button></header>
          {!assistantCollapsed && <><div className="thread" role="log" aria-live="polite">{exchanges.map((exchange, index) => <article className="exchange" key={index}>{exchange.question && <><span className="eyebrow">YOU</span><p className="question">{exchange.question}</p></>}<strong className="answer-brand">⌁ Habitat Watch</strong><p>{exchange.answer}</p>{exchange.actions && <div className="followups">{exchange.actions.map((action) => <button key={action} onClick={() => runQuestion(action)}>{action}</button>)}</div>}</article>)}</div><form className="ask" onSubmit={submitQuestion}><label htmlFor="question">Ask about this map</label><textarea id="question" value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} placeholder="Ask a follow-up or tell me what to map…" /><div><button type="button" onClick={() => fileInput.current?.click()} aria-label="Attach geographic data">+</button><span>⌖ Sublette County · 2025</span><button className="send" aria-label="Send question">↑</button></div></form></>}
        </aside> : <button className="assistant-launcher" onClick={() => setAssistantOpen(true)}>◌ Ask about this map</button>}
        {toast && <div className="toast" role="status">{toast}</div>}
      </section>
      <input ref={fileInput} type="file" accept=".geojson,.json,.csv" hidden onChange={importFile} />
    </main>
  </div>;
}

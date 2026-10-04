import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import type { Feature, GeoJsonObject, Geometry } from 'geojson';
import { element, formatNumber, node } from './dom';
import type { WorkspaceStore } from './store';
import type { DatasetSnapshot, ObservationFeature, WorkspaceState } from './types';

export class WorkspaceMap {
  readonly map = L.map('map', {
    zoomControl: false,
    preferCanvas: true,
  }).setView([15, 10], 2);
  private observations = L.featureGroup().addTo(this.map);
  private imports = L.featureGroup().addTo(this.map);
  private extent = L.featureGroup().addTo(this.map);
  private renderedSnapshot: DatasetSnapshot | null = null;
  private renderedMonth: number | null = null;
  private basemap = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 19,
  }).addTo(this.map);

  constructor(private store: WorkspaceStore) {
    L.control.zoom({ position: 'bottomright' }).addTo(this.map);
    L.control.scale({ imperial: false, position: 'bottomleft' }).addTo(this.map);
    this.map.attributionControl.setPrefix(false);
    new ResizeObserver(() => this.map.invalidateSize()).observe(element('map'));
    store.subscribe((state) => this.render(state));

    element('frame').addEventListener('click', () => this.frame());
    element('basemap').addEventListener('click', () => this.basemap.addTo(this.map));
  }

  frame(): void {
    this.store.setView('map');
    this.fitDataBounds();
  }

  private fitDataBounds(): void {
    this.map.invalidateSize();
    const bounds = this.observations.getBounds().isValid()
      ? this.observations.getBounds()
      : this.extent.getBounds();
    if (!bounds.isValid()) return;

    this.map.fitBounds(bounds, { padding: [40, 60], maxZoom: 12 });
  }

  addImport(data: GeoJsonObject, filename: string): L.GeoJSON {
    const layer = L.geoJSON(data, {
      style: { color: '#b17a40', weight: 2, fillOpacity: 0.12 },
      pointToLayer: (_, coordinates) =>
        L.circleMarker(coordinates, {
          radius: 5,
          color: '#fff',
          weight: 1.5,
          fillColor: '#b17a40',
          fillOpacity: 1,
        }),
      onEachFeature: (feature: Feature<Geometry>, featureLayer) => {
        const content = node('div');
        content.append(node('strong', filename));
        for (const [key, value] of Object.entries(feature.properties ?? {}).slice(0, 10)) {
          content.append(
            node(
              'p',
              `${key}: ${typeof value === 'object' ? JSON.stringify(value) : String(value)}`,
            ),
          );
        }
        featureLayer.bindPopup(content);
      },
    });
    if (!layer.getBounds().isValid()) throw new Error('The file has no map geometry.');

    layer.addTo(this.imports);
    this.store.setView('map');
    this.map.invalidateSize();
    this.map.fitBounds(layer.getBounds(), { padding: [45, 60], maxZoom: 13 });

    return layer;
  }

  setImportVisible(layer: L.GeoJSON, visible: boolean): void {
    if (visible) this.imports.addLayer(layer);
    else this.imports.removeLayer(layer);
  }

  private render(state: WorkspaceState): void {
    if (state.snapshot === this.renderedSnapshot && state.monthIndex === this.renderedMonth) return;

    const changedDataset = state.snapshot !== this.renderedSnapshot;
    this.renderedSnapshot = state.snapshot;
    this.renderedMonth = state.monthIndex;
    this.observations.clearLayers();
    this.extent.clearLayers();
    if (!state.snapshot || !state.selected) return;

    const bounds = state.selected.coverage.bbox;
    if (bounds) {
      const [west, south, east, north] = bounds;
      L.rectangle(
        [
          [south, west],
          [north, east],
        ],
        {
          color: '#687973',
          weight: 1,
          dashArray: '6 5',
          fillOpacity: 0,
          interactive: false,
        },
      ).addTo(this.extent);
    }

    if (state.selected.family === 'animal_locations') this.drawMovement(this.store.visibleFeatures);
    else this.drawEnvironment(this.store.visibleFeatures);
    if (changedDataset) this.fitDataBounds();
  }

  private drawMovement(features: ObservationFeature[]): void {
    const tracks = new Map<string, ObservationFeature[]>();
    for (const feature of features) {
      const id = feature.properties.entity_id;
      if (!id || feature.geometry.type !== 'Point') continue;

      const track = tracks.get(id) ?? [];
      track.push(feature);
      tracks.set(id, track);
    }

    for (const track of tracks.values()) {
      let previous: ObservationFeature | null = null;
      for (const feature of track) {
        if (feature.geometry.type !== 'Point') continue;

        const [longitude, latitude] = feature.geometry.coordinates;
        const location: L.LatLngTuple = [latitude, longitude];
        if (
          previous?.geometry.type === 'Point' &&
          feature.properties.daily_displacement_km != null
        ) {
          const [previousLongitude, previousLatitude] = previous.geometry.coordinates;
          L.polyline([[previousLatitude, previousLongitude], location], {
            color: '#27816b',
            weight: 1.5,
            opacity: 0.65,
            interactive: false,
          }).addTo(this.observations);
        }

        const current = feature.properties.observed_at.slice(0, 7) === this.store.through;
        L.circleMarker(location, {
          radius: current ? 3.5 : 2,
          color: '#24765f',
          weight: 0,
          fillOpacity: current ? 0.9 : 0.5,
        })
          .bindPopup(() => this.observationPopup(feature))
          .addTo(this.observations);
        previous = feature;
      }
    }
  }

  private drawEnvironment(features: ObservationFeature[]): void {
    const latestCells = new Map<string, ObservationFeature>();
    const variables = this.store.state.selected?.variables ?? [];
    const variable = variables.includes('ndvi') ? 'ndvi' : variables[0];
    for (const feature of features) {
      if (feature.properties.variable !== variable) continue;

      const key = feature.properties.cell_id ?? feature.properties.source_record_id;
      const previous = latestCells.get(key);
      if (!previous || previous.properties.observed_at < feature.properties.observed_at)
        latestCells.set(key, feature);
    }

    L.geoJSON([...latestCells.values()], {
      style: (feature) => {
        const value = (feature?.properties as ObservationFeature['properties']).value;

        return {
          color: variable === 'rainfall_mm' ? '#2272b5' : '#427b51',
          weight: 0.3,
          fillOpacity:
            value == null
              ? 0
              : variable === 'rainfall_mm'
                ? Math.min(0.8, 0.15 + value / 100)
                : Math.min(0.8, Math.max(0.1, (value + 1) / 2)),
        };
      },
      onEachFeature: (feature, layer) =>
        layer.bindPopup(() => this.observationPopup(feature as ObservationFeature)),
    }).addTo(this.observations);
  }

  private observationPopup(feature: ObservationFeature): HTMLElement {
    const properties = feature.properties;
    const content = node('div');
    content.append(node('strong', properties.entity_id ?? properties.cell_id ?? 'Observation'));
    content.append(node('p', properties.observed_at));
    if (properties.entity_id) {
      content.append(
        node('p', `Daily displacement: ${formatNumber(properties.daily_displacement_km)} km`),
      );
      content.append(node('p', `${properties.fix_count ?? 0} good fixes on this UTC day`));
    } else {
      content.append(
        node(
          'p',
          `${properties.variable}: ${formatNumber(properties.value, 3)} ${properties.unit ?? ''}`,
        ),
      );
      content.append(node('p', `Quality: ${properties.quality_flag ?? 'Unknown'}`));
    }
    content.append(
      node(
        'small',
        `Dataset version ${properties.dataset_version} · ${properties.source_record_id}`,
      ),
    );

    return content;
  }
}

import type Feature from 'ol/Feature';
import GeoJSON from 'ol/format/GeoJSON';
import { listen, unlistenByKey } from 'ol/events';
import { getTopLeft, getWidth, type Extent } from 'ol/extent';
import ImageLayer from 'ol/layer/Image';
import type Layer from 'ol/layer/Layer';
import TileLayer from 'ol/layer/Tile';
import VectorLayer from 'ol/layer/Vector';
import { get as getProjection, transformExtent } from 'ol/proj';
import ImageWMS from 'ol/source/ImageWMS';
import OSM from 'ol/source/OSM';
import VectorSource from 'ol/source/Vector';
import WMTS from 'ol/source/WMTS';
import { Circle, Fill, Stroke, Style } from 'ol/style';
import WMTSTileGrid from 'ol/tilegrid/WMTS';
import type { LayerDefinition } from '../catalog';
import { timeDimension, wfsGetFeatureUrl } from './requests';
import type { TimeWindow } from './time';

export type LoadStatus = 'loading' | 'ready' | 'error';

const MAP_PROJECTION = 'EPSG:3857';
// GeoWebCache names its built-in Web Mercator gridset EPSG:900913.
const GWC_MATRIX_SET = 'EPSG:900913';
const WMTS_TILE_SIZE = 256;
const WMTS_ZOOM_LEVELS = 19;

const geoJson = new GeoJSON();

const featureStyle = new Style({
  image: new Circle({
    radius: 4,
    fill: new Fill({ color: '#21775e' }),
    stroke: new Stroke({ color: '#ffffff', width: 1.5 }),
  }),
  stroke: new Stroke({ color: '#21775e', width: 2 }),
  fill: new Fill({ color: 'rgba(33, 119, 94, 0.15)' }),
});

export function layerExtent(definition: LayerDefinition): Extent {
  return transformExtent(definition.bounds, 'EPSG:4326', MAP_PROJECTION);
}

export function isTimeFiltered(definition: LayerDefinition): boolean {
  return definition.actions.includes('time-filter') && definition.timeRange !== undefined;
}

function createWmtsSource(definition: LayerDefinition): WMTS {
  const projectionExtent = getProjection(MAP_PROJECTION)!.getExtent();
  const resolutions = Array.from(
    { length: WMTS_ZOOM_LEVELS },
    (_, zoom) => getWidth(projectionExtent) / WMTS_TILE_SIZE / 2 ** zoom,
  );

  return new WMTS({
    url: definition.serviceUrl,
    layer: definition.layerName ?? '',
    matrixSet: GWC_MATRIX_SET,
    format: 'image/png',
    style: '',
    tileGrid: new WMTSTileGrid({
      origin: getTopLeft(projectionExtent),
      resolutions,
      matrixIds: resolutions.map((_, zoom) => `${GWC_MATRIX_SET}:${zoom}`),
    }),
    attributions: definition.attribution,
  });
}

function createSourceLayer(definition: LayerDefinition): Layer | undefined {
  switch (definition.sourceType) {
    case 'osm':
      return new TileLayer({ source: new OSM({ attributions: definition.attribution }) });
    case 'wms':
      return new ImageLayer({
        source: new ImageWMS({
          url: definition.serviceUrl,
          params: { LAYERS: definition.layerName, TRANSPARENT: true },
          ratio: 1,
          serverType: 'geoserver',
          attributions: definition.attribution,
        }),
      });
    case 'wmts':
      return new TileLayer({ source: createWmtsSource(definition) });
    case 'wfs':
    case 'geojson':
      return new VectorLayer({
        source: new VectorSource({
          format: geoJson,
          url: definition.sourceType === 'wfs' ? wfsGetFeatureUrl(definition) : definition.serviceUrl,
          attributions: definition.attribution,
        }),
        style: featureStyle,
      });
    case 'csv':
      return undefined;
  }
}

export function createLayer(definition: LayerDefinition): Layer | undefined {
  const layer = createSourceLayer(definition);
  if (!layer) return undefined;

  layer.set('catalogId', definition.id);
  layer.setVisible(definition.visible);
  layer.setOpacity(definition.opacity);
  layer.setZIndex(definition.order);
  if (definition.sourceType !== 'osm') layer.setExtent(layerExtent(definition));
  return layer;
}

export function setLayerTimeWindow(layer: Layer, definition: LayerDefinition, window: TimeWindow): void {
  const source = layer.getSource();

  if (source instanceof ImageWMS) {
    source.updateParams({ TIME: timeDimension(window) });
  } else if (source instanceof WMTS) {
    source.updateDimensions({ TIME: timeDimension(window) });
  } else if (source instanceof VectorSource && definition.sourceType === 'wfs') {
    source.setUrl(wfsGetFeatureUrl(definition, window));
    source.refresh();
  }
}

export function watchLoadStatus(layer: Layer, onStatus: (status: LoadStatus) => void): () => void {
  const source = layer.getSource();
  if (!source) return () => undefined;

  const prefix = source instanceof ImageWMS ? 'image' : source instanceof VectorSource ? 'features' : 'tile';
  const keys = [
    listen(source, `${prefix}loadstart`, () => onStatus('loading')),
    listen(source, `${prefix}loadend`, () => onStatus('ready')),
    listen(source, `${prefix}loaderror`, () => onStatus('error')),
  ];
  return () => keys.forEach(unlistenByKey);
}

export function loadedFeatures(layer: Layer): Feature[] {
  const source = layer.getSource();
  return source instanceof VectorSource ? source.getFeatures() : [];
}

export function featuresToGeoJson(features: Feature[]): string {
  return geoJson.writeFeatures(features, { featureProjection: MAP_PROJECTION, dataProjection: 'EPSG:4326' });
}

export function featureAttributes(feature: Feature): [string, string][] {
  const geometryName = feature.getGeometryName();
  return Object.entries(feature.getProperties())
    .filter(([name]) => name !== geometryName)
    .map(([name, value]) => [name, typeof value === 'object' ? JSON.stringify(value) : String(value)]);
}

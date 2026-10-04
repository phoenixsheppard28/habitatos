import { CATALOG_VERSION, type CatalogDocument, type LayerCatalogProvider } from './types';

const geoserver = (import.meta.env.VITE_GEOSERVER_URL ?? '/geoserver').replace(/\/$/, '');
const bounds: [number, number, number, number] = [-110.08, 42.56, -109.46, 43.01];
const timeRange = { start: '2025-01-15T00:00:00Z', end: '2025-12-15T23:59:59Z', field: 'observed_at' };

export const sampleCatalog: CatalogDocument = {
  version: CATALOG_VERSION,
  generatedAt: '2026-10-03T00:00:00Z',
  layers: [
    {
      id: 'basemap-osm', title: 'OpenStreetMap', description: 'Public basemap', sourceType: 'osm',
      bounds, crs: 'EPSG:3857', attribution: '© OpenStreetMap contributors', visible: true,
      opacity: 0.78, order: 0, actions: ['zoom'], sample: false,
    },
    {
      id: 'study-boundary', title: 'Study boundary', description: 'Sample study extent published as WMS',
      sourceType: 'wms', serviceUrl: `${geoserver}/habitat/wms`, layerName: 'habitat:study_boundary',
      bounds, crs: 'EPSG:4326', attribution: 'Habitat Watch sample', visible: true, opacity: 1,
      order: 10, actions: ['zoom'], sample: true,
    },
    {
      id: 'rainfall-wms', title: 'Monthly rainfall', description: 'Synthetic rainfall zones rendered by GeoServer WMS',
      sourceType: 'wms', serviceUrl: `${geoserver}/habitat/wms`, layerName: 'habitat:rainfall_zones',
      bounds, crs: 'EPSG:4326', timeRange, attribution: 'Habitat Watch synthetic sample', visible: true,
      opacity: 0.42, order: 20, actions: ['zoom', 'time-filter'], sample: true,
    },
    {
      id: 'movement-wmts', title: 'Movement cache', description: 'Synthetic movement positions cached by GeoWebCache',
      sourceType: 'wmts', serviceUrl: `${geoserver}/gwc/service/wmts`, layerName: 'habitat:movement_points',
      bounds, crs: 'EPSG:3857', timeRange, attribution: 'Habitat Watch synthetic sample', visible: true,
      opacity: 0.7, order: 30, actions: ['zoom', 'time-filter'], sample: true,
    },
    {
      id: 'movement-features', title: 'Inspectable movement', description: 'Bounded synthetic records loaded from GeoServer WFS',
      sourceType: 'wfs', serviceUrl: `${geoserver}/habitat/ows`, layerName: 'habitat:movement_points',
      bounds, crs: 'EPSG:4326', timeRange, attribution: 'Habitat Watch synthetic sample', visible: true,
      opacity: 1, order: 40, actions: ['inspect', 'zoom', 'export', 'time-filter'], sample: true,
      query: { maxFeatures: '5000' },
    },
  ],
};

export class SampleCatalogProvider implements LayerCatalogProvider {
  async getCatalog(signal?: AbortSignal): Promise<CatalogDocument> {
    await Promise.resolve();
    if (signal?.aborted) throw new DOMException('Catalog request aborted', 'AbortError');
    return structuredClone(sampleCatalog);
  }
}

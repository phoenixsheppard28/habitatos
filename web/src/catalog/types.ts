export const CATALOG_VERSION = '1.0';

export type LayerSourceType = 'osm' | 'wms' | 'wmts' | 'wfs' | 'geojson' | 'csv';
export type LayerAction = 'inspect' | 'zoom' | 'export' | 'time-filter' | 'remove';

export interface TimeRange {
  start: string;
  end: string;
  field?: string;
}

export interface LayerDefinition {
  id: string;
  title: string;
  description: string;
  sourceType: LayerSourceType;
  serviceUrl?: string;
  layerName?: string;
  bounds: [number, number, number, number];
  crs: string;
  timeRange?: TimeRange;
  attribution: string;
  visible: boolean;
  opacity: number;
  order: number;
  actions: LayerAction[];
  sample: boolean;
  query?: Record<string, string>;
}

export interface CatalogDocument {
  version: string;
  generatedAt: string;
  layers: LayerDefinition[];
}

export interface LayerCatalogProvider {
  getCatalog(signal?: AbortSignal): Promise<CatalogDocument>;
}

import type { LayerDefinition } from '../catalog';
import type { TimeWindow } from './time';

export function timeDimension(window: TimeWindow): string {
  return `${window.start}/${window.end}`;
}

export function wfsGetFeatureUrl(definition: LayerDefinition, window?: TimeWindow): string {
  const params = new URLSearchParams({
    service: 'WFS',
    version: '1.1.0',
    request: 'GetFeature',
    typeName: definition.layerName ?? '',
    outputFormat: 'application/json',
    srsName: 'EPSG:3857',
    ...definition.query,
  });

  const timeField = definition.timeRange?.field;
  if (window && timeField) params.set('CQL_FILTER', `${timeField} DURING ${timeDimension(window)}`);

  return `${definition.serviceUrl}?${params}`;
}

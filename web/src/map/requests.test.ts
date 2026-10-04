import { describe, expect, it } from 'vitest';
import { sampleCatalog } from '../catalog/sampleCatalog';
import { wfsGetFeatureUrl } from './requests';

const movementFeatures = sampleCatalog.layers.find((layer) => layer.id === 'movement-features')!;
const march = { label: 'Mar 2025', start: '2025-03-01T00:00:00Z', end: '2025-03-31T23:59:59Z' };

describe('wfsGetFeatureUrl', () => {
  it('requests GeoJSON in the map projection with the catalog query', () => {
    const url = new URL(wfsGetFeatureUrl(movementFeatures), 'http://localhost');

    expect(url.pathname).toBe('/geoserver/habitat/ows');
    expect(url.searchParams.get('typeName')).toBe('habitat:movement_points');
    expect(url.searchParams.get('outputFormat')).toBe('application/json');
    expect(url.searchParams.get('srsName')).toBe('EPSG:3857');
    expect(url.searchParams.get('maxFeatures')).toBe('5000');
    expect(url.searchParams.has('CQL_FILTER')).toBe(false);
  });

  it('filters on the time field of the layer', () => {
    const url = new URL(wfsGetFeatureUrl(movementFeatures, march), 'http://localhost');

    expect(url.searchParams.get('CQL_FILTER')).toBe(
      'observed_at DURING 2025-03-01T00:00:00Z/2025-03-31T23:59:59Z',
    );
  });
});

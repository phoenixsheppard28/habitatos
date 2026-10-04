import { describe, expect, it } from 'vitest';
import { sampleCatalog } from './fixtures/serviceCatalog';

describe('sample layer catalog', () => {
  it('has stable, unique IDs and complete spatial metadata', () => {
    const ids = sampleCatalog.layers.map((layer) => layer.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const layer of sampleCatalog.layers) {
      expect(layer.id).toMatch(/^[a-z0-9-]+$/);
      expect(layer.bounds).toHaveLength(4);
      expect(layer.crs).toMatch(/^EPSG:/);
      expect(layer.opacity).toBeGreaterThanOrEqual(0);
      expect(layer.opacity).toBeLessThanOrEqual(1);
      expect(layer.attribution.length).toBeGreaterThan(0);
    }
  });

  it('covers WMS, WMTS and bounded inspectable WFS without browser admin endpoints', () => {
    expect(sampleCatalog.layers.some((layer) => layer.sourceType === 'wms')).toBe(true);
    expect(sampleCatalog.layers.some((layer) => layer.sourceType === 'wmts')).toBe(true);
    const inspectable = sampleCatalog.layers.find((layer) => layer.sourceType === 'wfs');
    expect(inspectable?.actions).toContain('inspect');
    expect(Number(inspectable?.query?.maxFeatures)).toBeLessThanOrEqual(5000);
    for (const layer of sampleCatalog.layers) expect(layer.serviceUrl ?? '').not.toContain('/rest');
  });
});

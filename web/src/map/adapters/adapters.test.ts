// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import ImageLayer from 'ol/layer/Image';
import TileLayer from 'ol/layer/Tile';
import VectorLayer from 'ol/layer/Vector';
import { sampleCatalog } from '../../../tests/fixtures/serviceCatalog';
import { createLayerAdapter } from './index';

describe('catalog adapters', () => {
  const context = { onError: vi.fn() };

  it('creates the OpenLayers class that matches each service type', () => {
    const wms = createLayerAdapter(
      sampleCatalog.layers.find((layer) => layer.sourceType === 'wms')!,
      context,
    );
    const wmts = createLayerAdapter(
      sampleCatalog.layers.find((layer) => layer.sourceType === 'wmts')!,
      context,
    );
    const wfs = createLayerAdapter(
      sampleCatalog.layers.find((layer) => layer.sourceType === 'wfs')!,
      context,
    );
    expect(wms.layer).toBeInstanceOf(ImageLayer);
    expect(wmts.layer).toBeInstanceOf(TileLayer);
    expect(wfs.layer).toBeInstanceOf(VectorLayer);
    expect(wms.actions.has('inspect')).toBe(false);
    expect(wfs.actions.has('inspect')).toBe(true);
    wms.dispose();
    wmts.dispose();
    wfs.dispose();
  });
});

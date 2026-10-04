import ImageLayer from 'ol/layer/Image';
import ImageWMS from 'ol/source/ImageWMS';
import type { LayerDefinition } from '../../catalog';
import { BaseAdapter } from './common';
import type { AdapterContext } from './types';

export class WmsAdapter extends BaseAdapter {
  private source: ImageWMS;

  constructor(definition: LayerDefinition, context: AdapterContext) {
    if (!definition.serviceUrl || !definition.layerName) throw new Error(`WMS layer ${definition.id} is missing its service URL or layer name.`);
    const source = new ImageWMS({
      url: definition.serviceUrl,
      params: { LAYERS: definition.layerName, TILED: false },
      ratio: 1,
      serverType: 'geoserver',
      crossOrigin: 'anonymous',
      attributions: definition.attribution,
    });
    const layer = new ImageLayer({ source });
    super(definition, layer, context);
    this.source = source;
    this.keys.push(source.on('imageloaderror', () => context.onError(definition.id, `WMS failed to load ${definition.title}. Check that GeoServer is running and the layer is published.`)));
  }

  setTime(end: string): void {
    if (this.definition.timeRange) this.source.updateParams({ TIME: end });
  }
}

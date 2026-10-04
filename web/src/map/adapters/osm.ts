import TileLayer from 'ol/layer/Tile';
import OSM from 'ol/source/OSM';
import type { LayerDefinition } from '../../catalog';
import { BaseAdapter } from './common';
import type { AdapterContext } from './types';

export class OsmAdapter extends BaseAdapter {
  constructor(definition: LayerDefinition, context: AdapterContext) {
    const source = new OSM({
      url: definition.query?.tileUrl,
      attributions: definition.attribution,
      crossOrigin: 'anonymous',
    });
    const layer = new TileLayer({ source });
    super(definition, layer, context);
    this.keys.push(
      source.on('tileloaderror', () =>
        context.onError(definition.id, 'The basemap tile service did not respond.'),
      ),
    );
  }
  setTime(): void {}
}

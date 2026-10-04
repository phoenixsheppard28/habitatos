import TileLayer from 'ol/layer/Tile';
import WMTS from 'ol/source/WMTS';
import WMTSTileGrid from 'ol/tilegrid/WMTS';
import { get as getProjection } from 'ol/proj';
import { getWidth } from 'ol/extent';
import type { LayerDefinition } from '../../catalog';
import { BaseAdapter } from './common';
import type { AdapterContext } from './types';

export class WmtsAdapter extends BaseAdapter {
  private source: WMTS;

  constructor(definition: LayerDefinition, context: AdapterContext) {
    if (!definition.serviceUrl || !definition.layerName)
      throw new Error(`WMTS layer ${definition.id} is missing its service URL or layer name.`);
    const projection = getProjection('EPSG:3857');
    if (!projection) throw new Error('EPSG:3857 is unavailable.');
    const extent = projection.getExtent();
    const size = getWidth(extent) / 256;
    const resolutions = Array.from({ length: 22 }, (_, z) => size / Math.pow(2, z));
    const matrixIds = resolutions.map((_, z) => `EPSG:900913:${z}`);
    const source = new WMTS({
      url: definition.serviceUrl,
      layer: definition.layerName,
      matrixSet: 'EPSG:900913',
      format: 'image/png',
      projection,
      tileGrid: new WMTSTileGrid({ origin: [extent[0], extent[3]], resolutions, matrixIds }),
      style: '',
      wrapX: true,
      crossOrigin: 'anonymous',
      attributions: definition.attribution,
    });
    const layer = new TileLayer({ source });
    super(definition, layer, context);
    this.source = source;
    this.keys.push(
      source.on('tileloaderror', () =>
        context.onError(
          definition.id,
          `WMTS failed to load ${definition.title}. GeoWebCache may still be starting.`,
        ),
      ),
    );
  }

  setTime(end: string): void {
    if (this.definition.timeRange) this.source.updateDimensions({ TIME: end });
  }
}

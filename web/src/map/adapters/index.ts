import type { LayerDefinition } from '../../catalog';
import { OsmAdapter } from './osm';
import { WfsAdapter } from './wfs';
import { WmsAdapter } from './wms';
import { WmtsAdapter } from './wmts';
import type { AdapterContext, LayerAdapter } from './types';

export function createLayerAdapter(definition: LayerDefinition, context: AdapterContext): LayerAdapter {
  switch (definition.sourceType) {
    case 'osm': return new OsmAdapter(definition, context);
    case 'wms': return new WmsAdapter(definition, context);
    case 'wmts': return new WmtsAdapter(definition, context);
    case 'wfs': return new WfsAdapter(definition, context);
    default: throw new Error(`Catalog source ${definition.sourceType} requires local data or is unsupported.`);
  }
}

export { LocalVectorAdapter } from './local';
export type { LayerAdapter, AdapterContext } from './types';

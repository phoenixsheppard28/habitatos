import GeoJSON from 'ol/format/GeoJSON';
import VectorLayer from 'ol/layer/Vector';
import VectorSource from 'ol/source/Vector';
import { Circle as CircleStyle, Fill, Stroke, Style } from 'ol/style';
import type { FeatureCollection, GeoJsonObject } from 'geojson';
import type { LayerDefinition } from '../../catalog';
import { BaseAdapter } from './common';
import type { AdapterContext } from './types';

const style = new Style({
  stroke: new Stroke({ color: '#a7673f', width: 2 }),
  fill: new Fill({ color: 'rgba(167,103,63,.16)' }),
  image: new CircleStyle({ radius: 5, fill: new Fill({ color: '#a7673f' }), stroke: new Stroke({ color: '#fff', width: 1.5 }) }),
});

export class LocalVectorAdapter extends BaseAdapter {
  constructor(definition: LayerDefinition, data: FeatureCollection | GeoJsonObject, context: AdapterContext) {
    const format = new GeoJSON();
    const features = format.readFeatures(data, { dataProjection: 'EPSG:4326', featureProjection: 'EPSG:3857' });
    const source = new VectorSource({ features, attributions: definition.attribution });
    const layer = new VectorLayer({ source, style });
    super(definition, layer, context);
  }
  setTime(): void {}
}

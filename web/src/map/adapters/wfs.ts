import GeoJSON from 'ol/format/GeoJSON';
import VectorLayer from 'ol/layer/Vector';
import VectorSource from 'ol/source/Vector';
import { bbox as bboxStrategy } from 'ol/loadingstrategy';
import { Circle as CircleStyle, Fill, Stroke, Style } from 'ol/style';
import type { FeatureLike } from 'ol/Feature';
import type { LayerDefinition } from '../../catalog';
import { BaseAdapter } from './common';
import type { AdapterContext } from './types';

function movementStyle(feature: FeatureLike) {
  const animal = String(feature.get('animal_id') ?? 'unknown');
  const palette = ['#21775e', '#a7673f', '#656e9b', '#84649b'];
  const color =
    palette[Math.abs([...animal].reduce((n, c) => n + c.charCodeAt(0), 0)) % palette.length];
  return new Style({
    image: new CircleStyle({
      radius: 5,
      fill: new Fill({ color }),
      stroke: new Stroke({ color: '#fff', width: 1.5 }),
    }),
  });
}

export class WfsAdapter extends BaseAdapter {
  private source: VectorSource;
  private timeEnd?: string;
  private controller?: AbortController;

  constructor(definition: LayerDefinition, context: AdapterContext) {
    if (!definition.serviceUrl || !definition.layerName)
      throw new Error(`WFS layer ${definition.id} is missing its service URL or layer name.`);
    const source = new VectorSource({
      strategy: bboxStrategy,
      format: new GeoJSON(),
      loader: (extent, _resolution, projection, success, failure) => {
        this.controller?.abort();
        this.controller = new AbortController();
        const url = new URL(definition.serviceUrl!, window.location.origin);
        url.searchParams.set('service', 'WFS');
        url.searchParams.set('version', '2.0.0');
        url.searchParams.set('request', 'GetFeature');
        url.searchParams.set('typeNames', definition.layerName!);
        url.searchParams.set('outputFormat', 'application/json');
        url.searchParams.set('srsName', projection.getCode());
        url.searchParams.set('count', definition.query?.maxFeatures ?? '5000');
        url.searchParams.set('bbox', `${extent.join(',')},${projection.getCode()}`);
        if (definition.timeRange && this.timeEnd) {
          const field = definition.timeRange.field ?? 'observed_at';
          url.searchParams.set('cql_filter', `${field} <= '${this.timeEnd}'`);
        }
        fetch(url, { signal: this.controller.signal })
          .then((response) => {
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return response.json();
          })
          .then((json) => {
            const features = source
              .getFormat()!
              .readFeatures(json, { featureProjection: projection });
            source.addFeatures(features);
            success?.(features);
          })
          .catch((error: Error) => {
            if (error.name === 'AbortError') return;
            source.removeLoadedExtent(extent);
            context.onError(
              definition.id,
              `WFS failed to load ${definition.title}: ${error.message}`,
            );
            failure?.();
          });
      },
      attributions: definition.attribution,
    });
    const layer = new VectorLayer({ source, style: movementStyle, declutter: true });
    super(definition, layer, context);
    this.source = source;
  }

  setTime(end: string): void {
    if (!this.definition.timeRange || end === this.timeEnd) return;
    this.timeEnd = end;
    this.controller?.abort();
    this.source.clear(true);
    this.source.refresh();
  }

  override dispose(): void {
    this.controller?.abort();
    this.source.clear(true);
    super.dispose();
  }
}

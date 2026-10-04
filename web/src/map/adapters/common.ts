import type { EventsKey } from 'ol/events';
import { unByKey } from 'ol/Observable';
import type BaseLayer from 'ol/layer/Base';
import type { LayerDefinition } from '../../catalog';
import type { AdapterContext, LayerAdapter } from './types';

export abstract class BaseAdapter implements LayerAdapter {
  readonly actions;
  protected keys: EventsKey[] = [];

  constructor(
    readonly definition: LayerDefinition,
    readonly layer: BaseLayer,
    protected context: AdapterContext,
  ) {
    this.actions = new Set(definition.actions);
    layer.set('catalogId', definition.id);
    layer.set('title', definition.title);
    layer.set('sample', definition.sample);
    layer.setVisible(definition.visible);
    layer.setOpacity(definition.opacity);
    layer.setZIndex(definition.order);
  }

  abstract setTime(end: string): void;

  dispose(): void {
    unByKey(this.keys);
    this.keys = [];
    this.layer.dispose();
  }
}

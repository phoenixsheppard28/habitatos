import type BaseLayer from 'ol/layer/Base';
import type { LayerAction, LayerDefinition } from '../../catalog';

export interface AdapterContext {
  onError: (layerId: string, message: string) => void;
}

export interface LayerAdapter {
  readonly definition: LayerDefinition;
  readonly layer: BaseLayer;
  readonly actions: ReadonlySet<LayerAction>;
  setTime(end: string): void;
  dispose(): void;
}

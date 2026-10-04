import { fetchCatalog, fetchDataset } from './api';
import type {
  MonthlySummary,
  ObservationFeature,
  View,
  WorkspaceAnalysis,
  WorkspaceState,
} from './types';

type StateListener = (state: WorkspaceState) => void;

export class WorkspaceStore {
  private listeners = new Set<StateListener>();
  private pendingRequest: AbortController | null = null;
  readonly state: WorkspaceState = {
    analyses: [],
    datasets: [],
    selected: null,
    snapshot: null,
    status: 'loading',
    error: null,
    months: [],
    monthIndex: 0,
    view: 'map',
    assistantAvailable: false,
  };

  subscribe(listener: StateListener): () => void {
    this.listeners.add(listener);
    listener(this.state);

    return () => this.listeners.delete(listener);
  }

  private notify(): void {
    for (const listener of this.listeners) listener(this.state);
  }

  async refresh(preferredDatasetId?: string): Promise<void> {
    this.pendingRequest?.abort();
    const controller = new AbortController();
    this.pendingRequest = controller;
    this.state.status = 'loading';
    this.state.error = null;
    this.state.snapshot = null;
    this.notify();

    try {
      const response = await fetchCatalog(controller.signal);
      if (controller.signal.aborted) return;

      const previousId = this.state.selected?.dataset_id;
      this.state.datasets = response.datasets;
      this.state.assistantAvailable = response.assistant_available;
      const selected =
        response.datasets.find((dataset) => dataset.dataset_id === preferredDatasetId) ??
        response.datasets.find((dataset) => dataset.dataset_id === previousId) ??
        response.datasets.find((dataset) => dataset.family === 'animal_locations') ??
        response.datasets[0];

      if (selected) {
        await this.selectDataset(selected.dataset_id);
      } else {
        this.state.selected = null;
        this.state.months = [];
        this.state.status = 'empty';
        this.notify();
      }
    } catch (error) {
      if (!controller.signal.aborted) this.fail(error);
    }
  }

  async selectDataset(datasetId: string): Promise<void> {
    const selected = this.state.datasets.find((dataset) => dataset.dataset_id === datasetId);
    if (!selected) return;

    this.pendingRequest?.abort();
    const controller = new AbortController();
    this.pendingRequest = controller;
    this.state.selected = selected;
    this.state.snapshot = null;
    this.state.months = [];
    this.state.monthIndex = 0;
    this.state.status = 'loading';
    this.state.error = null;
    this.notify();

    try {
      const snapshot = await fetchDataset(datasetId, controller.signal);
      if (controller.signal.aborted) return;

      this.state.snapshot = snapshot;
      this.state.months = [...new Set(snapshot.monthly.map((summary) => summary.month))].sort();
      this.state.monthIndex = Math.max(0, this.state.months.length - 1);
      this.state.status = snapshot.total_records ? 'ready' : 'empty';
      this.notify();
    } catch (error) {
      if (!controller.signal.aborted) this.fail(error);
    }
  }

  setMonth(index: number): void {
    this.state.monthIndex = Math.max(0, Math.min(index, this.state.months.length - 1));
    this.notify();
  }

  setView(view: View): void {
    this.state.view = view;
    this.notify();
  }

  addAnalyses(analyses: WorkspaceAnalysis[], showCharts = true): void {
    if (!analyses.length) return;

    const incomingIds = new Set(analyses.map((analysis) => analysis.analysis_id));
    this.state.analyses = [
      ...analyses.slice().reverse(),
      ...this.state.analyses.filter((analysis) => !incomingIds.has(analysis.analysis_id)),
    ];
    if (showCharts) this.state.view = 'chart';
    this.notify();
  }

  get through(): string | undefined {
    return this.state.months[this.state.monthIndex];
  }

  get visibleFeatures(): ObservationFeature[] {
    const through = this.through;
    if (!through) return [];

    return (
      this.state.snapshot?.features.filter(
        (feature) => feature.properties.observed_at.slice(0, 7) <= through,
      ) ?? []
    );
  }

  get visibleSummary(): MonthlySummary[] {
    const through = this.through;
    if (!through) return [];

    return this.state.snapshot?.monthly.filter((summary) => summary.month <= through) ?? [];
  }

  private fail(error: unknown): void {
    this.state.status = 'error';
    this.state.error = error instanceof Error ? error.message : 'Cannot load workspace data.';
    this.state.snapshot = null;
    this.state.months = [];
    this.notify();
  }
}

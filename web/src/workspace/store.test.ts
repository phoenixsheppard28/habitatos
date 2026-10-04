import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fetchCatalog, fetchDataset } from './api';
import { WorkspaceStore } from './store';
import type { Dataset, DatasetSnapshot } from './types';

vi.mock('./api', () => ({ fetchCatalog: vi.fn(), fetchDataset: vi.fn() }));

const dataset: Dataset = {
  dataset_id: 'tracking',
  version: 1,
  family: 'animal_locations',
  source_id: 'movement-source',
  description: 'Tracking observations',
  summary: null,
  variables: [],
  row_count: 2,
  created_at: '2024-03-01T00:00:00Z',
  raw_artifact_refs: [],
  coverage: { bbox: [36, -2, 37, -1], start: '2024-01-01', end: '2024-02-01', species: [] },
};

const snapshot: DatasetSnapshot = {
  type: 'FeatureCollection',
  dataset_id: 'tracking',
  version: 1,
  total_records: 2,
  truncated: false,
  limit: 20000,
  sources: [],
  grain: 'UTC day',
  monthly: [
    { month: '2024-01', records: 1 },
    { month: '2024-02', records: 1 },
  ],
  features: ['2024-01-01', '2024-02-01'].map((observed_at) => ({
    type: 'Feature',
    geometry: { type: 'Point', coordinates: [36.9, -1.4] },
    properties: { observed_at, source_record_id: observed_at, dataset_version: 1 },
  })),
};

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(fetchCatalog).mockResolvedValue({ datasets: [dataset], assistant_available: true });
  vi.mocked(fetchDataset).mockResolvedValue(snapshot);
});

describe('workspace data transitions', () => {
  it('applies the same timeline cutoff to features and database summaries', async () => {
    const store = new WorkspaceStore();
    await store.refresh();
    store.setMonth(0);
    expect(store.visibleFeatures.map((feature) => feature.properties.observed_at)).toEqual([
      '2024-01-01',
    ]);
    expect(store.visibleSummary.map((summary) => summary.month)).toEqual(['2024-01']);
  });

  it('clears old observations when the backend fails rather than retaining misleading data', async () => {
    const store = new WorkspaceStore();
    await store.refresh();
    vi.mocked(fetchDataset).mockRejectedValueOnce(new Error('Connection failed'));
    await store.selectDataset(dataset.dataset_id);
    expect(store.state.status).toBe('error');
    expect(store.visibleFeatures).toEqual([]);
    expect(store.visibleSummary).toEqual([]);
  });

  it('does not let a slower previous selection replace the current dataset', async () => {
    const other = { ...dataset, dataset_id: 'other' };
    vi.mocked(fetchCatalog).mockResolvedValue({
      datasets: [dataset, other],
      assistant_available: true,
    });
    const store = new WorkspaceStore();
    await store.refresh();

    let resolvePrevious!: (value: DatasetSnapshot) => void;
    vi.mocked(fetchDataset).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolvePrevious = resolve;
        }),
    );
    const previous = store.selectDataset(dataset.dataset_id);
    vi.mocked(fetchDataset).mockResolvedValueOnce({ ...snapshot, dataset_id: 'other' });
    await store.selectDataset('other');
    resolvePrevious(snapshot);
    await previous;
    expect(store.state.snapshot?.dataset_id).toBe('other');
    expect(store.state.selected?.dataset_id).toBe('other');
  });
});

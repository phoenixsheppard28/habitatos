// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fetchCatalog, fetchDataset } from './api';
import type { WorkspacePanels } from './panels';
import { WorkspaceSidebar } from './sidebar';
import { WorkspaceStore } from './store';
import type { Dataset, DatasetSnapshot } from './types';

vi.mock('./api', () => ({ fetchCatalog: vi.fn(), fetchDataset: vi.fn() }));

const studies: Dataset[] = [
  {
    dataset_id: 'movebank--study-wildebeest',
    description: 'Wildebeest movement',
    coverage: { bbox: null, start: null, end: null, species: ['Connochaetes taurinus'] },
  },
  {
    dataset_id: 'movebank--study-bears',
    description: 'Black bear movement',
    coverage: { bbox: null, start: null, end: null, species: ['Ursus americanus'] },
  },
].map((study) => ({
  ...study,
  version: 1,
  family: 'animal_locations',
  source_id: 'movebank_repository',
  summary: null,
  variables: [],
  row_count: 1,
  created_at: '2026-10-04T00:00:00Z',
  raw_artifact_refs: [],
}));

const snapshot: DatasetSnapshot = {
  type: 'FeatureCollection',
  dataset_id: studies[0].dataset_id,
  version: 1,
  total_records: 1,
  truncated: false,
  limit: 20000,
  sources: [],
  grain: 'UTC day',
  monthly: [{ month: '2024-01', records: 1 }],
  features: [],
};

beforeEach(() => {
  vi.resetAllMocks();
  document.body.innerHTML = `
    <input id="object-search" />
    <details class="object-section" open><div id="layer-list"></div></details>
    <div id="import-list"></div>
    <span id="data-count"></span>
    <p id="catalog-note"></p>
    <p id="search-empty" hidden></p>
  `;
  vi.mocked(fetchCatalog).mockResolvedValue({ datasets: studies, assistant_available: false });
  vi.mocked(fetchDataset).mockImplementation(async (datasetId) => ({
    ...snapshot,
    dataset_id: datasetId,
  }));
});

describe('study selection in the sidebar', () => {
  it('shows distinct titles and selects each study independently', async () => {
    const store = new WorkspaceStore();
    const panels = { setSidebar: vi.fn() } as unknown as WorkspacePanels;
    new WorkspaceSidebar(store, panels);
    await store.refresh();

    const controls = [...document.querySelectorAll<HTMLButtonElement>('[data-dataset]')];
    expect(controls.map((control) => control.textContent)).toEqual([
      'Wildebeest movementConnochaetes taurinus',
      'Black bear movementUrsus americanus',
    ]);
    expect(document.getElementById('data-count')?.textContent).toBe('2');
    expect(controls[0].getAttribute('aria-pressed')).toBe('true');

    controls[1].click();

    expect(fetchDataset).toHaveBeenLastCalledWith(studies[1].dataset_id, expect.any(AbortSignal));
    await vi.waitFor(() => expect(store.state.snapshot?.dataset_id).toBe(studies[1].dataset_id));
    expect(controls[0].getAttribute('aria-pressed')).toBe('false');
    expect(controls[1].getAttribute('aria-pressed')).toBe('true');

    const search = document.getElementById('object-search') as HTMLInputElement;
    search.value = 'Black bear';
    search.dispatchEvent(new Event('input'));

    expect(controls[0].hidden).toBe(true);
    expect(controls[1].hidden).toBe(false);
  });
});

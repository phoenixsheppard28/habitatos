import type L from 'leaflet';
import { element, node } from './dom';
import type { WorkspaceMap } from './map';
import type { WorkspacePanels } from './panels';
import type { WorkspaceStore } from './store';
import type { Dataset } from './types';

export class WorkspaceSidebar {
  private list = element('layer-list');
  private imports = element('import-list');
  private search = element<HTMLInputElement>('object-search');
  private datasets: Dataset[] | null = null;

  constructor(
    private store: WorkspaceStore,
    private panels: WorkspacePanels,
  ) {
    this.search.addEventListener('input', () => this.filter());
    store.subscribe((state) => {
      if (this.datasets !== state.datasets) {
        this.datasets = state.datasets;
        this.renderDatasets(state.datasets);
      }
      for (const control of this.list.querySelectorAll<HTMLButtonElement>('[data-dataset]')) {
        const selected = control.dataset.dataset === state.selected?.dataset_id;
        control.classList.toggle('active', selected);
        control.setAttribute('aria-pressed', String(selected));
      }
      element('catalog-note').textContent =
        state.status === 'loading'
          ? 'Loading the public catalog…'
          : state.datasets.length
            ? 'Select a dataset. The map shows one published dataset at a time.'
            : 'No published datasets available.';
      this.updateCount();
      this.filter();
    });
  }

  addImport(filename: string, layer: L.GeoJSON, map: WorkspaceMap): void {
    const row = node('div', undefined, 'layer imported-layer');
    const label = node('label');
    const checkbox = node('input');
    checkbox.type = 'checkbox';
    checkbox.checked = true;
    checkbox.setAttribute('aria-label', `Show ${filename}`);
    checkbox.addEventListener('change', () => map.setImportVisible(layer, checkbox.checked));
    label.append(checkbox, node('span', filename, 'layer-name'));
    row.append(label);
    this.imports.append(row);
    this.updateCount();
    this.filter();
  }

  private renderDatasets(datasets: Dataset[]): void {
    this.list.replaceChildren();
    for (const dataset of datasets) {
      const control = node('button', undefined, 'object-link dataset-link');
      control.type = 'button';
      control.dataset.dataset = dataset.dataset_id;
      const label = node('span', dataset.source_id, 'dataset-label');
      label.append(
        node(
          'small',
          dataset.coverage.species.join(', ') || dataset.variables.join(', ') || dataset.family,
        ),
      );
      const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      icon.classList.add('icon');
      icon.setAttribute('aria-hidden', 'true');
      const symbol = document.createElementNS('http://www.w3.org/2000/svg', 'use');
      symbol.setAttribute('href', '#icon-table');
      icon.append(symbol);
      control.append(icon, label);
      control.title = dataset.description;
      control.addEventListener('click', () => {
        void this.store.selectDataset(dataset.dataset_id);
        if (window.innerWidth <= 760) this.panels.setSidebar(false);
      });
      this.list.append(control);
    }
  }

  private updateCount(): void {
    element('data-count').textContent = String(
      this.list.children.length + this.imports.children.length,
    );
  }

  private filter(): void {
    const query = this.search.value.trim().toLowerCase();
    let matches = 0;
    for (const section of document.querySelectorAll<HTMLDetailsElement>('.object-section')) {
      const controls = [...section.querySelectorAll<HTMLElement>('.object-link, .imported-layer')];
      for (const control of controls) {
        control.hidden = !control.textContent?.toLowerCase().includes(query);
        if (!control.hidden) matches++;
      }
      section.hidden = !!query && controls.every((control) => control.hidden);
      if (query) section.open = true;
    }
    element('search-empty').hidden = !query || matches > 0;
  }
}

import { button, element, formatMonth, formatNumber, node, sourceLink } from './dom';
import type { WorkspaceStore } from './store';
import type { ObservationFeature, View, WorkspaceState } from './types';

const viewTitles: Record<View, string> = {
  map: 'Observation map',
  table: 'Observation records',
  chart: 'Monthly summary',
  overview: 'Dataset overview',
  sources: 'Sources and provenance',
};
const tablePageSize = 100;

function contentHeading(title: string, description: string): HTMLElement {
  const heading = node('div', undefined, 'content-heading');
  const text = node('div');
  text.append(node('h2', title), node('p', description));
  heading.append(text);

  return heading;
}

function metric(label: string, value: string, note: string): HTMLElement {
  const card = node('article', undefined, 'metric-card');
  card.append(node('span', label), node('strong', value), node('small', note));

  return card;
}

function dataTable(headers: string[], rows: string[][]): HTMLElement {
  const container = node('div', undefined, 'table-scroll');
  const table = node('table');
  const head = table.createTHead().insertRow();
  for (const header of headers) {
    const cell = node('th', header);
    cell.scope = 'col';
    head.append(cell);
  }

  const body = table.createTBody();
  for (const values of rows) {
    const row = body.insertRow();
    for (const value of values) row.append(node('td', value));
  }
  container.append(table);

  return container;
}

export class WorkspaceViews {
  private filter = '';
  private page = 0;
  private lastDatasetId: string | null = null;

  constructor(
    private store: WorkspaceStore,
    private discuss: (question: string) => void,
  ) {
    document.querySelectorAll<HTMLButtonElement>('[data-view]').forEach((control) => {
      control.addEventListener('click', () => {
        const view = control.dataset.view as View;
        if (view in viewTitles) store.setView(view);
      });
    });
    store.subscribe((state) => this.render(state));
  }

  private render(state: WorkspaceState): void {
    const mapVisible = state.view === 'map';
    element('map-view').hidden = !mapVisible;
    element('object-content').hidden = mapVisible;
    element('object-tools').hidden = !mapVisible;
    element('object-title').textContent = viewTitles[state.view];
    element('object-category').textContent = ['overview', 'sources'].includes(state.view)
      ? 'Analysis'
      : 'Workspace';
    element('object-description').textContent =
      state.selected?.description ?? 'Select a dataset from the public catalog.';
    element('dataset-version').textContent = state.snapshot
      ? `Dataset version ${state.snapshot.version}`
      : '';

    for (const control of document.querySelectorAll<HTMLButtonElement>('[data-view]')) {
      const active = control.dataset.view === state.view;
      control.classList.toggle('active', active);
      if (active) control.setAttribute('aria-current', 'page');
      else control.removeAttribute('aria-current');
    }

    const notice = element('workspace-notice');
    notice.replaceChildren();
    notice.hidden = state.status === 'ready';
    if (state.status === 'loading')
      notice.append(node('p', 'Loading observations from the backend…'));
    if (state.status === 'empty') {
      notice.append(node('strong', 'No observations available'));
      notice.append(
        node('p', 'Select another dataset, import a local file, or request data through Dora.'),
      );
    }
    if (state.status === 'error') {
      notice.append(
        node('strong', 'Cannot load observations'),
        node('p', state.error ?? 'The backend request failed.'),
      );
      notice.append(
        button('Retry connection', () => {
          void this.store.refresh();
        }),
      );
    }

    element('connection-status').textContent = {
      loading: 'Connecting',
      ready: 'Connected',
      empty: 'Connected · no data',
      error: 'Connection error',
    }[state.status];
    element('connection-status').dataset.status = state.status;
    element('workspace-status').textContent = state.snapshot
      ? `${state.selected?.source_id} · version ${state.snapshot.version}`
      : (state.error ?? 'Waiting for observations');
    element<HTMLButtonElement>('export').disabled = !this.store.visibleFeatures.length;
    element('map-title').textContent =
      state.selected?.coverage.species.join(', ') ||
      state.selected?.source_id ||
      'Public observations';
    element('map-description').textContent = state.snapshot
      ? `${formatNumber(this.store.visibleFeatures.length, 0)} loaded observations through ${formatMonth(this.store.through)}`
      : 'No observations loaded';
    const mapVariable = state.selected?.variables.includes('ndvi')
      ? 'ndvi'
      : state.selected?.variables[0];
    element('map-status').textContent =
      state.selected?.family === 'cell_observations'
        ? `${mapVariable ?? 'Measurement'} · latest value per cell`
        : 'Last good fix per animal and UTC day';

    const content = element('object-content');
    content.replaceChildren();
    if (mapVisible || !state.snapshot || !state.selected) return;

    if (this.lastDatasetId !== state.selected.dataset_id) {
      this.filter = '';
      this.page = 0;
      this.lastDatasetId = state.selected.dataset_id;
    }

    if (state.snapshot.truncated) {
      content.append(
        node(
          'p',
          `The map, table, and export contain at most ${formatNumber(state.snapshot.limit, 0)} records. ` +
            `Database summaries cover all ${formatNumber(state.snapshot.total_records, 0)} records.`,
          'data-limit',
        ),
      );
    }

    if (state.view === 'table') this.renderTable(content);
    if (state.view === 'chart') this.renderChart(content);
    if (state.view === 'overview') this.renderOverview(content);
    if (state.view === 'sources') this.renderSources(content);
  }

  private recordValues(feature: ObservationFeature): string[] {
    const properties = feature.properties;
    if (
      this.store.state.selected?.family === 'animal_locations' &&
      feature.geometry.type === 'Point'
    ) {
      const [longitude, latitude] = feature.geometry.coordinates;

      return [
        properties.entity_id ?? '—',
        properties.observed_at.slice(0, 10),
        formatNumber(latitude, 5),
        formatNumber(longitude, 5),
        formatNumber(properties.fix_count, 0),
        formatNumber(properties.daily_displacement_km, 3),
      ];
    }

    return [
      properties.cell_id ?? '—',
      properties.observed_at,
      properties.variable ?? '—',
      formatNumber(properties.value, 4),
      properties.unit ?? '—',
      properties.quality_flag ?? '—',
    ];
  }

  private renderTable(content: HTMLElement): void {
    content.append(contentHeading('Observation records', this.store.state.snapshot?.grain ?? ''));
    const controls = node('div', undefined, 'content-heading');
    const count = node('p', undefined, 'content-note');
    const search = node('input', undefined, 'table-search');
    search.type = 'search';
    search.placeholder = 'Filter ID, date, or measurement…';
    search.setAttribute('aria-label', 'Filter observation records');
    search.value = this.filter;
    controls.append(count, search);
    const records = node('div');
    const pagination = node('div', undefined, 'table-pagination');
    content.append(controls, records, pagination);

    const update = (): void => {
      const query = this.filter.trim().toLowerCase();
      const matching = this.store.visibleFeatures.filter((feature) =>
        this.recordValues(feature).join(' ').toLowerCase().includes(query),
      );
      this.page = Math.min(this.page, Math.max(0, Math.ceil(matching.length / tablePageSize) - 1));
      const first = this.page * tablePageSize;
      const rows = matching
        .slice(first, first + tablePageSize)
        .map((feature) => this.recordValues(feature));
      const headers =
        this.store.state.selected?.family === 'animal_locations'
          ? [
              'Animal ID',
              'UTC day',
              'Latitude',
              'Longitude',
              'Good fixes',
              'Daily displacement (km)',
            ]
          : ['Cell ID', 'Observed at (UTC)', 'Variable', 'Value', 'Unit', 'Quality'];
      records.replaceChildren(dataTable(headers, rows));
      count.textContent = `${formatNumber(matching.length, 0)} matching loaded records`;
      pagination.replaceChildren();
      if (!matching.length) {
        records.append(node('p', 'No matching records.', 'content-note'));
        return;
      }

      const previous = button('Previous', () => {
        this.page--;
        update();
      });
      const next = button('Next', () => {
        this.page++;
        update();
      });
      previous.disabled = this.page === 0;
      next.disabled = first + tablePageSize >= matching.length;
      pagination.append(
        previous,
        node('span', `${first + 1}–${first + rows.length} of ${formatNumber(matching.length, 0)}`),
        next,
      );
    };
    search.addEventListener('input', () => {
      this.filter = search.value;
      this.page = 0;
      update();
    });
    update();
  }

  private renderChart(content: HTMLElement): void {
    const movement = this.store.state.selected?.family === 'animal_locations';
    const monthly = this.store.visibleSummary;
    const months = [...new Set(monthly.map((summary) => summary.month))];
    const data = months.map((month) => {
      const summaries = monthly.filter((summary) => summary.month === month);

      return {
        month,
        value: summaries.reduce(
          (sum, summary) => sum + (movement ? (summary.distance_km ?? 0) : summary.records),
          0,
        ),
        segments: summaries.reduce((sum, summary) => sum + (summary.segments ?? 0), 0),
      };
    });
    const maximum = Math.max(1, ...data.map((summary) => summary.value));
    content.append(
      contentHeading(
        movement ? 'Monthly daily displacement' : 'Monthly observation count',
        movement
          ? 'Sum of measured daily displacements in kilometers.'
          : 'Count of database observations across measured variables.',
      ),
    );
    const card = node('article', undefined, 'content-card');
    const chart = node('div', undefined, 'chart-container');
    chart.setAttribute('role', 'img');
    chart.setAttribute(
      'aria-label',
      data
        .map(
          (summary) =>
            `${formatMonth(summary.month)}: ${formatNumber(summary.value)} ${movement ? 'km' : 'records'}`,
        )
        .join('; '),
    );
    for (const summary of data) {
      const column = node('div', undefined, 'chart-column');
      const value =
        movement && !summary.segments ? '—' : formatNumber(summary.value, movement ? 1 : 0);
      column.append(node('div', value, 'chart-value'));
      const track = node('div', undefined, 'chart-track');
      const bar = node('div', undefined, 'chart-bar');
      bar.style.height = `${(summary.value / maximum) * 100}%`;
      bar.title = `${formatMonth(summary.month)}: ${value} ${movement ? 'km' : 'records'}`;
      track.append(bar);
      column.append(track, node('span', formatMonth(summary.month)));
      chart.append(column);
    }
    card.append(chart);
    content.append(
      card,
      node(
        'p',
        movement
          ? 'Daily displacement connects the last good fixes on consecutive UTC days. Missing days have no displacement value.'
          : 'Counts describe recorded measurements. Counts do not establish ecological improvement or decline.',
        'content-note',
      ),
    );
  }

  private renderOverview(content: HTMLElement): void {
    const { selected, snapshot } = this.store.state;
    if (!selected || !snapshot) return;

    const summary = this.store.visibleSummary;
    const records = summary.reduce((sum, month) => sum + month.records, 0);
    const movement = selected.family === 'animal_locations';
    const distance = summary.reduce((sum, month) => sum + (month.distance_km ?? 0), 0);
    const segments = summary.reduce((sum, month) => sum + (month.segments ?? 0), 0);
    content.append(
      contentHeading(
        'Dataset overview',
        `Database coverage through ${formatMonth(this.store.through)}`,
      ),
    );
    const metrics = node('div', undefined, 'metric-grid');
    metrics.append(metric('Observation records', formatNumber(records, 0), snapshot.grain));
    if (movement) {
      metrics.append(
        metric(
          'Measured displacement',
          `${formatNumber(distance)} km`,
          `${formatNumber(segments, 0)} consecutive-day segments`,
        ),
      );
      metrics.append(
        metric(
          'Mean daily displacement',
          segments ? `${formatNumber(distance / segments, 2)} km` : '—',
          'Excludes missing displacement values',
        ),
      );
    } else {
      metrics.append(
        metric(
          'Measured variables',
          selected.variables.join(', '),
          'Values retain the source units',
        ),
      );
      metrics.append(
        metric(
          'Observed months',
          String(new Set(summary.map((month) => month.month)).size),
          'Months with database records',
        ),
      );
    }
    content.append(metrics);
    const method = node('article', undefined, 'content-card');
    method.append(
      node('h3', 'Inputs and method'),
      node(
        'p',
        movement
          ? 'The backend retains the last good GPS fix per animal and UTC day. Geodesic distance uses consecutive days only.'
          : 'The backend returns normalized measurements for spatial cells. The map shows the latest available value per cell through the selected month.',
      ),
    );
    method.append(
      node(
        'p',
        movement
          ? 'These distances do not measure the full route traveled. Sampled animals do not represent the entire population.'
          : 'Acquisition dates, cloud cover, valid pixel fractions, and source resolution can limit comparisons.',
      ),
    );
    method.append(
      button('Analyze with assistant', () =>
        this.discuss(
          'Analyze the selected dataset for the dates and region shown. Cite the evidence and limitations.',
        ),
      ),
    );
    content.append(method);

    if (!movement) {
      const rows = summary.map((month) => [
        formatMonth(month.month),
        month.variable ?? '—',
        formatNumber(month.records, 0),
        formatNumber(month.cells, 0),
        formatNumber(month.mean_value, 4),
        formatNumber(month.measured_records, 0),
      ]);
      content.append(
        dataTable(
          ['Month', 'Variable', 'Records', 'Cells', 'Mean value', 'Non-null measurements'],
          rows,
        ),
      );
    }
  }

  private renderSources(content: HTMLElement): void {
    const { selected, snapshot } = this.store.state;
    if (!selected || !snapshot) return;

    content.append(contentHeading('Sources and provenance', selected.description));
    const details = node('article', undefined, 'content-card');
    details.append(node('h3', 'Published dataset'));
    const metadata: Record<string, string> = {
      'Dataset ID': selected.dataset_id,
      Version: String(snapshot.version),
      Source: selected.source_id,
      'Source records': formatNumber(selected.row_count, 0),
      'Observation grain': snapshot.grain,
      'Coverage start': selected.coverage.start ?? 'Unknown',
      'Coverage end': selected.coverage.end ?? 'Unknown',
      Species: selected.coverage.species.join(', ') || 'Not species-specific',
      'Published at': selected.created_at,
    };
    for (const [label, value] of Object.entries(metadata))
      details.append(node('p', `${label}: ${value}`));
    content.append(details);

    for (const citation of snapshot.sources) {
      const card = node('article', undefined, 'content-card');
      const heading = node('h3');
      heading.append(sourceLink(citation.source?.name ?? selected.source_id, citation.source?.url));
      card.append(
        heading,
        node('p', `Attribution: ${citation.rights?.attribution ?? 'Not supplied'}`),
        node('p', `License: ${citation.rights?.license ?? 'Not supplied'}`),
      );
      if (citation.source?.study_id)
        card.append(node('p', `Study ID: ${citation.source.study_id}`));
      content.append(card);
    }
    if (!snapshot.sources.length)
      content.append(
        node('p', 'The published batches contain no source citation metadata.', 'content-note'),
      );
  }
}

import type { Data, Layout } from 'plotly.js-dist-min';
import { formatNumber, node } from './dom';
import type { Chart, WorkspaceAnalysis } from './types';

async function drawFigure(element: HTMLElement, figure: Chart['figure']): Promise<void> {
  try {
    const Plotly = await import('plotly.js-dist-min');
    // Plotly measures its container, so wait until the card is in the document.
    if (!element.isConnected) await new Promise(requestAnimationFrame);
    await Plotly.newPlot(
      element,
      figure.data as Data[],
      { ...(figure.layout as Partial<Layout>), autosize: true },
      { responsive: true, displaylogo: false },
    );
  } catch (error) {
    console.error('Plotly could not draw the chart.', error);
    element.replaceChildren(node('p', 'The chart could not be drawn.', 'content-note'));
  }
}

export function renderChart(chart: Chart): HTMLElement {
  const section = node('section', undefined, 'analysis-plot');
  section.dataset.chartId = chart.chart_id;
  section.append(node('h3', chart.title));
  // Conversations saved in localStorage can hold charts from the retired template format.
  if (!chart.figure) {
    section.append(
      node(
        'p',
        'This chart uses an older format. Ask the question again to redraw it.',
        'content-note',
      ),
    );
    return section;
  }

  const plot = node('div', undefined, 'analysis-chart');
  plot.setAttribute('role', 'img');
  plot.setAttribute('aria-label', chart.title);
  section.append(plot);
  void drawFigure(plot, chart.figure);

  const details = node('details', undefined, 'analysis-data');
  details.append(node('summary', 'Chart code'), node('pre', chart.code, 'analysis-code'));
  section.append(details);

  return section;
}

export function renderAnalysis(analysis: WorkspaceAnalysis): HTMLElement {
  const { result } = analysis;
  const card = node('article', undefined, 'content-card analysis-result');
  card.dataset.analysisId = analysis.analysis_id;
  card.append(node('h2', result.question));
  const datasets =
    result.evidence.datasets?.map((dataset) => `${dataset.dataset_id}@${dataset.version}`) ?? [];
  card.append(
    node(
      'p',
      [
        analysis.status === 'partial' ? 'Partial analysis' : 'Completed analysis',
        result.created_at,
        result.artifact_versions.method,
        ...datasets,
      ]
        .filter(Boolean)
        .join(' · '),
      'content-note',
    ),
  );
  for (const finding of result.findings) card.append(node('p', finding));
  for (const chart of analysis.charts) card.append(renderChart(chart));
  for (const data of result.tables ?? []) {
    const section = node('section', undefined, 'analysis-table');
    const scroll = node('div', undefined, 'table-scroll');
    const table = node('table');
    table.append(node('caption', data.title));
    const header = table.createTHead().insertRow();
    for (const label of data.columns) {
      const cell = node('th', label);
      cell.scope = 'col';
      header.append(cell);
    }
    const body = table.createTBody();
    for (const values of data.rows) {
      const row = body.insertRow();
      for (const value of values) {
        row.append(
          node(
            'td',
            value == null ? 'Missing' : typeof value === 'number' ? formatNumber(value, 4) : value,
          ),
        );
      }
    }
    scroll.append(table);
    section.append(scroll);
    card.append(section);
  }
  if (!analysis.charts.length && !result.tables?.length)
    card.append(node('p', 'This analysis contains no charts.', 'content-note'));

  const notes = [...result.limitations, ...analysis.warnings];
  if (notes.length) {
    const details = node('details', undefined, 'analysis-data');
    details.append(node('summary', 'Limitations and warnings'));
    const list = node('ul');
    for (const note of new Set(notes)) list.append(node('li', note));
    details.append(list);
    card.append(details);
  }
  if (result.evidence.rights?.attribution)
    card.append(node('p', result.evidence.rights.attribution, 'content-note'));

  return card;
}

import { formatNumber, node } from './dom';
import type { ChartSpec, WorkspaceAnalysis } from './types';

const colors = ['#387c78', '#c77723', '#7561a8', '#4779b8', '#b85270', '#78823b'];

function svgNode<K extends keyof SVGElementTagNameMap>(
  tag: K,
  attributes: Record<string, string | number>,
  text?: string,
): SVGElementTagNameMap[K] {
  const created = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [name, value] of Object.entries(attributes)) created.setAttribute(name, String(value));
  if (text !== undefined) created.textContent = text;

  return created;
}

function measured(value: number | null | undefined): value is number {
  return value != null && Number.isFinite(value);
}

export function renderChart(spec: ChartSpec): HTMLElement {
  const section = node('section', undefined, 'analysis-plot');
  section.dataset.chartId = spec.chart_id;
  section.append(node('h3', spec.title));
  if (spec.description) section.append(node('p', spec.description, 'content-note'));
  if (spec.schema_version !== '1.0' || !['line', 'bar', 'scatter'].includes(spec.kind)) {
    section.append(node('p', 'This chart format is not supported.', 'content-note'));
    return section;
  }

  const categories = [
    ...new Set(spec.series.flatMap((series) => series.points.map((point) => String(point.x)))),
  ];
  const categoryIndices = new Map(categories.map((category, index) => [category, index]));
  const coordinate = (value: string | number): number => {
    if (spec.x_axis.type === 'category') return categoryIndices.get(String(value)) ?? NaN;
    if (spec.x_axis.type === 'temporal') return typeof value === 'string' ? Date.parse(value) : NaN;

    return typeof value === 'number' ? value : NaN;
  };
  const series = spec.series.map((series) => ({
    ...series,
    points: series.points
      .filter((point) => Number.isFinite(coordinate(point.x)))
      .slice()
      .sort((first, second) => coordinate(first.x) - coordinate(second.x)),
  }));
  const points = series.flatMap((series) => series.points);
  const values = points.filter((point) => measured(point.y)).map((point) => point.y!);
  if (!values.length) {
    section.append(node('p', 'No measured values are available for this chart.', 'content-note'));
  } else {
    const width = 760;
    const height = 320;
    const left = 76;
    const right = width - 30;
    const top = 30;
    const bottom = height - 70;
    let minimum = values.reduce((minimum, value) => Math.min(minimum, value), 0);
    let maximum = values.reduce((maximum, value) => Math.max(maximum, value), 0);
    const padding = (maximum - minimum || 1) * 0.1;
    if (minimum < 0) minimum -= padding;
    maximum += padding;
    const coordinates = points.map((point) => coordinate(point.x));
    const first = coordinates.reduce((minimum, value) => Math.min(minimum, value), Infinity);
    const last = coordinates.reduce((maximum, value) => Math.max(maximum, value), -Infinity);
    const xCount = new Set(coordinates).size;
    const groupWidth = ((right - left) / Math.max(1, xCount)) * 0.7;
    const barWidth = Math.min(100, groupWidth / Math.max(1, series.length));
    const axisInset = spec.kind === 'bar' ? (barWidth * series.length) / 2 : 0;
    const continuousAxis = spec.x_axis.type !== 'category';
    const x = (value: string | number): number => {
      if (continuousAxis) {
        return first === last
          ? (left + right) / 2
          : left +
              axisInset +
              ((coordinate(value) - first) / (last - first)) * (right - left - axisInset * 2);
      }

      return left + ((coordinate(value) + 0.5) / categories.length) * (right - left);
    };
    const y = (value: number): number =>
      bottom - ((value - minimum) / (maximum - minimum)) * (bottom - top);
    const svg = svgNode('svg', {
      viewBox: `0 0 ${width} ${height}`,
      role: 'img',
      class: 'analysis-chart',
      'aria-label': `${spec.title}. ${spec.x_axis.label}; ${spec.y_axis.label}${spec.y_axis.unit ? ` (${spec.y_axis.unit})` : ''}.`,
    });
    svg.append(svgNode('title', {}, spec.title));
    for (let tick = 0; tick <= 4; tick++) {
      const value = minimum + ((maximum - minimum) * tick) / 4;
      svg.append(
        svgNode('line', {
          x1: left,
          x2: right,
          y1: y(value),
          y2: y(value),
          class: 'analysis-gridline',
        }),
        svgNode(
          'text',
          { x: left - 10, y: y(value) + 4, 'text-anchor': 'end' },
          formatNumber(value, 3),
        ),
      );
    }
    svg.append(
      svgNode('line', { x1: left, x2: right, y1: y(0), y2: y(0), class: 'analysis-axis' }),
    );

    for (const [seriesIndex, group] of series.entries()) {
      const color = colors[seriesIndex % colors.length];
      const path: string[] = [];
      let previous: (typeof group.points)[number] | undefined;
      for (const point of group.points) {
        if (!measured(point.y)) {
          previous = undefined;
          continue;
        }

        const center =
          x(point.x) +
          (spec.kind === 'bar' ? (seriesIndex - (series.length - 1) / 2) * barWidth : 0);
        const tooltip = `${group.name} · ${point.x}: ${formatNumber(point.y, 4)} ${spec.y_axis.unit ?? ''}${point.note ? ` · ${point.note}` : ''}`;
        if (spec.kind === 'line') {
          const continuous =
            previous &&
            (spec.max_gap == null || coordinate(point.x) - coordinate(previous.x) <= spec.max_gap);
          path.push(`${continuous ? 'L' : 'M'} ${center} ${y(point.y)}`);
        }
        if (spec.kind === 'bar') {
          const bar = svgNode('rect', {
            x: center - barWidth * 0.45,
            y: Math.min(y(point.y), y(0)),
            width: barWidth * 0.9,
            height: Math.abs(y(point.y) - y(0)),
            fill: color,
            class: 'analysis-bar',
          });
          bar.append(svgNode('title', {}, tooltip));
          svg.append(bar);
        }
        const marker = svgNode('circle', {
          cx: center,
          cy: y(point.y),
          r: 3,
          fill: color,
          class: 'analysis-point',
        });
        marker.append(svgNode('title', {}, tooltip));
        svg.append(marker);
        previous = point;
      }
      if (path.length) {
        svg.append(
          svgNode('path', {
            d: path.join(' '),
            class: 'analysis-line',
            stroke: color,
            ...(group.style === 'dashed' ? { 'stroke-dasharray': '5 4' } : {}),
          }),
        );
      }
    }

    const ordered = points.slice().sort((a, b) => coordinate(a.x) - coordinate(b.x));
    const labels = [...new Map(ordered.map((point) => [coordinate(point.x), point.x])).values()];
    const ticks =
      labels.length <= 6
        ? labels
        : [labels[0], labels[Math.floor((labels.length - 1) / 2)], labels.at(-1)!];
    for (const value of ticks) {
      const label = String(value);
      const text = svgNode(
        'text',
        {
          x: x(value),
          y: bottom + 24,
          'text-anchor':
            continuousAxis && value === labels[0]
              ? 'start'
              : continuousAxis && value === labels.at(-1)
                ? 'end'
                : 'middle',
        },
        label.length > 22 ? `${label.slice(0, 20)}…` : label,
      );
      text.append(svgNode('title', {}, label));
      svg.append(text);
    }
    svg.append(
      svgNode(
        'text',
        { x: (left + right) / 2, y: height - 10, 'text-anchor': 'middle' },
        spec.x_axis.label,
      ),
      svgNode(
        'text',
        { x: left, y: 14 },
        `${spec.y_axis.label}${spec.y_axis.unit ? ` (${spec.y_axis.unit})` : ''}`,
      ),
    );
    const chartScroll = node('div', undefined, 'analysis-chart-scroll');
    chartScroll.tabIndex = 0;
    chartScroll.setAttribute('role', 'region');
    chartScroll.setAttribute('aria-label', spec.title);
    chartScroll.append(svg);
    section.append(chartScroll);
    const legend = node('div', undefined, 'analysis-legend');
    for (const [index, group] of series.entries()) {
      const label = node('span');
      const swatch = node('i');
      swatch.style.borderColor = colors[index % colors.length];
      swatch.style.borderTopStyle = group.style === 'dashed' ? 'dashed' : 'solid';
      label.append(swatch, document.createTextNode(group.name));
      legend.append(label);
    }
    section.append(legend);
  }

  const details = node('details', undefined, 'analysis-data');
  details.append(node('summary', 'View chart data'));
  const scroll = node('div', undefined, 'table-scroll');
  const table = node('table');
  const header = table.createTHead().insertRow();
  for (const label of ['Series', spec.x_axis.label, spec.y_axis.label, 'Unit', 'Evidence']) {
    const cell = node('th', label);
    cell.scope = 'col';
    header.append(cell);
  }
  const body = table.createTBody();
  for (const group of series) {
    for (const point of group.points) {
      const row = body.insertRow();
      for (const value of [
        group.name,
        String(point.x),
        measured(point.y) ? formatNumber(point.y, 4) : 'Missing',
        spec.y_axis.unit ?? '',
        point.note ?? '',
      ]) {
        row.append(node('td', value));
      }
    }
  }
  scroll.append(table);
  details.append(scroll);
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
  if (!analysis.charts.length)
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

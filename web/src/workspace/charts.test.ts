// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { movementAnalysis } from '../../tests/fixtures/analysis';
import chartFixtures from '../../tests/fixtures/charts.json';
import { renderAnalysis, renderChart } from './charts';
import type { Chart } from './types';

const newPlot = vi.hoisted(() => vi.fn());
vi.mock('plotly.js-dist-min', () => ({ newPlot }));

const [chart] = chartFixtures as Chart[];

describe('backend Plotly charts', () => {
  it('draws the backend figure unchanged and shows the code that built it', async () => {
    const section = renderChart(chart);
    document.body.append(section);

    await vi.waitFor(() => expect(newPlot).toHaveBeenCalled());
    const [element, data, layout, config] = newPlot.mock.calls[0];
    expect(element).toBe(section.querySelector('.analysis-chart'));
    expect(data).toEqual(chart.figure.data);
    expect(layout).toMatchObject(chart.figure.layout);
    expect(config).toMatchObject({ responsive: true });
    expect(section.querySelector('h3')?.textContent).toBe(chart.title);
    expect(section.querySelector('.analysis-chart')?.getAttribute('aria-label')).toBe(chart.title);
    expect(section.querySelector('pre')?.textContent).toBe(chart.code);
  });

  it('shows a note instead of a blank area when Plotly cannot draw the figure', async () => {
    newPlot.mockRejectedValueOnce(new Error('bad figure'));
    const section = renderChart(chart);
    document.body.append(section);

    await vi.waitFor(() =>
      expect(section.querySelector('.analysis-chart')?.textContent).toBe(
        'The chart could not be drawn.',
      ),
    );
  });

  it('explains that a chart saved in the retired template format must be redrawn', () => {
    const legacy = { chart_id: 'old', title: 'Daily median displacement', kind: 'line' };
    const section = renderChart(legacy as unknown as Chart);

    expect(section.textContent).toContain('older format');
    expect(section.querySelector('.analysis-chart')).toBeNull();
  });

  it('renders findings, charts and limitations in one analysis card', () => {
    const card = renderAnalysis(movementAnalysis);

    expect(card.querySelectorAll('.analysis-plot')).toHaveLength(1);
    expect(card.textContent).toContain('Median daily displacement was 7 km.');
    expect(card.textContent).toContain('tracking@3');
    expect(card.textContent).toContain(movementAnalysis.result.limitations[0]);
  });

  it('renders statistics-only results as an accessible table', () => {
    const card = renderAnalysis({
      ...movementAnalysis,
      charts: [],
      result: {
        ...movementAnalysis.result,
        tables: [
          {
            title: 'Descriptive statistics',
            columns: ['Variable', 'Count', 'Mean', 'Sample SD'],
            rows: [['Rainfall', 4, 0, null]],
          },
        ],
      },
    });

    expect(card.querySelector('caption')?.textContent).toBe('Descriptive statistics');
    expect(card.querySelector('tbody')?.textContent).toBe('Rainfall40Missing');
    expect(card.textContent).not.toContain('contains no charts');
  });
});

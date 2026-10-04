// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { movementAnalysis } from '../../tests/fixtures/analysis';
import chartFixtures from '../../tests/fixtures/charts.json';
import { renderAnalysis, renderChart } from './charts';
import type { ChartSpec } from './types';

const specs = chartFixtures as ChartSpec[];

describe('generic backend chart specifications', () => {
  it('plots real values and zero, preserves nulls, and stops lines at backend-defined gaps', () => {
    const card = renderAnalysis(movementAnalysis);
    const chart = card.querySelector('svg')!;

    expect(chart.getAttribute('aria-label')).toContain('Daily median displacement (km)');
    expect(chart.querySelectorAll('circle')).toHaveLength(4);
    expect(chart.querySelector('path')?.getAttribute('d')?.match(/L /g)).toHaveLength(1);
    const rows = [...card.querySelectorAll('tbody tr')];
    expect(rows[0].children[2].textContent).toBe('Missing');
    expect(rows[2].children[2].textContent).toBe('0');
    expect(card.textContent).toContain('tracking@3');
    expect(card.textContent).toContain(movementAnalysis.result.limitations[0]);
  });

  it('renders arbitrary grouped bars with backend labels, negative values, and multiple series', () => {
    const chart = renderChart(specs[1]);

    expect(chart.querySelector('h3')?.textContent).toBe('Change by monitoring site');
    expect(chart.querySelectorAll('rect.analysis-bar')).toHaveLength(3);
    expect(chart.querySelector('.analysis-legend')?.textContent).toBe('BeforeAfter');
    const rows = chart.querySelectorAll('tbody tr');
    expect(rows[0].children[2].textContent).toBe('-2');
    expect(rows[3].children[2].textContent).toBe('Missing');
    const bars = [...chart.querySelectorAll('rect')];
    expect(bars[0].getAttribute('fill')).not.toBe(bars[2].getAttribute('fill'));
    expect(bars[0].getAttribute('x')).not.toBe(bars[2].getAttribute('x'));
    expect(chart.textContent).toContain('Measured change (%)');
  });

  it('renders numeric scatter coordinates proportionally without inventing connecting lines', () => {
    const chart = renderChart(specs[2]);
    const circles = [...chart.querySelectorAll('circle')];
    const positions = circles.map((circle) => Number(circle.getAttribute('cx')));

    expect(circles).toHaveLength(3);
    expect(chart.querySelector('path')).toBeNull();
    expect(positions[1] - positions[0]).toBeCloseTo((positions[2] - positions[0]) / 4);
    expect(chart.textContent).toContain('Rainfall (mm)');
    expect(chart.textContent).toContain('-0.2');
  });

  it('uses backend series names and styles without needing analysis-specific fields', () => {
    const spec: ChartSpec = {
      ...specs[2],
      kind: 'line',
      series: [
        {
          name: 'Measured',
          points: [
            { x: 0, y: -0.2 },
            { x: 5, y: 0.4 },
          ],
        },
        {
          name: 'Estimated',
          style: 'dashed',
          points: [
            { x: 5, y: 0.4 },
            { x: 20, y: 0.5 },
          ],
        },
      ],
    };

    const chart = renderChart(spec);

    expect(chart.querySelectorAll('path')).toHaveLength(2);
    expect(chart.querySelector('path[stroke-dasharray]')).not.toBeNull();
    expect(chart.querySelector('.analysis-legend')?.textContent).toBe('MeasuredEstimated');
  });

  it('retains chart data when all measurements are missing without inventing values', () => {
    const spec: ChartSpec = {
      ...specs[0],
      series: [{ name: 'Observed', points: [{ x: '2024-01-01', y: null }] }],
    };

    const chart = renderChart(spec);

    expect(chart.querySelector('svg')).toBeNull();
    expect(chart.textContent).toContain('No measured values are available');
    expect(chart.querySelector('tbody')?.textContent).toContain('Missing');
  });
});

import { describe, expect, it } from 'vitest';
import { monthsInRange, spanningRange } from './time';

describe('monthsInRange', () => {
  it('clips the first and last month to the range', () => {
    const months = monthsInRange({ start: '2025-01-15T00:00:00Z', end: '2025-12-15T23:59:59Z' });

    expect(months).toHaveLength(12);
    expect(months[0]).toEqual({ label: 'Jan 2025', start: '2025-01-15T00:00:00Z', end: '2025-01-31T23:59:59Z' });
    expect(months[11]).toEqual({ label: 'Dec 2025', start: '2025-12-01T00:00:00Z', end: '2025-12-15T23:59:59Z' });
  });

  it('crosses year boundaries', () => {
    const months = monthsInRange({ start: '2024-11-01T00:00:00Z', end: '2025-02-10T00:00:00Z' });

    expect(months.map((month) => month.label)).toEqual(['Nov 2024', 'Dec 2024', 'Jan 2025', 'Feb 2025']);
  });

  it('returns no months for an invalid or inverted range', () => {
    expect(monthsInRange({ start: 'not a date', end: '2025-01-01T00:00:00Z' })).toEqual([]);
    expect(monthsInRange({ start: '2025-02-01T00:00:00Z', end: '2025-01-01T00:00:00Z' })).toEqual([]);
  });
});

describe('spanningRange', () => {
  it('covers every range', () => {
    expect(
      spanningRange([
        { start: '2025-03-01T00:00:00Z', end: '2025-04-01T00:00:00Z' },
        { start: '2025-01-01T00:00:00Z', end: '2025-02-01T00:00:00Z' },
      ]),
    ).toEqual({ start: '2025-01-01T00:00:00Z', end: '2025-04-01T00:00:00Z' });
  });

  it('returns undefined without ranges', () => {
    expect(spanningRange([])).toBeUndefined();
  });
});

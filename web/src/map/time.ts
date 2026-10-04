import type { TimeRange } from '../catalog';

export interface TimeWindow {
  label: string;
  start: string;
  end: string;
}

const monthLabel = new Intl.DateTimeFormat('en-GB', { month: 'short', year: 'numeric', timeZone: 'UTC' });

function toIsoSeconds(date: Date): string {
  return date.toISOString().replace(/\.\d{3}Z$/, 'Z');
}

export function spanningRange(ranges: TimeRange[]): TimeRange | undefined {
  if (ranges.length === 0) return undefined;

  const starts = ranges.map((range) => new Date(range.start).getTime());
  const ends = ranges.map((range) => new Date(range.end).getTime());
  return { start: toIsoSeconds(new Date(Math.min(...starts))), end: toIsoSeconds(new Date(Math.max(...ends))) };
}

export function monthsInRange(range: Pick<TimeRange, 'start' | 'end'>): TimeWindow[] {
  const rangeStart = new Date(range.start).getTime();
  const rangeEnd = new Date(range.end).getTime();
  if (Number.isNaN(rangeStart) || Number.isNaN(rangeEnd) || rangeStart > rangeEnd) return [];

  const firstYear = new Date(rangeStart).getUTCFullYear();
  const firstMonth = new Date(rangeStart).getUTCMonth();
  const windows: TimeWindow[] = [];
  for (let offset = 0; Date.UTC(firstYear, firstMonth + offset, 1) <= rangeEnd; offset += 1) {
    const monthStart = Date.UTC(firstYear, firstMonth + offset, 1);
    const monthEnd = Date.UTC(firstYear, firstMonth + offset + 1, 1) - 1000;
    windows.push({
      label: monthLabel.format(monthStart),
      start: toIsoSeconds(new Date(Math.max(monthStart, rangeStart))),
      end: toIsoSeconds(new Date(Math.min(monthEnd, rangeEnd))),
    });
  }

  return windows;
}

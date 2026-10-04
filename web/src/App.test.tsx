// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { parseCsv } from './App';

describe('temporary CSV import', () => {
  it('parses quoted WGS84 point records without publishing them', () => {
    const result = parseCsv('animal,latitude,longitude,note\nA14,42.63,-110.00,"sample, local"\n');
    expect(result.features).toHaveLength(1);
    expect(result.features[0].geometry).toEqual({ type: 'Point', coordinates: [-110, 42.63] });
    expect(result.features[0].properties?.note).toBe('sample, local');
  });

  it('rejects coordinates outside WGS84 bounds', () => {
    expect(() => parseCsv('lat,lon\n120,-110')).toThrow(/Invalid coordinates/);
  });
});

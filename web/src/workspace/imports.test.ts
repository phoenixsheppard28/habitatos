import { describe, expect, it } from 'vitest';
import { parseGeographicFile } from './imports';

describe('local geographic imports', () => {
  it('preserves quoted commas, escaped quotes, and multiline values', () => {
    const result = parseGeographicFile(
      'lat,lon,note\r\n-1.4,36.9,"wetland, ""north""\nvisit"\r\n',
      'observations.csv',
    );
    expect(result.features[0].geometry).toEqual({ type: 'Point', coordinates: [36.9, -1.4] });
    expect(result.features[0].properties?.note).toBe('wetland, "north"\nvisit');
  });

  it('does not convert missing coordinates to a zero coordinate', () => {
    expect(() => parseGeographicFile('lat,lon\n,36.9', 'observations.csv')).toThrow(
      /Missing coordinates/,
    );
  });

  it('rejects infinite and out-of-range coordinates', () => {
    expect(() => parseGeographicFile('lat,lon\n-1.4,Infinity', 'observations.csv')).toThrow(
      /finite/,
    );
    expect(() => parseGeographicFile('lat,lon\n95,36.9', 'observations.csv')).toThrow(/WGS84/);
  });

  it('validates every geometry within a geometry collection', () => {
    const data = {
      type: 'GeometryCollection',
      geometries: [
        { type: 'Point', coordinates: [36.9, -1.4] },
        { type: 'Point', coordinates: [200, -1.4] },
      ],
    };
    expect(() => parseGeographicFile(JSON.stringify(data), 'observations.geojson')).toThrow(
      /WGS84/,
    );
  });

  it('retains attributes without interpreting HTML as markup', () => {
    const data = {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [36.9, -1.4] },
      properties: { note: '<img src=x onerror=alert(1)>' },
    };
    const imported = parseGeographicFile(JSON.stringify(data), 'observations.geojson');
    expect(imported.features[0].properties?.note).toBe(data.properties.note);
  });
});

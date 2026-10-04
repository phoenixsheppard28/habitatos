import type { Feature, FeatureCollection, Geometry } from 'geojson';

const maximumFeatures = 10_000;

export function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = '';
  let quoted = false;
  for (let index = 0; index < text.length; index++) {
    const character = text[index];
    if (character === '"') {
      if (quoted && text[index + 1] === '"') {
        cell += '"';
        index++;
      } else quoted = !quoted;
    } else if (character === ',' && !quoted) {
      row.push(cell);
      cell = '';
    } else if ((character === '\n' || character === '\r') && !quoted) {
      if (character === '\r' && text[index + 1] === '\n') index++;
      row.push(cell);
      if (row.some((value) => value.trim())) rows.push(row);
      row = [];
      cell = '';
    } else cell += character;
  }
  if (quoted) throw new Error('CSV has an unclosed quoted field.');

  row.push(cell);
  if (row.some((value) => value.trim())) rows.push(row);

  return rows;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function validatePosition(value: unknown): void {
  if (
    !Array.isArray(value) ||
    value.length < 2 ||
    !value.every((coordinate) => typeof coordinate === 'number' && Number.isFinite(coordinate))
  ) {
    throw new Error('Coordinates must contain finite longitude and latitude values.');
  }
  if (Math.abs(value[0]) > 180 || Math.abs(value[1]) > 90)
    throw new Error('Coordinates must use WGS84 longitude and latitude.');
}

function validateCoordinates(value: unknown, depth: number, minimumLength: number): void {
  if (depth === 0) {
    validatePosition(value);
    return;
  }
  if (!Array.isArray(value) || value.length < minimumLength)
    throw new Error('The geometry has too few coordinates.');

  for (const coordinates of value) validateCoordinates(coordinates, depth - 1, depth === 1 ? 2 : 1);
}

function validateGeometry(value: unknown): void {
  if (!isObject(value)) throw new Error('Every feature needs a geometry.');
  if (value.type === 'GeometryCollection') {
    if (!Array.isArray(value.geometries) || !value.geometries.length)
      throw new Error('The geometry collection is empty.');
    value.geometries.forEach(validateGeometry);
    return;
  }

  const depths: Record<string, number> = {
    Point: 0,
    MultiPoint: 1,
    LineString: 1,
    MultiLineString: 2,
    Polygon: 2,
    MultiPolygon: 3,
  };
  if (typeof value.type !== 'string' || !(value.type in depths))
    throw new Error('Unsupported GeoJSON geometry.');

  const minimumLength = value.type === 'LineString' ? 2 : 1;
  validateCoordinates(value.coordinates, depths[value.type], minimumLength);
}

function csvFeatures(text: string): FeatureCollection {
  const rows = parseCsv(text);
  const headers = (rows.shift() ?? []).map((value) =>
    value
      .replace(/^\uFEFF/, '')
      .trim()
      .toLowerCase(),
  );
  const latitudeIndex = headers.findIndex((header) => ['lat', 'latitude'].includes(header));
  const longitudeIndex = headers.findIndex((header) =>
    ['lon', 'lng', 'longitude'].includes(header),
  );
  if (latitudeIndex < 0 || longitudeIndex < 0)
    throw new Error('CSV needs latitude and longitude columns, or lat and lon.');
  if (!rows.length || rows.length > maximumFeatures)
    throw new Error('Import a file with 1–10,000 records.');

  const features: Feature[] = rows.map((row, index) => {
    if (!row[latitudeIndex]?.trim() || !row[longitudeIndex]?.trim())
      throw new Error(`Missing coordinates in row ${index + 2}.`);

    const coordinates = [Number(row[longitudeIndex]), Number(row[latitudeIndex])];
    validatePosition(coordinates);

    return {
      type: 'Feature',
      geometry: { type: 'Point', coordinates },
      properties: Object.fromEntries(headers.map((header, column) => [header, row[column] ?? ''])),
    };
  });

  return { type: 'FeatureCollection', features };
}

export function parseGeographicFile(text: string, filename: string): FeatureCollection {
  if (/\.csv$/i.test(filename)) return csvFeatures(text);

  const data: unknown = JSON.parse(text);
  if (!isObject(data)) throw new Error('Import a valid GeoJSON object.');

  const candidates = data.type === 'FeatureCollection' ? data.features : [data];
  if (!Array.isArray(candidates) || !candidates.length || candidates.length > maximumFeatures) {
    throw new Error('Import a file with 1–10,000 features.');
  }
  const features = candidates.map((candidate: unknown): Feature => {
    if (!isObject(candidate)) throw new Error('Invalid GeoJSON feature.');

    const geometry = candidate.type === 'Feature' ? candidate.geometry : candidate;
    validateGeometry(geometry);
    const properties =
      candidate.type === 'Feature' && isObject(candidate.properties) ? candidate.properties : {};

    return { type: 'Feature', geometry: geometry as Geometry, properties };
  });

  return { type: 'FeatureCollection', features };
}

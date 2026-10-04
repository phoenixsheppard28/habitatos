CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE movement_points (
  id bigserial PRIMARY KEY,
  animal_id text NOT NULL,
  observed_at timestamptz NOT NULL,
  rainfall_mm integer NOT NULL,
  sample boolean NOT NULL DEFAULT true,
  geom geometry(Point, 4326) NOT NULL
);

WITH tracks(animal_id, points) AS (
  VALUES
    ('A14', '[[42.63,-110.00],[42.65,-109.96],[42.68,-109.98],[42.71,-109.92],[42.75,-109.88],[42.80,-109.91],[42.85,-109.84],[42.89,-109.80],[42.92,-109.76],[42.91,-109.73],[42.94,-109.78],[42.97,-109.74]]'::jsonb),
    ('A22', '[[42.61,-109.90],[42.63,-109.86],[42.66,-109.88],[42.69,-109.82],[42.73,-109.79],[42.78,-109.81],[42.83,-109.75],[42.87,-109.72],[42.90,-109.68],[42.89,-109.66],[42.93,-109.70],[42.95,-109.67]]'::jsonb),
    ('A31', '[[42.64,-109.80],[42.65,-109.77],[42.67,-109.79],[42.70,-109.74],[42.73,-109.71],[42.77,-109.73],[42.82,-109.68],[42.86,-109.64],[42.90,-109.61],[42.92,-109.58],[42.94,-109.62],[42.97,-109.59]]'::jsonb),
    ('A40', '[[42.60,-109.72],[42.61,-109.69],[42.62,-109.71],[42.64,-109.67],[42.66,-109.64],[42.69,-109.66],[42.78,-109.60],[42.86,-109.56],[42.91,-109.54],[42.90,-109.52],[42.93,-109.55],[42.96,-109.53]]'::jsonb)
), points AS (
  SELECT animal_id, ordinality::integer AS month_number,
    (point->>0)::double precision AS latitude,
    (point->>1)::double precision AS longitude
  FROM tracks, jsonb_array_elements(points) WITH ORDINALITY AS item(point, ordinality)
)
INSERT INTO movement_points (animal_id, observed_at, rainfall_mm, geom)
SELECT animal_id, make_timestamptz(2025, month_number, 15, 12, 0, 0, 'UTC'),
  greatest(2, round((42.98 - latitude) * 70 - CASE WHEN month_number > 6 THEN 10 ELSE 0 END)::integer),
  ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
FROM points;

CREATE INDEX movement_points_geom_idx ON movement_points USING gist (geom);
CREATE INDEX movement_points_time_idx ON movement_points (observed_at);

CREATE TABLE study_boundary (
  id integer PRIMARY KEY,
  name text NOT NULL,
  sample boolean NOT NULL DEFAULT true,
  geom geometry(Polygon, 4326) NOT NULL
);
INSERT INTO study_boundary VALUES (
  1, 'Sublette County sample extent', true,
  ST_GeomFromText('POLYGON((-110.08 42.56,-109.46 42.56,-109.46 43.01,-110.08 43.01,-110.08 42.56))', 4326)
);

CREATE TABLE rainfall_zones (
  id bigserial PRIMARY KEY,
  observed_at timestamptz NOT NULL,
  rainfall_mm integer NOT NULL,
  sample boolean NOT NULL DEFAULT true,
  geom geometry(Polygon, 4326) NOT NULL
);
INSERT INTO rainfall_zones (observed_at, rainfall_mm, geom)
SELECT make_timestamptz(2025, month_number, 15, 12, 0, 0, 'UTC'),
  greatest(2, round((42.98 - (42.56 + row_number * 0.09 + 0.04)) * 70 - CASE WHEN month_number > 6 THEN 10 ELSE 0 END)::integer),
  ST_MakeEnvelope(-110.08 + column_number * 0.13, 42.56 + row_number * 0.09,
    least(-109.46, -110.08 + (column_number + 1) * 0.13), least(43.01, 42.56 + (row_number + 1) * 0.09), 4326)
FROM generate_series(0, 4) AS column_number,
     generate_series(0, 4) AS row_number,
     generate_series(1, 12) AS month_number;
CREATE INDEX rainfall_zones_geom_idx ON rainfall_zones USING gist (geom);
CREATE INDEX rainfall_zones_time_idx ON rainfall_zones (observed_at);

CREATE TABLE vegetation_extent (
  id integer PRIMARY KEY,
  label text NOT NULL,
  sample boolean NOT NULL DEFAULT true,
  geom geometry(Polygon, 4326) NOT NULL
);
INSERT INTO vegetation_extent VALUES (
  1, 'Illustrative vegetation extent', true,
  ST_GeomFromText('POLYGON((-110.05 42.78,-109.50 42.78,-109.50 42.99,-110.05 42.99,-110.05 42.78))', 4326)
);

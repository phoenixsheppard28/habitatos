-- Queries that the Recipe lane can run on the canonical tables.
-- The views already apply version, replacement and de-duplication rules.

-- name: catalog datasets
SELECT dataset_id, version, family, row_count, variables, species_keys, lower(time_range), upper(time_range)
FROM latest_datasets
ORDER BY dataset_id;

-- name: wildebeest per animal, March-April 2011, inside the area
SELECT entity_id, count(*) AS fixes, min(observed_at) AS first_fix, max(observed_at) AS last_fix
FROM current_animal_locations
WHERE observed_at >= '2011-03-01' AND observed_at < '2011-05-01'
  AND ST_Intersects(geometry, ST_MakeEnvelope(36.85, -1.60, 37.10, -1.35, 4326))
  AND quality_flag = 'ok'
GROUP BY entity_id
ORDER BY fixes DESC;

-- name: rainfall in the 14 days before each daily position
WITH daily_positions AS (
    SELECT DISTINCT ON (entity_id, observed_at::date)
        entity_id, observed_at::date AS day, cell_id
    FROM current_animal_locations
    WHERE observed_at >= '2011-03-15' AND observed_at < '2011-05-01'
      AND ST_Intersects(geometry, ST_MakeEnvelope(36.85, -1.60, 37.10, -1.35, 4326))
      AND quality_flag = 'ok'
    ORDER BY entity_id, observed_at::date, observed_at
)
SELECT p.entity_id, p.day, p.cell_id,
       sum(r.rainfall_mm) AS rainfall_14d_mm,
       count(r.rainfall_mm) AS days_with_data
FROM daily_positions p
LEFT JOIN rainfall_observations r
       ON r.cell_id = p.cell_id
      AND r.interval_start >= p.day - 14
      AND r.interval_end <= p.day
GROUP BY p.entity_id, p.day, p.cell_id
ORDER BY p.entity_id, p.day;

-- name: MODIS NDVI composite that covers each daily position
WITH daily_positions AS (
    SELECT DISTINCT ON (entity_id, observed_at::date)
        entity_id, observed_at, cell_id
    FROM current_animal_locations
    WHERE observed_at >= '2011-03-01' AND observed_at < '2011-05-01'
      AND ST_Intersects(geometry, ST_MakeEnvelope(36.85, -1.60, 37.10, -1.35, 4326))
      AND quality_flag = 'ok'
    ORDER BY entity_id, observed_at::date, observed_at
)
SELECT p.entity_id, p.observed_at::date AS day, v.observed_at::date AS composite_start, v.index_value AS ndvi,
       v.valid_fraction
FROM daily_positions p
JOIN vegetation_observations v
  ON v.cell_id = p.cell_id
 AND v.index_name = 'ndvi'
 AND v.source_id = 'modis_mod13q1'
 AND p.observed_at >= v.observed_at AND p.observed_at < v.observed_until
ORDER BY p.entity_id, day;

-- name: Sentinel-2 indices over the area on 2024-02-17
SELECT index_name, count(*) AS cells, count(index_value) AS reliable_cells,
       round(avg(index_value)::numeric, 3) AS mean_value
FROM vegetation_observations
WHERE source_id = 'sentinel2' AND observed_at::date = '2024-02-17'
GROUP BY index_name
ORDER BY index_name;

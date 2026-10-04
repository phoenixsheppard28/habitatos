-- Inputs for the Recipe lane. Recipe pins a dataset as (dataset_id, version) and filters a shared
-- relation on dataset_id, dataset_version and access_scope. A catalog version of a series holds every
-- batch with added_in_version <= version that is not superseded at that version, so these views
-- expand each registered catalog version to its rows. Only ready catalog versions appear.
-- The partition starts with dataset_id and version, so a filter on them reaches the base tables.

CREATE VIEW recipe_cell_observations WITH (security_invoker = true) AS
SELECT
    dataset_id, dataset_version, access_scope, series_id, batch_key, cell_id, time_start, time_end,
    time_precision, available_at, source_id, source_item_id, processing_version, product_status,
    mapping_version, quality_flag, variable, stat, value, std, unit, valid_fraction, pixel_count
FROM (
    SELECT
        d.dataset_id, d.version::text AS dataset_version, d.access_scope,
        o.series_id, o.batch_key, o.cell_id, o.time_start, o.time_end, o.time_precision, o.available_at,
        o.source_id, o.source_item_id, o.processing_version, o.product_status, o.mapping_version,
        o.quality_flag, o.variable, o.stat, o.value, o.std, o.unit, o.valid_fraction, o.pixel_count,
        row_number() OVER (
            PARTITION BY d.dataset_id, d.version, d.access_scope, o.cell_id, o.variable, o.source_id,
                         (o.time_start AT TIME ZONE 'UTC')::date
            ORDER BY (o.product_status = 'final') DESC, o.processing_version DESC, o.mapping_version DESC,
                     o.valid_fraction DESC, o.available_at DESC, o.source_item_id DESC
        ) AS current_rank
    FROM datasets d
    JOIN ingest_batches b
      ON b.series_id = d.dataset_id
     AND b.added_in_version <= d.version
     AND (b.superseded_in_version IS NULL OR b.superseded_in_version > d.version)
    JOIN cell_observations o ON o.series_id = b.series_id AND o.batch_key = b.batch_key
    WHERE d.status = 'ready' AND d.family = 'cell_observations'
) ranked
WHERE current_rank = 1;

CREATE VIEW recipe_rainfall_observations WITH (security_invoker = true) AS
SELECT
    c.dataset_id, c.dataset_version, c.access_scope, c.source_item_id AS source_record_id, c.cell_id,
    g.geometry, c.time_start AS interval_start, c.time_end AS interval_end, c.value AS rainfall_mm,
    c.available_at, c.product_status, c.quality_flag, c.valid_fraction
FROM recipe_cell_observations c
JOIN grid_cells g USING (cell_id)
WHERE c.variable = 'rainfall_mm';

CREATE VIEW recipe_vegetation_observations WITH (security_invoker = true) AS
SELECT
    c.dataset_id, c.dataset_version, c.access_scope, c.source_item_id AS source_record_id, c.cell_id,
    g.geometry, c.time_start AS observed_at, c.time_end AS observed_until, c.variable AS index_name,
    c.value AS index_value, c.std AS index_std, c.available_at, c.quality_flag, c.valid_fraction,
    c.pixel_count
FROM recipe_cell_observations c
JOIN grid_cells g USING (cell_id)
WHERE c.variable IN ('ndvi', 'evi', 'mndwi', 'ndmi');

-- A later batch replaces an earlier fix of the same animal, time and sensor, as in current_animal_locations.
CREATE VIEW recipe_animal_locations WITH (security_invoker = true) AS
SELECT
    dataset_id, dataset_version, access_scope, source_record_id, entity_id, species, observed_at,
    available_at, longitude, latitude, cell_id, sensor_type, quality_flag
FROM (
    SELECT
        d.dataset_id, d.version::text AS dataset_version, d.access_scope,
        l.source_record_id, l.entity_id, e.taxon_name AS species, l.observed_at, l.available_at,
        l.longitude, l.latitude, l.cell_id, l.sensor_type, l.quality_flag,
        row_number() OVER (
            PARTITION BY d.dataset_id, d.version, d.access_scope, l.entity_id, l.observed_at, l.sensor_type
            ORDER BY b.added_in_version DESC, l.source_record_id DESC
        ) AS current_rank
    FROM datasets d
    JOIN ingest_batches b
      ON b.series_id = d.dataset_id
     AND b.added_in_version <= d.version
     AND (b.superseded_in_version IS NULL OR b.superseded_in_version > d.version)
    JOIN animal_locations l ON l.series_id = b.series_id AND l.batch_key = b.batch_key
    JOIN animal_entities e ON e.entity_id = l.entity_id
    WHERE d.status = 'ready' AND d.family = 'animal_locations'
) ranked
WHERE current_rank = 1;

GRANT SELECT ON recipe_cell_observations, recipe_rainfall_observations, recipe_vegetation_observations,
    recipe_animal_locations TO habitat_reader;

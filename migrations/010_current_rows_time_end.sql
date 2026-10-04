-- A value is current per cell, variable, source, UTC day of time_start and interval length.
-- Without the length, an annual value and a five-year trend that start on the same day compete, and only one
-- stays current. The length, not time_end itself, keeps two instant scenes of one UTC day in competition.
-- The column lists do not change, so the dependent views and grants stay in place.
CREATE OR REPLACE VIEW current_cell_observations WITH (security_invoker = true) AS
SELECT
    series_id, batch_key, cell_id, time_start, time_end, time_precision, available_at, source_id,
    source_item_id, processing_version, product_status, dataset_id, dataset_version, mapping_version,
    quality_flag, variable, stat, value, std, unit, valid_fraction, pixel_count, source_resolution_m
FROM (
    SELECT
        o.*,
        b.added_in_version AS dataset_version,
        row_number() OVER (
            PARTITION BY o.cell_id, o.variable, o.source_id, (o.time_start AT TIME ZONE 'UTC')::date,
                         o.time_end - o.time_start
            ORDER BY (o.product_status = 'final') DESC, o.processing_version DESC, o.mapping_version DESC,
                     o.valid_fraction DESC, o.available_at DESC, o.source_item_id DESC
        ) AS current_rank
    FROM cell_observations o
    JOIN ingest_batches b USING (series_id, batch_key)
    WHERE b.superseded_in_version IS NULL
) ranked
WHERE current_rank = 1;

CREATE OR REPLACE FUNCTION current_cell_observations_at(
    p_series_id text,
    p_version integer DEFAULT NULL,
    p_cutoff timestamptz DEFAULT NULL
)
RETURNS SETOF current_cell_observations
LANGUAGE sql STABLE
SET search_path = ''
BEGIN ATOMIC
    SELECT
        series_id, batch_key, cell_id, time_start, time_end, time_precision, available_at, source_id,
        source_item_id, processing_version, product_status, dataset_id, dataset_version, mapping_version,
        quality_flag, variable, stat, value, std, unit, valid_fraction, pixel_count, source_resolution_m
    FROM (
        SELECT
            o.*,
            b.added_in_version AS dataset_version,
            row_number() OVER (
                PARTITION BY o.cell_id, o.variable, o.source_id, (o.time_start AT TIME ZONE 'UTC')::date,
                         o.time_end - o.time_start
                ORDER BY (o.product_status = 'final') DESC, o.processing_version DESC, o.mapping_version DESC,
                         o.valid_fraction DESC, o.available_at DESC, o.source_item_id DESC
            ) AS current_rank
        FROM cell_observations o
        JOIN ingest_batches b ON b.series_id = o.series_id AND b.batch_key = o.batch_key
        WHERE o.series_id = p_series_id
          AND (p_cutoff IS NULL OR o.available_at <= p_cutoff)
          AND CASE
                  WHEN p_version IS NULL THEN b.superseded_in_version IS NULL
                  ELSE b.added_in_version <= p_version
                       AND (b.superseded_in_version IS NULL OR b.superseded_in_version > p_version)
              END
    ) ranked
    WHERE current_rank = 1;
END;

CREATE OR REPLACE VIEW recipe_cell_observations WITH (security_invoker = true) AS
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
                         (o.time_start AT TIME ZONE 'UTC')::date, o.time_end - o.time_start
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

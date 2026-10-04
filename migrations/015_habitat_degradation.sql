-- Habitat degradation indicators for the Recipe lane: Landsat indices, land cover fractions, the burned fraction
-- and the derived vegetation indicators. The variables have different units, so the view keeps the variable name,
-- the unit and the statistic in each row. The source list is the same as HABITAT_INDICATOR_SOURCES in
-- src/habitat/recipe_inputs.py.
CREATE VIEW recipe_habitat_indicators WITH (security_invoker = true) AS
SELECT
    c.dataset_id, c.dataset_version, c.access_scope, c.source_item_id AS source_record_id, c.cell_id, g.geometry,
    c.time_start AS interval_start, c.time_end AS interval_end, c.source_id, c.variable AS indicator, c.value,
    c.std, c.unit, c.stat, c.available_at, c.product_status, c.quality_flag, c.valid_fraction, c.pixel_count
FROM recipe_cell_observations c
JOIN grid_cells g USING (cell_id)
WHERE c.source_id IN (
    'landsat_c2_l2', 'esa_cci_lc', 'io_lulc_annual', 'modis_mcd64a1', 'vegetation_annual_derived',
    'vegetation_trend_derived'
);

GRANT SELECT ON recipe_habitat_indicators TO habitat_reader;

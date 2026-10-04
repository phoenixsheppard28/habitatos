-- One physical feature (a river, a lake, a borehole) per row, with the time the source first shows this version.
-- A point has longitude, latitude and cell_id. A line or an area has only a geometry; join it to cells by overlap.
CREATE TABLE site_features (
    series_id                 text             NOT NULL,
    batch_key                 text             NOT NULL,
    source_record_id          text             NOT NULL,
    dataset_id                text             NOT NULL,
    source_id                 text             NOT NULL,
    source_item_id            text             NOT NULL,
    processing_version        text             NOT NULL,
    mapping_version           text             NOT NULL,
    feature_id                text             NOT NULL,
    feature_class             text             NOT NULL,
    feature_type              text             NOT NULL,
    origin                    text             NOT NULL CHECK (origin IN ('natural', 'artificial', 'unknown')),
    permanence                text             NOT NULL CHECK (permanence IN ('permanent', 'seasonal', 'intermittent', 'unknown')),
    status                    text             NOT NULL,
    name                      text,
    time_start                timestamptz      NOT NULL,
    time_end                  timestamptz      NOT NULL,
    time_precision            text             NOT NULL,
    available_at              timestamptz      NOT NULL,
    longitude                 double precision CHECK (longitude BETWEEN -180 AND 180),
    latitude                  double precision CHECK (latitude BETWEEN -90 AND 90),
    cell_id                   text             REFERENCES grid_cells,
    geometry                  geometry(Geometry, 4326) NOT NULL,
    coordinate_uncertainty_m  double precision,
    quality_flag              text             NOT NULL,
    attributes                jsonb            NOT NULL DEFAULT '{}',
    PRIMARY KEY (series_id, batch_key, source_record_id),
    FOREIGN KEY (series_id, batch_key) REFERENCES ingest_batches (series_id, batch_key)
);

CREATE INDEX site_features_feature_time_idx ON site_features (feature_id, time_start);
CREATE INDEX site_features_class_idx ON site_features (feature_class, time_start);
CREATE INDEX site_features_geometry_idx ON site_features USING gist (geometry);

-- In each catalog version one row per feature and time_start is current. A later version of the same feature
-- ends the earlier one: valid_until is the time_start of the next current version, else time_end.
CREATE VIEW recipe_site_features WITH (security_invoker = true) AS
SELECT
    dataset_id, dataset_version, access_scope, source_id, source_record_id, feature_id, feature_class,
    feature_type, origin, permanence, status, name, time_start, time_end,
    least(time_end, lead(time_start) OVER (
        PARTITION BY dataset_id, dataset_version, access_scope, feature_id ORDER BY time_start
    )) AS valid_until,
    time_precision, available_at, longitude, latitude, cell_id, geometry, quality_flag
FROM (
    SELECT
        d.dataset_id, d.version::text AS dataset_version, d.access_scope,
        f.source_id, f.source_record_id, f.feature_id, f.feature_class, f.feature_type, f.origin, f.permanence,
        f.status, f.name, f.time_start, f.time_end, f.time_precision, f.available_at, f.longitude, f.latitude,
        f.cell_id, f.geometry, f.quality_flag,
        row_number() OVER (
            PARTITION BY d.dataset_id, d.version, d.access_scope, f.feature_id, f.time_start
            ORDER BY b.added_in_version DESC, f.available_at DESC, f.source_record_id DESC
        ) AS current_rank
    FROM datasets d
    JOIN ingest_batches b
      ON b.series_id = d.dataset_id
     AND b.added_in_version <= d.version
     AND (b.superseded_in_version IS NULL OR b.superseded_in_version > d.version)
    JOIN site_features f ON f.series_id = b.series_id AND f.batch_key = b.batch_key
    WHERE d.status = 'ready' AND d.family = 'site_features'
) ranked
WHERE current_rank = 1;

CREATE VIEW recipe_water_observations WITH (security_invoker = true) AS
SELECT
    c.dataset_id, c.dataset_version, c.access_scope, c.source_item_id AS source_record_id, c.cell_id,
    g.geometry, c.time_start AS interval_start, c.time_end AS interval_end, c.variable, c.stat, c.value, c.unit,
    c.available_at, c.quality_flag, c.valid_fraction, c.pixel_count, c.source_id
FROM recipe_cell_observations c
JOIN grid_cells g USING (cell_id)
WHERE c.variable IN (
    'surface_water_fraction', 'distance_to_surface_water_m', 'distance_to_water_m', 'distance_to_permanent_water_m',
    'distance_to_natural_water_m', 'distance_to_artificial_water_m', 'water_point_density'
);

ALTER TABLE site_features ENABLE ROW LEVEL SECURITY;

CREATE POLICY site_features_read ON site_features FOR SELECT TO habitat_reader USING (true);
CREATE POLICY site_features_insert ON site_features FOR INSERT TO habitat_writer WITH CHECK (true);

GRANT SELECT ON site_features, recipe_site_features, recipe_water_observations TO habitat_reader;
GRANT INSERT ON site_features TO habitat_writer;

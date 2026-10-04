-- Water-quality samples at monitoring stations. See docs/ingestion/WATER_POLLUTION.md.
-- site_id is namespaced by source, for example gemstat:ARG00014 or wqp:USGS-01646500.
-- site_feature_id refers to a site_features row. There is no foreign key, because site_features rows have versions.
CREATE TABLE monitoring_sites (
    site_id                   text             PRIMARY KEY,
    source_id                 text             NOT NULL,
    local_site_id             text             NOT NULL,
    site_name                 text,
    water_body_type           text             NOT NULL CHECK (water_body_type IN (
                                  'river', 'lake', 'reservoir', 'wetland', 'canal', 'spring', 'groundwater', 'other')),
    water_body_name           text,
    longitude                 double precision NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    latitude                  double precision NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    geometry                  geometry(Point, 4326) GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)) STORED,
    cell_id                   text             REFERENCES grid_cells,
    coordinate_uncertainty_m  double precision,
    site_feature_id           text,
    site_feature_distance_m   double precision,
    upstream_area_km2         double precision,
    elevation_m               double precision,
    attributes                jsonb            NOT NULL DEFAULT '{}'
);

-- A censored row holds the limit in value and in detection_limit. A value below the limit is never stored as 0.
CREATE TABLE site_observations (
    series_id             text             NOT NULL,
    batch_key             text             NOT NULL,
    source_record_id      text             NOT NULL,
    dataset_id            text             NOT NULL,
    site_id               text             NOT NULL REFERENCES monitoring_sites,
    time_start            timestamptz      NOT NULL,
    time_end              timestamptz      NOT NULL,
    time_precision        text             NOT NULL,
    available_at          timestamptz      NOT NULL,
    longitude             double precision NOT NULL,
    latitude              double precision NOT NULL,
    cell_id               text             REFERENCES grid_cells,
    source_id             text             NOT NULL,
    source_item_id        text             NOT NULL,
    processing_version    text             NOT NULL,
    product_status        text             NOT NULL,
    mapping_version       text             NOT NULL,
    parameter             text             NOT NULL,
    fraction              text             NOT NULL CHECK (fraction IN ('total', 'dissolved', 'suspended', 'not_applicable')),
    value                 double precision,
    unit                  text             NOT NULL,
    censored              text             NOT NULL CHECK (censored IN ('none', 'left', 'right')),
    detection_limit       double precision,
    detection_limit_type  text,
    sample_depth_m        double precision,
    method                text,
    quality_flag          text             NOT NULL,
    attributes            jsonb            NOT NULL DEFAULT '{}',
    PRIMARY KEY (series_id, batch_key, source_record_id),
    FOREIGN KEY (series_id, batch_key) REFERENCES ingest_batches (series_id, batch_key)
);

CREATE INDEX site_observations_site_time_idx ON site_observations (site_id, parameter, time_start);
CREATE INDEX site_observations_cell_time_idx ON site_observations (cell_id, time_start);
CREATE INDEX monitoring_sites_geometry_idx ON monitoring_sites USING gist (geometry);

ALTER TABLE monitoring_sites ENABLE ROW LEVEL SECURITY;
ALTER TABLE site_observations ENABLE ROW LEVEL SECURITY;

CREATE POLICY monitoring_sites_read ON monitoring_sites FOR SELECT TO habitat_reader USING (true);
CREATE POLICY monitoring_sites_insert ON monitoring_sites FOR INSERT TO habitat_writer WITH CHECK (true);
CREATE POLICY monitoring_sites_update ON monitoring_sites FOR UPDATE TO habitat_writer USING (true) WITH CHECK (true);
CREATE POLICY site_observations_read ON site_observations FOR SELECT TO habitat_reader USING (true);
CREATE POLICY site_observations_insert ON site_observations FOR INSERT TO habitat_writer WITH CHECK (true);

GRANT SELECT ON monitoring_sites, site_observations TO habitat_reader;
GRANT INSERT, UPDATE ON monitoring_sites TO habitat_writer;
GRANT INSERT ON site_observations TO habitat_writer;

-- One row is current per site, parameter, fraction, sample time and sample depth.
-- A final value replaces a preliminary value. Then a later batch replaces an earlier batch.
CREATE VIEW recipe_site_observations WITH (security_invoker = true) AS
SELECT
    dataset_id, dataset_version, access_scope, source_record_id, site_id, water_body_type, site_feature_id,
    time_start, time_end, time_precision, available_at, longitude, latitude, cell_id, parameter, fraction,
    value, unit, censored, detection_limit, sample_depth_m, product_status, quality_flag
FROM (
    SELECT
        d.dataset_id, d.version::text AS dataset_version, d.access_scope,
        o.source_record_id, o.site_id, s.water_body_type, s.site_feature_id, o.time_start, o.time_end,
        o.time_precision, o.available_at, o.longitude, o.latitude, o.cell_id, o.parameter, o.fraction,
        o.value, o.unit, o.censored, o.detection_limit, o.sample_depth_m, o.product_status, o.quality_flag,
        row_number() OVER (
            PARTITION BY d.dataset_id, d.version, d.access_scope, o.site_id, o.parameter, o.fraction,
                         o.time_start, coalesce(o.sample_depth_m, -1)
            ORDER BY (o.product_status = 'final') DESC, b.added_in_version DESC, o.available_at DESC,
                     o.source_record_id DESC
        ) AS current_rank
    FROM datasets d
    JOIN ingest_batches b
      ON b.series_id = d.dataset_id
     AND b.added_in_version <= d.version
     AND (b.superseded_in_version IS NULL OR b.superseded_in_version > d.version)
    JOIN site_observations o ON o.series_id = b.series_id AND o.batch_key = b.batch_key
    JOIN monitoring_sites s ON s.site_id = o.site_id
    WHERE d.status = 'ready' AND d.family = 'site_observations'
) ranked
WHERE current_rank = 1;

-- Satellite water-quality proxies in cell_observations. NDTI and NDCI are relative indices; water_turbidity is in
-- NTU. Never mix a relative index with a concentration. The Recipe dataset id is the catalog dataset id with the
-- suffix '--water-quality', because one Sentinel-2 series also gives vegetation indices.
CREATE VIEW recipe_water_quality_observations WITH (security_invoker = true) AS
SELECT
    c.dataset_id || '--water-quality' AS dataset_id, c.dataset_version, c.access_scope,
    c.dataset_id AS source_dataset_id, c.source_item_id AS source_record_id, c.cell_id, g.geometry,
    c.time_start AS observed_at, c.time_end AS observed_until, c.variable, c.value, c.std, c.unit,
    c.available_at, c.product_status, c.quality_flag, c.valid_fraction, c.pixel_count
FROM recipe_cell_observations c
JOIN grid_cells g USING (cell_id)
WHERE c.variable IN ('ndti', 'ndci', 'water_turbidity', 'trophic_state_index');

GRANT SELECT ON recipe_site_observations, recipe_water_quality_observations TO habitat_reader;

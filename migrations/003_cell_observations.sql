CREATE TABLE grid_cells (
    cell_id    text    PRIMARY KEY,
    grid_id    text    NOT NULL,
    row_index  integer NOT NULL,
    col_index  integer NOT NULL,
    geometry   geometry(Polygon, 4326) NOT NULL,
    centroid   geometry(Point, 4326)   NOT NULL
);

CREATE INDEX grid_cells_geometry_idx ON grid_cells USING gist (geometry);

CREATE TABLE series (
    series_id       text        PRIMARY KEY,
    family          text        NOT NULL,
    source_id       text        NOT NULL,
    product         text        NOT NULL,
    latest_version  integer     NOT NULL DEFAULT 0,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE series_versions (
    series_id       text        NOT NULL REFERENCES series,
    version         integer     NOT NULL,
    parent_version  integer,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (series_id, version)
);

-- A series version holds every batch with added_in_version <= version
-- and superseded_in_version either null or > version.
CREATE TABLE ingest_batches (
    series_id              text        NOT NULL,
    batch_key              text        NOT NULL,
    source_item_id         text        NOT NULL,
    processing_version     text        NOT NULL,
    product_status         text        NOT NULL,
    mapping_version        text        NOT NULL,
    row_count              integer     NOT NULL,
    raw_manifest           jsonb       NOT NULL,
    created_at             timestamptz NOT NULL DEFAULT now(),
    added_in_version       integer     NOT NULL,
    superseded_in_version  integer,
    PRIMARY KEY (series_id, batch_key),
    FOREIGN KEY (series_id, added_in_version) REFERENCES series_versions (series_id, version)
);

CREATE INDEX ingest_batches_item_idx ON ingest_batches (series_id, source_item_id);

CREATE TABLE cell_observations (
    series_id            text             NOT NULL,
    batch_key            text             NOT NULL,
    cell_id              text             NOT NULL REFERENCES grid_cells,
    time_start           timestamptz      NOT NULL,
    time_end             timestamptz      NOT NULL,
    time_precision       text             NOT NULL,
    available_at         timestamptz      NOT NULL,
    source_id            text             NOT NULL,
    source_item_id       text             NOT NULL,
    processing_version   text             NOT NULL,
    product_status       text             NOT NULL,
    dataset_id           text             NOT NULL,
    mapping_version      text             NOT NULL,
    quality_flag         text             NOT NULL,
    variable             text             NOT NULL,
    stat                 text             NOT NULL,
    value                double precision,
    std                  double precision,
    unit                 text             NOT NULL,
    valid_fraction       double precision NOT NULL,
    pixel_count          bigint           NOT NULL,
    source_resolution_m  double precision NOT NULL,
    PRIMARY KEY (series_id, batch_key, variable, cell_id),
    FOREIGN KEY (series_id, batch_key) REFERENCES ingest_batches (series_id, batch_key)
);

CREATE INDEX cell_observations_lookup_idx ON cell_observations (variable, cell_id, time_start);
CREATE INDEX cell_observations_time_idx ON cell_observations (source_id, time_start);

-- One overlapping or replaced value per cell, variable, source and UTC day is current:
-- final beats preliminary, then the newest processing, then the clearest view, then the latest publication.
CREATE VIEW current_cell_observations WITH (security_invoker = true) AS
SELECT
    series_id, batch_key, cell_id, time_start, time_end, time_precision, available_at, source_id,
    source_item_id, processing_version, product_status, dataset_id, dataset_version, mapping_version,
    quality_flag, variable, stat, value, std, unit, valid_fraction, pixel_count, source_resolution_m
FROM (
    SELECT
        o.*,
        b.added_in_version AS dataset_version,
        row_number() OVER (
            PARTITION BY o.cell_id, o.variable, o.source_id, (o.time_start AT TIME ZONE 'UTC')::date
            ORDER BY (o.product_status = 'final') DESC, o.processing_version DESC, o.mapping_version DESC,
                     o.valid_fraction DESC, o.available_at DESC
        ) AS current_rank
    FROM cell_observations o
    JOIN ingest_batches b USING (series_id, batch_key)
    WHERE b.superseded_in_version IS NULL
) ranked
WHERE current_rank = 1;

-- The current view as a pinned series version would see it, limited to values public at the cutoff.
CREATE FUNCTION current_cell_observations_at(
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
                PARTITION BY o.cell_id, o.variable, o.source_id, (o.time_start AT TIME ZONE 'UTC')::date
                ORDER BY (o.product_status = 'final') DESC, o.processing_version DESC, o.mapping_version DESC,
                         o.valid_fraction DESC, o.available_at DESC
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

CREATE VIEW rainfall_observations WITH (security_invoker = true) AS
SELECT
    c.dataset_id, c.dataset_version, c.source_item_id AS source_record_id, c.cell_id, g.geometry,
    c.time_start AS interval_start, c.time_end AS interval_end, c.value AS rainfall_mm,
    c.product_status, c.quality_flag, c.available_at, c.source_id
FROM current_cell_observations c
JOIN grid_cells g USING (cell_id)
WHERE c.variable = 'rainfall_mm';

CREATE VIEW vegetation_observations WITH (security_invoker = true) AS
SELECT
    c.dataset_id, c.dataset_version, c.source_item_id AS source_record_id, c.cell_id, g.geometry,
    c.time_start AS observed_at, c.time_end AS observed_until, c.variable AS index_name, c.value AS index_value,
    c.std AS index_std, c.valid_fraction, c.pixel_count, c.quality_flag, c.available_at, c.source_id
FROM current_cell_observations c
JOIN grid_cells g USING (cell_id)
WHERE c.variable IN ('ndvi', 'evi', 'mndwi', 'ndmi');

ALTER TABLE grid_cells ENABLE ROW LEVEL SECURITY;
ALTER TABLE series ENABLE ROW LEVEL SECURITY;
ALTER TABLE series_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingest_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE cell_observations ENABLE ROW LEVEL SECURITY;

CREATE POLICY grid_cells_read ON grid_cells FOR SELECT TO habitat_reader USING (true);
CREATE POLICY grid_cells_insert ON grid_cells FOR INSERT TO habitat_writer WITH CHECK (true);
CREATE POLICY series_read ON series FOR SELECT TO habitat_reader USING (true);
CREATE POLICY series_insert ON series FOR INSERT TO habitat_writer WITH CHECK (true);
CREATE POLICY series_update ON series FOR UPDATE TO habitat_writer USING (true) WITH CHECK (true);
CREATE POLICY series_versions_read ON series_versions FOR SELECT TO habitat_reader USING (true);
CREATE POLICY series_versions_insert ON series_versions FOR INSERT TO habitat_writer WITH CHECK (true);
CREATE POLICY ingest_batches_read ON ingest_batches FOR SELECT TO habitat_reader USING (true);
CREATE POLICY ingest_batches_insert ON ingest_batches FOR INSERT TO habitat_writer WITH CHECK (true);
CREATE POLICY ingest_batches_supersede ON ingest_batches FOR UPDATE TO habitat_writer USING (true) WITH CHECK (true);
CREATE POLICY cell_observations_read ON cell_observations FOR SELECT TO habitat_reader USING (true);
CREATE POLICY cell_observations_insert ON cell_observations FOR INSERT TO habitat_writer WITH CHECK (true);

GRANT SELECT ON grid_cells, series, series_versions, ingest_batches, cell_observations,
    current_cell_observations, rainfall_observations, vegetation_observations TO habitat_reader;
GRANT EXECUTE ON FUNCTION current_cell_observations_at(text, integer, timestamptz) TO habitat_reader;
GRANT INSERT ON grid_cells, series, series_versions, ingest_batches, cell_observations TO habitat_writer;
GRANT UPDATE (latest_version) ON series TO habitat_writer;
GRANT UPDATE (superseded_in_version) ON ingest_batches TO habitat_writer;

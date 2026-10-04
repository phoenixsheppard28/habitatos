-- One row is one thing that happened at one place and time: a sighting, a fire pixel, a dead animal.
-- See docs/ingestion/EVENTS.md.
CREATE TABLE point_events (
    series_id                 text             NOT NULL,
    batch_key                 text             NOT NULL,
    source_record_id          text             NOT NULL,
    dataset_id                text             NOT NULL,
    source_id                 text             NOT NULL,
    source_item_id            text             NOT NULL,
    processing_version        text             NOT NULL,
    product_status            text             NOT NULL,
    mapping_version           text             NOT NULL,
    event_type                text             NOT NULL,
    occurrence_status         text             NOT NULL CHECK (occurrence_status IN ('present', 'absent')),
    sampling_design           text             NOT NULL CHECK (sampling_design IN ('presence_only', 'systematic', 'effort_known')),
    taxon_name                text,
    gbif_taxon_key            bigint,
    time_start                timestamptz      NOT NULL,
    time_end                  timestamptz      NOT NULL CHECK (time_end >= time_start),
    time_precision            text             NOT NULL,
    available_at              timestamptz      NOT NULL,
    longitude                 double precision NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    latitude                  double precision NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    geometry                  geometry(Point, 4326) GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)) STORED,
    cell_id                   text             REFERENCES grid_cells,
    coordinate_uncertainty_m  double precision CHECK (coordinate_uncertainty_m >= 0),
    individual_count          integer          CHECK (individual_count >= 0),
    value                     double precision,
    unit                      text,
    basis                     text             NOT NULL,
    method                    text,
    origin_record_id          text,
    license                   text,
    quality_flag              text             NOT NULL,
    attributes                jsonb            NOT NULL DEFAULT '{}',
    CHECK (value IS NULL OR unit IS NOT NULL),
    PRIMARY KEY (series_id, batch_key, source_record_id),
    FOREIGN KEY (series_id, batch_key) REFERENCES ingest_batches (series_id, batch_key)
);

CREATE INDEX point_events_type_cell_time_idx ON point_events (event_type, cell_id, time_start);
CREATE INDEX point_events_taxon_time_idx ON point_events (gbif_taxon_key, time_start);
CREATE INDEX point_events_origin_idx ON point_events (origin_record_id);
CREATE INDEX point_events_geometry_idx ON point_events USING gist (geometry);

-- One row per source record. The GBIF processing_version is a checksum with no order,
-- so the latest batch wins after the product status.
CREATE VIEW current_point_events WITH (security_invoker = true) AS
SELECT
    series_id, batch_key, source_record_id, dataset_id, dataset_version, source_id, source_item_id,
    processing_version, product_status, mapping_version, event_type, occurrence_status, sampling_design,
    taxon_name, gbif_taxon_key, time_start, time_end, time_precision, available_at, longitude, latitude,
    geometry, cell_id, coordinate_uncertainty_m, individual_count, value, unit, basis, method,
    origin_record_id, license, quality_flag, attributes
FROM (
    SELECT
        e.*,
        b.added_in_version AS dataset_version,
        row_number() OVER (
            PARTITION BY e.source_id, e.source_record_id
            ORDER BY (e.product_status = 'final') DESC, b.added_in_version DESC, e.batch_key DESC
        ) AS current_rank
    FROM point_events e
    JOIN ingest_batches b USING (series_id, batch_key)
    WHERE b.superseded_in_version IS NULL
) ranked
WHERE current_rank = 1;

-- Recipe input, as recipe_animal_locations in migration 008.
CREATE VIEW recipe_point_events WITH (security_invoker = true) AS
SELECT
    dataset_id, dataset_version, access_scope, source_id, source_record_id, origin_record_id, event_type,
    occurrence_status, sampling_design, taxon_name AS species, gbif_taxon_key, time_start, time_end,
    time_precision, available_at, longitude, latitude, cell_id, coordinate_uncertainty_m, individual_count,
    value, unit, basis, license, quality_flag
FROM (
    SELECT
        d.dataset_id, d.version::text AS dataset_version, d.access_scope,
        e.source_id, e.source_record_id, e.origin_record_id, e.event_type, e.occurrence_status,
        e.sampling_design, e.taxon_name, e.gbif_taxon_key, e.time_start, e.time_end, e.time_precision,
        e.available_at, e.longitude, e.latitude, e.cell_id, e.coordinate_uncertainty_m, e.individual_count,
        e.value, e.unit, e.basis, e.license, e.quality_flag,
        row_number() OVER (
            PARTITION BY d.dataset_id, d.version, d.access_scope, e.source_id, e.source_record_id
            ORDER BY (e.product_status = 'final') DESC, b.added_in_version DESC, e.batch_key DESC
        ) AS current_rank
    FROM datasets d
    JOIN ingest_batches b
      ON b.series_id = d.dataset_id
     AND b.added_in_version <= d.version
     AND (b.superseded_in_version IS NULL OR b.superseded_in_version > d.version)
    JOIN point_events e ON e.series_id = b.series_id AND e.batch_key = b.batch_key
    WHERE d.status = 'ready' AND d.family = 'point_events'
) ranked
WHERE current_rank = 1;

ALTER TABLE point_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY point_events_read ON point_events FOR SELECT TO habitat_reader USING (true);
CREATE POLICY point_events_insert ON point_events FOR INSERT TO habitat_writer WITH CHECK (true);

GRANT SELECT ON point_events, current_point_events, recipe_point_events TO habitat_reader;
GRANT INSERT ON point_events TO habitat_writer;

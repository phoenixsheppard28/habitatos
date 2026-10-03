-- entity_id is namespaced by source and study, for example movebank:1095:Naboisho.
-- Equal local names in two studies are two animals.
CREATE TABLE animal_entities (
    entity_id         text   PRIMARY KEY,
    source_id         text   NOT NULL,
    study_id          text   NOT NULL,
    local_identifier  text   NOT NULL,
    taxon_name        text,
    gbif_taxon_key    bigint,
    sex               text,
    life_stage        text,
    deploy_on         timestamptz,
    deploy_off        timestamptz,
    study_site        text,
    attributes        jsonb  NOT NULL DEFAULT '{}'
);

CREATE TABLE animal_locations (
    series_id         text             NOT NULL,
    batch_key         text             NOT NULL,
    source_record_id  text             NOT NULL,
    dataset_id        text             NOT NULL,
    entity_id         text             NOT NULL REFERENCES animal_entities,
    tag_id            text,
    observed_at       timestamptz      NOT NULL,
    available_at      timestamptz      NOT NULL,
    longitude         double precision NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    latitude          double precision NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    geometry          geometry(Point, 4326) GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)) STORED,
    cell_id           text             REFERENCES grid_cells,
    sensor_type       text             NOT NULL,
    quality_flag      text             NOT NULL,
    mapping_version   text             NOT NULL,
    attributes        jsonb            NOT NULL DEFAULT '{}',
    PRIMARY KEY (series_id, batch_key, source_record_id),
    FOREIGN KEY (series_id, batch_key) REFERENCES ingest_batches (series_id, batch_key)
);

CREATE INDEX animal_locations_entity_time_idx ON animal_locations (entity_id, observed_at);
CREATE INDEX animal_locations_cell_time_idx ON animal_locations (cell_id, observed_at);
CREATE INDEX animal_locations_geometry_idx ON animal_locations USING gist (geometry);

-- A later batch of the same study replaces earlier fixes of the same animal and time.
CREATE VIEW current_animal_locations WITH (security_invoker = true) AS
SELECT
    series_id, batch_key, source_record_id, dataset_id, dataset_version, entity_id, tag_id, observed_at,
    available_at, longitude, latitude, geometry, cell_id, sensor_type, quality_flag, mapping_version, attributes
FROM (
    SELECT
        l.*,
        b.added_in_version AS dataset_version,
        row_number() OVER (
            PARTITION BY l.entity_id, l.observed_at, l.sensor_type
            ORDER BY b.added_in_version DESC
        ) AS current_rank
    FROM animal_locations l
    JOIN ingest_batches b USING (series_id, batch_key)
    WHERE b.superseded_in_version IS NULL
) ranked
WHERE current_rank = 1;

ALTER TABLE animal_entities ENABLE ROW LEVEL SECURITY;
ALTER TABLE animal_locations ENABLE ROW LEVEL SECURITY;

CREATE POLICY animal_entities_read ON animal_entities FOR SELECT TO habitat_reader USING (true);
CREATE POLICY animal_entities_insert ON animal_entities FOR INSERT TO habitat_writer WITH CHECK (true);
CREATE POLICY animal_entities_update ON animal_entities FOR UPDATE TO habitat_writer USING (true) WITH CHECK (true);
CREATE POLICY animal_locations_read ON animal_locations FOR SELECT TO habitat_reader USING (true);
CREATE POLICY animal_locations_insert ON animal_locations FOR INSERT TO habitat_writer WITH CHECK (true);

GRANT SELECT ON animal_entities, animal_locations, current_animal_locations TO habitat_reader;
GRANT INSERT, UPDATE ON animal_entities TO habitat_writer;
GRANT INSERT ON animal_locations TO habitat_writer;

CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA extensions;

CREATE TABLE datasets (
    dataset_id    text        NOT NULL,
    version       integer     NOT NULL,
    created_at    timestamptz NOT NULL,
    access_scope  text        NOT NULL,
    family        text        NOT NULL,
    source_id     text        NOT NULL,
    status        text        NOT NULL CHECK (status IN ('ready', 'quarantined')),
    description   text        NOT NULL,
    summary       text,
    footprint     geometry(MultiPolygon, 4326),
    time_range    tstzrange,
    variables     text[]      NOT NULL DEFAULT '{}',
    species_keys  bigint[]    NOT NULL DEFAULT '{}',
    descriptor    jsonb       NOT NULL,
    PRIMARY KEY (dataset_id, version)
);

CREATE INDEX datasets_footprint_idx ON datasets USING gist (footprint);
CREATE INDEX datasets_time_idx ON datasets USING gist (time_range);
CREATE INDEX datasets_variables_idx ON datasets USING gin (variables);
CREATE INDEX datasets_species_idx ON datasets USING gin (species_keys);

CREATE TABLE dataset_tags (
    dataset_id  text    NOT NULL,
    version     integer NOT NULL,
    key         text    NOT NULL,
    value       text    NOT NULL,
    origin      text    NOT NULL CHECK (origin IN ('deterministic', 'ai')),
    model       text,
    confidence  real,
    evidence    text,
    PRIMARY KEY (dataset_id, version, key, value, origin),
    FOREIGN KEY (dataset_id, version) REFERENCES datasets (dataset_id, version)
);

CREATE INDEX dataset_tags_lookup_idx ON dataset_tags (key, value);

CREATE VIEW latest_datasets WITH (security_invoker = true) AS
SELECT DISTINCT ON (dataset_id) *
FROM datasets
ORDER BY dataset_id, version DESC;

ALTER TABLE datasets ENABLE ROW LEVEL SECURITY;
ALTER TABLE dataset_tags ENABLE ROW LEVEL SECURITY;

CREATE POLICY datasets_read ON datasets FOR SELECT TO habitat_reader USING (true);
CREATE POLICY datasets_insert ON datasets FOR INSERT TO habitat_writer WITH CHECK (true);
CREATE POLICY dataset_tags_read ON dataset_tags FOR SELECT TO habitat_reader USING (true);
CREATE POLICY dataset_tags_insert ON dataset_tags FOR INSERT TO habitat_writer WITH CHECK (true);

GRANT SELECT ON datasets, dataset_tags, latest_datasets TO habitat_reader;
GRANT INSERT ON datasets, dataset_tags TO habitat_writer;

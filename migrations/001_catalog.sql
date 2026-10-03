CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE datasets (
    dataset_id        text        NOT NULL,
    version           integer     NOT NULL,
    created_at        timestamptz NOT NULL,
    access_scope      text        NOT NULL,
    family            text        NOT NULL,
    source_id         text        NOT NULL,
    status            text        NOT NULL CHECK (status IN ('ready', 'quarantined')),
    description       text        NOT NULL,
    summary           text,
    footprint         geometry(MultiPolygon, 4326),
    time_range        tstzrange,
    variables         text[]      NOT NULL DEFAULT '{}',
    species_keys      bigint[]    NOT NULL DEFAULT '{}',
    descriptor        jsonb       NOT NULL,
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

CREATE TABLE ingest_batches (
    batch_key           text        PRIMARY KEY,
    series_id           text        NOT NULL,
    source_item_id      text        NOT NULL,
    processing_version  text        NOT NULL,
    mapping_version     text        NOT NULL,
    files               text[]      NOT NULL,
    row_count           integer     NOT NULL,
    created_at          timestamptz NOT NULL
);

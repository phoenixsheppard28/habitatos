-- Manifests of the raw files in the archive. The files stay in the archive; this table holds no raw data.
CREATE TABLE raw_artifacts (
    artifact_id         text        NOT NULL,
    version             text        NOT NULL,
    source_id           text        NOT NULL,
    source_item_id      text        NOT NULL,
    source_key          text        NOT NULL,
    processing_version  text        NOT NULL,
    product_status      text        NOT NULL,
    checksum            text        NOT NULL,
    storage_uri         text        NOT NULL CHECK (storage_uri LIKE 'artifact://%'),
    access_scope        text        NOT NULL,
    retrieved_at        timestamptz NOT NULL,
    manifest            jsonb       NOT NULL,
    PRIMARY KEY (artifact_id, version)
);

CREATE UNIQUE INDEX raw_artifacts_source_key_idx ON raw_artifacts (source_key);
CREATE INDEX raw_artifacts_source_item_idx ON raw_artifacts (source_id, source_item_id);

ALTER TABLE raw_artifacts ENABLE ROW LEVEL SECURITY;

-- Account-scoped downloads, such as authenticated Movebank exports, are not visible to readers.
CREATE POLICY raw_artifacts_read ON raw_artifacts FOR SELECT TO habitat_reader USING (access_scope = 'public');
CREATE POLICY raw_artifacts_writer_read ON raw_artifacts FOR SELECT TO habitat_writer USING (true);
CREATE POLICY raw_artifacts_insert ON raw_artifacts FOR INSERT TO habitat_writer WITH CHECK (true);
-- A re-download after local corruption replaces the manifest for the same source key.
CREATE POLICY raw_artifacts_replace ON raw_artifacts FOR UPDATE TO habitat_writer USING (true) WITH CHECK (true);

GRANT SELECT ON raw_artifacts TO habitat_reader;
GRANT SELECT, INSERT, UPDATE ON raw_artifacts TO habitat_writer;

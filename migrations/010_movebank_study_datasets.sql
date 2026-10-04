-- Retain the original series for pinned historical recipes. Publish one new series per study.
CREATE TEMP TABLE movebank_study_batches AS
SELECT b.series_id AS original_id,
       b.series_id || '--study-' || (b.raw_manifest #>> '{extensions,properties,study_id}') AS study_series_id,
       b.batch_key
FROM ingest_batches b JOIN series s USING (series_id)
WHERE s.source_id IN ('movebank_repository', 'movebank_study')
  AND s.family = 'animal_locations'
  AND position('--study-' IN b.series_id) = 0
  AND b.raw_manifest #>> '{extensions,properties,study_id}' IS NOT NULL;

INSERT INTO series (series_id, family, source_id, product, latest_version, created_at)
SELECT DISTINCT m.study_series_id, s.family, s.source_id, s.product, s.latest_version, s.created_at
FROM movebank_study_batches m JOIN series s ON s.series_id = m.original_id;

INSERT INTO series_versions (series_id, version, parent_version, created_at)
SELECT DISTINCT m.study_series_id, v.version, v.parent_version, v.created_at
FROM movebank_study_batches m JOIN series_versions v ON v.series_id = m.original_id;

INSERT INTO ingest_batches (series_id, batch_key, source_item_id, processing_version, product_status,
                           mapping_version, row_count, raw_manifest, created_at, added_in_version,
                           superseded_in_version)
SELECT m.study_series_id, b.batch_key, b.source_item_id, b.processing_version, b.product_status,
       b.mapping_version, b.row_count, b.raw_manifest, b.created_at, b.added_in_version,
       b.superseded_in_version
FROM movebank_study_batches m
JOIN ingest_batches b ON b.series_id = m.original_id AND b.batch_key = m.batch_key;

INSERT INTO animal_locations (series_id, batch_key, source_record_id, dataset_id, entity_id, tag_id,
                              observed_at, available_at, longitude, latitude, cell_id, sensor_type,
                              quality_flag, mapping_version, attributes)
SELECT m.study_series_id, l.batch_key, l.source_record_id, m.study_series_id, l.entity_id, l.tag_id,
       l.observed_at, l.available_at, l.longitude, l.latitude, l.cell_id, l.sensor_type,
       l.quality_flag, l.mapping_version, l.attributes
FROM movebank_study_batches m
JOIN animal_locations l ON l.series_id = m.original_id AND l.batch_key = m.batch_key;

DO $$
DECLARE
    study record;
    original jsonb;
    descriptor jsonb;
    title text;
    first_fix timestamptz;
    last_fix timestamptz;
    records bigint;
    footprint geometry;
    taxa jsonb;
    species_names jsonb;
    species_keys bigint[];
    artifact_refs jsonb;
    tags jsonb;
BEGIN
    FOR study IN
        SELECT DISTINCT m.original_id, s.*
        FROM movebank_study_batches m JOIN series s ON s.series_id = m.study_series_id
    LOOP
        SELECT d.descriptor INTO original
        FROM datasets d WHERE d.dataset_id = study.original_id ORDER BY d.version DESC LIMIT 1;
        IF original IS NULL THEN
            CONTINUE;
        END IF;

        SELECT min(l.observed_at), max(l.observed_at), count(*)
        INTO first_fix, last_fix, records
        FROM animal_locations l JOIN ingest_batches b USING (series_id, batch_key)
        WHERE l.series_id = study.series_id AND b.superseded_in_version IS NULL;

        SELECT ST_Multi(ST_UnaryUnion(ST_Collect(g.geometry))) INTO footprint
        FROM grid_cells g WHERE g.cell_id IN (
            SELECT l.cell_id FROM animal_locations l JOIN ingest_batches b USING (series_id, batch_key)
            WHERE l.series_id = study.series_id AND b.superseded_in_version IS NULL
        );

        SELECT coalesce(jsonb_agg(jsonb_build_object('gbif_key', gbif_taxon_key, 'name', taxon_name)), '[]'),
               coalesce(jsonb_agg(taxon_name), '[]'), coalesce(array_agg(gbif_taxon_key), '{}')
        INTO taxa, species_names, species_keys
        FROM (
            SELECT DISTINCT e.gbif_taxon_key, e.taxon_name
            FROM animal_locations l JOIN animal_entities e USING (entity_id)
            JOIN ingest_batches b USING (series_id, batch_key)
            WHERE l.series_id = study.series_id AND b.superseded_in_version IS NULL
              AND e.gbif_taxon_key IS NOT NULL
            ORDER BY e.gbif_taxon_key
        ) names;

        SELECT coalesce(jsonb_agg(source_item_id ORDER BY added_in_version, source_item_id), '[]')
        INTO artifact_refs FROM ingest_batches
        WHERE series_id = study.series_id AND superseded_in_version IS NULL;

        SELECT regexp_replace(raw_manifest #>> '{extensions,properties,title}', '^Data from: ', '')
        INTO title FROM ingest_batches WHERE series_id = study.series_id
        ORDER BY added_in_version DESC LIMIT 1;
        title := coalesce(nullif(title, ''), original->>'description');

        tags := '[{"key":"sensor_type","value":"gps","origin":"deterministic"},
                  {"key":"temporal_resolution","value":"sub_daily","origin":"deterministic"},
                  {"key":"study_design","value":"gps_collar","origin":"deterministic"}]'::jsonb;
        SELECT tags || coalesce(jsonb_agg(jsonb_build_object(
            'key', 'year', 'value', year::text, 'origin', 'deterministic')), '[]')
        INTO tags FROM generate_series(extract(year FROM first_fix)::int,
                                      extract(year FROM last_fix)::int) year;

        descriptor := original || jsonb_build_object(
            'dataset_id', study.series_id, 'version', study.latest_version, 'created_at', now(),
            'description', title, 'summary', NULL, 'row_count', records,
            'raw_artifact_refs', artifact_refs, 'species', taxa, 'tags', tags,
            'footprint_wkt', ST_AsText(footprint),
            'coverage', jsonb_build_object('start', first_fix, 'end', last_fix, 'species', species_names,
                'bbox', CASE WHEN footprint IS NOT NULL THEN jsonb_build_array(
                    ST_XMin(footprint::box3d), ST_YMin(footprint::box3d),
                    ST_XMax(footprint::box3d), ST_YMax(footprint::box3d)) END),
            'storage', jsonb_build_object('format', 'postgres', 'uri',
                'postgres://animal_locations?series_id=' || study.series_id || '&version=' || study.latest_version)
        );

        INSERT INTO datasets (dataset_id, version, created_at, access_scope, family, source_id, status,
                              description, summary, footprint, time_range, variables, species_keys, descriptor)
        VALUES (study.series_id, study.latest_version, now(), original->>'access_scope', study.family,
                study.source_id, original->>'status', title, NULL, footprint,
                tstzrange(first_fix, last_fix, '[]'), '{}', species_keys, descriptor);

        INSERT INTO dataset_tags (dataset_id, version, key, value, origin)
        SELECT study.series_id, study.latest_version, tag->>'key', tag->>'value', tag->>'origin'
        FROM jsonb_array_elements(tags) tag;
    END LOOP;
END $$;

DROP TABLE movebank_study_batches;

CREATE OR REPLACE VIEW latest_datasets WITH (security_invoker = true) AS
SELECT DISTINCT ON (d.dataset_id) d.*
FROM datasets d
WHERE NOT (
    d.source_id IN ('movebank_repository', 'movebank_study')
    AND EXISTS (SELECT 1 FROM series s WHERE starts_with(s.series_id, d.dataset_id || '--study-'))
)
ORDER BY d.dataset_id, d.version DESC;

CREATE OR REPLACE VIEW current_animal_locations WITH (security_invoker = true) AS
SELECT
    series_id, batch_key, source_record_id, dataset_id, dataset_version, entity_id, tag_id, observed_at,
    available_at, longitude, latitude, geometry, cell_id, sensor_type, quality_flag, mapping_version, attributes
FROM (
    SELECT l.*, b.added_in_version AS dataset_version,
           row_number() OVER (
               PARTITION BY l.entity_id, l.observed_at, l.sensor_type
               ORDER BY b.added_in_version DESC, l.source_record_id DESC
           ) AS current_rank
    FROM animal_locations l JOIN ingest_batches b USING (series_id, batch_key)
    WHERE b.superseded_in_version IS NULL
      AND NOT EXISTS (SELECT 1 FROM series s WHERE starts_with(s.series_id, l.series_id || '--study-'))
) ranked
WHERE current_rank = 1;

-- Counts and estimates of animals per area and interval (docs/ingestion/POPULATION.md).
-- area_id is namespaced by source, for example ke_county:2 or literature:nairobi_np.
CREATE TABLE count_areas (
    area_id          text        PRIMARY KEY,
    source_id        text        NOT NULL,
    area_name        text        NOT NULL,
    area_type        text        NOT NULL CHECK (area_type IN
                         ('survey_block', 'park', 'admin_unit', 'ecosystem', 'site', 'region')),
    area_km2         double precision,
    geometry         geometry(Geometry, 4326),
    geometry_source  text,
    valid_from       timestamptz,
    valid_to         timestamptz,
    attributes       jsonb       NOT NULL DEFAULT '{}'
);

CREATE INDEX count_areas_geometry_idx ON count_areas USING gist (geometry);

-- The 1 km cells of each area. overlap_fraction is the share of the cell inside the area; a point area has
-- the one cell that contains it. cell_id has no foreign key to grid_cells: a county has up to 76,000 cells,
-- and a join to cell_observations needs only the id.
CREATE TABLE count_area_cells (
    area_id           text             NOT NULL REFERENCES count_areas ON DELETE CASCADE,
    cell_id           text             NOT NULL,
    overlap_fraction  double precision NOT NULL CHECK (overlap_fraction > 0 AND overlap_fraction <= 1),
    PRIMARY KEY (area_id, cell_id)
);

CREATE INDEX count_area_cells_cell_idx ON count_area_cells (cell_id);

CREATE TABLE population_counts (
    series_id             text             NOT NULL,
    batch_key             text             NOT NULL,
    source_record_id      text             NOT NULL,
    dataset_id            text             NOT NULL,
    source_id             text             NOT NULL,
    source_item_id        text             NOT NULL,
    processing_version    text             NOT NULL,
    mapping_version       text             NOT NULL,
    area_id               text             NOT NULL REFERENCES count_areas,
    taxon_name            text             NOT NULL,
    gbif_taxon_key        bigint,
    time_start            timestamptz      NOT NULL,
    time_end              timestamptz      NOT NULL,
    time_precision        text             NOT NULL,
    available_at          timestamptz      NOT NULL,
    metric                text             NOT NULL,
    method                text             NOT NULL,
    value                 double precision CHECK (value >= 0),
    unit                  text             NOT NULL,
    se                    double precision,
    ci_low                double precision,
    ci_high               double precision,
    ci_level              double precision,
    effort_value          double precision,
    effort_unit           text,
    comparability_group   text             NOT NULL,
    quality_flag          text             NOT NULL,
    attributes            jsonb            NOT NULL DEFAULT '{}',
    PRIMARY KEY (series_id, batch_key, source_record_id),
    FOREIGN KEY (series_id, batch_key) REFERENCES ingest_batches (series_id, batch_key),
    CHECK (time_start <= time_end),
    CHECK (ci_low IS NULL OR ci_high IS NULL OR ci_low <= ci_high)
);

CREATE INDEX population_counts_taxon_time_idx ON population_counts (gbif_taxon_key, time_start);
CREATE INDEX population_counts_area_idx ON population_counts (area_id, time_start);
CREATE INDEX population_counts_group_idx ON population_counts (comparability_group, time_start);

-- One row per area, taxon, metric, method, source and interval is current in a catalog version.
-- Rows of two sources never replace each other; Recipe chooses by comparability_group.
CREATE VIEW recipe_population_counts WITH (security_invoker = true) AS
SELECT
    dataset_id, dataset_version, access_scope, source_record_id, area_id, area_name, area_type, area_km2,
    taxon_name, gbif_taxon_key, time_start, time_end, time_precision, available_at, metric, method, value, unit,
    se, ci_low, ci_high, ci_level, effort_value, effort_unit, comparability_group, quality_flag
FROM (
    SELECT
        d.dataset_id, d.version::text AS dataset_version, d.access_scope,
        p.source_record_id, p.area_id, a.area_name, a.area_type, a.area_km2, p.taxon_name, p.gbif_taxon_key,
        p.time_start, p.time_end, p.time_precision, p.available_at, p.metric, p.method, p.value, p.unit, p.se,
        p.ci_low, p.ci_high, p.ci_level, p.effort_value, p.effort_unit, p.comparability_group, p.quality_flag,
        row_number() OVER (
            PARTITION BY d.dataset_id, d.version, d.access_scope, p.area_id,
                         coalesce(p.gbif_taxon_key::text, p.taxon_name), p.metric, p.method,
                         p.source_id, p.time_start, p.time_end
            ORDER BY b.added_in_version DESC, p.processing_version DESC, p.mapping_version DESC,
                     p.available_at DESC, p.source_record_id DESC
        ) AS current_rank
    FROM datasets d
    JOIN ingest_batches b
      ON b.series_id = d.dataset_id
     AND b.added_in_version <= d.version
     AND (b.superseded_in_version IS NULL OR b.superseded_in_version > d.version)
    JOIN population_counts p ON p.series_id = b.series_id AND p.batch_key = b.batch_key
    JOIN count_areas a ON a.area_id = p.area_id
    WHERE d.status = 'ready' AND d.family = 'population_counts'
) ranked
WHERE current_rank = 1;

ALTER TABLE count_areas ENABLE ROW LEVEL SECURITY;
ALTER TABLE count_area_cells ENABLE ROW LEVEL SECURITY;
ALTER TABLE population_counts ENABLE ROW LEVEL SECURITY;

CREATE POLICY count_areas_read ON count_areas FOR SELECT TO habitat_reader USING (true);
CREATE POLICY count_areas_insert ON count_areas FOR INSERT TO habitat_writer WITH CHECK (true);
CREATE POLICY count_areas_update ON count_areas FOR UPDATE TO habitat_writer USING (true) WITH CHECK (true);
CREATE POLICY count_area_cells_read ON count_area_cells FOR SELECT TO habitat_reader USING (true);
CREATE POLICY count_area_cells_insert ON count_area_cells FOR INSERT TO habitat_writer WITH CHECK (true);
CREATE POLICY count_area_cells_delete ON count_area_cells FOR DELETE TO habitat_writer USING (true);
CREATE POLICY population_counts_read ON population_counts FOR SELECT TO habitat_reader USING (true);
CREATE POLICY population_counts_insert ON population_counts FOR INSERT TO habitat_writer WITH CHECK (true);

GRANT SELECT ON count_areas, count_area_cells, population_counts, recipe_population_counts TO habitat_reader;
GRANT INSERT, UPDATE ON count_areas TO habitat_writer;
GRANT INSERT, DELETE ON count_area_cells TO habitat_writer;
GRANT INSERT ON population_counts TO habitat_writer;

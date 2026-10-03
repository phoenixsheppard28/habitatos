-- Joins from animal fixes to cells read every variable of one cell, so the cell leads this index.
CREATE INDEX cell_observations_cell_time_idx ON cell_observations (cell_id, time_start);
CREATE INDEX ingest_batches_version_idx ON ingest_batches (series_id, added_in_version);

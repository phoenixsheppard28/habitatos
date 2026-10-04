-- Daily movement for the Analysis lane: one row per animal and UTC day of a pinned catalog version.
-- The row keeps the last good fix of the day. daily_displacement_km is the geodesic distance from the
-- last good fix of the previous UTC day. It is NULL when the animal has no good fix on the previous day;
-- a gap is never filled with zero. Recipe has no operation for path distance, so Stage 2 derives it here.
-- The Recipe dataset id is the catalog dataset id with the suffix '--daily-movement'.

CREATE VIEW recipe_animal_daily_movement WITH (security_invoker = true) AS
WITH good_fixes AS (
    SELECT
        dataset_id, dataset_version, access_scope, entity_id, species, source_record_id, observed_at,
        available_at, longitude, latitude, cell_id,
        (observed_at AT TIME ZONE 'UTC')::date AS utc_day
    FROM recipe_animal_locations
    WHERE quality_flag = 'ok'
),
last_fix AS (
    SELECT
        *,
        count(*) OVER same_day AS fix_count,
        row_number() OVER (same_day ORDER BY observed_at DESC, source_record_id DESC) AS last_rank
    FROM good_fixes
    WINDOW same_day AS (PARTITION BY dataset_id, dataset_version, access_scope, entity_id, utc_day)
),
daily AS (
    SELECT
        *,
        lag(utc_day) OVER track AS previous_day,
        lag(longitude) OVER track AS previous_longitude,
        lag(latitude) OVER track AS previous_latitude,
        lag(available_at) OVER track AS previous_available_at
    FROM last_fix
    WHERE last_rank = 1
    WINDOW track AS (PARTITION BY dataset_id, dataset_version, access_scope, entity_id ORDER BY utc_day)
)
SELECT
    dataset_id || '--daily-movement' AS dataset_id, dataset_version, access_scope,
    dataset_id AS source_dataset_id, source_record_id, entity_id, species,
    utc_day::timestamp AT TIME ZONE 'UTC' AS day, observed_at AS last_fix_at, fix_count,
    longitude, latitude, cell_id,
    CASE WHEN previous_day = utc_day - 1 THEN
        ST_Distance(
            ST_SetSRID(ST_MakePoint(previous_longitude, previous_latitude), 4326)::geography,
            ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography
        ) / 1000.0
    END AS daily_displacement_km,
    CASE WHEN previous_day = utc_day - 1 THEN greatest(available_at, previous_available_at)
         ELSE available_at
    END AS available_at
FROM daily;

GRANT SELECT ON recipe_animal_daily_movement TO habitat_reader;

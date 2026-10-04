# Point events

Status: proposal.

This document defines the `point_events` family and the first sources for the family.
All shapes obey the shared rules in [README.md](README.md).

## 1. Purpose

A point event is one thing that happened at one place and time.
Examples are a species sighting, a camera-trap detection, an active fire, a disease outbreak and a dead animal.

Recipe uses events as labels and as covariates.
A sighting can confirm presence where no GPS collar exists.
A fire changes the forage near a migration route.
An outbreak or a conflict incident can explain a change in movement.

## 2. Current data and the gap

| Current family | What it gives | What it cannot give |
| --- | --- | --- |
| `cell_observations` | NDVI, EVI, MNDWI, NDMI and rainfall per 1 km cell | Discrete events. A fire or a sighting is not a cell average. |
| `animal_locations` | GPS fixes of collared animals | Animals without a collar. Other species. Events that are not movement. |

The gap has three parts:

- Presence records of species without collars, from many observers.
- Fire detections with a known time and a known energy.
- Rare events: disease outbreaks, mortality, conflict incidents.

GBIF has few wildebeest records for the focus case.
On 2026-10-03, the GBIF search gave 137 records of *Connochaetes taurinus* in Kenya for 2010–2013.
Only 2 of these records are in the Athi-Kaputiei box `36.7,-1.6,37.2,-1.25`.
The same box has 17,861 records of all taxa for 2010–2013, and 88 % of the records come from eBird.
Thus GBIF adds context about other species and observer effort. GBIF does not replace the GPS data.

## 3. Sources

| `source_id` | Provider | `event_type` | Space | Time | Latency | Access and auth | Format | License | Priority |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `gbif_occurrence` | GBIF occurrence API (includes iNaturalist, eBird, Observation.org) | `species_occurrence`, `wildlife_mortality` | Global | All years | Days to weeks after the publisher (not verified) | Search: none. Download: GBIF account | JSON pages. Download: zip with SIMPLE_CSV or DWCA | Per record: CC0 1.0, CC-BY 4.0 or CC-BY-NC 4.0 | P1 |
| `firms_modis` | NASA LANCE FIRMS, MODIS Terra and Aqua | `active_fire` | Global, 1 km pixels | Terra from 2000-11, Aqua from 2002-07 | Standard product: 2–3 months | Area API: free MAP_KEY. Country files: none | CSV | NASA open data, acknowledgment requested | P1 |
| `firms_viirs` | NASA LANCE FIRMS, VIIRS S-NPP | `active_fire` | Global, 375 m pixels | From 2012-01-20 | Standard product (lag not verified) | As `firms_modis` | CSV | As `firms_modis` | P1 |
| `lila_snapshot_serengeti` | LILA BC, Snapshot Serengeti | `camera_trap_detection` | Serengeti NP, Tanzania | Seasons 1–11, from 2010 (exact dates not verified) | Archive | None | COCO Camera Traps JSON in a zip | CDLA-Permissive | P2 |
| `firms_nrt` | NASA LANCE FIRMS NRT | `active_fire` | Global | Recent days only (window not verified) | 3 hours, best effort | MAP_KEY | CSV | As `firms_modis` | P3 |
| `inaturalist` | iNaturalist API v1 | `species_occurrence` | Global | All years | Minutes | None for read | JSON | Per observation, some "all rights reserved" | P3 |
| `lila_camera_traps` | LILA BC: Snapshot Safari 2024 Expansion, Biome Health Maasai Mara 2018, Great Zebra and Giraffe Count (Nairobi NP) | `camera_trap_detection` | East Africa | Varies | Archive | None | COCO Camera Traps JSON | Per dataset (not verified) | P3 |
| `wahis` | WOAH WAHIS | `disease_outbreak` | Global, reported by countries | From 2005 (not verified) | Weeks (not verified) | No documented public API | JSON from the web interface | Terms not verified | P3 |
| `wildlife_insights` | Wildlife Insights | `camera_trap_detection` | Global | Varies | Embargo up to 48 months | Account for download | CSV package | Metadata CC0 or CC-BY | P3 |
| `kws_hwc` | Kenya Wildlife Service | `human_wildlife_conflict` | Kenya, 154 stations | From the 1990s | Unknown | By request only | Unknown | Agreement | P3 |
| none | ACLED | none | — | — | — | Institutional account, OAuth | — | Restrictive EULA | Dropped |
| none | MODIS MCD64A1 burned area | — | — | — | — | — | Raster | — | See [HABITAT_DEGRADATION.md](HABITAT_DEGRADATION.md) |

Notes:

- Read eBird and iNaturalist through GBIF. The GBIF dataset keys are `4fa7b334-ce0d-4e88-aaae-2e0c138d049e` (eBird EOD, CC-BY 4.0) and `50c9509d-22c7-4a22-a47d-8c48425ef4a7` (iNaturalist research-grade, CC-BY-NC 4.0 at dataset level).
- The direct eBird Basic Dataset needs a request and has its own terms (not verified). Do not use the direct eBird Basic Dataset.
- Global Roadkill Data is on GBIF under CC-BY-NC 4.0. Africa is 0.3 % of its records. Read Global Roadkill Data through `gbif_occurrence` with the dataset key, and map the records to `wildlife_mortality`.
- ACLED gives conflict events, not wildlife events. The ACLED EULA forbids access to raw data by other users and forbids use for machine learning. Thus the agent handoff and Recipe cannot use ACLED data.
- WAHIS has an undocumented endpoint `POST https://wahis.woah.org/api/v1/pi/event/filtered-list`. The endpoint returned HTTP 200 on 2026-10-03. WOAH announces a public API for a later date. Do not build on the undocumented endpoint.
- Kenya has no open human-wildlife conflict feed. KWS data needs an agreement. A KWS source gets `access_scope = "kws-agreement"`.

## 4. P1 and P2 sources

### gbif_occurrence

Two modes. The connector selects the mode from the record count.

1. Search mode, for at most `max_records` records (default 1,000, maximum 10,000).
   - `GET https://api.gbif.org/v1/occurrence/search`
   - Parameters: `geometry` (WKT polygon of the bbox), `eventDate=YYYY-MM-DD,YYYY-MM-DD`, `hasCoordinate=true`, optional `taxonKey`, optional `datasetKey`, `limit=300`, `offset`.
   - The maximum `limit` is 300. The service rejects `offset + limit > 100000`. Both limits were verified.
   - First send `limit=0` to get `count`. Use download mode when `count > max_records`.
2. Download mode, for larger requests.
   - `POST https://api.gbif.org/v1/occurrence/download/request` with HTTP basic auth (`GBIF_USERNAME`, `GBIF_PASSWORD`).
   - The body has `format` and a `predicate`. Use the same filters as search mode: `within` the polygon, `EVENT_DATE` range, `HAS_COORDINATE = true`, optional `TAXON_KEY`.
   - Poll `GET https://api.gbif.org/v1/occurrence/download/{key}` until `status` is `SUCCEEDED`. Return fetch status `pending` while the job runs.
   - Get the file from `downloadLink`. The status gives `doi`, `license`, `totalRecords`, `size` and `eraseAfter`.
   - GBIF runs at most 3 downloads per user at a time, and 1 when the queue is long.
   - Use `SIMPLE_CSV`. Confirm that SIMPLE_CSV has `modified` and `issue` (not verified). Else use `DWCA`. `SIMPLE_PARQUET` is undocumented. Do not use `SIMPLE_PARQUET`.

The archive keeps the JSON pages (search mode) or the zip as downloaded (download mode).

| Field | Value |
| --- | --- |
| `source_item_id` | `gbif-download:<downloadKey>` or `gbif-search:<sha256 of the canonical query>` |
| `processing_version` | The download key, or `sha256:<checksum of the archived pages>` |
| `source_key` | `gbif_occurrence:<source_item_id>:<processing_version>` |
| `product_status` | `final` |
| `source_record_id` | `gbifID` |
| `time_precision` | `instant` for a date and time with an offset. `day` for a date. `composite` for a year, a month or an interval |
| `available_at` | Record `modified`. When `modified` is missing or before the end of the event, use the dataset `pubDate` and flag `available_at_from_dataset` |
| `Rights.license` | The download `license` (the most restrictive record license), or the most restrictive license of the pages |
| `Rights.attribution` | `GBIF.org (<date>) GBIF Occurrence Download https://doi.org/<doi>` |

Do not use `lastInterpreted` for `available_at`. GBIF interprets all records again at intervals, so `lastInterpreted` is late and changes often.

Search mode has no DOI. GBIF offers a derived dataset DOI for search results. Register one before publication (process not verified).

Field mapping:

| GBIF field | Column |
| --- | --- |
| `decimalLongitude`, `decimalLatitude` | `longitude`, `latitude` |
| `coordinateUncertaintyInMeters` | `coordinate_uncertainty_m` |
| `eventDate`, `year`, `month`, `day` | `time_start`, `time_end`, `time_precision` |
| `acceptedTaxonKey`, `acceptedScientificName` | `gbif_taxon_key`, `taxon_name` |
| `individualCount` | `individual_count` |
| `occurrenceStatus` | `occurrence_status` (`PRESENT` to `present`, `ABSENT` to `absent`) |
| `basisOfRecord` | `basis` |
| `samplingProtocol` | `method` |
| `license` | `license` (SPDX id) |
| `occurrenceID` | `origin_record_id` (see section 5) |
| `datasetKey`, `issues`, `isInCluster`, `recordedBy`, `verbatimEventDate` | `attributes` |

An `eventDate` without an offset is local time. Do not guess the zone. Use precision `day` and the local date. Keep the verbatim time in `attributes`.

Dedup: the same `gbifID` in two batches is one event. The current-row rule keeps one row. `isInCluster = true` marks a possible duplicate in another dataset. Keep the flag in `attributes`. Do not merge clusters automatically.

Limits: GBIF data has a bias to roads, towns, parks and popular species. Section 5 shows how to flag the bias.

### firms_modis and firms_viirs

Two request paths give the same CSV columns.

1. Area API, any bbox.
   - `GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/{SOURCE}/{west,south,east,north}/{DAY_RANGE}/{DATE}`
   - `SOURCE` is `MODIS_SP` or `VIIRS_SNPP_SP`. `VIIRS_NOAA20_SP` covers dates from 2020.
   - `DAY_RANGE` is 1 to 5. A year needs 73 requests.
   - The MAP_KEY limit is 5,000 transactions in 10 minutes. Read `FIRMS_MAP_KEY` from the environment.
   - An invalid key gives the text `Invalid MAP_KEY.` with no CSV. Treat this text as an error.
2. Yearly country files, no key.
   - `https://firms.modaps.eosdis.nasa.gov/data/country/modis/{year}/modis_{year}_{Country}.csv`
   - `https://firms.modaps.eosdis.nasa.gov/data/country/viirs-snpp/{year}/viirs-snpp_{year}_{Country}.csv`
   - The 2012 MODIS file for Kenya is 475,659 bytes. The response has a `Last-Modified` header.
   - The current year has no file. The 2025 file did not exist on 2026-10-03.

The archive keeps each CSV as downloaded. The normalizer clips the rows to the request bbox.

| Field | Value |
| --- | --- |
| `source_item_id` | `firms:<SOURCE>:<bbox>:<first day>` (area API) or `firms:<instrument>:<Country>:<year>` (country file) |
| `processing_version` | The `version` column, for example `6.2` (MODIS) or `2` (VIIRS) |
| `source_key` | `<source_item_id>:<processing_version>` |
| `product_status` | `final` for SP files. NRT data goes to the separate source `firms_nrt` |
| `source_record_id` | `<satellite>:<acq_date>T<acq_time>Z:<latitude>:<longitude>`, with the source text unchanged |
| `time_precision` | `instant`. `acq_date` and `acq_time` are UTC. `time_end = time_start` |
| `available_at` | `Last-Modified` of the file. The area API gives no date (not verified); use the `Last-Modified` of the country file for the same year |

Field mapping:

| FIRMS column | Column |
| --- | --- |
| `latitude`, `longitude` | `latitude`, `longitude` (pixel center) |
| `scan`, `track` (km) | `coordinate_uncertainty_m = 500 * sqrt(scan² + track²)`, the half diagonal of the pixel |
| `frp` | `value`, `unit = "MW"` |
| `instrument`, `version` | `method`, for example `MODIS 6.2` |
| `satellite`, `confidence`, `daynight`, `type`, `brightness` or `bright_ti4`, `bright_t31` or `bright_ti5` | `attributes` |

`basis` is `satellite_detection`. `sampling_design` is `systematic`.

MODIS `confidence` is 0–100. VIIRS `confidence` is `l`, `n` or `h`.
Flag `low_confidence` for MODIS below 30 and for VIIRS `l` (MODIS threshold not verified).
Flag `non_vegetation_fire` when `type` is not 0. Type 0 is a presumed vegetation fire (code list not verified).

Dedup: overlapping area requests give the same `source_record_id`. The current-row rule keeps one row.
Terra, Aqua and S-NPP can see the same fire. Keep each detection. A detection is one satellite overpass, not one fire.

Limits:

- Clouds hide fires. FIRMS gives no cloud mask, so a cell without a detection is not a cell without fire.
- The FIRMS country file of a year can change when NASA reprocesses the archive. The country files for 2012 have dates from 2024 and 2025.

### lila_snapshot_serengeti

- Metadata: `https://storage.googleapis.com/public-datasets-lila/snapshotserengeti-v-2-0/SnapshotSerengeti_S1-11_v2_1.json.zip` (188,504,736 bytes). LILA also gives one JSON file per season, and copies on AWS and Azure.
- Do not download the images. Season 1 alone is 242 GB.
- Format: COCO Camera Traps. `images` have `id`, `datetime`, `seq_id`, `location` and `frame_num`. `annotations` have `image_id`, `category_id` and an optional `count`. Category 0 is `empty`.
- LILA states that the labels are reliable at the sequence level only. One event is one sequence and one category.
- `location` is a camera id with no coordinates. The camera coordinates must come from a second file (source not verified). Without the camera coordinates, the normalizer raises `QuarantineError`.
- Wildebeest is one of the most common labels.
- License: CDLA-Permissive. Cite Swanson et al. 2015, Scientific Data 2: 150026.

| Field | Value |
| --- | --- |
| `source_item_id` | `lila:snapshot-serengeti:<season or all>` |
| `processing_version` | The `info.version` of the JSON file, for example `2.1` |
| `source_record_id` | `<seq_id>:<category_id>` |
| `time_precision` | `day`. The `datetime` is camera clock time with no zone |
| `available_at` | The `Last-Modified` header of the file (2023-06-29 for the combined file) |
| `basis`, `method` | `MACHINE_OBSERVATION`, `camera_trap_sequence` |
| `sampling_design` | `effort_known`. The empty images show when a camera was active |

## 5. Shape

### SQL table

Add a migration `0NN_point_events.sql` with the next free number. The style matches migrations 003 and 004.

```sql
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
```

Add `current_point_events`, row level security, policies and grants as migration 004 does for `animal_locations`.

### PyArrow schema

Add `POINT_EVENTS_SCHEMA` to `src/habitat/contracts.py` and `POINT_EVENTS = "point_events"` to `src/habitat/normalize/rows.py`.

```python
POINT_EVENTS_SCHEMA = pa.schema(
    [
        pa.field("source_record_id", pa.string(), nullable=False),
        pa.field("dataset_id", pa.string(), nullable=False),
        pa.field("source_id", pa.string(), nullable=False),
        pa.field("source_item_id", pa.string(), nullable=False),
        pa.field("processing_version", pa.string(), nullable=False),
        pa.field("product_status", pa.string(), nullable=False),
        pa.field("mapping_version", pa.string(), nullable=False),
        pa.field("event_type", pa.string(), nullable=False),
        pa.field("occurrence_status", pa.string(), nullable=False),
        pa.field("sampling_design", pa.string(), nullable=False),
        pa.field("taxon_name", pa.string(), nullable=True),
        pa.field("gbif_taxon_key", pa.int64(), nullable=True),
        pa.field("time_start", UTC_TIMESTAMP, nullable=False),
        pa.field("time_end", UTC_TIMESTAMP, nullable=False),
        pa.field("time_precision", pa.string(), nullable=False),
        pa.field("available_at", UTC_TIMESTAMP, nullable=False),
        pa.field("longitude", pa.float64(), nullable=False),
        pa.field("latitude", pa.float64(), nullable=False),
        pa.field("cell_id", pa.string(), nullable=False),
        pa.field("coordinate_uncertainty_m", pa.float64(), nullable=True),
        pa.field("individual_count", pa.int32(), nullable=True),
        pa.field("value", pa.float64(), nullable=True),
        pa.field("unit", pa.string(), nullable=True),
        pa.field("basis", pa.string(), nullable=False),
        pa.field("method", pa.string(), nullable=True),
        pa.field("origin_record_id", pa.string(), nullable=True),
        pa.field("license", pa.string(), nullable=True),
        pa.field("quality_flag", pa.string(), nullable=False),
        pa.field("attributes", pa.string(), nullable=False),
    ]
)
```

### event_type vocabulary

| `event_type` | One row is | First sources |
| --- | --- | --- |
| `species_occurrence` | One record of a taxon at a place and time | `gbif_occurrence` |
| `camera_trap_detection` | One taxon in one camera sequence | `lila_snapshot_serengeti` |
| `active_fire` | One fire pixel in one satellite overpass | `firms_modis`, `firms_viirs` |
| `wildlife_mortality` | One dead animal: roadkill, poaching or disease | `gbif_occurrence` with a mortality dataset |
| `disease_outbreak` | One reported outbreak at one place | `wahis` (P3) |
| `human_wildlife_conflict` | One incident: crop damage, livestock loss, injury | `kws_hwc` (P3) |

Keep the vocabulary in one constant, `EVENT_TYPES`. A new value needs a change to this document and to the constant. A normalizer that maps to an unknown value is a bug, so the tests reject it.

### sampling_design and presence-only bias

`sampling_design` tells Recipe what a missing row means.

| Value | Meaning | Sources |
| --- | --- | --- |
| `presence_only` | No row does not mean "absent". Effort is unknown and uneven. | `gbif_occurrence` (except datasets with a known protocol), `inaturalist` |
| `systematic` | The sensor sees the full area at each pass. No row means "no detection". | FIRMS |
| `effort_known` | The source records when and where observers looked. | Camera traps with active days |

GBIF records have a strong sampling bias. The count of records in a cell shows observer effort as much as animal density.
Flag the bias with two derived variables per cell and month:

- `occurrence_count`: records of the requested taxon.
- `occurrence_effort_count`: records of all taxa with the same `basis`. This is a target-group background for Recipe.

A GBIF record with `occurrenceStatus = ABSENT` is a real absence. Keep the record with `occurrence_status = absent`. Do not flag the record.

### Current-row rule and cross-source dedup

`current_point_events` and `recipe_point_events` keep one row per `(source_id, source_record_id)`.
The order is: final before preliminary, then the newest `processing_version`, then the newest `mapping_version`, then the latest batch.

The same observation can come from two sources. For example, an iNaturalist observation comes through GBIF and through the iNaturalist API.
`origin_record_id` gives one key for both rows:

| Origin | `origin_record_id` |
| --- | --- |
| `occurrenceID` like `https://www.inaturalist.org/observations/<id>` | `inaturalist:<id>` |
| `occurrenceID` like `https://observation.org/observation/<id>` | `observation_org:<id>` |
| iNaturalist API | `inaturalist:<id>` |
| Other GBIF records | `gbif:<datasetKey>:<occurrenceID>` |
| FIRMS, LILA | null |

A Recipe dataset is one series, so one dataset has no cross-source duplicates.
Recipe must drop duplicates on `origin_record_id` when Recipe combines two event datasets. Recipe keeps the row from the source with more fields.

### recipe_point_events view

The view follows `recipe_animal_locations` in migration 008.

```sql
CREATE VIEW recipe_point_events WITH (security_invoker = true) AS
SELECT
    dataset_id, dataset_version, access_scope, source_id, source_record_id, origin_record_id, event_type,
    occurrence_status, sampling_design, taxon_name AS species, gbif_taxon_key, time_start, time_end,
    time_precision, available_at, longitude, latitude, cell_id, coordinate_uncertainty_m, individual_count,
    value, unit, basis, license, quality_flag
FROM (
    SELECT
        d.dataset_id, d.version::text AS dataset_version, d.access_scope, e.*,
        row_number() OVER (
            PARTITION BY d.dataset_id, d.version, d.access_scope, e.source_id, e.source_record_id
            ORDER BY (e.product_status = 'final') DESC, e.processing_version DESC, e.mapping_version DESC,
                     b.added_in_version DESC
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
```

### Derived cell_observations variables

The pipeline derives per-cell counts from the current events, as README.md describes for `_derived` sources.

| `variable` | `unit` | Interval | `source_id` | Zero rows |
| --- | --- | --- | --- | --- |
| `fire_count` | `count` | Day | `firms_modis_derived`, `firms_viirs_derived` | No. Recipe fills zero, because FIRMS is `systematic` |
| `fire_frp_sum_mw` | `MW` | Day | As `fire_count` | No |
| `occurrence_count` | `count` | Month | `gbif_occurrence_derived` | Never. A zero is unknown |
| `occurrence_effort_count` | `count` | Month | `gbif_occurrence_derived` | Never |

Count only rows with `quality_flag = ok`. The derived `available_at` is the latest `available_at` of the counted rows.

### How Recipe treats presence-only data

- Recipe never reads a missing `presence_only` row as an absence.
- Recipe uses `presence_only` rows as presence points against a background from `occurrence_effort_count`.
- Recipe can read a missing `systematic` row as "no detection". A cloud can hide a fire, so "no detection" is not "no fire".
- Recipe filters on `available_at` for point-in-time joins, as for the other families.

## 6. Normalizer design

Each source has a normalizer in `src/habitat/normalize/sources/<source>.py`. Each normalizer does these steps:

1. Read the archived file. Check the required columns. Raise `QuarantineError` when a required column is missing.
2. Parse the time. Set `time_start`, `time_end` and `time_precision`. Use half-open intervals, as CHIRPS does.
3. Clip the rows to the request bbox. Compute `cell_id` with the grid.
4. Resolve the taxon with `habitat.catalog.taxa`. Use the GBIF key of the source when the source gives one.
5. Map the source fields to the canonical columns. Put the other fields in `attributes`.
6. Set `quality_flag`. Use the first matching reason in the table below.
7. Return `NormalizedBatch(table, mapping_version, family=POINT_EVENTS)`.

| `quality_flag` | Rule |
| --- | --- |
| `geospatial_issue` | GBIF `issues` has `ZERO_COORDINATE`, `COORDINATE_INVALID`, `COORDINATE_OUT_OF_RANGE`, `COUNTRY_COORDINATE_MISMATCH`, `PRESUMED_SWAPPED_COORDINATE`, `COORDINATE_REPROJECTION_FAILED`, `COORDINATE_REPROJECTION_SUSPICIOUS` or `GEODETIC_DATUM_INVALID` |
| `date_issue` | GBIF `issues` has `RECORDED_DATE_INVALID`, `RECORDED_DATE_UNLIKELY` or `RECORDED_DATE_MISMATCH` |
| `coordinate_uncertainty_too_large` | `coordinate_uncertainty_m > 2000`, two grid cells |
| `imprecise_date` | `time_end - time_start > 31 days`, for example an `eventDate` with only a year |
| `captive_record` | `basisOfRecord` is `LIVING_SPECIMEN`, for example a zoo or an orphanage |
| `not_live_observation` | `basisOfRecord` is `FOSSIL_SPECIMEN` |
| `taxon_unresolved` | The taxon has no GBIF key. Keep `gbif_taxon_key = null` |
| `low_confidence` | FIRMS confidence below the threshold in section 4 |
| `non_vegetation_fire` | FIRMS `type` is not 0 |
| `available_at_from_dataset` | GBIF `modified` is missing or before the end of the event |
| `ok` | No rule matches |

Raise `QuarantineError` in these cases:

- A required column is missing, for example `gbifID`, `acq_time` or `seq_id`.
- A row has no coordinates. The fetch predicate asks for coordinates, so a row without coordinates shows a file problem.
- A row has no time. The fetch predicate asks for a date range, so a row without a time shows a file problem.
- A camera `location` has no coordinates in the camera table.
- A record license is not CC0 1.0, CC-BY 4.0 or CC-BY-NC 4.0.
- FIRMS `confidence` has a value outside the documented encoding.
- A `value` has no documented unit.
- The area API returns text that is not CSV, for example `Invalid MAP_KEY.`

## 7. Fetch agent changes

Add these `data_kinds` in `src/habitat/sources.py`:

| `source_id` | `data_kinds` | Request type |
| --- | --- | --- |
| `gbif_occurrence` | `species_occurrences`, `wildlife_mortality_events` | `needs_area_and_dates=True` |
| `firms_modis`, `firms_viirs` | `fire_events` | `needs_area_and_dates=True` |
| `lila_snapshot_serengeti` | `camera_trap_detections` | `needs_item=True` |

Change `ConnectorRequest`:

- Add `taxon_keys: tuple[int, ...] = ()`. The agent resolves names with `resolve_taxon` before the fetch.
- Add `max_records: int = 1000`. `validate_area_and_dates` accepts 1 to 10,000.

Add one tool `fetch_events` in `src/habitat/fetch/tools.py`:

```python
@beta_tool
def fetch_events(
    bbox: list[float],
    start: str,
    end: str,
    event_types: list[str],
    species: list[str] | None = None,
    max_records: int = 1000,
    max_days: int = 31,
) -> str:
    """Fetch point events for a WGS84 bbox and inclusive YYYY-MM-DD dates.

    Event types: species_occurrence (GBIF, includes iNaturalist and eBird), active_fire
    (NASA FIRMS standard product, MODIS and VIIRS). Species records are presence-only:
    a missing record is not an absence. Resolve species names first. Never guess the region
    or dates. Default bounds: 1,000 GBIF records, 31 fire days. A larger GBIF request
    starts an asynchronous download and returns status pending.
    """
```

Bounds:

- GBIF search: at most `max_records` records, in pages of 300.
- GBIF download: only with credentials, and only when the agent sets `max_records` above the count. One download per request.
- FIRMS area API: at most `max_days` days, in requests of 5 days. `max_days` is 1 to 366.
- LILA: metadata only, through `download_dataset("lila:snapshot-serengeti:<season>")`. The file limit is 512 MiB.
- A limit gives a warning. The result is then a bounded sample, as for the other sources.

## 8. Tests

- One recorded fixture per source in `tests/fixtures/`: 2 GBIF search pages, 1 small SIMPLE_CSV zip, 1 FIRMS MODIS CSV, 1 FIRMS VIIRS CSV, 1 COCO Camera Traps JSON with 3 sequences and a camera table.
- Schema test: each normalizer returns a table that equals `POINT_EVENTS_SCHEMA`.
- Vocabulary test: each `event_type` is in `EVENT_TYPES`.
- Time tests: a year-only `eventDate` gives `composite` and `imprecise_date`. A date without an offset gives `day`. FIRMS `acq_time = 0824` gives 08:24 UTC.
- Flag tests: one row for each `quality_flag` value.
- Quarantine tests: one case for each `QuarantineError` item in section 6.
- Dedup tests: the same `gbifID` in two batches gives one current row. An iNaturalist URL gives `inaturalist:<id>`.
- Rights test: a download with one CC-BY-NC record gives `Rights.license = "CC-BY-NC-4.0"`.
- Bound tests: `max_records = 1000` sends at most 4 search pages. 31 FIRMS days send 7 requests.
- SQL test: the migration applies, and `recipe_point_events` returns one row per event.
- Live tests in `tests/test_live.py`: one GBIF search with `limit=1` and one FIRMS country file `HEAD` request.

## 9. Risks and open questions

- `available_at` for archives. FIRMS 2012 files have a `Last-Modified` date in 2024 or 2025. A strict point-in-time join at 2012 then excludes all fires. CHIRPS has the same problem. Recipe must decide if a backtest can use a nominal latency instead.
- GBIF `modified` is set by the publisher. Some publishers set `modified` at each export. Then `available_at` is late, but never early.
- Local time. GBIF and camera traps often give local time with no zone. A local day differs from a UTC day by up to 14 hours.
- GBIF taxonomy. The download API accepts a `checklistKey`. GBIF can move to a new backbone. A taxon key can then change. Pin the checklist in the request and keep it in `attributes`.
- CC-BY-NC records. CC-BY-NC permits redistribution with attribution, so `access_scope` stays `public`. A commercial user must filter on `license`. Decide if the catalog needs a separate scope.
- `gbifID` stability. A publisher can change `occurrenceID`. GBIF then can give a new `gbifID` (not verified).
- GBIF downloads expire. `eraseAfter` is 6 months after creation. The archive keeps the zip, so the DOI and the archive stay valid.
- Snapshot Serengeti camera coordinates. The LILA metadata has none. Find the official camera table before the P2 build.
- FIRMS NRT. NRT and SP detections have no shared id. `firms_nrt` uses its own series. Recipe must not mix the two in one join.
- WAHIS, KWS and Wildlife Insights have no usable open API. Ask the providers before a build.

## 10. Build steps

1. Do the shared code changes in [README.md](README.md).
2. Add `POINT_EVENTS_SCHEMA`, `POINT_EVENTS`, `EVENT_TYPES` and the migration with `point_events`, `current_point_events` and `recipe_point_events`.
3. Add `point_events` to `ROW_GRAIN` in `src/habitat/catalog/publish.py`: "one row per event". Add the family to `recipe_inputs.py` and to `RECIPE_INTEGRATION.md`.
4. Add `taxon_keys` and `max_records` to `ConnectorRequest`.
5. Build `firms_modis` and `firms_viirs`: connector, normalizer, registration and tests. FIRMS has no taxa and no licenses per record, so FIRMS is the simplest first source.
6. Build `gbif_occurrence` in search mode. Then add download mode with `pending` status.
7. Add the `fetch_events` tool and the new data kinds.
8. Add the derived variables `fire_count`, `fire_frp_sum_mw`, `occurrence_count` and `occurrence_effort_count`.
9. Build `lila_snapshot_serengeti` when the camera table is available.
10. Add a section for each source to `SOURCES.md`.

## Provider references

- [GBIF occurrence API](https://techdocs.gbif.org/en/openapi/v1/occurrence)
- [GBIF API downloads](https://techdocs.gbif.org/en/data-use/api-downloads)
- [GBIF download formats](https://techdocs.gbif.org/en/data-use/download-formats)
- [FIRMS area API](https://firms.modaps.eosdis.nasa.gov/api/area/)
- [FIRMS MAP_KEY](https://firms.modaps.eosdis.nasa.gov/api/map_key/)
- [FIRMS FAQ](https://www.earthdata.nasa.gov/data/tools/firms/faq)
- [Snapshot Serengeti on LILA](https://lila.science/datasets/snapshot-serengeti)
- [COCO Camera Traps format](https://lila.science/coco-camera-traps)
- [Global Roadkill Data](https://figshare.com/articles/dataset/Global_Roadkill_Data_a_data_set_on_terrestrial_vertebrate_mortality_caused_by_collision_with_vehicles/25714233)
- [ACLED EULA](https://acleddata.com/eula)
- [Wildlife Insights FAQ](https://www.wildlifeinsights.org/faq)

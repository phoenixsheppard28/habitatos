# Water points

Status: proposal.

This document defines the `site_features` family and the per-cell water variables that the pipeline derives from it.
All rules in [README.md](README.md) apply. This document gives only the rules that are specific to water.

## 1. Purpose

Wildebeest and other grazers must drink. The distance to water and the season of that water control where they move.
The Recipe stage needs these inputs for each 1 km cell and each month:

- The distance to the nearest water that is available in that month.
- The distance to the nearest permanent water.
- The difference between natural water and artificial water.
- The number of water points near the cell.

A water feature can be natural or artificial. Natural features are rivers, streams, lakes, wetlands, springs and pans.
Artificial features are dams, reservoirs, boreholes, wells, troughs and taps.
A feature can be permanent, seasonal or intermittent. The state of a seasonal feature changes from month to month.

## 2. What the current data gives and the gap

| Current source | Water content | Gap |
| --- | --- | --- |
| `sentinel2` | `mndwi` mean per cell and scene | Starts 2015-06-27. The focus period is 2010–2013. Clouds hide many scenes in the rainy seasons. |
| `modis_mod13q1` | No water variable | None for water. |
| `chirps` | `rainfall_mm` per day | Rain is a cause of surface water. It is not the water itself. |
| `movebank_*` | Animal fixes | No water data. |

The pipeline has no vector features. It has no record of artificial water points, of rivers or of lakes.
It has no monthly surface water for the years before 2015.
It has no distance variable.

## 3. Sources

Each fact in this table comes from the provider page or from a live request on 2026-10-03, unless the table says "(not verified)".

| `source_id` | Provider | Content | Space coverage | Time coverage | Access and auth | Format | License | Priority |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `osm_overpass` | OpenStreetMap, Overpass API | Water features and water points by tag | Global | Live data. History from 2012-09-12 with `[date:]` | HTTPS POST, no auth | JSON | ODbL 1.0 | P1 |
| `jrc_gsw_monthly` | EC Joint Research Centre | Monthly water / not water / no data, 30 m | 180°W–180°E, 80°N–60°S (tile grid) | 1984-03 to 2021-12 (v1.4) | HTTPS file server, no auth | GeoTIFF, uint8, 10° tiles | CC BY 4.0 | P1 |
| `wpdx` | Water Point Data Exchange (WPdx+) | Human water points with type, status and report date | Mainly Africa, Asia, Latin America | Kenya reports 2006-12 to 2024-12 | Socrata SODA API, no auth | JSON | CC BY 4.0 | P1 |
| `jrc_gsw` | JRC on Planetary Computer, collection `jrc-gsw` | Occurrence, recurrence, seasonality, change, transitions, extent | 180°W–180°E, 56°S–78°N | 1984-03-01 to 2020-12-31 (v1.3) | STAC, signed URLs, no account | COG | Copernicus terms, free use | P2 |
| `hydrorivers` | HydroSHEDS | River network lines with discharge and order | Global, Africa file | Static, 15 arc-second source | HTTPS zip, no auth | Shapefile or GDB | HydroSHEDS license, free use with credit | P2 |
| `gires` | Messager et al. 2021, figshare 14633022 | Probability of flow intermittence per river reach | Global | Static, published 2021-06-03 | HTTPS, no auth | Shapefile or GDB, 1.7 GB | CC BY 4.0 | P2 |
| `hydrolakes` | HydroSHEDS | Lake and reservoir polygons of 10 ha or more | Global | Static | HTTPS zip, no auth | Shapefile or GDB, 820 MB | CC BY 4.0 | P2 |
| `osm_geofabrik` | Geofabrik extracts | Full OSM data per country | Per country | Daily current file. Kenya yearly files from 2015-01-01 | HTTPS, no auth | `.osm.pbf` (334 MB for Kenya) | ODbL 1.0 | P3 |
| `glwd_v2` | HydroSHEDS, GLWD v2 | 33 waterbody and wetland classes, fraction per cell | Global, 15 arc-second grid | Static, 2025 release | figshare DOI 10.6084/m9.figshare.28519994 | Raster | CC BY 4.0 | P3 |
| `gdw_dams` | Global Dam Watch v1 | 41,145 dams and 35,295 reservoir polygons | Global | Static | Google Form registration | Not verified | CC BY | P3 |
| `sentinel1_rtc` | Planetary Computer, collection `sentinel-1-rtc` | VV and VH backscatter, 10 m | Global | 2014-10-10 to now | STAC, Planetary Computer account required | COG | CC BY 4.0 | P3 |
| `landsat_c2_l2` | Planetary Computer, collection `landsat-c2-l2` | Surface reflectance for MNDWI | Global | 1982-08-22 to now | STAC, signed URLs, no account | COG | USGS terms (not verified) | P3 |

Dropped sources:

- GRanD and GOODD. Global Dam Watch says that both are now part of GDW v1 and that GRanD gets no more updates.
- A Planetary Computer collection for HydroRIVERS. The id `hydro-rivers` does not exist.
- The `jrc-gsw` collection for monthly history. The collection has only the six aggregate layers. It has no monthly layer.

## 4. Sources in detail

### osm_overpass (P1)

- Endpoint: `POST https://overpass-api.de/api/interpreter` with the form field `data`.
- Query for one bbox and one snapshot date. The connector writes south, west, north, east in the Overpass order.

```
[out:json][timeout:180][date:"2013-12-31T23:59:59Z"];
(
  nwr["natural"~"^(water|spring|wetland)$"](S,W,N,E);
  nwr["waterway"~"^(river|stream|canal|dam|weir)$"](S,W,N,E);
  nwr["landuse"="reservoir"](S,W,N,E);
  nwr["man_made"~"^(water_well|water_tap|reservoir_covered|dam)$"](S,W,N,E);
  nwr["amenity"~"^(drinking_water|water_point)$"](S,W,N,E);
);
out meta geom;
```

- `out meta` gives `version`, `timestamp`, `changeset` and `user` for each element. A live test on 2026-10-03 returned these fields.
- Omit `[date:]` for the current state. The oldest snapshot date is 2012-09-12T06:55:00Z. The Overpass wiki gives this limit.
- The main server rejected two attic requests with "server is probably too busy". The `[date:]` request is therefore not verified live.
- The archive keeps the JSON response as received.
- `source_key`: `osm_overpass:<query hash>:<bbox key>:<snapshot date>`.
- `source_item_id`: `<bbox key>@<snapshot date>`.
- `available_at`: the item value is `osm3s.timestamp_osm_base`. Each row has the `timestamp` of its element version.
- `processing_version`: `osm3s.timestamp_osm_base`. For an attic query, use the snapshot date.
- `time_precision`: `static`.
- Rights: `license="ODbL-1.0"`, `attribution="© OpenStreetMap contributors"`, `reuse_allowed=True`, `access_scope="public"`.

| OSM tag | `feature_class` | `feature_type` | `origin` | `permanence` |
| --- | --- | --- | --- | --- |
| `waterway=river` | `river` | `river` | `natural` | from `intermittent`, else `unknown` |
| `waterway=stream` | `river` | `stream` | `natural` | from `intermittent`, else `unknown` |
| `waterway=canal` | `river` | `canal` | `artificial` | `unknown` |
| `natural=water` + `water=lake` | `lake` | `lake` | `natural` | from `intermittent` / `seasonal` |
| `natural=water` + `water=pond` | `pan` | `pond` | `natural` | from `intermittent` / `seasonal` |
| `natural=water` + `water=reservoir`, `landuse=reservoir` | `reservoir` | `reservoir` | `artificial` | `unknown` |
| `natural=wetland` | `wetland` | value of `wetland`, else `wetland` | `natural` | `unknown` |
| `natural=spring` | `water_point` | `spring` | `natural` | `unknown` |
| `waterway=dam`, `man_made=dam` | `dam` | `dam` | `artificial` | `unknown` |
| `man_made=water_well` | `water_point` | `borehole` when `pump` is set, else `well` | `artificial` | `unknown` |
| `man_made=water_tap`, `amenity=drinking_water`, `amenity=water_point` | `water_point` | `tap` | `artificial` | `permanent` |

- `intermittent=yes` or `seasonal=yes` gives `seasonal`. `intermittent=no` gives `permanent`.
- `operational_status`, `disused:*` and `abandoned:*` give `status`. No tag gives `unknown`.
- `start_date` goes to `attributes.start_date`. It does not change `time_start`. See the time rule in section 5.
- Limits: the OSM date is a mapping date, not a construction date. Coverage of water points depends on local mappers.
- Limits: the public server has a fair-use limit (not verified: about 10,000 requests and 1 GB per day). Use one request per bbox and snapshot.

### jrc_gsw_monthly (P1)

- Endpoint: `https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GSWE/MonthlyHistory/LATEST/tiles/<YYYY>/<YYYY_MM>/<YYYY_MM>-<row offset>-<col offset>.tif`.
- Each tile is 40,000 × 40,000 pixels of 0.00025°, so one tile is 10° × 10°. The first tile starts at 180°W, 80°N.
- Offset rule: row offset = `(80 − tile north) / 0.00025`. Column offset = `(tile west + 180) / 0.00025`. Both have 10 digits.
- Example for Athi-Kaputiei, March 2011: `2011/2011_03/2011_03-0000320000-0000840000.tif`. A HEAD request returned 200.
- The files are tiled GeoTIFFs with no overviews. The connector reads only the AOI window with HTTP range requests, as `stac.py` does.
- Pixel values: 0 no data, 1 not water, 2 water. The Earth Engine catalog page for `JRC/GSW1_4/MonthlyHistory` gives these values.
- `LATEST` stops at 2021-12. JRC publishes 2022–2024 as v1.5 on a different server. The v1.5 monthly path is not verified.
- The archive keeps one clipped GeoTIFF per tile and month.
- `source_key`: `jrc_gsw_monthly:<YYYY_MM>-<row>-<col>:<bbox key>`.
- `source_item_id`: `<YYYY_MM>-<row>-<col>`.
- `available_at`: the `Last-Modified` header, as for `chirps`. Example: 2019-01-05 for the 2011-03 Kenya tile.
- `processing_version`: `1.4@<Last-Modified as ISO UTC>`. The folder `2011_06` has a 2026-07-11 date, so JRC republishes some months.
- `time_precision`: `composite`. `time_start` is the first day of the month. `time_end` is the first day of the next month.
- Rights: `license="CC-BY-4.0"`, attribution "Pekel et al. 2016, Nature 540, 418–422, © European Union".

The normalizer writes these `cell_observations` variables. They have `source_id = "jrc_gsw_monthly"`.

| `variable` | `stat` | `unit` | Meaning |
| --- | --- | --- | --- |
| `surface_water_fraction` | `mean` | `fraction` | Water pixels ÷ observed pixels in the cell |
| `distance_to_surface_water_m` | `centroid` | `m` | Distance from the cell centroid to the nearest water pixel in that month |

- `valid_fraction` is observed pixels ÷ all pixels. `pixel_count` is the observed pixel count. `source_resolution_m` is 30.
- Limits: Landsat 7 SLC-off stripes and clouds give many "no data" pixels from 2003. Small pans and troughs are below 30 m.

### wpdx (P1)

- Endpoint: `GET https://data.waterpointdata.org/resource/eqje-vguj.json` (WPdx+). WPdx-Basic is `jfkt-jmqa`.
- Request: `$where=lat_deg between S and N AND lon_deg between W and E AND lat_deg IS NOT NULL`, `$order=row_id`, `$limit=50000`, `$offset=<page>`.
- The `IS NOT NULL` filter keeps rows without coordinates out of the archive. See section 6.
- An app token is optional. Without a token, Socrata throttles requests (not verified).
- Kenya has 39,959 rows in WPdx-Basic. The bbox 36.6–37.3°E, 1.2–1.9°S has 367 rows with reports from 2011-01-01 to 2021-02-15.
- The archive keeps the JSON pages as one CSV file.
- `source_key`: `wpdx:eqje-vguj:<bbox key>:<max updated>`.
- `source_item_id`: `eqje-vguj:<bbox key>`.
- `available_at`: the item value is the maximum `updated`. Each row has its own `updated`.
- `processing_version`: the maximum `updated` in the response.
- `time_precision`: `static`.

| WPdx field | Canonical column |
| --- | --- |
| `row_id` | `source_record_id` |
| `wpdx_id` | `feature_id = wpdx:<wpdx_id>` |
| `lat_deg`, `lon_deg` | `latitude`, `longitude` |
| `report_date` | `time_start` |
| `updated` | `available_at` |
| `water_source_clean` | `feature_type` (see below) |
| `status_clean` | `status` (see below) |
| `install_year`, `water_tech_clean`, `management_clean`, `source`, `dataset_title` | `attributes` |

| `water_source_clean` | `feature_type` | `permanence` |
| --- | --- | --- |
| `Borehole/Tubewell` | `borehole` | `permanent` |
| `Protected Well`, `Unprotected Well`, `Undefined Well` | `well` | `unknown` |
| `Protected Spring`, `Undefined Spring` | `spring` | `unknown` |
| `Piped Water` | `tap` | `permanent` |
| `Sand or Sub-surface Dam` | `sand_dam` | `seasonal` |
| `Rainwater Harvesting` | `rainwater_tank` | `seasonal` |
| `Delivered Water` | `delivered` | `intermittent` |
| empty | `unknown` | `unknown` |

| `status_clean` | `status` |
| --- | --- |
| `Functional` | `functional` |
| `Functional, needs repair` | `functional_needs_repair` |
| `Functional, not in use` | `functional_not_in_use` |
| `Non-Functional` | `non_functional` |
| `Non-Functional, dry season` | `non_functional_dry_season` (sets `permanence = seasonal`) |
| `Abandoned/Decommissioned` | `abandoned` |
| empty | `unknown` |

- All WPdx rows have `feature_class = water_point` and `origin = artificial`, except springs, which are `natural`.
- Limits: WPdx records human water supply. It does not record wildlife troughs or pans. Most Kenya rows are `Non-Functional`.

### jrc_gsw (P2)

- Endpoint: Planetary Computer STAC, collection `jrc-gsw`. One item per 10° tile, for example `30E_0Nv1_3_2020`.
- Assets: `occurrence`, `recurrence`, `seasonality`, `change`, `transitions`, `extent`. The connector reads `occurrence` and `recurrence`.
- `seasonality` covers 2020 only. Do not use it for 2010–2013.
- Variables: `water_occurrence_pct` and `water_recurrence_pct`, unit `percent`, `stat = mean`, `time_precision = composite`.
- `time_start` and `time_end` come from the item `start_datetime` and `end_datetime`.
- `available_at`: the item has no publication date. Use the v1.3 publication date (not verified).
- `processing_version`: `1.3`. JRC v1.5 (1984–2024) corrects the occurrence layer. v1.5 is on `s3.waw4-1.cloudferro.com`, not on Planetary Computer.

### hydrorivers and gires (P2)

- HydroRIVERS endpoint: `https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_af_shp.zip` (108 MB). Global file: `HydroRIVERS_v10_shp.zip`.
- The archive keeps the zip. The normalizer clips the lines to the bbox.
- `source_item_id`: `HydroRIVERS_v10_af`. `processing_version`: `v10`. `time_precision`: `static`.
- `available_at`: the publication date of v1.0 (not verified). Do not use `Last-Modified`. The file shows 2026-10-01, which is an upload date.
- `feature_id`: `hydrorivers:<HYRIV_ID>`. Field names `HYRIV_ID`, `DIS_AV_CMS`, `ORD_STRA` are not verified.
- GIRES gives a probability of flow intermittence per reach. The normalizer joins GIRES to HydroRIVERS by reach id (not verified).
- GIRES endpoint: `https://ndownloader.figshare.com/files/28254582` (shapefile, 1.66 GB, global only).
- Limits: the network starts from a 15 arc-second grid. Small seasonal streams are absent or have a wrong position.

### hydrolakes (P2)

- Endpoint: `https://data.hydrosheds.org/file/hydrolakes/HydroLAKES_polys_v10_shp.zip` (820 MB, global only).
- `feature_id`: `hydrolakes:<Hylak_id>`. `Lake_type` 2 gives `reservoir` and `artificial` (not verified).
- Limits: the minimum area is 10 ha. Pans and small dams are absent.

## 5. Shape

### site_features table

Migration `010_site_features.sql`:

```sql
CREATE TABLE site_features (
    series_id                 text             NOT NULL,
    batch_key                 text             NOT NULL,
    source_record_id          text             NOT NULL,
    dataset_id                text             NOT NULL,
    source_id                 text             NOT NULL,
    source_item_id            text             NOT NULL,
    processing_version        text             NOT NULL,
    mapping_version           text             NOT NULL,
    feature_id                text             NOT NULL,
    feature_class             text             NOT NULL,
    feature_type              text             NOT NULL,
    origin                    text             NOT NULL CHECK (origin IN ('natural', 'artificial', 'unknown')),
    permanence                text             NOT NULL CHECK (permanence IN ('permanent', 'seasonal', 'intermittent', 'unknown')),
    status                    text             NOT NULL,
    name                      text,
    time_start                timestamptz      NOT NULL,
    time_end                  timestamptz      NOT NULL,
    time_precision            text             NOT NULL,
    available_at              timestamptz      NOT NULL,
    longitude                 double precision CHECK (longitude BETWEEN -180 AND 180),
    latitude                  double precision CHECK (latitude BETWEEN -90 AND 90),
    cell_id                   text             REFERENCES grid_cells,
    geometry                  geometry(Geometry, 4326) NOT NULL,
    coordinate_uncertainty_m  double precision,
    quality_flag              text             NOT NULL,
    attributes                jsonb            NOT NULL DEFAULT '{}',
    PRIMARY KEY (series_id, batch_key, source_record_id),
    FOREIGN KEY (series_id, batch_key) REFERENCES ingest_batches (series_id, batch_key)
);

CREATE INDEX site_features_feature_time_idx ON site_features (feature_id, time_start);
CREATE INDEX site_features_class_idx ON site_features (feature_class, time_start);
CREATE INDEX site_features_geometry_idx ON site_features USING gist (geometry);
```

- A point feature has `longitude`, `latitude`, `cell_id` and a Point `geometry`.
- A line or an area has `longitude`, `latitude` and `cell_id` set to null. Recipe joins it to cells by overlap.
- The migration adds row level security and grants in the style of migration 003.

### PyArrow schema

`SITE_FEATURES_SCHEMA` in `src/habitat/contracts.py` has the same columns, without `series_id` and `batch_key`.
`time_*` and `available_at` use `UTC_TIMESTAMP`. `longitude`, `latitude`, `cell_id`, `name` and `coordinate_uncertainty_m` are nullable.
`geometry` is `pa.binary()` with WKB. `attributes` is `pa.string()` with JSON, as in `ANIMAL_LOCATIONS_SCHEMA`.
`src/habitat/normalize/rows.py` gets `SITE_FEATURES = "site_features"`.

### Time rule for features

- `time_start` is the first date the source shows this version of the feature, as in [README.md](README.md).
- OSM: the element version `timestamp`. WPdx: `report_date`. HydroSHEDS: the publication date of the dataset.
- `time_end` is `9999-12-31` in the batch.
- A later version of the same `feature_id` ends the earlier version. The view computes `valid_until` for this.
- `start_date` (OSM) and `install_year` (WPdx) stay in `attributes`. The derive step can use them. See below.

### Current-row rule

- Inside one dataset version, one row per `feature_id` and `time_start` is current.
- Order: the newest `added_in_version`, then the latest `available_at`, then `source_record_id`.
- The view does not merge features across sources. Each source is a separate series. The derive step removes duplicates.

### recipe_site_features view

```sql
CREATE VIEW recipe_site_features WITH (security_invoker = true) AS
SELECT
    dataset_id, dataset_version, access_scope, source_id, source_record_id, feature_id, feature_class,
    feature_type, origin, permanence, status, name, time_start, time_end,
    least(time_end, lead(time_start) OVER (
        PARTITION BY dataset_id, dataset_version, access_scope, feature_id ORDER BY time_start
    )) AS valid_until,
    time_precision, available_at, longitude, latitude, cell_id, geometry, quality_flag
FROM (
    SELECT
        d.dataset_id, d.version::text AS dataset_version, d.access_scope, f.*,
        row_number() OVER (
            PARTITION BY d.dataset_id, d.version, d.access_scope, f.feature_id, f.time_start
            ORDER BY b.added_in_version DESC, f.available_at DESC, f.source_record_id DESC
        ) AS current_rank
    FROM datasets d
    JOIN ingest_batches b
      ON b.series_id = d.dataset_id
     AND b.added_in_version <= d.version
     AND (b.superseded_in_version IS NULL OR b.superseded_in_version > d.version)
    JOIN site_features f ON f.series_id = b.series_id AND f.batch_key = b.batch_key
    WHERE d.status = 'ready' AND d.family = 'site_features'
) ranked
WHERE current_rank = 1;
```

### Derived cell variables

A new derive step writes these variables into `cell_observations` with `source_id = "water_derived"`.
The `mapping_version` is `water-derived-v1`. One batch covers one bbox and one calendar month.

| `variable` | `stat` | `unit` | Value |
| --- | --- | --- | --- |
| `distance_to_water_m` | `centroid` | `m` | Distance from the cell centroid to the nearest water that is available in the month |
| `distance_to_permanent_water_m` | `centroid` | `m` | The same, for permanent water only |
| `distance_to_natural_water_m` | `centroid` | `m` | The same, for `origin = natural` only |
| `distance_to_artificial_water_m` | `centroid` | `m` | The same, for `origin = artificial` only |
| `water_point_density` | `density` | `count_per_km2` | Available point features within 5 km of the centroid ÷ 78.54 km² |

Time semantics:

- `time_precision` is `composite`. `time_start` is the first day of the month. `time_end` is the first day of the next month.
- `available_at` is the latest `available_at` of all input rows. A derived value is never older than its inputs.
- `source_resolution_m` is 1000. `valid_fraction` is the observed fraction of JRC pixels within the search radius, or 1.0 without JRC input.
- `pixel_count` is the number of features within the search radius.

Availability rule for one feature in one month:

1. The feature is valid when `time_start ≤ month end` and `valid_until > month start`.
2. A natural feature from a static source is also valid before `time_start`. The row gets `feature_backfilled`.
3. An artificial feature is valid before `time_start` only from its documented `start_date` or `install_year`.
4. A `non_functional` or `abandoned` water point is not available.
5. A `permanent` feature is available in every valid month.
6. A `seasonal`, `intermittent` or `unknown` feature is available when surface water shows water in its cells that month.
7. Surface water comes from `surface_water_fraction` (JRC monthly). After 2021-12, use `mndwi > 0` from `sentinel2` (threshold not verified).
8. When no observation exists for the month, the feature counts as available. The row gets `seasonal_state_unknown`.

A water pixel is also water, also without a feature row. The step takes the minimum of the feature distance and `distance_to_surface_water_m`.
A JRC pixel with water in 90% or more of the observed months of the year counts as permanent (threshold is an open question).

Search radius and edges:

- The step reads features in the bbox plus a 20 km buffer. The fetch agent fetches the same buffer.
- When nothing is in 20 km, `value` is null and the flag is `beyond_search_radius`.
- When the buffer has no input coverage, the flag is `edge_effect`.

Recomputation:

- A new batch of any input series marks the months in its time range as stale for its bbox.
- The step recomputes each stale month and passes the old derived batch keys as `supersedes` to `SeriesStore.append_batch`.
- `processing_version` is `<latest input available_at>+<first 8 hex of a SHA-256 of the input batch keys>`. The text order then follows the newest input.
- The step writes a small JSON artifact with the input batch keys. The derived `RawManifest` points to this artifact.
- `source_item_id` is `<bbox key>:<YYYY-MM>`.

## 6. Normalizer design

New files: `src/habitat/normalize/sources/osm_water.py`, `wpdx.py`, `jrc_gsw.py`, `hydrosheds.py`, and `src/habitat/derive/water.py`.

Steps for a vector source:

1. Read the archived file. Check the required fields. A missing field raises `QuarantineError`.
2. Map each element to one row with the tables in section 4.
3. Compute the representative point for points only. Compute `cell_id` with `Grid`.
4. Set `time_start`, `time_end`, `available_at` from the source fields. Never use `retrieved_at`.
5. Set `quality_flag`. Put unmapped source fields into `attributes`.
6. Return `NormalizedBatch(table, MAPPING_VERSION, family=SITE_FEATURES)`.

The JRC monthly normalizer follows `chirps.py`. It reads the clipped tile, aggregates per cell with `zonal.py`, and calls `to_cell_observations`.

`quality_flag` values for `site_features`:

| Value | Reason |
| --- | --- |
| `ok` | No problem |
| `status_unknown` | The source gives no functional status |
| `permanence_unknown` | The source gives no permanence |
| `feature_id_missing` | WPdx row without `wpdx_id`. `feature_id` is `wpdx:row:<row_id>` |
| `location_imprecise` | `coordinate_uncertainty_m` is more than 500 m |
| `geometry_repaired` | The source geometry was invalid. `ST_MakeValid` or Shapely `make_valid` repaired it |

`quality_flag` values for derived rows: `ok`, `feature_backfilled`, `seasonal_state_unknown`, `beyond_search_radius`, `edge_effect`, `low_valid_fraction`.
When a row has two reasons, the flag uses the first value in this order: `beyond_search_radius`, `edge_effect`, `seasonal_state_unknown`, `feature_backfilled`, `low_valid_fraction`.

`QuarantineError` cases:

- An Overpass response without `osm3s.timestamp_osm_base`, or an element without `timestamp`. `available_at` is then unknown.
- An OSM element that matches no row of the tag table.
- A WPdx row without coordinates, or a `water_source_clean` or `status_clean` value that is not in the tables.
- A JRC tile with a CRS other than EPSG:4326, or with a pixel value outside 0, 1 and 2.
- A HydroSHEDS file without the id field or the geometry.
- A length or area field in a unit that the documentation does not give.

## 7. Fetch agent changes

New `data_kinds`:

| Data kind | Sources |
| --- | --- |
| `water_features` | `osm_overpass`, `hydrorivers`, `gires`, `hydrolakes` |
| `water_points` | `osm_overpass`, `wpdx` |
| `water_observations` | `sentinel2` (exists), `jrc_gsw_monthly`, `jrc_gsw` |

Registration in `src/habitat/sources.py`:

- `osm_overpass`, `wpdx`, `jrc_gsw_monthly`, `jrc_gsw`: `needs_area_and_dates=True`.
- `hydrorivers`, `gires`, `hydrolakes`: `needs_area_and_dates=True`. The dates select nothing, but the bbox clips the file.
- `SourceItem.kind = "vector"` for the OSM, WPdx and HydroSHEDS items.

New agent tool in `src/habitat/fetch/tools.py`:

```
fetch_water(bbox, start, end, sources=None, buffer_km=20, discover_only=False)
"""Fetch water features, water points and monthly surface water for a WGS84 bbox and inclusive
YYYY-MM-DD dates. Sources: osm_overpass, wpdx, jrc_gsw_monthly; also hydrorivers, hydrolakes,
jrc_gsw. The bbox grows by buffer_km so that distances near the edge are correct. Requires a
resolved region and dates: never guess them. OSM history starts 2012-09-12; JRC monthly ends 2021-12."""
```

- `fetch_environment` keeps its satellite and rainfall sources. Water features need a different bbox buffer, so they get a separate tool.
- The tool returns a warning when the dates are before 2012-09-12 for OSM, or after 2021-12 for JRC monthly.

## 8. Tests

Fixtures in `tests/fixtures/water/`:

- `overpass_athi.json`: 10 elements, one per tag row. Include one `intermittent=yes` river and one well with `pump`.
- `wpdx_athi.json`: 8 rows. Include each `status_clean` value and one row without `wpdx_id`.
- `jrc_monthly_2011_03.tif`: a 40 × 40 pixel clip with values 0, 1 and 2.
- `hydrorivers_clip.gpkg`: 3 reaches.

Unit tests:

- `test_osm_water.py`: tag mapping, permanence, `available_at` per element, quarantine of an unknown tag and of a missing `timestamp`.
- `test_wpdx.py`: status mapping, `feature_id_missing`, quarantine of an unknown status.
- `test_jrc_gsw_monthly.py`: tile path from a bbox, `surface_water_fraction`, `valid_fraction`, quarantine of the value 3.
- `test_water_derive.py`: distance to a known point; seasonal pan dry in one month and wet in the next; `feature_backfilled`; `beyond_search_radius`; `edge_effect`; recomputation supersedes the old batch.
- `test_series.py`: `recipe_site_features` gives `valid_until` from the next version.

Live tests in `tests/test_live.py`:

- One Overpass request for a 0.05° bbox near Nairobi.
- One HEAD request for the 2011-03 JRC tile `0000320000-0000840000`.
- One WPdx request with `$limit=1`.

## 9. Risks and open questions

- ODbL. A derived database from OSM must stay under ODbL when the project publishes the database. Do the derived `cell_observations` count as a derived database? Decide before publication.
- OSM dates are mapping dates. Most Kenya water points in OSM have dates after 2012. A point-in-time cutoff in 2011 excludes them.
- JRC publication dates are 2016 or later. A strict cutoff for 2010–2013 excludes all JRC data. Recipe must decide if a historical analysis relaxes the cutoff.
- WPdx records human water supply. Wildlife water (troughs in conservancies, pans) is mostly absent. Is there a Kenya Wildlife Service or conservancy source for the Athi-Kaputiei area?
- Thresholds are not verified: 90% occurrence for permanent water, `mndwi > 0` for water, 5 km for density, 20 km for search.
- JRC v1.5 (2022–2024) monthly files: the path on the v1.5 server is not verified.
- Duplicates across sources: the derive step merges points of the same `feature_type` within 50 m. Is 50 m correct for WPdx coordinates?
- Overpass load: attic requests failed twice during the research. Geofabrik yearly files (from 2015) are the fallback.
- Sentinel-1 RTC needs a Planetary Computer account and starts 2014-10. It cannot help the 2010–2013 focus period.

## 10. Build steps

1. Do the four shared code changes in [README.md](README.md).
2. Add migration `010_site_features.sql` with the table, the grants and `recipe_site_features`.
3. Add `SITE_FEATURES_SCHEMA` and `SITE_FEATURES`. Add the summary function for `site_features`.
4. Write the `jrc_gsw_monthly` connector and normalizer. Test with the fixture.
5. Write the `osm_overpass` connector and normalizer. Test with the fixture.
6. Write the `wpdx` connector and normalizer. Test with the fixture.
7. Write `src/habitat/derive/water.py` and its tests.
8. Register the sources and the `fetch_water` tool.
9. Add the live tests. Run them for the Athi-Kaputiei bbox, 2010-01-01 to 2013-12-31.
10. Add the new variables to `recipe_inputs.py`, `RECIPE_INTEGRATION.md` and `SOURCES.md`.
11. Add the P2 sources `jrc_gsw`, `hydrorivers`, `gires` and `hydrolakes`.

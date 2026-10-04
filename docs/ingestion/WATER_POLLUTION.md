# Water pollution and water quality

Status: implemented (P1). See section 12 for the implemented parts and the deferred parts.

This document defines the family `site_observations` and its reference table `monitoring_sites`.
It also adds satellite water-quality variables to `cell_observations`.
All shapes obey the shared rules in [README.md](README.md).

## 1. Purpose

Water quality controls where animals and people can drink safely.
The pipeline needs water-quality data for three questions:

- Drinking water: is the water at a river, dam or pan safe for wildlife and people?
- Disease: do faecal bacteria, nutrients or algal blooms increase the risk of disease?
- Livestock: do herders and wildebeest use the same polluted water points?

The focus case is the wildebeest of Athi-Kaputiei and Nairobi National Park, 2010–2013.
The Athi and Nairobi rivers receive untreated waste from Nairobi.
The sources in this document must also work in other regions.

## 2. What the current data gives, and the gap

- `sentinel2` gives MNDWI. MNDWI shows where water is. MNDWI does not show the quality of the water.
- No current source gives a measured concentration of a pollutant.
- Open in-situ water-quality data for East Africa is very sparse. For the focus area, it is almost absent.

We checked the gap directly:

- The open GEMStat archive (Zenodo, v3) has 22,982 stations in 42 countries. It has no station in Africa. We counted the countries in `GEMStat_station_metadata.csv`.
- The Water Quality Portal returned zero stations for `countrycode=KE`.
- The Kenya Water Resources Authority (WRA) gives data only on request, for a fee. The data is not open.
- Studies of the Nairobi River and the Athi River publish tables in papers. We found no deposited dataset on Zenodo or Dryad.

Satellite proxies do not remove the gap for 2010–2013. Sentinel-2 starts in June 2015. Sentinel-3 OLCI on Planetary Computer starts in November 2017.
For 2010–2013 the only satellite inputs are MERIS (until April 2012) and Landsat 5 and 7.
The Athi River is too narrow for most of these sensors (see section 6).

Thus, for the focus case, this design gives a tested global shape and a few satellite values on lakes and dams.
The design does not give measured pollution in the Athi River for 2010–2013.

## 3. Sources

| `source_id` | Provider | Path | Parameters | Space | Time | Access and auth | Format | License | Priority |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `wqp` | USGS, EPA, NWQMC Water Quality Portal | station | All physical, chemical and bacterial parameters | USA, some territories; zero KE stations | 1900s–now | REST, no auth | CSV, GeoJSON | U.S. public domain | P1 |
| `gemstat` | UNEP GEMS/Water, BfG | station | 622 parameters | 42 countries, none in Africa (open part) | 1906–2024 | Zenodo file, no auth | ZIP of CSV | CC-BY-4.0 | P1 |
| `cgls_lwq` | Copernicus Global Land Service, via Digital Earth Africa | satellite | Turbidity, trophic state index, reflectance | Africa, lakes over about 50 ha | 2002-05 to 2012-03 (MERIS); 2016–now (OLCI) | STAC, no auth | GeoTIFF | CC-BY-4.0 | P1 |
| `sentinel2` (new variables) | ESA, Planetary Computer | satellite | NDTI, NDCI | Global | 2015-06–now | STAC, signed URLs | GeoTIFF | Copernicus Sentinel data terms | P1 |
| `landsat_c2_l2` | USGS, Planetary Computer | satellite | Water surface temperature, NDTI | Global | 1982–now | STAC, signed URLs | GeoTIFF | USGS public domain (not verified) | P2 |
| `sentinel3_olci_wfr` | EUMETSAT, Planetary Computer | satellite | Chlorophyll-a, total suspended matter | Global | 2017-11–now on PC | STAC, signed URLs | NetCDF | Copernicus Sentinel data terms | P2 |
| `waterbase` | European Environment Agency | station | All WFD parameters | Europe | 1900–2025 | File download, no auth | CSV, SQLite | CC-BY-4.0 | P2 |
| `grqa` | Virro et al., Zenodo | station | 43 nutrient, carbon, oxygen, sediment forms | Global rivers | 1898–2023 | Zenodo file, no auth | ZIP of CSV | CC-BY-4.0 | P2 |
| `literature_tabular` | Zenodo and Dryad records | station | Varies | Varies | Varies | `zenodo` connector, Dryad API (not verified) | CSV, XLSX | Per record | P2 |
| `glorich` | Hartmann et al., PANGAEA | station | Major ions, nutrients, carbon | Global rivers | Varies | File download, no auth | CSV | CC-BY-NC-SA-4.0 | P3 |
| `gemstat_portal` | UNEP GEMS/Water | station | As GEMStat | Includes LIMITED data | Varies | Contact form; e-mail link | XLS, CSV | CC-BY-NC-4.0 | P3 |
| `freshwater_watch` | Earthwatch | station | Nitrate, phosphate, turbidity, observations | Global, citizen science | 2012–now (not verified) | Web download | CSV, GeoJSON | No license stated | P3 |
| `wra_kenya` | Water Resources Authority, Kenya | station | Ambient surface and groundwater quality | Kenya | Irregular | Request on eCitizen, fee | Unknown | Not open | P3 |

Rejected or deferred:

- GLORICH as P1: the NC-SA license blocks public reuse. Use `access_scope = "glorich-nc"`.
- GRQA includes GLORICH rows but has a CC-BY-4.0 license. Exclude the GLORICH rows from GRQA until the conflict has a clear answer.
- Sentinel-3 SLSTR `sentinel-3-slstr-wst-l2-netcdf` is a sea surface product. Inland water values are not reliable (not verified).
- The WQ4Nile record (Zenodo 20664052) covers the Nile basin only. The Athi basin is not in the Nile basin.

## 4. Source details

### 4.1 `wqp` (P1)

The Water Quality Portal is the test case for a generic station connector. Use it to test the shape. It has no Kenya data.

- Station endpoint: `https://www.waterqualitydata.us/wqx3/Station/search` (not verified; the WQX 2.2 path `/data/Station/search` is verified).
- Result endpoint: `https://www.waterqualitydata.us/wqx3/Result/search`. Use `dataProfile=fullPhysChem` and `mimeType=csv`.
- Parameters: `bBox=west,south,east,north`, `startDateLo=MM-DD-YYYY`, `startDateHi=MM-DD-YYYY`, `characteristicName` (separate values with `;`).
- No auth. A query can name up to 250,000 sites. Use POST with JSON for a long query.
- The WQX 2.2 paths under `/data/` do not contain USGS data added after 11 March 2024. Use the WQX 3.0 paths.
- Archive: one Result CSV and one Station CSV per request.
- `source_key`: hash of the query parameters.
- `source_item_id`: `wqp:<bbox>:<start>:<end>:<characteristic list hash>`.
- `available_at`: per row, `LastChangeDate` (for example `Fri Jan 31 08:59:46 UTC 2025`). The fullPhysChem profile has this column. The basic profile does not.
- `processing_version`: maximum `LastChangeDate` in the file.
- `time_precision`: `instant` when `Activity_StartTime` and `Activity_StartTimeZone` exist, else `day`.
- `product_status`: `final` when `Result_MeasureStatusIdentifier` is `Final` or `Accepted`, else `preliminary`.
- Rights: public domain. Cite `https://doi.org/10.5066/P9QRKUVJ`.

| WQX 3.0 column | Canonical field |
| --- | --- |
| `Location_Identifier` | `monitoring_sites.local_site_id` |
| `Location_LatitudeStandardized`, `Location_LongitudeStandardized` | `latitude`, `longitude` |
| `Activity_StartDate`, `Activity_StartTime`, `Activity_StartTimeZone` | `time_start` |
| `Result_Characteristic` | `parameter` (through the vocabulary) |
| `Result_SampleFraction` | `fraction` |
| `Result_Measure`, `Result_MeasureUnit` | `value`, `unit` |
| `Result_ResultDetectionCondition` | `censored` |
| `DetectionLimit_MeasureA`, `DetectionLimit_TypeA` | `detection_limit`, `detection_limit_type` |
| `Activity_DepthHeightMeasure` and unit | `sample_depth_m` |
| `ResultAnalyticalMethod_Identifier` | `method` |
| `Result_MeasureIdentifier` | part of `source_record_id` |

Limits: the datum of the standardized coordinates is not documented on the pages we read (not verified). pH has the unit `None` in the source.

### 4.2 `gemstat` (P1)

- Endpoint: `https://zenodo.org/records/18459694/files/GFQA_v3.zip`. The concept DOI is `10.5281/zenodo.13881899`.
- One ZIP of about 201 MB. It has 80 CSV files, one per parameter group, and three metadata files.
- Metadata files: `GEMStat_station_metadata.csv`, `GEMStat_parameter_metadata.csv`, `GEMStat_method_metadata.csv`.
- No auth. The ZIP holds only data with the OPEN policy.
- `source_key`: Zenodo record id and file checksum.
- `source_item_id`: `gemstat:<record id>`.
- `available_at`: Zenodo publication date of the version (v3: 2026-02-02).
- `processing_version`: Zenodo version label, for example `v3`.
- `time_precision`: `day`. The source gives local time without a zone. The value `00:00` or `12:00` can be a default.
- `time_start` is the local sample date at 00:00 UTC. `time_end` is one day later.
- Keep the local sample time in `attributes.local_sample_time`.
- `Data Quality` map: `Good`, `Fair` and `Unknown` give `ok`. `Estimated` gives `estimated`. `Pending review` and `Suspect` give `suspect`. `Contamination` gives `contamination`.

| GEMStat column | Canonical field |
| --- | --- |
| `GEMS Station Number` | `local_site_id` |
| `Latitude`, `Longitude` (WGS84) | `latitude`, `longitude` |
| `Water Type` | `water_body_type` |
| `Water Body Name` | `water_body_name` |
| `Sample Date` | `time_start` (local day) |
| `Depth` (m) | `sample_depth_m` |
| `Parameter Code` | `parameter` and `fraction` (for example `Pb-Dis`, `Pb-Tot`) |
| `Analysis Method Code` | `method` |
| `Value Flags` (`<`, `>`, `~`) | `censored`, or `quality_flag = estimated` |
| `Value`, `Unit` | `value`, `unit` |
| `Data Quality` | `quality_flag` |

Limits: no station in Africa. The station metadata CSV is Latin-1 text. Kenya data in the GEMStat portal is LIMITED or RESTRICTED (not verified).

### 4.3 `cgls_lwq` (P1)

- Catalog: Digital Earth Africa STAC, `https://explorer.digitalearth.africa/stac/search`.
- Collections: `cgls_lwq300_2002_2012`, `cgls_lwq300_2016_2024`, `cgls_lwq100_2019_2024`, and NRT collections.
- A search for the Athi area in January 2011 returned 10-day items with a bbox that covers the region.
- Assets: `turbidity_mean`, `turbidity_sigma`, `trophic_state_index`, `num_obs`, `stats_valid_obs_turbidity_sum`, `stats_valid_obs_tsi_sum`, `Rw*_rep`.
- Asset files are COGs in `s3://deafrica-input-datasets` (region `af-south-1`). Unsigned public read is not verified.
- `source_key`: item id and bbox.
- `source_item_id`: STAC item id.
- `available_at`: item property `created`.
- `processing_version`: `odc:dataset_version`, for example `v1.3.0`.
- `time_precision`: `composite`. Use `start_datetime` and `end_datetime`.
- Rights: CC-BY-4.0. Attribution to the Copernicus Global Land Service, PML and Brockmann Consult.

Limits: the product covers lakes of about 50 ha or more. It does not cover rivers. The lakes near Athi-Kaputiei are few and small. The MERIS series stops in March 2012.

### 4.4 `sentinel2` water indices (P1)

- No new connector. Add the asset `B05` (red edge, 20 m) to `SENTINEL2_ASSETS` for NDCI.
- NDTI uses `B03` and `B04`. The connector fetches these bands now.
- All other fields are as in `SOURCES.md`.
- Limits: no data before June 2015. No value for the focus period.

### 4.5 `landsat_c2_l2` (P2)

- Catalog: Planetary Computer STAC, collection `landsat-c2-l2`. Platforms: Landsat 4, 5, 7, 8 and 9.
- Assets: `lwir` (ST_B6, Landsat 4–7), `lwir11` (ST_B10, Landsat 8–9), `green`, `red`, `qa_pixel`.
- Temperature in kelvin = DN × 0.00341802 + 149. Nodata is 0. These values come from the collection `raster:bands`.
- Reflectance = DN × 0.0000275 − 0.2.
- `available_at`: `landsat:processing_date` or the product creation time (not verified).
- `processing_version`: collection number and processing level from the item id.
- Limits: Landsat 5 stops in November 2011. Landsat 7 has gaps after May 2003 (SLC-off). Landsat 8 starts in 2013.
- The thermal band is 60–120 m before resampling to 30 m.

### 4.6 `sentinel3_olci_wfr` (P2)

- Catalog: Planetary Computer STAC, collection `sentinel-3-olci-wfr-l2-netcdf`.
- Assets: `chl-oc4me`, `chl-nn`, `tsm-nn`, `wqsf`, `geo-coordinates`.
- Pixel size is about 300 m. The data is on a swath, not on a grid. The normalizer reprojects with `geo-coordinates`.
- Scaling and the log10 encoding of the chlorophyll values are not verified.
- Limits: OC4ME is for open ocean water. Use `chl-nn` for inland water. Flag inland values `algorithm_ocean`.

### 4.7 `waterbase` (P2)

- Direct download: `https://sdi.eea.europa.eu/data/82e194cc-2506-411c-b341-9eb50b6ad5e0`. Version 2026, coverage 1900–2025.
- DISCODATA (`https://discodata.eea.europa.eu/`) gives SQL access with filters. The exact table name is not verified.
- License: CC-BY-4.0, copyright EEA.
- Use it as a second station test case with a different vocabulary and units.

### 4.8 `grqa` (P2)

- Zenodo record 15335450, version 1.4. Files: `GRQA_data_v1.4.zip` (1.2 GB) and `GRQA_meta_v1.4.zip`.
- The data joins CESI, GEMStat, GLORICH, Waterbase and WQP for 43 parameter forms.
- Do not load GRQA together with its own inputs in one Recipe query. The same sample would occur two times.

### 4.9 `literature_tabular` (P2)

This path reads one data file from a paper record. An agent proposes the column mapping. Deterministic code validates the mapping.

- Example: Dryad `doi:10.5061/dryad.g886d9v`, CC0. It has nutrient data from the Mara River, Kenya, with wildebeest carcass inputs.
- The Nairobi River study (PMC5559568) gives values in article tables only. Do not scrape article tables in this phase.

The agent writes a JSON mapping. The mapping names the site, time and coordinate columns, and one entry per parameter column with its source unit.

The validator accepts the mapping only when all of these checks pass:

1. Each `parameter` is in the vocabulary.
2. Each `source_unit` has a documented factor to the canonical unit.
3. Each row has a site with coordinates inside the record bbox, when the record gives a bbox.
4. Each time parses with `time_format`.
5. Each value is a number or a censored text, for example `<0.01`.
6. The row count after the mapping equals the row count of the file.

A mapping that passes stays quarantined until a person approves it. Store an approved mapping in `src/habitat/normalize/mappings/`. `mapping_version` is `agent-<sha256 of the mapping file>`.

## 5. Shape

### 5.1 SQL

Use the next free migration number. Follow the style of migrations 003 and 004.

```sql
-- site_id is namespaced by source, for example gemstat:ARG00014 or wqp:USGS-01646500.
CREATE TABLE monitoring_sites (
    site_id                   text             PRIMARY KEY,
    source_id                 text             NOT NULL,
    local_site_id             text             NOT NULL,
    site_name                 text,
    water_body_type           text             NOT NULL,
    water_body_name           text,
    longitude                 double precision NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    latitude                  double precision NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    geometry                  geometry(Point, 4326) GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)) STORED,
    cell_id                   text             REFERENCES grid_cells,
    coordinate_uncertainty_m  double precision,
    site_feature_id           text,
    site_feature_distance_m   double precision,
    upstream_area_km2         double precision,
    elevation_m               double precision,
    attributes                jsonb            NOT NULL DEFAULT '{}'
);

CREATE TABLE site_observations (
    series_id             text             NOT NULL,
    batch_key             text             NOT NULL,
    source_record_id      text             NOT NULL,
    dataset_id            text             NOT NULL,
    site_id               text             NOT NULL REFERENCES monitoring_sites,
    time_start            timestamptz      NOT NULL,
    time_end              timestamptz      NOT NULL,
    time_precision        text             NOT NULL,
    available_at          timestamptz      NOT NULL,
    longitude             double precision NOT NULL,
    latitude              double precision NOT NULL,
    cell_id               text             REFERENCES grid_cells,
    source_id             text             NOT NULL,
    source_item_id        text             NOT NULL,
    processing_version    text             NOT NULL,
    product_status        text             NOT NULL,
    mapping_version       text             NOT NULL,
    parameter             text             NOT NULL,
    fraction              text             NOT NULL,
    value                 double precision,
    unit                  text             NOT NULL,
    censored              text             NOT NULL CHECK (censored IN ('none', 'left', 'right')),
    detection_limit       double precision,
    detection_limit_type  text,
    sample_depth_m        double precision,
    method                text,
    quality_flag          text             NOT NULL,
    attributes            jsonb            NOT NULL DEFAULT '{}',
    PRIMARY KEY (series_id, batch_key, source_record_id),
    FOREIGN KEY (series_id, batch_key) REFERENCES ingest_batches (series_id, batch_key)
);

CREATE INDEX site_observations_site_time_idx ON site_observations (site_id, parameter, time_start);
CREATE INDEX site_observations_cell_time_idx ON site_observations (cell_id, time_start);
CREATE INDEX monitoring_sites_geometry_idx ON monitoring_sites USING gist (geometry);
```

Add row level security, policies and grants as migration 004 does for `animal_locations`.

`water_body_type` values: `river`, `lake`, `reservoir`, `wetland`, `canal`, `spring`, `groundwater`, `other`.
`fraction` values: `total`, `dissolved`, `suspended`, `not_applicable`.
`site_feature_id` refers to a `site_features` row from [WATER_POINTS.md](WATER_POINTS.md). There is no foreign key, because `site_features` rows have versions.

### 5.2 PyArrow

Add `SITE_OBSERVATIONS_SCHEMA` and `MONITORING_SITES_SCHEMA` to `src/habitat/contracts.py`. Add `SITE_OBSERVATIONS = "site_observations"` to `src/habitat/normalize/rows.py`.

`SITE_OBSERVATIONS_SCHEMA` has the columns of `site_observations` in the same sequence, without `series_id` and `batch_key`.
Use `UTC_TIMESTAMP` for the time columns, as `ANIMAL_LOCATIONS_SCHEMA` does.
`cell_id`, `value`, `detection_limit`, `detection_limit_type`, `sample_depth_m` and `method` are nullable.

`MONITORING_SITES_SCHEMA` has the columns of `monitoring_sites` without `geometry`. `attributes` is JSON text.

### 5.3 Parameter vocabulary

The vocabulary is a Python dict in `src/habitat/normalize/water_quality.py`. Each entry has a canonical unit and a list of accepted source units with a factor.
The normalizer converts only with a factor from this table.

| `parameter` | Canonical unit | Accepted source units and factor |
| --- | --- | --- |
| `dissolved_oxygen` | mg/L | mg/l ×1 |
| `dissolved_oxygen_saturation` | % | % ×1 |
| `bod5` | mg/L | mg/l ×1 |
| `cod` | mg/L | mg/l ×1 |
| `nitrate_n` | mg/L as N | mg/l as N ×1; mg/l as NO3 ×0.2259 (14.007/62.004) |
| `ammonium_n` | mg/L as N | mg/l as N ×1; mg/l as NH4 ×0.7765 (14.007/18.038) |
| `total_nitrogen` | mg/L as N | mg/l ×1 |
| `total_phosphorus` | mg/L as P | mg/l ×1; µg/l ×0.001 |
| `orthophosphate_p` | mg/L as P | mg/l as P ×1; mg/l as PO4 ×0.3261 (30.974/94.971) |
| `ecoli` | cfu/100mL | cfu/100ml ×1 |
| `ecoli_mpn` | MPN/100mL | MPN/100ml ×1 |
| `faecal_coliforms` | cfu/100mL | cfu/100ml ×1 |
| `conductivity` | µS/cm at 25 °C | µS/cm ×1; mS/cm ×1000; mS/m ×10 |
| `turbidity` | NTU | NTU ×1 |
| `turbidity_fnu` | FNU | FNU ×1 |
| `total_suspended_solids` | mg/L | mg/l ×1 |
| `total_dissolved_solids` | mg/L | mg/l ×1 |
| `ph` | pH | `None`, `std units`, `pH units` ×1 |
| `water_temperature` | °C | deg C ×1; deg F: (x − 32) × 5/9; K: x − 273.15 |
| `chlorophyll_a` | µg/L | µg/l ×1; mg/m3 ×1 |
| `fluoride` | mg/L | mg/l ×1 |
| `lead` | µg/L | µg/l ×1; mg/l ×1000 |
| `cadmium`, `chromium`, `arsenic`, `mercury` | µg/L | µg/l ×1; mg/l ×1000 |

Do not convert between `ecoli` and `ecoli_mpn`. Do not convert NTU to FNU. The methods are different.
Fluoride is in the list because Rift Valley groundwater has high natural fluoride.
Add a parameter only with a source code map and a test.

### 5.4 Censored values

- A value below the limit gets `censored = 'left'`. A value above the range gets `censored = 'right'`.
- For a censored row, `value` is the limit, and `detection_limit` is the same limit.
- `detection_limit_type` is `LOD`, `LOQ` or `reporting` when the source says so. Else it is null.
- When the source gives no limit, `value` is null and `quality_flag` is `censored_no_limit`.
- Never write 0 for a value below the limit.
- Convert `detection_limit` with the same factor as `value`.

### 5.5 Current-row rule

One row is current per site, parameter, fraction, sample time and sample depth.
A final value replaces a preliminary value. Then a later batch replaces an earlier batch.

The view in section 5.6 has the exact `row_number()` order.

### 5.6 `recipe_site_observations`

```sql
CREATE VIEW recipe_site_observations WITH (security_invoker = true) AS
SELECT
    dataset_id, dataset_version, access_scope, source_record_id, site_id, water_body_type, site_feature_id,
    time_start, time_end, time_precision, available_at, longitude, latitude, cell_id, parameter, fraction,
    value, unit, censored, detection_limit, sample_depth_m, product_status, quality_flag
FROM (
    SELECT
        d.dataset_id, d.version::text AS dataset_version, d.access_scope,
        o.source_record_id, o.site_id, s.water_body_type, s.site_feature_id, o.time_start, o.time_end,
        o.time_precision, o.available_at, o.longitude, o.latitude, o.cell_id, o.parameter, o.fraction,
        o.value, o.unit, o.censored, o.detection_limit, o.sample_depth_m, o.product_status, o.quality_flag,
        row_number() OVER (
            PARTITION BY d.dataset_id, d.version, d.access_scope, o.site_id, o.parameter, o.fraction,
                         o.time_start, coalesce(o.sample_depth_m, -1)
            ORDER BY (o.product_status = 'final') DESC, b.added_in_version DESC, o.available_at DESC,
                     o.source_record_id DESC
        ) AS current_rank
    FROM datasets d
    JOIN ingest_batches b
      ON b.series_id = d.dataset_id
     AND b.added_in_version <= d.version
     AND (b.superseded_in_version IS NULL OR b.superseded_in_version > d.version)
    JOIN site_observations o ON o.series_id = b.series_id AND o.batch_key = b.batch_key
    JOIN monitoring_sites s ON s.site_id = o.site_id
    WHERE d.status = 'ready' AND d.family = 'site_observations'
) ranked
WHERE current_rank = 1;
```

### 5.7 How Recipe joins a station to cells and animals

- Join by `cell_id` only when the animal is in the same cell as the station. This join is rare.
- The main join goes through the water body. Recipe finds the water point that the animal uses. Then Recipe takes the stations on the same `site_feature_id`.
- A station describes the water at its own location. It does not describe water upstream of a pollution source.
- On a river, a station downstream of Nairobi does not describe a pool upstream of the city.
- A correct river join needs a network with flow direction. Recipe must not use the nearest station along a river without that network.
- Until a network exists, Recipe joins river stations only to the same feature, within a maximum distance. Recipe must show the distance.
- On a lake or a dam, the nearest station on the same feature is acceptable.

### 5.8 Reuse for weather stations

Weather stations can use the same shape later. NOAA GHCN-Daily is an example. The station list is at `https://www.ncei.noaa.gov/pub/data/ghcn/daily/ghcnd-stations.txt`.
Use `water_body_type = 'other'` and add a `site_type` column at that time. Kenya coverage in GHCN-Daily is not verified.

## 6. Satellite proxies in `cell_observations`

| `variable` | Source | Formula or asset | Unit | Native size |
| --- | --- | --- | --- | --- |
| `ndti` | `sentinel2`, `landsat_c2_l2` | (red − green) / (red + green) | 1 | 10 m, 30 m |
| `ndci` | `sentinel2` | (B05 − B04) / (B05 + B04), B04 resampled to 20 m | 1 | 20 m |
| `water_turbidity` | `cgls_lwq` | `turbidity_mean` | NTU | 300 m, 100 m |
| `trophic_state_index` | `cgls_lwq` | `trophic_state_index` | 1 | 300 m, 100 m |
| `chlorophyll_a` | `sentinel3_olci_wfr` | `chl-nn` | mg/m3 | 300 m |
| `total_suspended_matter` | `sentinel3_olci_wfr` | `tsm-nn` | g/m3 | 300 m |
| `water_surface_temperature` | `landsat_c2_l2` | `lwir` or `lwir11` in kelvin − 273.15 | °C | 60–120 m |

NDTI is from Lacaux et al. (2007). NDCI is from Mishra and Mishra (2012). Both are relative indices, not concentrations.
Use `normalized_difference` from `src/habitat/normalize/indices.py`. Use `stat = "mean"` as the current sources do.

### 6.1 Water masking

Compute a water proxy only on water pixels. Use these masks in this sequence:

1. Clear view: `sentinel2_clear_mask` for Sentinel-2, `qa_pixel` for Landsat, `wqsf` for OLCI.
2. Water in the scene: SCL class 6 for Sentinel-2, `qa_pixel` water bit for Landsat, MNDWI > 0 for both.
3. Erode the water mask by one pixel. This step removes shore pixels with mixed land and water.
4. Optional static mask: JRC Global Surface Water `occurrence` ≥ 50 % (Planetary Computer `jrc-gsw`, 1984–2020).

`pixel_count` is the count of water pixels that pass all masks. `valid_fraction` is that count divided by all water pixels in the cell.

### 6.2 Limits for small rivers

- A pure water pixel needs a river about three pixels wide, because of the erosion step.
- Sentinel-2 at 10 m needs a river of about 30 m. At 20 m (NDCI), it needs about 60 m.
- Landsat thermal data needs a water body of about 300 m or more.
- CGLS and OLCI at 300 m see only large lakes and reservoirs.
- The width of the Athi and Nairobi rivers is mostly below 30 m (not verified). Expect no valid river pixels in most scenes.

### 6.3 Quality flags

- `ok`: the value passes all masks.
- `low_valid_fraction`: no valid pixel. This is the current flag of `quality_flags`.
- `few_water_pixels`: `pixel_count` is less than 9.
- `algorithm_ocean`: an OLCI ocean algorithm on inland water.
- `preliminary`: an NRT product.

## 7. Normalizer design

Write `src/habitat/normalize/sources/<source>.py` for each station source. Share the steps below in `src/habitat/normalize/water_quality.py`.

1. Read the station file. Build `monitoring_sites` rows with a namespaced `site_id`.
2. Reject a station without coordinates (see the quarantine cases).
3. Assign `cell_id` with the grid, as `animal_locations` does.
4. Read the result rows. Map the source parameter code to `parameter` and `fraction`.
5. Map the source unit. Apply the factor to `value` and `detection_limit`.
6. Set `censored`, `detection_limit` and `detection_limit_type`.
7. Build `time_start`, `time_end` and `time_precision` from the date, time and zone.
8. Set `quality_flag`. Keep the first flag that applies, in the sequence of the table below.
9. Put unmapped source fields in `attributes`.
10. Return a `NormalizedBatch` with `family = SITE_OBSERVATIONS` and `references = {"monitoring_sites": sites}`.

`quality_flag` values:

| Flag | Meaning |
| --- | --- |
| `contamination` | The source reports sample contamination. |
| `suspect` | The source marks the value as suspect. |
| `out_of_range` | The value is not physically possible, for example pH outside 0–14 or a negative concentration. |
| `censored_no_limit` | Below or above a limit, but the source gives no limit. |
| `estimated` | The source marks the value as estimated. |
| `preliminary` | The source status is not final. |
| `time_default` | The source time is a default value, for example GEMStat `00:00`. |
| `site_off_water` | The station is more than 500 m from all `site_features` water bodies. |
| `agent_mapped` | The mapping came from an agent and a person approved it. |
| `ok` | No issue. |

`QuarantineError` cases:

- An unknown unit, or a unit without a documented factor.
- An unknown parameter in a file that has no other mapped parameters.
- A station with missing coordinates, or coordinates of 0, 0.
- A source datum other than WGS84 without a documented transformation.
- An agent mapping that fails one validator check.

Drop single rows with an unknown parameter only when the file has mapped parameters too. Count the dropped rows in the validation report.

## 8. Fetch agent changes

New `data_kinds`:

- `water_quality_samples`: station rows. Sources: `wqp`, `gemstat`, `waterbase`, `grqa`, `literature_tabular`.
- `water_quality_observations`: satellite proxies. Sources: `sentinel2`, `landsat_c2_l2`, `cgls_lwq`, `sentinel3_olci_wfr`.

Add `water_quality_observations` to the `sentinel2` source. Add the new sources to `src/habitat/sources.py`.

Tool changes in `src/habitat/fetch/tools.py`:

- `fetch_environment`: add `cgls_lwq`, `landsat_c2_l2` and `sentinel3_olci_wfr` to the list of sources in the description.
- New tool `fetch_water_quality(bbox, start, end, sources, parameters)`. It calls the station connectors.

Text for the new tool description:

```text
Fetch water-quality samples at monitoring stations for a WGS84 bbox and inclusive YYYY-MM-DD dates.
Sources: wqp (USA), gemstat (open archive, no African stations), waterbase (Europe), grqa (global rivers).
Open station data for East Africa is very sparse. Report an empty result as a coverage gap, not an error.
```

`search_catalog` must also search Zenodo and Dryad for water-quality records when the station sources are empty.

## 9. Tests

- One recorded fixture per source in `tests/fixtures/water_quality/`. Keep each file under 100 KB.
- WQP: a 20-row WQX 3.0 CSV with a censored row, a row with a time zone, and a `None` pH unit.
- GEMStat: a 10-row CSV with `<` and `~` flags, a `00:00` time and a `Pb-Dis` and `Pb-Tot` pair.
- Unit tests for each factor in the vocabulary, including molar mass factors.
- A test that a censored row never gets the value 0.
- Quarantine tests: unknown unit, missing coordinates, failed agent mapping.
- A satellite test with a synthetic water mask: a one-pixel river gives no value after erosion.
- A SQL test for the current-row rule: a final row replaces a preliminary row of the same sample.
- One live test in `tests/test_live.py`: a small WQP query for one site and one month.

## 10. Risks and open questions

- Kenya coverage: the open sources have almost no in-situ data for the focus area and period. Ask WRA for data, or accept the gap.
- `available_at` for archives: GEMStat v3 has an `available_at` of 2026. A Recipe cutoff before 2026 excludes all GEMStat rows. This result is correct but limits backtests.
- GEMStat times are local without a zone. Should the normalizer find the zone from the coordinates? This design keeps `day` precision instead.
- GRQA has a CC-BY-4.0 license but includes GLORICH data with an NC license. Ask the GRQA authors.
- FreshWater Watch has no stated license. Do not ingest it until Earthwatch confirms reuse.
- Satellite proxies are relative. NDTI is not a turbidity in NTU. Recipe must not mix the two.
- The upstream and downstream join needs a river network. WATER_POINTS.md must say if it supplies flow direction.
- Literature mappings need human review. This step can be slow.

## 11. Build steps

1. Do the shared code changes in [README.md](README.md).
2. Add the vocabulary module and its unit tests.
3. Add the migration, the PyArrow schemas and the `site_observations` family constant.
4. Write the `wqp` connector and normalizer. Test them with the fixture.
5. Write the `gemstat` connector and normalizer.
6. Add `recipe_site_observations` and the Recipe descriptor. Update `RECIPE_INTEGRATION.md`.
7. Add NDTI and NDCI to `sentinel2`. Add `B05` to the fetched assets.
8. Write the `cgls_lwq` connector for the Digital Earth Africa STAC.
9. Write `landsat_c2_l2` water surface temperature and NDTI.
10. Add the `fetch_water_quality` tool and the new `data_kinds`.
11. Add the `literature_tabular` path with the mapping validator.
12. Add a section per source to `SOURCES.md`.

## 12. Implementation status

### 12.1 Implemented (P1)

| Part | Files |
| --- | --- |
| Tables `site_observations` and `monitoring_sites`, the view `recipe_site_observations` | `migrations/014_site_observations.sql` |
| View `recipe_water_quality_observations` for `ndti`, `ndci`, `water_turbidity` and `trophic_state_index` | `migrations/014_site_observations.sql` |
| `SITE_OBSERVATIONS_SCHEMA`, `MONITORING_SITES_SCHEMA` | `src/habitat/contracts.py` |
| Family constants `SITE_OBSERVATIONS`, `MONITORING_SITES` | `src/habitat/normalize/rows.py` |
| Reference upsert, family summary | `src/habitat/storage/series.py` |
| Parameter vocabulary, unit factors, censored values, quality flags, shared table steps | `src/habitat/normalize/water_quality.py` |
| `wqp` connector and normalizer | `src/habitat/fetch/connectors/wqp.py`, `src/habitat/normalize/sources/wqp.py` |
| `gemstat` connector and normalizer | `src/habitat/fetch/connectors/gemstat.py`, `src/habitat/normalize/sources/gemstat.py` |
| `cgls_lwq` connector and normalizer | `src/habitat/fetch/connectors/cgls_lwq.py`, `src/habitat/normalize/sources/cgls_lwq.py` |
| Sentinel-2 `ndti` and `ndci`, band B05 | `src/habitat/normalize/water_indices.py`, `src/habitat/normalize/sources/sentinel2.py` |
| Data kinds `water_quality_samples` and `water_quality_observations` | `src/habitat/sources.py` |
| Agent tool `fetch_water_quality`, `cgls_lwq` in `fetch_environment` | `src/habitat/fetch/tools.py`, `src/habitat/fetch/service.py` |
| Recipe descriptors | `src/habitat/recipe_inputs.py` |
| Source sections | `SOURCES.md` |

Decisions that the design did not state:

- The vocabulary accepts more spellings of documented units. Examples: `umho/cm` (1 µmho = 1 µS), `°C`, `---` for pH, `mg/l as NH3` (14.007/17.031), `mg/l` for chlorophyll a (×1000), `ng/l` for mercury (×0.001).
- WQP gives coordinates in NAD83. NAD83 and WGS84 differ by less than 2 m in the USA. The normalizer uses the coordinates without a transformation. `coordinate_uncertainty_m` is 2 when the source gives no accuracy.
- WQP: when one characteristic covers two parameters, the unit selects the parameter. Examples: NTU or FNU, cfu or MPN, mg/L or %.
- WQP: a value such as `<1` is a censored value with the limit 1.
- GEMStat: the connector keeps the full ZIP once. For each request it keeps an extract ZIP with the stations in the bbox and the samples in the dates. The `source_item_id` holds the bbox and the dates. Thus each area and period is a separate batch.
- GEMStat: BOD is not mapped, because the archive does not give the incubation time. The bacteria are not mapped, because the unit `1/100 ml` does not tell cfu from MPN.
- `cgls_lwq`: the asset files use the fill value 9.97e36, although the STAC item says `nan`. The normalizer removes this value.
- Water indices: `valid_fraction` is the share of the cell with valid water pixels. It is not the share of the water pixels of the cell.
- A Sentinel-2 series gives two Recipe datasets. The water-quality dataset id has the suffix `--water-quality`.
- `ConnectorRequest.parameters` limits a station source to vocabulary parameters.

### 12.2 Deferred

- P2 and P3 sources: `landsat_c2_l2`, `sentinel3_olci_wfr`, `waterbase`, `grqa`, `literature_tabular`, `glorich`, `gemstat_portal`, `freshwater_watch`, `wra_kenya`.
- The mapping validator and the human approval of agent mappings (section 4.9). The flag `agent_mapped` is not used yet.
- The flag `site_off_water` and the columns `site_feature_id` and `site_feature_distance_m`. They need `site_features` from WATER_POINTS.md.
- The optional static mask from JRC Global Surface Water (section 6.1, step 4).
- The `cgls_lwq` 100 m collection and the NRT collections.
- A Dryad search in `search_catalog`. The agent can search Zenodo now.
- A validation report with the count of dropped rows. The normalizers log the count now.

### 12.3 Limits of the strict rules

- An unknown unit quarantines the whole item. A large WQP query often has one such unit, for example `NTRU` or `MPN` without a volume. Then the agent must ask for fewer parameters or a smaller area.
- A station without coordinates quarantines the whole item.
- The GEMStat live test downloads the full ZIP of about 201 MB.

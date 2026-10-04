# Animal populations over time

Status: implemented (P1).

This document defines the family `population_counts` and its reference table `count_areas`.
It also proposes an optional reference table `species_traits`.
All shared rules are in [README.md](README.md). This document follows them.
Section 11 lists what is implemented, the decisions of the implementation, and what is deferred.

## 1. Purpose

Recipe must answer questions of this type:

- Did the migratory wildebeest of Athi-Kaputiei decline between 1977 and 2013?
- Does a population change follow a drought year, a low NDVI season or an increase of livestock?
- Is a decline in one area also present in the next area?

To answer these questions, Recipe needs counts and estimates of animals per area and per interval.
Rainfall and vegetation are already in `cell_observations`. Livestock counts are in the same surveys as wildlife counts, so `population_counts` also carries livestock.

## 2. What the current data gives, and the gap

- `animal_locations` gives GPS fixes of collared animals. One study has 10 to 50 animals.
- A track shows where an animal goes. A track does not show how many animals are in an area.
- The number of fixes in a cell depends on the number of collars and on the fix rate. It is not an index of abundance.
- `cell_observations` gives environment values. It has no animal numbers.

The gap: there is no table for "N animals of taxon T in area A during interval I, with method M and uncertainty U".
Counts come from areas of very different size: a 5 km survey block, a park, a county, or a full ecosystem.
Counts from two methods are usually not comparable. The shape must keep the method and the comparability of each row.

## 3. Sources

| `source_id` | Provider | Content | Space | Time | Taxa | Access and auth | Format | License | Priority |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `ogutu_kenya_rangelands` | Ogutu et al. 2016, PLOS ONE, S4 Data | DRSRS aerial sample survey estimates per county and survey, with SE and model estimates | 20 Kenya rangeland counties in the file (the paper says 21), Kajiado included | 1977–2016 | 18 wildlife species, 4 livestock groups | Public HTTPS, no auth | XLSX, 1 sheet | CC-BY-4.0 | P1 |
| `literature_counts` | Curated CSV in the repo, one row per published value | Values from papers and census reports, for example Ogutu et al. 2013 (Nairobi NP) and TAWIRI | Parks, ecosystems | Per paper | Per paper | No auth | CSV | Per paper | P1 |
| `living_planet` | ZSL and WWF, Living Planet Database | Vertebrate population time series | Global, point per population | 1950–2020 (not verified for the public file) | Vertebrates | Web form and data-use agreement, no API | CSV | Non-commercial use only | P2 |
| `biotime` | BioTIME 2.0, Zenodo record 15222193 | Assemblage time series with abundance per species and sample | Global, few large mammals in East Africa | Per study | All taxa | Public, no auth | SQL dump 1.18 GB, metadata CSV | CC-BY-4.0, and a license per study | P2 |
| `dryad` | Dryad API v2 | Data-paper files, mapped by an agent proposal | Per dataset | Per dataset | Per dataset | Public, no auth for metadata | Any | CC0-1.0 for all Dryad data | P2 |
| `zenodo` | Zenodo records API (connector exists) | Data-paper files, mapped by an agent proposal | Per record | Per record | Per record | Public | Any | Per record | P2 |
| `gbif_sampling_events` | GBIF API | Occurrences with `organismQuantity` from sampling-event datasets | Global | Per dataset | All taxa | Public, no auth | JSON, or Darwin Core Archive | Per dataset (CC0, CC-BY or CC-BY-NC) | P3 |
| `iucn_redlist` | IUCN Red List API v4 | Population trend class per species assessment | Global or regional assessment | Assessment year | All assessed taxa | Personal token, non-commercial use only | JSON | Red List Terms of Use | P3 |
| `ebird_status` | Cornell Lab, eBird Status and Trends | Weekly relative abundance rasters for birds | Global, 3, 9 or 27 km | Weekly, version year 2023 | Birds | Access key per user | GeoTIFF, Equal Earth projection | eBird Products Terms of Use | P3, to `cell_observations` |
| `snapshot_serengeti` | LILA BC | Camera-trap images and species labels | Serengeti NP grid of camera sites | 11 seasons (years not verified) | 61 categories | Public cloud buckets | COCO Camera Traps JSON, CSV | CDLA-Permissive | P3, to `point_events` first |
| `animal_traits`, `elton_traits`, `pantheria` | Herberstein et al. 2022; Wilman et al. 2014; Jones et al. 2009 | Body mass, diet, life history | Species level | None | Animals; birds and mammals; mammals | Public | CSV | CC0-1.0; CC-BY-4.0 or CC0 (not verified); not verified | P3, to `species_traits` |

Sources that this design drops:

- **Raw DRSRS survey data.** DRSRS gives the data on request only. There is no public download (not verified for 2026).
- **WRTI National Wildlife Census 2021 and 2025.** The reports are PDF only. Add single values through `literature_counts`.
- **TAWIRI census series.** The reports are PDF only, for example the 2023 wildebeest survey with 1,366,109 animals and SE 231,741. Add values through `literature_counts`.

## 4. Sources in detail

### 4.1 `ogutu_kenya_rangelands` (P1)

- Paper: Ogutu, Piepho, Said, Ojwang et al. 2016. "Extreme Wildlife Declines and Concurrent Increase in Livestock Numbers in Kenya: What Are the Causes?" PLOS ONE. DOI `10.1371/journal.pone.0163249`.
- The data statement says: "All relevant data are within the paper and its Supporting Information files."
- Request: `GET https://journals.plos.org/plosone/article/file?type=supplementary&id=10.1371/journal.pone.0163249.s004`. The server sends a 302 redirect to a signed Google Cloud Storage URL. The file is XLSX, about 1.1 MB.
- Method of the surveys: systematic aerial sample survey. Transects are 5 km apart, in 5 × 5 km sampling units. The mean strip width is 285 m, and the mean sampling intensity is 5.7 %.
- Archive contents: the XLSX file as downloaded. One sheet, about 12,390 rows, header on row 3.
- Columns: County, County number, Area of County (Km2), Survey Code, End Date of survey, Species, Species number, Actual number counted, Population size estimated from survey, Standard Error estimated from survey, Population estimate with 22 outliers removed, Overall Mean population size, Logarithm of mean population size, Population size estimate from model, Lower 95% Prediction Limit, Upper 95% Prediction Limit.
- Dates are Excel serial days. Survey Code has the form `YYNN`, for example `7701` (format not verified in the paper text).
- Kajiado county has 39 wildebeest surveys. Athi-Kaputiei is a part of Kajiado county.

| Item field | Value |
| --- | --- |
| `source_item_id` | `10.1371/journal.pone.0163249.s004` |
| `source_key` | `ogutu_kenya_rangelands:10.1371/journal.pone.0163249.s004` |
| `available_at` | `2016-09-27`, the online publication date from Crossref |
| `processing_version` | `sha256:<file hash>`. PLOS gives no checksum, so the connector computes it. |
| `time_precision` | `composite` |
| `kind` | `tabular` |
| `access_scope` | `public` |
| `Rights` | `license="CC-BY-4.0"`, `reuse_allowed=True`, attribution with the paper citation |

Field mapping. Each source row gives one or two `population_counts` rows:

| Source column | Target |
| --- | --- |
| County, County number | `area_id = ke_county:<number>`, `area_name`, `area_type = admin_unit` |
| Area of County (Km2) | `count_areas.area_km2`. Compare with the area of the boundary geometry. |
| End Date of survey | `time_end`. `time_start` is the same day, see section 9. |
| Survey Code | `attributes.survey_code`, and part of `source_record_id` |
| Species | `taxon_name` through a fixed table of common names, then `resolve_taxon` |
| Population size estimated from survey | Row 1: `metric = population_estimate`, `method = aerial_sample`, `value` |
| Standard Error estimated from survey | Row 1: `se` |
| Actual number counted | Row 1: `attributes.animals_counted_in_strips` |
| Population estimate with 22 outliers removed | Row 1: when the value differs from the survey estimate, `quality_flag = source_outlier` |
| Population size estimate from model | Row 2: `metric = population_estimate`, `method = model`, `value` |
| Lower and Upper 95% Prediction Limit | Row 2: `ci_low`, `ci_high`, `ci_level = 0.95` |
| Overall Mean, Logarithm of mean | `attributes`. These are series statistics, not values per survey. |

`source_record_id` is `<county number>:<survey code>:<species number>:survey` or `...:model`.
Row 1 and row 2 are in two comparability groups, because the methods differ.

Limits:

- A county is much larger than Athi-Kaputiei. Kajiado county is 21,851 km². Recipe must not assign a county total to one park.
- The source has no county geometry. The normalizer takes the geometry from a boundary source. Use geoBoundaries KEN ADM1. The geoBoundaries API gives its license as Public Domain. Store the boundary source in `count_areas.geometry_source`.
- Some species names are groups, for example "Sheep and goats". Such a row gets `gbif_taxon_key = null` and `quality_flag = taxon_unresolved`.

### 4.2 `literature_counts` (P1)

Many key values exist only in PDF reports and papers. Examples are the Nairobi NP ground counts in Ogutu et al. 2013 (DOI `10.2174/1874839201307010011`) and the TAWIRI wildebeest censuses.

- A person types each value into `reference/literature_counts/<citation_key>.csv`. One file per publication.
- Each row has the canonical columns of `population_counts` and also `page` and `table_or_figure`.
- A value read from a figure gets `attributes.read_from_figure = true` and `quality_flag = digitized_from_figure`.
- The connector copies the file into the archive. It does not use the network.

| Item field | Value |
| --- | --- |
| `source_item_id` | `<citation_key>`, for example `ogutu2013_nairobi` |
| `source_key` | `literature_counts:<citation_key>:<git blob sha>` |
| `available_at` | The publication date of the paper |
| `processing_version` | The git blob SHA of the CSV file |
| `time_precision` | `composite` |

- Rights: the license of the paper. The license of Ogutu et al. 2013 is not verified. Use `access_scope = "literature-review"` until a person checks the license.
- Limits: a person can make a typing error. Two persons check each file, and a test compares totals with the paper.

### 4.3 `living_planet` (P2)

- Download page: `https://www.livingplanetindex.org/data_portal`. The user gives a name, an email and a use description, and accepts the data-use agreement.
- There is no API. The connector cannot accept the terms, as for `movebank_study`. The user downloads the CSV and gives its path as the item.
- The public CSV excludes about 3,000 confidential populations.
- The file is wide: one row per population, one column per year (`X1950`, `X1951`, and so on). Column names are not verified for the current file.
- Each population has a binomial, a location text, a latitude, a longitude, units and a method text (field names not verified).

| Item field | Value |
| --- | --- |
| `source_item_id` | `lpd-public:<release label>` |
| `source_key` | `living_planet:<sha256 of file>` |
| `available_at` | The release date on the portal. When the portal gives no date, use the file date from the user (not verified that a release date exists). |
| `processing_version` | `sha256:<file hash>` |
| `time_precision` | `composite`, one row per population and year |
| `access_scope` | `lpi-noncommercial` |
| `Rights` | `license="LPD data use agreement"`, `reuse_allowed=False` for commercial use |

- Mapping: one row per population and year with a value. `area_id = lpd:<population id>`, `area_type = site`, point geometry.
- The `Units` and method texts are free text. A reviewed table maps each text to `metric`, `method` and `unit`. The table is part of `mapping_version`.
- Limit the request with a filter, for example `living_planet:country=Kenya`. Then the reviewed table only needs to cover the values in that subset.

### 4.4 `biotime` (P2)

- Record: `https://zenodo.org/api/records/15222193`. Version 2.0, published 2025-04-15, CC-BY-4.0.
- Files: `biotime_v2_sql_15April25.sql` (1.18 GB), `biotime_v2_query_15April25.rds` (174 MB), `biotime_v2_metadata_15April25.csv` (2.55 MB), and two reference files.
- The metadata CSV has `STUDY_ID`, `CEN_LATITUDE`, `CEN_LONGITUDE`, `AREA_SQ_KM`, `TAXA`, `START_YEAR`, `END_YEAR`, `PERMISSIONS`, `ABUNDANCE_TYPE` and `METHODS`.
- The connector downloads the metadata CSV first. It selects studies by bbox, years and taxa.
- Then it reads the SQL dump once and writes one CSV per selected study. This is the same idea as the Sentinel-2 clip to the AOI.
- `source_item_id = biotime:<STUDY_ID>`. `processing_version` is the Zenodo file checksum of the SQL dump.
- `available_at` is `2025-04-15`. `time_precision` is `composite`.
- `PERMISSIONS` gives the license per study. A non-open study gets `access_scope = "biotime-restricted"`.
- `ABUNDANCE_TYPE` gives the metric. Map "Count" to `count` and "Density" to `density` (exact values not verified).
- Limit: the 1.18 GB file is above the default `max_file_bytes` of 512 MiB. The connector needs its own limit. The SQL dialect is not verified.
- Limit: the metadata has only 5 study rows that name Kenya, Serengeti or Tanzania. None is a large mammal count.

### 4.5 `dryad` and `zenodo` with a proposed column mapping (P2)

Data papers often give one CSV with counts per site and year. No fixed normalizer can read all of them. This path uses an agent proposal and a deterministic check.

Dryad endpoints (verified on a sample record):

- Search: `GET https://datadryad.org/api/v2/search?q=<text>&per_page=20`
- Dataset: `GET https://datadryad.org/api/v2/datasets/<url-encoded doi>`. The response has `license`, `publicationDate` and a version link.
- Files: `GET https://datadryad.org/api/v2/versions/<version id>/files`. Each file has `path`, `size`, `mimeType`, `digest` and `digestType = md5`.
- Download: `GET https://datadryad.org/api/v2/files/<file id>/download` (auth need not verified).

Item fields for `dryad`:

| Item field | Value |
| --- | --- |
| `source_item_id` | `<doi>/<file path>` |
| `source_key` | `dryad:<doi>:<file path>:md5:<digest>` |
| `available_at` | `publicationDate` of the dataset version |
| `processing_version` | `md5:<digest>` |
| `Rights` | `license="CC0-1.0"`, `reuse_allowed=True` |

The `zenodo` connector already exists. It keeps the smallest file under 10 MiB. Add an option to select one file by name.

Mapping procedure:

1. The connector archives the file. The normalizer has no mapping, so the item stays in quarantine, as now.
2. The agent reads the header, 20 sample rows and the dataset description.
3. The agent proposes a `ColumnMapping` JSON: column to canonical field, constant `metric`, `method` and `unit`, date format, and area columns.
4. A deterministic validator applies the mapping to all rows. The validator accepts the mapping only when all checks pass:
   - Each required canonical field has a column or a constant.
   - `metric`, `method` and `unit` are in the controlled vocabularies.
   - 100 % of the dates parse with the given format.
   - 100 % of the values parse as numbers, and no value is negative.
   - `ci_low <= value <= ci_high` where all three exist.
   - Each area has coordinates or a geometry.
5. The validator stores the mapping in the archive. `mapping_version` is `agent-map:<sha256 of mapping JSON>`.
6. A person approves the mapping. Before approval, the catalog version has `status = quarantined`.
7. The catalog gets the tag `mapping_origin = ai`, with `origin = ai` and the model name.

The agent never writes rows. The agent only proposes the mapping. The validator and the normalizer write rows.

## 5. Shape

### 5.1 Tables

The migration is `013_population_counts.sql`, as README.md gives.

```sql
-- area_id is namespaced by source, for example ke_county:2 or lpd:1234.
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
```

Add row level security, policies and grants as in migration 004.
The pipeline upserts `count_areas` through `NormalizedBatch.references`, as README.md requires.

`comparability_group` is `<source_id>:<area_id>:<taxon>:<metric>:<method>:<unit>`, plus a protocol label when the source gives one.
Two rows in the same group are comparable. Two rows in different groups are not comparable without a model.
Recipe computes a trend only inside one group.

### 5.2 PyArrow schemas

Add `POPULATION_COUNTS_SCHEMA` and `COUNT_AREAS_SCHEMA` to `src/habitat/contracts.py`. Add `POPULATION_COUNTS = "population_counts"` to `src/habitat/normalize/rows.py`.

`COUNT_AREAS_SCHEMA` has the columns of `count_areas`, with `geometry_wkt` (string) in place of `geometry`.

```python
POPULATION_COUNTS_SCHEMA = pa.schema([
    pa.field("source_record_id", pa.string(), nullable=False),
    pa.field("dataset_id", pa.string(), nullable=False),
    pa.field("source_id", pa.string(), nullable=False),
    pa.field("source_item_id", pa.string(), nullable=False),
    pa.field("processing_version", pa.string(), nullable=False),
    pa.field("mapping_version", pa.string(), nullable=False),
    pa.field("area_id", pa.string(), nullable=False),
    pa.field("taxon_name", pa.string(), nullable=False),
    pa.field("gbif_taxon_key", pa.int64(), nullable=True),
    pa.field("time_start", UTC_TIMESTAMP, nullable=False),
    pa.field("time_end", UTC_TIMESTAMP, nullable=False),
    pa.field("time_precision", pa.string(), nullable=False),
    pa.field("available_at", UTC_TIMESTAMP, nullable=False),
    pa.field("metric", pa.string(), nullable=False),
    pa.field("method", pa.string(), nullable=False),
    pa.field("value", pa.float64(), nullable=True),
    pa.field("unit", pa.string(), nullable=False),
    pa.field("se", pa.float64(), nullable=True),
    pa.field("ci_low", pa.float64(), nullable=True),
    pa.field("ci_high", pa.float64(), nullable=True),
    pa.field("ci_level", pa.float64(), nullable=True),
    pa.field("effort_value", pa.float64(), nullable=True),
    pa.field("effort_unit", pa.string(), nullable=True),
    pa.field("comparability_group", pa.string(), nullable=False),
    pa.field("quality_flag", pa.string(), nullable=False),
    pa.field("attributes", pa.string(), nullable=False),
])
```

The table keeps the geometry as WKT text, as `DatasetVersion.footprint_wkt` does. `SeriesStore` converts it with `ST_GeomFromText(..., 4326)`.

### 5.3 Controlled vocabularies

| `metric` | Meaning | Allowed `unit` |
| --- | --- | --- |
| `count` | Animals seen in the counted part only, not scaled to the area | `individuals` |
| `population_estimate` | Total for the full area | `individuals` |
| `density` | Animals per area | `individuals_per_km2` |
| `index` | Relative value without a scale, for example an LPI series | `index` |
| `relative_abundance` | Detections per effort | `detections_per_100_trap_nights`, `individuals_per_hour`, `individuals_per_km_transect` |
| `presence` | 1 when seen, 0 when not seen | `boolean` |

| `method` | Meaning |
| --- | --- |
| `aerial_total` | Aircraft flies the full area and counts all animals |
| `aerial_sample` | Aircraft counts strips or blocks, and a ratio gives the total |
| `aerial_photo` | Count from aerial or satellite images |
| `ground_total` | Ground teams count the full area |
| `ground_transect` | Line transect or distance sampling on the ground |
| `camera_trap` | Detections from fixed cameras |
| `point_count` | Fixed observer points, mainly birds |
| `model` | Value from a statistical model of other counts |
| `compiled` | Value from a database that does not give the field method |

Any other text is an unknown method. The normalizer raises `QuarantineError` for an unknown method.
`compiled` is for LPI and BioTIME rows where the reviewed table gives no field method.

### 5.4 Current-row rule

One row per `area_id`, taxon, `metric`, `method`, `source_id`, `time_start` and `time_end` is current.
The newest catalog version wins. Then the newest `processing_version`, then the newest `mapping_version`, then the latest `available_at`, then `source_record_id`.
Rows from two sources never replace each other. Recipe sees both and chooses by `comparability_group`.

### 5.5 Recipe view

```sql
CREATE VIEW recipe_population_counts WITH (security_invoker = true) AS
SELECT
    dataset_id, dataset_version, access_scope, source_record_id, area_id, area_type, taxon_name,
    gbif_taxon_key, time_start, time_end, time_precision, available_at, metric, method, value, unit,
    se, ci_low, ci_high, ci_level, effort_value, effort_unit, comparability_group, quality_flag
FROM (
    SELECT
        d.dataset_id, d.version::text AS dataset_version, d.access_scope,
        p.source_record_id, p.area_id, a.area_type, p.taxon_name, p.gbif_taxon_key, p.time_start,
        p.time_end, p.time_precision, p.available_at, p.metric, p.method, p.value, p.unit, p.se,
        p.ci_low, p.ci_high, p.ci_level, p.effort_value, p.effort_unit, p.comparability_group,
        p.quality_flag,
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
```

Also add a table `count_area_cells (area_id, cell_id, overlap_fraction)`. `SeriesStore` fills it when it upserts an area.
`overlap_fraction` is the part of the cell inside the area. A point area gets the one cell that contains the point, with `overlap_fraction = 1`.

### 5.6 How Recipe joins

- Recipe joins an area to cells through `count_area_cells`. It never joins by a point lookup of the area centroid.
- Recipe joins the other way too: it aggregates rainfall or NDVI over the cells of an area for the count interval.
- Recipe must not spread a total evenly over the cells of an area. A county total is a value for the county only.
- Recipe must not interpolate between survey years. A gap of 4 years stays a gap.
- For a point-in-time join, Recipe uses the latest row with `time_end <= t` and `available_at <= cutoff`. Recipe also reports the age of that row.
- Recipe compares values only inside one `comparability_group`.

### 5.7 `species_traits` (P3, optional)

One row per taxon, trait and source. There is no time column.

```sql
CREATE TABLE species_traits (
    gbif_taxon_key   bigint           NOT NULL,
    taxon_name       text             NOT NULL,
    trait            text             NOT NULL,
    value            double precision,
    value_text       text,
    unit             text             NOT NULL,
    source_id        text             NOT NULL,
    source_record_id text             NOT NULL,
    mapping_version  text             NOT NULL,
    attributes       jsonb            NOT NULL DEFAULT '{}',
    PRIMARY KEY (gbif_taxon_key, trait, source_id)
);
```

- Traits: `body_mass_g`, `diet_plant_pct`, `diet_vertebrate_pct`, `diet_invertebrate_pct`, `activity_nocturnal`, and similar.
- Recipe joins it to `population_counts` by `gbif_taxon_key`.
- It is a reference table without series versions. A new source release replaces the rows of that `source_id`.
- Sources: AnimalTraits (CC0-1.0, Zenodo), EltonTraits 1.0 (Wiley figshare collection 3306933), PanTHERIA (Ecological Archives E090-184).

## 6. Normalizer design

Write one normalizer per source in `src/habitat/normalize/sources/<source>.py`. Each normalizer does these steps:

1. Read the archived file. Check the header against the expected columns. A missing column is a `QuarantineError`.
2. Map each source row to canonical rows with the mapping of the source.
3. Map `metric`, `method` and `unit` through the controlled vocabularies.
4. Build `count_areas` rows. Load the geometry from the boundary source, or build a point.
5. Resolve each taxon name once with `habitat.catalog.taxa.resolve_taxon`. Use the same pattern as `resolve_entity_taxa`.
6. Compute `comparability_group` for each row.
7. Check each group: one `metric`, one `unit`, and one `method`.
8. Set `quality_flag`.
9. Return a `NormalizedBatch` with `family = POPULATION_COUNTS` and `references = {"count_areas": table}`.

`quality_flag` values. A row gets the first value that applies:

| Value | Rule |
| --- | --- |
| `taxon_unresolved` | `resolve_taxon` gives no single species |
| `area_unlocated` | The area has no geometry |
| `source_outlier` | The source marks the value as an outlier |
| `digitized_from_figure` | A person read the value from a plot |
| `no_uncertainty` | The method is a sample or a model, and there is no SE and no CI |
| `zero_count` | `value = 0`. Zero is valid data. The flag helps Recipe to check it. |
| `ok` | No rule applies |

`QuarantineError` cases:

- An unknown `method` text, or an unknown unit text.
- One comparability group with two metrics or two units. This happens when a source mixes counts and densities in one column.
- A value that is not a number and is not empty.
- A negative value, or `ci_low > ci_high`.
- A date that does not parse, or `time_start > time_end`.
- An area without a name and without coordinates.
- A conversion between units without a documented factor. For example, density to total needs the area, and the source must give that area.

## 7. Fetch agent changes

- Add the data kinds `population_counts` and `species_traits` to the `Source` entries in `src/habitat/sources.py`.
- Add `dryad` with the data kind `external_research_data`, as for `zenodo`.
- Add a `bbox` argument and a `data_kinds` argument to `search_catalog` in `src/habitat/fetch/tools.py`.
- New tool description text:

> Search animal counts and population estimates by species and region. Resolve the species name first. Use a WGS84 bbox. Results include the method and the area type. Counts from different methods are not comparable.

- Search by species and region works in this order:
  1. Resolve the name with `resolve_taxon`. When the name is ambiguous, ask the user. Do not expand a group silently.
  2. Search the catalog for `population_counts` rows with overlap of the bbox and the time range.
  3. Check the fixed sources: `ogutu_kenya_rangelands` for Kenya, and `literature_counts` by taxon.
  4. Search Dryad and Zenodo with the species name and the region name. These results need the mapping procedure.
- The agent reports each `living_planet` or `iucn_redlist` result with its terms. The handoff excludes a non-public `access_scope`.

## 8. Tests

- `tests/fixtures/population/ogutu_s4_sample.xlsx`: 20 rows from the S4 file, Kajiado wildebeest and Narok cattle included.
- `test_ogutu_kenya_rangelands.py`: the survey row and the model row have the correct `metric`, `method`, `se`, CI and dates. The serial date 28161 becomes 1977-02-05.
- Test that "Sheep and goats" gives `taxon_unresolved` and a kept row.
- Test that an unknown method text raises `QuarantineError`.
- Test that a group with two metrics raises `QuarantineError`.
- Test that a second batch with a new `processing_version` replaces the old row in `recipe_population_counts`.
- Test that `count_area_cells` gives fractions between 0 and 1, and that the sum over cells is close to `area_km2`.
- Test the mapping validator with a good mapping, a bad date format and a bad unit.
- Test `literature_counts` totals against the values in the paper.
- `tests/test_live.py`: one live download of the S4 file. Compare the header row.

## 9. Risks and open questions

- **Survey dates.** The S4 file gives only the end date. An aerial survey takes more than one day (duration not verified). This design sets `time_start` to the same day. README.md asks for an interval for a survey.
- **Boundaries.** Kenya changed from districts to counties in 2013. The S4 file uses counties for all years. A boundary file of 2013 or later fits. Check the geoBoundaries license.
- **Scale mismatch.** The focus area, Athi-Kaputiei, is about one tenth of Kajiado county (not verified). County values show the trend of the county, not of the plains. Nairobi NP values come from `literature_counts` only.
- **Comparability.** Aerial sample counts miss animals under trees. Ground counts differ. Recipe must not join two methods into one series.
- **Free text in LPI and BioTIME.** The reviewed mapping tables need human time. Start with a subset by country.
- **Agent mapping.** A wrong mapping can pass all checks, for example a swap of two numeric columns. Human approval is mandatory.
- **IUCN trend.** A trend class is not a number. This design does not store it in `population_counts`. Open question: store it in `species_traits` with the assessment year, or drop it.
- **Livestock taxa.** Cattle in Kenya are often zebu. The fixed name table must state the taxon choice.
- **Migration number.** README.md gives the fixed number 013 to this topic.

## 10. Build steps

1. Do the shared code changes in [README.md](README.md).
2. Add the migration for `count_areas`, `population_counts`, `count_area_cells` and `recipe_population_counts`.
3. Add the schemas to `contracts.py` and the family constant to `rows.py`.
4. Add the vocabularies and the validation checks in `src/habitat/normalize/population.py`.
5. Write the `ogutu_kenya_rangelands` connector and normalizer. Add the fixture and the tests.
6. Add the Kenya county boundaries as a reference source.
7. Write `literature_counts`. Enter the Nairobi NP values from Ogutu et al. 2013 and check them.
8. Add `population_counts` to `recipe_inputs.py`, to the catalog `family` values and to `RECIPE_INTEGRATION.md`.
9. Add `dryad`, the mapping proposal and the validator.
10. Add `living_planet` and `biotime` with reviewed mapping tables for a Kenya subset.
11. Add sections to `SOURCES.md` for each source.
12. Optional: add `species_traits` from AnimalTraits.

## 11. Implementation status (P1)

### 11.1 Implemented

- Migration `013_population_counts.sql`: the tables `count_areas`, `count_area_cells` and `population_counts`, the view `recipe_population_counts`, row level security and grants.
- `POPULATION_COUNTS_SCHEMA` and `COUNT_AREAS_SCHEMA` in `src/habitat/contracts.py`.
- `POPULATION_COUNTS` and `COUNT_AREAS` in `src/habitat/normalize/rows.py`.
- `src/habitat/normalize/population.py`: the vocabularies, the checks, the comparability groups and the quality flags. Each normalizer calls `population_batch`.
- `src/habitat/area_cells.py`: the cells of an area, with the overlap fraction of each cell. The calculation uses the equal-area grid CRS.
- `upsert_count_areas` in `REFERENCE_UPSERTS`. It fills `count_area_cells` for a new area and for an area with a new geometry.
- `summarize_population_counts` in `FAMILY_SUMMARIES`. The catalog footprint is the union of the count areas.
- `ROW_GRAIN`, the `families` values of `parse_question`, the Recipe descriptor in `recipe_inputs.py`, and `RECIPE_INTEGRATION.md`.
- `ogutu_kenya_rangelands`: connector, normalizer, fixture and tests.
- `literature_counts`: file format, validator, connector, normalizer, one example file and tests.
- Fetch agent: `search_catalog` has the arguments `bbox` and `data_kinds`.
- Fetch agent: `inspect_source`, `check_access` and `download_dataset` accept `ogutu_kenya_rangelands:<item>` and `literature_counts:<citation_key>`.
- Live tests in `tests/test_live.py`: the S4 header, the county boundaries, and the GBIF keys of both sources.
- `SOURCES.md` has a section for each source.

### 11.2 Decisions of the implementation

- **Boundaries.** The connector downloads the simplified geoBoundaries KEN ADM1 file of commit `9469f09` with the S4 file. Both files are in one artifact.
- **Boundary license.** The geoBoundaries API gives the license of KEN ADM1 as Public Domain, from the RCMRD GeoPortal. Thus the access scope is `public`.
- **Machakos.** The S4 county "Machakos" has 14,225 km². This is the old Machakos district. Its geometry is the union of the geoBoundaries counties Machakos and Makueni.
- **County area.** Some rows give a different area for one county, for example 21,851 and 21,852 km² for Kajiado. `area_km2` is the most frequent value.
- **Area attributes.** `attributes.source_area_km2_values` keeps all source values. `attributes.geometry_area_km2` gives the area of the boundary.
- **Area differences.** The S4 area of Marsabit is 70,729 km². The boundary gives 75,927 km². Turkana, Tana River and Garissa also differ, by about 3 % to 9 %. A reason can be that the surveys cover a part of the county (not verified).
- **Survey days.** The S4 file gives only the end day. Rows have `time_start = time_end` and the flag `interval_unknown`, as README.md requires.
- **Flag order.** `interval_unknown` comes after `zero_count` and before `ok`. Thus no S4 row has the flag `ok`.
- **Years without a survey.** The file has model values for years without a survey. These rows have no survey code and a date of 1 June. They give only a model row, with `attributes.without_survey = true`.
- **Record ids.** A row without a survey code uses the date in `source_record_id`, for example `2:d19790601:9:model`.
- **Survey codes.** The file keeps numeric codes as numbers. The normalizer writes four digits, for example `0703`.
- **Repeated rows.** The file repeats 21 rows of one county, survey and species with other values, for example survey `0501` in Laikipia. The second row gets the suffix `#2`.
- **Repeated rows in Recipe.** The current-row rule keeps one of the two rows, the row with the highest `source_record_id`. A person must decide which value is correct.
- **Taxa.** A fixed table gives the scientific name and the GBIF key of each species name in the file. The normalizer needs no network.
- **Livestock taxa.** "Cattle" is `Bos taurus`. The GBIF backbone keeps zebu in that species. "Camel" is `Camelus dromedarius`. "Donkey" is `Equus asinus`.
- **Wildlife taxa.** "Burchell's zebra" is `Equus quagga`. "Oryx" is `Oryx beisa`. "Sheep and goats" has no key and gets `taxon_unresolved`.
- **no_uncertainty.** "A sample or a model" means the methods `aerial_sample`, `ground_transect` and `model`.
- **Literature folder.** Git ignores `data/`, and `data/` is the runtime folder. Thus the literature files are in `reference/literature_counts/`.
- **Literature metadata.** Each CSV file has a metadata file `<citation_key>.json`. It gives the citation, the DOI, the publication date, the license, `reuse_allowed`, `access_scope`, `entered_by` and `checked_by`.
- **Literature access scope.** The access scope of the item comes from the metadata file. `check_access` reports a non-public file as `restricted`.
- **Literature source key.** The source key also has the git blob SHA of the metadata file. Thus a change of the license makes a new artifact.
- **Literature areas.** `area_id` gets the prefix `literature:`. An area with coordinates is a point.
- **Literature taxa.** A row without `gbif_taxon_key` gets its key from `resolve_taxon`. A name that does not resolve gets `taxon_unresolved`.
- **Literature validator.** `uv run python -m habitat.normalize.sources.literature_counts <file>` lists all problems of a file. The normalizer quarantines a file with a problem.
- **Example file.** `ogutu2013_nairobi.csv` has 12 values from pages 22 and 23 of Ogutu et al. 2013. The values are the 1948 Game Department counts of wildebeest and zebra.
- **Example method.** The paper gives no field method for these counts. Thus the method is `compiled`.
- **Example areas.** The paper gives no coordinates for the Athi-Kaputiei Plains. These rows have the flag `area_unlocated`.
- **Example review.** No person has checked the example file. Its access scope is `literature-review`. The article states the license CC BY-NC 3.0.
- **Footprint.** `SeriesSummary` has a new optional field `footprint_wkt`. `publish_series_version` uses it in place of the union of cells.
- **Footprint speed.** The union of 500,000 cells takes about 24 s. The PostGIS union of the 20 county areas takes less than 1 s.
- **Cells.** `count_area_cells.cell_id` has no foreign key to `grid_cells`. The 20 counties have 529,034 cells.
- **Recipe view.** The view also gives `area_name` and `area_km2`.
- **Size.** The full S4 file gives 16,601 rows. Normalization takes about 1 s. The append with the cells takes about 7 s.

### 11.3 Deferred

- The P2 sources `living_planet`, `biotime` and `dryad`.
- The agent mapping proposal and its validator for `dryad` and `zenodo` (section 4.5). The tests of section 8 for the mapping validator apply to the literature validator now.
- The P3 sources `gbif_sampling_events`, `iucn_redlist`, `ebird_status`, `snapshot_serengeti`, `animal_traits`, `elton_traits` and `pantheria`.
- The table `species_traits` and the data kind `species_traits` (P3).
- A Recipe operation that joins an area to cells through `count_area_cells`. The Recipe lane owns this change.
- A second person must check `ogutu2013_nairobi.csv` and its license. Then the access scope can change to `public`.
- The TAWIRI and WRTI census values. Enter them through `literature_counts`.

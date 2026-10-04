# Sources

This document describes each source of the `habitat` package: how to fetch it, what the archive keeps, and the limits.
For the package layout and the data flow, see `MERGE_PLAN.md`.

## Summary

| `source_id` | Dataset id for the agent | Fetch input | Archived files | Normalized to |
| --- | --- | --- | --- | --- |
| `sentinel2` | none (use `fetch_environment`) | bbox, dates | 5 clipped GeoTIFF bands per scene | `cell_observations` |
| `modis_mod13q1` | none (use `fetch_environment`) | bbox, dates | 3 clipped GeoTIFF layers per composite | `cell_observations` |
| `chirps` | none (use `fetch_environment`) | bbox, dates | 1 `.tif.gz` per day | `cell_observations` |
| `landsat_c2_l2` | none (use `fetch_environment`) | bbox, dates | 5 clipped GeoTIFF bands per scene | `cell_observations` |
| `esa_cci_lc` | none (use `fetch_environment`) | bbox, dates | 2 clipped GeoTIFF layers per map year and tile | `cell_observations` |
| `io_lulc_annual` | none (use `fetch_environment`) | bbox, dates | 1 clipped GeoTIFF per map year and tile | `cell_observations` |
| `modis_mcd64a1` | none (use `fetch_environment`) | bbox, dates | 1 clipped GeoTIFF per month | `cell_observations` |
| `vegetation_annual_derived` | none (use `derive_habitat_indicators`) | bbox, years | 1 `inputs.json` per year | `cell_observations` |
| `vegetation_trend_derived` | none (use `derive_habitat_indicators`) | bbox, years | 1 `inputs.json` per window | `cell_observations` |
| `movebank_repository` | `movebank-repository:<uuid>` | package UUID | Location CSV and reference CSV | `animal_locations` |
| `movebank_study` | `movebank:<study_id>` | study id | 1 CSV | `animal_locations` |
| `zenodo` | `zenodo:<record_id>` | record id | 1 file | quarantined (no mapping) |
| `fixture` | `fixture-movement-001`, `fixture-rainfall-001` | fixture id | 1 CSV | quarantined (synthetic) |

General limits:

- Coordinates are west, south, east, north in WGS84 degrees. Split a region that crosses the antimeridian.
- Dates are inclusive `YYYY-MM-DD` days.
- Defaults per connector run: 10 scenes, 31 CHIRPS days, 512 MiB per file, 2 GiB in total. The agent tool `fetch_environment` uses 1 scene and 3 days unless the agent asks for more.
- A scene limit or a day limit gives a warning. The result is then a bounded sample, not complete coverage.
- A cache hit uses the archived file. The cache does not check the provider for a newer revision.

## sentinel2

- Catalog: Planetary Computer STAC, collection `sentinel-2-l2a`.
- The search uses `eo:cloud_cover <= 80` by default. A cloud filter does not give cloud-free pixels.
- The connector signs the asset URLs and reads only the AOI window of each COG. It never downloads a full tile.
- Assets: `green` B03, `red` B04, `nir` B08, `swir16` B11, `scl` SCL.
- `processing_version` is the processing baseline, for example `05.10`. `properties.boa_add_offset` is −1000 from baseline 04.00, else 0.
- `available_at` is `s2:generation_time`. The connector skips a scene without a publication date. It never uses the retrieval time.
- `source_key` holds the item id and the bbox, because the clipped file depends on the bbox.
- One clipped scene for a 0.3° × 0.3° area is about 60 MB. The full tiles are about 3.4 GB.

## modis_mod13q1

- Catalog: Planetary Computer STAC, collection `modis-13Q1-061`.
- The connector keeps only items with an id that starts with `MOD13Q1.` (Terra). Old records have an empty platform field, so the id is the filter.
- Assets: `ndvi`, `evi`, `pixel_reliability`, clipped to the AOI.
- `processing_version` is `<collection>.<production time>` from the item id.
- `available_at` is the item `created` date. The Planetary Computer items have no `created` date, so the connector uses the production time from the item id.
- The value covers a 16-day composite window. It is not a daily observation.

## chirps

- Provider: Climate Hazards Center, UC Santa Barbara. Product CHIRPS v2.0 daily, 0.05°.
- For each day the connector asks for the final file first, then the preliminary file. When neither exists, there is no manifest.
- `source_key` holds the product status, so a final file later makes a new artifact.
- The archive keeps the `.tif.gz` file as downloaded.
- Coverage is 50°S to 50°N. A region outside this band gives a warning and no request.
- `available_at` is the `Last-Modified` time of the file.
- License: CC-BY-4.0. Cite Funk et al. 2015.

## landsat_c2_l2

- Catalog: Planetary Computer STAC, collection `landsat-c2-l2`. The search uses `eo:cloud_cover <= 80` by default.
- Assets: `blue`, `red`, `nir08`, `swir16` and `qa_pixel`, clipped to the AOI. All are 30 m.
- Reflectance is `DN × 0.0000275 − 0.2`. DN 0 is no data. A reflectance outside 0 to 1 is removed.
- A pixel is valid only when the QA_PIXEL bit 6 (clear) is 1 and the bits 0, 1, 3, 4 and 5 are 0.
- Variables: `ndvi`, `ndmi` and `bare_soil_index`, as cell means.
- `processing_version` is `<collection number>.<collection category>`, for example `02.T1`.
- `available_at` is the item `created` date. The connector skips an item without `created`.
- The normalizer quarantines an item that is not Collection 2, or that has a different scale or offset.
- Landsat 7 values after 2003-05-31 have the flag `slc_off`. Tier 2 values have the flag `tier2_geometry`.
- TM, ETM+ and OLI have different band responses. Do not mix platforms in one trend without a correction.

## esa_cci_lc

- Catalog: Planetary Computer STAC, collection `esa-cci-lc`. One item is one map year and one 45° tile.
- Assets: `lccs_class` and `processed_flag`. The raster is EPSG:4326 at 300 m. The normalizer warps it to the grid CRS.
- Only pixels with `processed_flag = 1` are valid.
- Variables: `landcover_fraction_<class>` for `tree`, `rangeland`, `cropland`, `wetland`, `built`, `bare`, `water` and `snow`.
- The class table is in `src/habitat/normalize/sources/landcover.py`. An unknown class code goes to quarantine.
- `processing_version` is `esa_cci_lc:version`: `v2.0.7cds` for 1992–2015 and `v2.1.1` for 2016–2020. Do not compare the two versions as a land change.
- `available_at` is the item `created` date.
- The licence on Planetary Computer is "proprietary". Confirm the reuse terms before publication.

## io_lulc_annual

- Catalog: Planetary Computer STAC, collection `io-lulc-annual-v02`. One item is one map year (2017–2023) and one UTM tile.
- The search returns the tiles of UTM zones 1 and 60 everywhere. The connector keeps only the tiles of the UTM zones of the bbox.
- Asset: `data`, 10 m. Class 0 (no data) and class 10 (clouds) are no data.
- Variables: the same `landcover_fraction_<class>` variables as `esa_cci_lc`. `rangeland` is IO class 11.
- The items have no `created` date. `available_at` is the creation time of the file in the Planetary Computer storage (`x-ms-creation-time`, else `Last-Modified`). The connector skips a tile without this date.

## modis_mcd64a1

- Catalog: Planetary Computer STAC, collection `modis-64A1-061`. The connector keeps only items with an id that starts with `MCD64A1.`.
- Asset: `Burn_Date`, 500 m, sinusoidal.
- `Burn_Date` 1–366 is a burn day, 0 is unburned land, −1 is unmapped and −2 is water.
- Variable: `burned_fraction`, the share of mapped pixels with a burn in the month.
- A `Burn_Date` value outside −2 to 366 goes to quarantine.
- `processing_version` and `available_at` follow the MOD13Q1 rules.
- MCD64A1 does not see burns smaller than about 500 m.

## vegetation_annual_derived and vegetation_trend_derived

- These sources download nothing. The agent tool `derive_habitat_indicators` computes them from the stored `modis_mod13q1` and `chirps` series.
- `vegetation_annual_derived` gives one batch per year: `ndvi_annual_mean`, `ndvi_annual_integral`, `ndvi_seasonal_amplitude`, `ndvi_dry_floor` and `rain_annual_mm`.
- A year needs 16 valid composites of 23. `rain_annual_mm` needs every day of the year.
- `vegetation_trend_derived` gives one batch per window: the NDVI trend, the rain-use efficiency and RESTREND.
- `properties.inputs` of the manifest gives the dataset id, the dataset version, the mapping version and the variable of each input.
- `available_at` of a value is the latest `available_at` of its inputs.
- The values are indicators. They do not say that a cell is degraded.
- The method and the flags are in `docs/ingestion/HABITAT_DEGRADATION.md`.

## movebank_repository

- Provider: Movebank Data Repository (DSpace API). Packages are public, with a DOI, a license and a citation. No login.
- Find a package with `habitat.fetch.connectors.movebank_repository.search_data_packages("Connochaetes taurinus")`, or with the agent tool `search_catalog`.
- The package id must be a UUID.
- One manifest per location file. The reference file goes into the same artifact.
- `processing_version` is the MD5 of the location file from the repository.
- `available_at` is the publication date of the package.

## movebank_study

- Provider: Movebank direct-read API, by study.
- With `MOVEBANK_USERNAME` and `MOVEBANK_PASSWORD`: the full GPS event CSV. The manifest has `access_scope = "movebank-account"`. The agent handoff excludes it.
- Credentials permit an attempt only. A study can need the permission of its owner or a license acceptance on the Movebank site. The connector does not accept license terms.
- Without credentials: a public preview of the two known public studies, at most one event per animal. A preview cannot show movement. The agent gets a warning.
- Without credentials, search covers a demo index of two studies, not all studies.
- `properties.movebank_download_mode` is `full_csv` or `public_preview`.
- The archive keeps CSV only. The preview JSON becomes a CSV with the direct-read column names.

## zenodo

- Provider: Zenodo records API.
- The connector selects one file per record: the smallest CSV, JSON, TXT or ZIP file under 10 MiB.
- The file can have any format. There is no canonical mapping, so the pipeline quarantines it.
- Search Zenodo only when Movebank has no match. Zenodo search is slower.

## fixture

- Synthetic antelope tracks and rainfall cells in `tests/fixtures/`.
- Use them only for demos and tests. The pipeline quarantines them and never publishes them.

## Provider references

- [Movebank API](https://github.com/movebank/movebank-api-doc/blob/master/movebank-api.md)
- [Movebank Data Repository](https://datarepository.movebank.org/)
- [Sentinel-2 STAC collection](https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-2-l2a)
- [MODIS STAC collection](https://planetarycomputer.microsoft.com/api/stac/v1/collections/modis-13Q1-061)
- [Planetary Computer signing](https://planetarycomputer.microsoft.com/docs/concepts/sas/)
- [Landsat C2 L2 STAC collection](https://planetarycomputer.microsoft.com/api/stac/v1/collections/landsat-c2-l2)
- [ESA CCI LC STAC collection](https://planetarycomputer.microsoft.com/api/stac/v1/collections/esa-cci-lc)
- [IO annual LULC v02 STAC collection](https://planetarycomputer.microsoft.com/api/stac/v1/collections/io-lulc-annual-v02)
- [MODIS MCD64A1 STAC collection](https://planetarycomputer.microsoft.com/api/stac/v1/collections/modis-64A1-061)
- [CHIRPS download directory](https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_daily/tifs/p05/)
- [CHIRPS product documentation](https://chc.ucsb.edu/data/chirps)
- [Zenodo REST API](https://developers.zenodo.org/)

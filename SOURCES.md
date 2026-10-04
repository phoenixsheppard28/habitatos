# Sources

This document describes each source of the `habitat` package: how to fetch it, what the archive keeps, and the limits.
For the package layout and the data flow, see `MERGE_PLAN.md`.

## Summary

| `source_id` | Dataset id for the agent | Fetch input | Archived files | Normalized to |
| --- | --- | --- | --- | --- |
| `sentinel2` | none (use `fetch_environment`) | bbox, dates | 5 clipped GeoTIFF bands per scene | `cell_observations` |
| `modis_mod13q1` | none (use `fetch_environment`) | bbox, dates | 3 clipped GeoTIFF layers per composite | `cell_observations` |
| `chirps` | none (use `fetch_environment`) | bbox, dates | 1 `.tif.gz` per day | `cell_observations` |
| `movebank_repository` | `movebank-repository:<uuid>` | package UUID | Location CSV and reference CSV | `animal_locations` |
| `movebank_study` | `movebank:<study_id>` | study id | 1 CSV | `animal_locations` |
| `zenodo` | `zenodo:<record_id>` | record id | 1 file | quarantined (no mapping) |
| `fixture` | `fixture-movement-001`, `fixture-rainfall-001` | fixture id | 1 CSV | quarantined (synthetic) |
| `firms_modis` | none (use `fetch_events`) | bbox, dates | 1 FIRMS country CSV per country and year | `point_events` |
| `firms_viirs` | none (use `fetch_events`) | bbox, dates | 1 FIRMS country CSV per country and year | `point_events` |
| `gbif_occurrence` | none (use `fetch_events`) | bbox, dates, optional taxa or dataset key | JSON search pages and `datasets.json` | `point_events` |

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

## firms_modis and firms_viirs

- Provider: NASA FIRMS, standard product. `firms_modis` is MODIS Terra and Aqua. `firms_viirs` is VIIRS S-NPP at 375 m.
- The connector reads the yearly country files. These files need no MAP_KEY.
- `src/habitat/fetch/connectors/firms_countries.json` gives the land boxes of each FIRMS country. The boxes come from Natural Earth 1:50m admin-0, with a 0.05° margin.
- The connector gets the file of each country that overlaps the bbox, for each year of the dates.
- The current year has no file. A missing file gives a warning.
- A file with no detection in the bbox gives a warning and no manifest.
- `source_item_id` is `firms:<instrument>:<Country>:<year>:<bbox>`. The normalizer keeps only the rows in the bbox.
- `processing_version` is the `version` column, for example `6.2` (MODIS) or `2` (VIIRS).
- `available_at` is the `Last-Modified` time of the file. A file without this header is skipped. The 2012 files have dates in 2024 and 2025.
- One row is one fire pixel in one satellite overpass. `value` is the fire radiative power in MW.
- `quality_flag` is `low_confidence` for MODIS confidence below 30 and VIIRS confidence `l`. It is `non_vegetation_fire` when `type` is not 0.
- `max_days` shortens the date range. A year needs `max_days = 366`.
- The FIRMS area API (MAP_KEY) is not implemented.
- License: NASA open data. NASA asks for an acknowledgment of FIRMS.

## gbif_occurrence

- Provider: GBIF occurrence search API. It includes iNaturalist research-grade and eBird records.
- The search uses the bbox polygon, the dates, `hasCoordinate=true`, optional taxon keys and an optional dataset key.
- The connector reads at most `max_records` records (default 1,000, maximum 10,000) in pages of 300.
- When GBIF has more records, the result is a bounded sample with a warning. Download mode is not implemented.
- The archive keeps the JSON pages as downloaded. `datasets.json` gives the title, `pubDate`, license and DOI of each dataset.
- `processing_version` is the SHA-256 of the pages. A later search of the same query makes a new batch. The current-row rule keeps one row per `gbifID`.
- `available_at` is the record `modified` date. When that date is missing or before the end of the event, the normalizer uses the dataset `pubDate`, then the record `lastCrawled` time. Both fallbacks have the flag `available_at_from_dataset`.
- `taxon_name` and `gbif_taxon_key` are the species name and key. A record above species rank gets the most specific rank.
- A time without an offset is local time. The row then has the local date with precision `day`.
- The record license must be CC0 1.0, CC-BY 4.0 or CC-BY-NC 4.0. Another license quarantines the item.
- `Rights.license` is the most restrictive record license.
- Records of Global Roadkill Data get `event_type = wildlife_mortality`. Search them with the agent tool `fetch_events`.
- GBIF records are presence-only. A missing record is not an absence.

## Derived event counts

- `habitat.event_counts.derive_event_counts` counts the current events of one event series per cell.
- FIRMS gives `fire_count` and `fire_frp_sum_mw` per UTC day, with the source id `firms_modis_derived` or `firms_viirs_derived`.
- GBIF gives `occurrence_count` and `occurrence_effort_count` per month for one taxon key, with the source id `gbif_occurrence_derived`.
- The counts use only rows with the flag `ok` or `available_at_from_dataset`.
- Each run records its inputs in `properties.inputs` and supersedes the previous run.

## Provider references

- [Movebank API](https://github.com/movebank/movebank-api-doc/blob/master/movebank-api.md)
- [Movebank Data Repository](https://datarepository.movebank.org/)
- [Sentinel-2 STAC collection](https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-2-l2a)
- [MODIS STAC collection](https://planetarycomputer.microsoft.com/api/stac/v1/collections/modis-13Q1-061)
- [Planetary Computer signing](https://planetarycomputer.microsoft.com/docs/concepts/sas/)
- [CHIRPS download directory](https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_daily/tifs/p05/)
- [CHIRPS product documentation](https://chc.ucsb.edu/data/chirps)
- [Zenodo REST API](https://developers.zenodo.org/)
- [NASA FIRMS country files](https://firms.modaps.eosdis.nasa.gov/country/)
- [NASA FIRMS FAQ](https://www.earthdata.nasa.gov/data/tools/firms/faq)
- [GBIF occurrence API](https://techdocs.gbif.org/en/openapi/v1/occurrence)
- [Natural Earth admin-0 countries](https://www.naturalearthdata.com/downloads/50m-cultural-vectors/)

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
| `osm_overpass` | none (use `fetch_water`) | bbox, dates | 1 Overpass JSON response per bbox and snapshot | `site_features` |
| `wpdx` | none (use `fetch_water`) | bbox, dates | 1 JSON array of WPdx+ rows per bbox | `site_features` |
| `jrc_gsw_monthly` | none (use `fetch_water`) | bbox, dates | 1 clipped GeoTIFF per month and 10° tile | `cell_observations` |
| `water_derived` | none (derived after a water fetch) | bbox, dates | 1 JSON list of input batches per bbox and month | `cell_observations` |

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

## osm_overpass

- Provider: OpenStreetMap, through the Overpass API at `https://overpass-api.de/api/interpreter`. No login.
- One POST request per bbox and snapshot. The query asks for rivers, streams, canals, lakes, ponds, reservoirs, wetlands, springs, dams, weirs, wells, taps and water points.
- The snapshot is the end of the last requested day. When the last day is today or later, the query asks for the current state.
- OSM history starts 2012-09-12. An earlier end date gives the oldest snapshot and a warning.
- The archive keeps the JSON response as received. An HTML overload page or a `remark` with a runtime error gives a retryable error, not an empty result.
- `available_at` of the item is `osm3s.timestamp_osm_base`. Each row has the `timestamp` of its element version.
- `processing_version` is the snapshot time, or `timestamp_osm_base` for the current state.
- A past snapshot is cached by its source key. A current query always asks the server.
- License: ODbL-1.0, "© OpenStreetMap contributors".
- Limits: the OSM date is a mapping date, not a construction date. The public server is often busy.

## wpdx

- Provider: Water Point Data Exchange, dataset WPdx+ (`eqje-vguj`), through the Socrata API. No login. The connector sends no app token.
- The request filters on the bbox and drops rows without coordinates. It reads pages of 50,000 rows.
- The archive keeps all rows as one JSON array.
- `processing_version` and `available_at` are the latest `updated` value of the rows. Each row has its own `updated`.
- The dates of the request do not filter the rows. `report_date` gives the time of each row.
- License: CC-BY-4.0.
- Limits: WPdx records human water supply. It does not record wildlife troughs or pans.

## jrc_gsw_monthly

- Provider: EC Joint Research Centre, Global Surface Water monthly history v1.4, 30 m. No login.
- One file per month and 10° tile. The connector reads only the bbox window with HTTP range requests.
- Pixel values: 0 no data, 1 not water, 2 water. Another value or a CRS other than EPSG:4326 goes to quarantine.
- The months are 1984-03 to 2021-12. Later months give a warning and no request.
- `max_items` limits the number of month tiles. `fetch_water` uses 12.
- `available_at` is the `Last-Modified` time of the file. `processing_version` is `1.4@<Last-Modified>`.
- Variables: `surface_water_fraction` (water pixels ÷ observed pixels) and `distance_to_surface_water_m` (cell centre to the nearest water pixel).
- License: CC-BY-4.0. Cite Pekel et al. 2016, Nature 540, 418–422.

## water_derived

- No provider. The pipeline computes these values after it appends `osm_overpass`, `wpdx` or `jrc_gsw_monthly` rows.
- Run it alone with `uv run python -m habitat.derive.water --bbox W,S,E,N --start YYYY-MM-DD --end YYYY-MM-DD`.
- Variables per cell and month: `distance_to_water_m`, `distance_to_permanent_water_m`, `distance_to_natural_water_m`, `distance_to_artificial_water_m` and `water_point_density`.
- The step reads features in the bbox plus 20 km. A value beyond 20 km is null.
- Unchanged inputs give the same batch key. Changed inputs make a new batch that supersedes the old one.
- The rights are those of the inputs. With OSM input the license is ODbL-1.0.

## Provider references

- [Movebank API](https://github.com/movebank/movebank-api-doc/blob/master/movebank-api.md)
- [Movebank Data Repository](https://datarepository.movebank.org/)
- [Sentinel-2 STAC collection](https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-2-l2a)
- [MODIS STAC collection](https://planetarycomputer.microsoft.com/api/stac/v1/collections/modis-13Q1-061)
- [Planetary Computer signing](https://planetarycomputer.microsoft.com/docs/concepts/sas/)
- [CHIRPS download directory](https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_daily/tifs/p05/)
- [CHIRPS product documentation](https://chc.ucsb.edu/data/chirps)
- [Zenodo REST API](https://developers.zenodo.org/)
- [Overpass API](https://wiki.openstreetmap.org/wiki/Overpass_API)
- [WPdx+ dataset](https://data.waterpointdata.org/dataset/Water-Point-Data-Exchange-Plus-WPdx-/eqje-vguj)
- [JRC Global Surface Water downloads](https://global-surface-water.appspot.com/download)

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
| `wqp` | none (use `fetch_water_quality`) | bbox, dates, parameters | Result CSV and station CSV | `site_observations` |
| `gemstat` | none (use `fetch_water_quality`) | bbox, dates | Full ZIP once, and 1 extract ZIP per request | `site_observations` |
| `cgls_lwq` | none (use `fetch_environment`) | bbox, dates | 2 clipped GeoTIFF layers per 10-day composite | `cell_observations` |

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
- Assets: `green` B03, `red` B04, `nir` B08, `swir16` B11, `scl` SCL, `rededge` B05.
- Water indices: `ndti` from B04 and B03 at 10 m, and `ndci` from B05 and B04 at 20 m. Both use only water pixels: a clear view, SCL class 6 and MNDWI above 0, eroded by one pixel.
- A water index needs one valid water pixel. A cell value from fewer than 9 water pixels gets the flag `few_water_pixels`.
- An archived scene without B05 still gives `ndvi`, `mndwi`, `ndmi` and `ndti`. It gives no `ndci`.
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

## wqp

- Provider: Water Quality Portal (USGS, EPA, NWQMC). WQX 3.0 paths, no login.
- Result search: `https://www.waterqualitydata.us/wqx3/Result/search` with `dataProfile=fullPhysChem` and `mimeType=csv`. Station search: `.../wqx3/Station/search`.
- Query: `bBox`, `startDateLo`, `startDateHi` (`MM-DD-YYYY`), and one `characteristicName` parameter per name. A list with `;` returns only the first name.
- The connector asks only for the characteristic names of the parameter vocabulary. The agent can limit them with `parameters`.
- `source_key` is a hash of the query. `source_item_id` is `wqp:<bbox>:<start>:<end>:<hash of the names>`.
- `available_at` of a row is its `LastChangeDate`. The value is a date or a text such as `Fri Jan 31 08:59:46 UTC 2025`.
- `processing_version` is the latest `LastChangeDate` in the file. `product_status` is `final` only when every row is `Final` or `Accepted`.
- An empty result is a coverage gap, not an error. The Water Quality Portal has no station in Kenya.
- Coordinates are NAD83. NAD83 and WGS84 differ by less than 2 m in the USA. The normalizer uses the coordinates without a transformation and sets `coordinate_uncertainty_m` to 2 when the source gives no accuracy. Another datum quarantines the item.
- An unknown unit quarantines the item. Examples are `NTRU` for turbidity and `MPN` without a volume for E. coli.
- Rights: U.S. public domain. Cite `https://doi.org/10.5066/P9QRKUVJ`.

## gemstat

- Provider: UNEP GEMS/Water Data Centre (BfG). Zenodo record `18459694`, concept DOI `10.5281/zenodo.13881899`, file `GFQA_v3.zip` (about 201 MB).
- The connector downloads the ZIP once. The archive keeps it with the `source_key` `gemstat:<record>:<md5>`. The MD5 must match the Zenodo checksum.
- For each request the connector also keeps an extract ZIP. The extract holds the stations in the bbox and their sample rows in the dates. The rows do not change.
- `source_item_id` is `gemstat:<record>:<bbox>:<start>:<end>`. Each area and period is thus a separate batch.
- `available_at` is the Zenodo publication date (v3: 2026-02-02). `processing_version` is the version label from the file name, for example `v3`.
- All CSV files are Latin-1 text. Sample times are local, without a zone. A row is a UTC day with `time_precision = day`. The local time is in `attributes.local_sample_time`.
- The open archive has no station in Africa. A request for Kenya gives a coverage gap warning and no manifest.
- BOD and the bacteria are not mapped. GEMStat does not give the BOD incubation time, and the unit `1/100 ml` does not tell cfu from MPN.
- Rights: CC-BY-4.0.

## cgls_lwq

- Provider: Copernicus Global Land Service lake water quality, 300 m, through the Digital Earth Africa STAC catalog `https://explorer.digitalearth.africa/stac`.
- Collections: `cgls_lwq300_2002_2012` (MERIS) and `cgls_lwq300_2016_2024` (OLCI). The 100 m and NRT collections are not used.
- Assets: `turbidity_mean` (NTU) and `trophic_state_index`. The connector changes the `s3://deafrica-input-datasets/` links to public HTTPS links and reads only the AOI window.
- The files use the fill value 9.97e36, although the STAC item says `nan`. The normalizer removes the fill value.
- Variables: `water_turbidity` (NTU) and `trophic_state_index` (index). A cell value needs one valid pixel.
- `time_precision` is `composite`, with `start_datetime` and `end_datetime` of the 10-day period.
- `available_at` is the item property `created`. `processing_version` is `odc:dataset_version`, for example `v1.3.0`.
- The product covers lakes of about 50 ha or more. It does not cover rivers.
- Rights: CC-BY-4.0. Attribution to the Copernicus Global Land Service, PML and Brockmann Consult.

## Provider references

- [Movebank API](https://github.com/movebank/movebank-api-doc/blob/master/movebank-api.md)
- [Movebank Data Repository](https://datarepository.movebank.org/)
- [Sentinel-2 STAC collection](https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-2-l2a)
- [MODIS STAC collection](https://planetarycomputer.microsoft.com/api/stac/v1/collections/modis-13Q1-061)
- [Planetary Computer signing](https://planetarycomputer.microsoft.com/docs/concepts/sas/)
- [CHIRPS download directory](https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_daily/tifs/p05/)
- [CHIRPS product documentation](https://chc.ucsb.edu/data/chirps)
- [Zenodo REST API](https://developers.zenodo.org/)
- [Water Quality Portal web services](https://www.waterqualitydata.us/webservices_documentation/)
- [GEMStat archive on Zenodo](https://zenodo.org/records/18459694)
- [Digital Earth Africa STAC](https://explorer.digitalearth.africa/stac)

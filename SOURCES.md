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
- `available_at` is `s2:generation_time`.
- `source_key` holds the item id and the bbox, because the clipped file depends on the bbox.
- One clipped scene for a 0.3° × 0.3° area is about 60 MB. The full tiles are about 3.4 GB.

## modis_mod13q1

- Catalog: Planetary Computer STAC, collection `modis-13Q1-061`.
- The connector keeps only items with an id that starts with `MOD13Q1.` (Terra). Old records have an empty platform field, so the id is the filter.
- Assets: `ndvi`, `evi`, `pixel_reliability`, clipped to the AOI.
- `processing_version` is `<collection>.<production time>` from the item id.
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

## Provider references

- [Movebank API](https://github.com/movebank/movebank-api-doc/blob/master/movebank-api.md)
- [Movebank Data Repository](https://datarepository.movebank.org/)
- [Sentinel-2 STAC collection](https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-2-l2a)
- [MODIS STAC collection](https://planetarycomputer.microsoft.com/api/stac/v1/collections/modis-13Q1-061)
- [Planetary Computer signing](https://planetarycomputer.microsoft.com/docs/concepts/sas/)
- [CHIRPS download directory](https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_daily/tifs/p05/)
- [CHIRPS product documentation](https://chc.ucsb.edu/data/chirps)
- [Zenodo REST API](https://developers.zenodo.org/)

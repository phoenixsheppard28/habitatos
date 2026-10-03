⏺ cc-plugin-agents-md: no CLAUDE.md found; AGENTS.md loaded: /Users/phoenixsheppard/AGENTS.md

❯ read the readme, understand this project

Read 1 file, listed 1 directory

Habitat Watch (repo dir: habitatos) is a spec-only project. No code exists yet, just README.md, which describes what to build and how to split the work.

What it is: A self-service ecological data platform. A user asks about a species, region and time period. The system finds public data, combines animal tracking (Movebank) with rainfall and satellite vegetation data, and returns an evidence-backed answer with maps and timelines. Target users are conservation groups, researchers and land managers.

Three capabilities:

- Analyze history: deterministic calculations on movement patterns. The system separates sampled animals from population-wide claims.
- Forecast: conditional predictions from fitted models, not from the LLM. Every forecast is evaluated on a later held-out period against a simple baseline. If the evidence is too thin, the system says so.
- Find new data: a retrieval agent searches catalogs and APIs, checks coverage and permissions, and imports usable records.

Design principles:

- The database is a cache and online repositories are external storage. The system reuses fresh records and fetches only what is missing.
- Agents choose sources, mappings and methods. Typed specs, bounded tools and deterministic code do the work, and every handoff passes machine validation.
- Rights and licenses are tracked per dataset. Unknown rights are null, never assumed granted.
- The system never silently broadens dates or species to fake coverage, and it returns explicit pending or insufficient-data statuses.
- Raw bytes are always preserved. Large imagery and big tables stay out of LLM context.

Pipeline: QuerySpec → catalog check → Fetch (raw archive and manifest) → Normalize (canonical datasets: animal_locations, rainfall_observations, vegetation_observations) → Recipe (typed, versioned, cached feature dataset) → Analysis (evidence report and optional model).

Stack: Python, PostgreSQL with JSONB staging, Parquet files (local first, object storage later), and a durable job table with a worker.

Four contributor lanes, all currently Unclaimed:

1. Fetch: src/fetch/
2. Normalize: src/normalize/, src/storage/, contracts/, migrations/. This lane also owns the shared contracts.
3. Recipe: src/recipe/
4. Analysis and workflow: src/analysis/, src/workflow/, tests/integration/. This lane coordinates integration.

Each lane has inputs, outputs, bounded tools, acceptance criteria and a ready-made agent handoff prompt.

Integration contract (v1): Each lane exposes run(request) -> response with a JSON envelope. The envelope carries contract_version, request_id, query_id, access_scope, status (ok/partial/pending/insufficient_data/error), output, warnings, error and extensions. The README also fixes minimum canonical column names and a storage adapter (resolve_artifact, read_dataset, register_dataset, search_datasets) so no lane depends on another's internals.

Hackathon MVP: one region and species, a licensed tracking study, rainfall and vegetation data, a historical analysis with map and timeline, one live retrieval showing a cache miss then a cache hit, and one experimental evaluated forecast. If the data can't support the forecast, the system returns insufficient data. It also needs source citations and an exportable evidence summary.

Longer term: The moat is reliable normalization, evaluated analyses and permissioned field histories, not copied public data. Contractor CSV imports and a freemium business model are later steps to validate.

The next step is probably to claim a lane, or to start on the shared contracts in contracts/ since everything else depends on them. Which would you like to do?

✻ Crunched for 14s · done 2:15 PM

❯ I want to talk to you about the part where in between where we turn anything that he collects, lets talk about satelite data, its gonna be in .tiff format when we download it right, we wanna assume that everything will have location and time data becuase this is all geospatial data in the context of time. how would we use this .tiff format, what are the metrics we can extract from that, its my understanding that its a 2d array of like different layers like depending on waht the satelites is measruing, lets define waht the .tiff is first and what format we need to process it into so that we can use this in the pipeline

What a GeoTIFF is

A GeoTIFF is a normal TIFF image with georeferencing metadata. Your mental model is right: it is a stack of 2D arrays on a shared grid.

What the file contains

┌──────────────────┬─────────────────────────────────┬─────────────────────────────────┐
│ Part │ What it holds │ Why we care │
├──────────────────┼─────────────────────────────────┼─────────────────────────────────┤
│ │ N layers, each a 2D array of │ One band per measurement (red, │
│ Bands │ height × width │ near-infrared, cloud flag, │
│ │ │ rainfall) │
├──────────────────┼─────────────────────────────────┼─────────────────────────────────┤
│ Data type │ uint16, int16, float32, and so │ Raw values are often scaled │
│ │ on │ integers, not real units │
├──────────────────┼─────────────────────────────────┼─────────────────────────────────┤
│ │ Coordinate reference system, │ Pixel positions mean nothing │
│ CRS │ such as EPSG:4326 (lat/lon) or │ without it │
│ │ a UTM zone │ │
├──────────────────┼─────────────────────────────────┼─────────────────────────────────┤
│ │ The origin plus the pixel size, │ This gives each pixel its │
│ Affine transform │ which maps (row, col) to (x, │ location │
│ │ y) │ │
├──────────────────┼─────────────────────────────────┼─────────────────────────────────┤
│ Nodata value │ Sentinel for "no measurement" │ Treating it as a real value │
│ │ (-9999, 0, NaN) │ corrupts statistics │
├──────────────────┼─────────────────────────────────┼─────────────────────────────────┤
│ Scale/offset │ real = raw × scale + offset │ Sentinel-2 stores reflectance │
│ │ │ ×10000 │
├──────────────────┼─────────────────────────────────┼─────────────────────────────────┤
│ Tags and band │ │ Sometimes names the band or the │
│ descriptions │ Free-text key/value metadata │ acquisition date, but not │
│ │ │ reliably │
└──────────────────┴─────────────────────────────────┴─────────────────────────────────┘

Where your assumption needs care: a GeoTIFF has location built in, but time is not reliably in the file. Time usually lives in the filename, the catalog entry (STAC metadata), or an optional tag. So the Fetch manifest must capture time at download. Normalize can't recover it from the pixels.

Three kinds of time

- An instant, such as a single Sentinel-2 or Landsat overpass.
- An interval, such as CHIRPS daily rainfall, a 16-day MODIS composite, or a monthly mean. This matches the README's interval_start/interval_end for rainfall.
- Undated, such as a static elevation or land-cover layer.

Other format facts

- One "file" is not always one multiband image. Landsat ships one GeoTIFF per band. Sentinel-2 ships separate JP2 files. MODIS is traditionally HDF. Rainfall products (CHIRPS, IMERG) are GeoTIFF or NetCDF.
- Prefer Cloud-Optimized GeoTIFF (COG) where a source offers it. It allows reading a small window over HTTP without downloading the whole file.

What we can extract

Most satellite "metrics" are band math on the raw layers.

Vegetation and water

- NDVI = (NIR − Red) / (NIR + Red). It measures greenness and productivity, and is the main one for habitat questions.
- EVI is similar but better in dense canopy.
- NDWI/MNDWI = (Green − NIR or SWIR) / (Green + NIR or SWIR). It measures open water and wetland extent.
- NBR measures burn severity.

Environment

- Rainfall (mm per interval) from CHIRPS or IMERG.
- Land surface temperature, soil moisture, and elevation or slope.

Quality and masks (required before using any value)

- Cloud, shadow and snow masks, such as Sentinel-2 SCL or the Landsat QA_PIXEL band. These are bit-packed, so decoding is a deterministic code step.

These are indicators, as the README says. NDVI going down shows vegetation reflectance changed, not why.

What format the pipeline needs

The README already says raster data stays as files until values are extracted. I'd define two stages.

Stage A: Raster dataset (Normalize publishes this)

Keep the raster as a COG, with a standardized descriptor. This is a gap in the current contract, which only has tabular families. I'd add a raster descriptor with:

- Identity: dataset_id, version, raw_artifact_refs, mapping_version
- Grid: CRS (normalize to a defined one), transform, width, height, resolution with units
- Time: acquired_at (instant) or interval_start/interval_end, in UTC
- Bands: a list of {index, name, unit, scale, offset, nodata, role}, where role is measurement, quality_mask, and so on
- Mask semantics: how to read the quality band (which bits mean cloud)
- Coverage: bbox and the valid-pixel fraction

The rule is the same one the tabular side follows: reject ambiguity. If we can't determine the time, the unit, or the nodata value, the raster is quarantined, not guessed.

Stage B: Extracted tables (deterministic code, not an agent)

When a question needs numbers, a tested function turns raster into rows. Three extraction shapes cover the use cases:

┌───────────────┬────────────────────────────────────────────────┬──────────────────────┐
│ Shape │ Output grain │ Used for │
├───────────────┼────────────────────────────────────────────────┼──────────────────────┤
│ Cell table │ one row per cell per acquisition │ Small regions or │
│ │ (vegetation_observations) │ coarse grids │
├───────────────┼────────────────────────────────────────────────┼──────────────────────┤
│ Zonal │ one row per polygon per acquisition (mean, │ "NDVI in this │
│ statistics │ median, valid fraction) │ wetland over time" │
├───────────────┼────────────────────────────────────────────────┼──────────────────────┤
│ Point │ one row per animal location, with the values │ Linking movement to │
│ sampling │ at that point and time │ habitat │
└───────────────┴────────────────────────────────────────────────┴──────────────────────┘

The sequence is: apply scale/offset, mask nodata and cloud, compute the index, then aggregate, with the valid-pixel fraction kept as a quality column.

Open decision: the cell table doesn't scale

At 10 m resolution, one Sentinel-2 tile has about 120 million pixels. A cell table for a large region over a year is far too big for PostgreSQL. So I'd put the tabular vegetation_observations at zonal or coarse-grid level, and keep point sampling in Recipe, reading the COG on demand. That fits the README's point-to-cell matching operation.

Where it fits in the lanes

- Fetch: downloads the TIFF bytes and records the acquisition time and source in the RawManifest.
- Normalize: inspects the raster, decodes bands, and publishes the Stage A descriptor. It may also write Stage B zonal tables.
- Recipe: does point sampling and preceding-window rainfall aggregation by reading rasters.
- Analysis: never touches pixels.

The point-sampling question is the one to settle first, because it decides whether raster reading belongs to Normalize or Recipe.

Questions for you

1. Which data sources first? I'd suggest Sentinel-2 or Landsat for NDVI and water, and CHIRPS for rainfall. These choices set the band semantics and the time model.
2. Who owns pixel extraction? Should point sampling be in Recipe (my suggestion), or should Normalize pre-extract everything to tables?
3. Should I draft the raster descriptor as a proposed addition to the contracts section of the README?

✻ Brewed for 23s · done 2:45 PM

※ recap: We're designing how Habitat Watch turns satellite GeoTIFFs into pipeline-ready data, and I've proposed a raster descriptor plus extracted tables. Next, please tell me which sources to start with and who owns pixel extraction. (disable recaps in /config)

❯ the next step is gonan expect a location and time column, how can we make it confirm to a structure wehre its like that, should we just precompute certain sattelite statistics like NDVI for each square and store those instead of the full bands

Yes, precompute indices into a table with location and time columns. Keep the raw COG as the source of truth, though, because you can't recompute from stored statistics.

Why precompute

- Downstream lanes already expect cell_id, geometry, observed_at, index_name, index_value (the README's vegetation_observations).
- Joins to animal points and rainfall become plain table operations.
- Analysis never touches pixels.

Why keep the raw raster too

- Adding a new index later (say NBR) needs the original bands.
- A bug fix in cloud masking needs reprocessing.
- The README requires raw bytes preserved, and the Recipe lane may need to sample at exact points.

So the raster is the archive and the table is a derived, versioned dataset. Each table row traces back through raw_artifact_refs and mapping_version.

How to force the location and time structure

Define a fixed analysis grid and resample everything onto it. This is the key decision, because raw pixels from different sensors don't line up.

1. Choose one grid, for example 1 km cells. The options are a regular lat/lon grid or a standard system like H3 or the MODIS sinusoidal grid. Pick based on the species' movement scale, since a 10 m grid is meaningless for an antelope track with 5 m GPS error and daily fixes.
2. Give each cell a stable cell_id derived deterministically from the grid definition, so rainfall and vegetation share the same IDs and join on them.
3. For each acquisition and cell, take the valid pixels inside the cell and compute the aggregate (mean NDVI).
4. Emit one row per cell, per acquisition, per index.

Columns I'd store

┌──────────────────────────────────────────────┬───────────────────────────────────────┐
│ Column │ Meaning │
├──────────────────────────────────────────────┼───────────────────────────────────────┤
│ cell_id, geometry │ Location (WGS84) │
├──────────────────────────────────────────────┼───────────────────────────────────────┤
│ observed_at or interval_start/interval_end │ Time (UTC), instant or composite │
│ │ window │
├──────────────────────────────────────────────┼───────────────────────────────────────┤
│ index_name, index_value │ e.g. ndvi, 0.42 │
├──────────────────────────────────────────────┼───────────────────────────────────────┤
│ valid_pixel_fraction │ Share of the cell's pixels that were │
│ │ cloud-free │
├──────────────────────────────────────────────┼───────────────────────────────────────┤
│ pixel_count, aggregation │ How the value was computed (mean, │
│ │ median) │
├──────────────────────────────────────────────┼───────────────────────────────────────┤
│ source_record_id, dataset_version, │ Provenance │
│ mapping_version │ │
└──────────────────────────────────────────────┴───────────────────────────────────────┘

valid_pixel_fraction matters most. A cell that is 90% cloud has an NDVI of low quality, and we must not silently treat it as a measurement. Below a threshold (say 50%), the value is null, not zero, which matches the README's rule that null means unknown. The raw bands we drop are exactly the information that would make that call later, which is another reason to keep the raster.

Trade-offs to decide

- Grid resolution: coarse is small and fast but loses detail, such as a narrow wetland. Fine is accurate but big. One option is a coarse grid for the main table, with zonal statistics on demand over specific polygons (like a wetland boundary) from the COG.
- Which indices to precompute: NDVI and a water index cover the MVP. Compute others on demand.
- Mean over a cell blurs mixed land cover. Storing a standard deviation column as well is cheap and lets Analysis flag heterogeneous cells.

Recommendation

1. Fixed 1 km grid (adjust after checking the animal data's resolution).
2. Precompute NDVI and one water index per cell per acquisition into vegetation_observations, with valid_pixel_fraction.
3. Keep the COGs in the raw archive for reprocessing and on-demand zonal queries.

Which species or study are you targeting? Its fix interval and movement range would let us pick the grid size with evidence instead of a guess.

✻ Worked for 11s · done 3:05 PM

❯ what is a COG

COG stands for Cloud-Optimized GeoTIFF. It is a normal GeoTIFF whose internal layout lets a program read just the part it needs, without downloading the whole file.

The problem it solves

A regular TIFF can store its pixels in one long strip, row after row. To read a small area, a program may have to read most of the file. Satellite scenes are often hundreds of MB to several GB, so that is slow and wasteful.

How a COG fixes it

Three layout choices, all inside a standard .tif file:

1. Tiling: pixels are stored in square blocks (typically 256×256 or 512×512), not in long rows. To read a small area, the program loads only the blocks that overlap it.
2. Overviews: the file has pre-built lower-resolution copies of the image (½, ¼, ⅛ size, and so on). A zoomed-out view reads a small overview, not the full-resolution data.
3. Header first: the metadata (CRS, transform, tile offsets) sits at the start of the file. A program reads a few KB of header, learns where each tile is, and fetches only those byte ranges.

The program fetches those ranges with HTTP range requests, which ask a server for "bytes 500000 to 700000 of this file". The file can sit in object storage like S3, and the reader pulls only what it needs.

What is different about the file itself

Nothing is different in kind. A COG is a valid GeoTIFF, and any GeoTIFF tool opens it. It is only arranged more carefully. You can convert a normal GeoTIFF with GDAL: gdal_translate -of COG input.tif output.tif.

Why it matters for this project

- Point sampling: to get the NDVI at an animal's GPS location, Recipe reads one tiny tile, not the whole scene.
- Zonal statistics: for one wetland polygon, it reads only the tiles that cover it.
- Cheap storage: keep one file per scene in the raw archive and read it directly. There is no need to extract everything to tables up front.
- Source availability: many providers already publish COGs (Landsat and Sentinel-2 on AWS, for example), so Fetch can sometimes read remotely without downloading at all.

One limit: the README says to preserve original bytes. If a source gives a non-COG GeoTIFF, we keep the original and write a COG as a derived copy for fast reads, rather than replacing it.

✻ Worked for 6s · done 3:05 PM

※ recap: We're designing how satellite GeoTIFFs become location-and-time tables for the Habitat Watch pipeline, and we settled on precomputed NDVI per grid cell with raw COGs kept. Next, you pick the target species or study so we can choose the grid size. (disable recaps in /config)

❯ select statistics that we will look at, then lets doa 1km grid, how much table space does 1 sentienl2 tile take up?

Statistics to compute

I'd keep the MVP to three indices, all from bands Sentinel-2 provides:

┌───────┬───────────────────────┬──────────────────────────────────────────────────────┐
│ Index │ Formula (Sentinel-2 │ Question it answers │
│ │ bands) │ │
├───────┼───────────────────────┼──────────────────────────────────────────────────────┤
│ NDVI │ (B8 − B4) / (B8 + B4) │ How green and productive is the vegetation? │
├───────┼───────────────────────┼──────────────────────────────────────────────────────┤
│ MNDWI │ (B3 − B11) / (B3 + │ Is there open water or wetland? │
│ │ B11) │ │
├───────┼───────────────────────┼──────────────────────────────────────────────────────┤
│ NDMI │ (B8 − B11) / (B8 + │ How much moisture is in the vegetation? Useful for │
│ │ B11) │ dry-period questions. │
└───────┴───────────────────────┴──────────────────────────────────────────────────────┘

NDVI and MNDWI cover the README's habitat and wetland examples. NDMI is the one I'd drop first if you want fewer.

Per cell, per index, per acquisition:

- mean: the main value
- std: lets Analysis flag mixed-cover cells
- valid_pixel_fraction: the share of cloud-free pixels
- pixel_count: how many valid pixels went into the mean

Skip the median, min, and max in the MVP. They add columns without answering a question we have.

Rainfall comes from a separate source (CHIRPS), so it is not part of this table.

Table size for one Sentinel-2 tile

These are my estimates from the tile geometry and typical Postgres row overhead. I haven't measured them. Treat them as rough.

Cell count

- A tile covers about 110 × 110 km, so a 1 km grid gives about 12,000 cells.
- Each cell holds about 10,000 pixels at 10 m. The 20 m band (B11) is resampled to match.

Per acquisition (one satellite pass over the tile)

- Long format, one row per cell per index: 12,000 × 3 = ~36,000 rows.
- Each row is about 100 bytes in Postgres, counting overhead.
- That is ~3.5 MB, or ~7 MB with indexes.
- As Parquet it is about 0.5–1 MB.

Per year

- Sentinel-2 revisits about every 5 days, so roughly 70 acquisitions per tile per year.
- Postgres: ~250 MB of table, ~500 MB with indexes.
- Parquet: ~50 MB.

Compare with the raw data

- The raw tile is about 1 GB with all bands. Even just the 4 bands we need take several hundred MB per acquisition.
- Across 70 acquisitions that is tens of GB.
- The derived table is about 100× smaller than the rasters, which supports precomputing.

Two design consequences

▎ Your voice can help guide AI
▎ Take 15 min to share your experiences with Anthropic Interviewer. Start now

⏺ cc-plugin-agents-md: no CLAUDE.md found; AGENTS.md loaded: /Users/phoenixsheppard/AGENTS.md

❯ Lets go over the different data sources and types we will take in. satelite data is one, csv is another, what else can we realistically expect for thigns that we are takeing in like what are other sensors that are being used here and what format data do they give

Ran 1 shell command

"CSV" is a file format, not a source. Sources and formats vary separately: one sensor can deliver CSV, JSON, or NetCDF depending on the provider. Below I group things by what produces the data, then by what the files look like. Everything is matched to the README's three canonical families (animal_locations, rainfall_observations, vegetation_observations).

1. Sources by type

Animal-borne sensors (tracking)

Sensor: GPS collars/tags
What it gives: lat/lon fixes every 15 min to 1 day
Typical format: Movebank REST API → CSV (or JSON); vendor exports (Vectronic, Lotek) as
CSV/Excel
Normalize gotchas: Fix type (2D/3D), DOP/HDOP quality, timestamps often missing a timezone
────────────────────────────────────────
Sensor: Argos satellite telemetry
What it gives: Doppler-derived locations, used for birds and marine animals
Typical format: CSV via Movebank, or raw Argos DS/DIAG text
Normalize gotchas: Location classes (3, 2, 1, 0, A, B, Z) mean errors from about 250 m to
tens of km. You can't treat them like GPS.
────────────────────────────────────────
Sensor: Accelerometers
What it gives: Activity bursts at 10–40 Hz
Typical format: Movebank "ACC" CSV, with values packed as space-separated strings in one cell
Normalize gotchas: Huge volume. Aggregate to an activity index; don't store every row.
────────────────────────────────────────
Sensor: Light-level geolocators
What it gives: Daily location estimated from sunrise/sunset, ±100–200 km
Typical format: Raw .lux/.lig files that need modeling (e.g. the R package GeoLight)
Normalize gotchas: Probably out of scope for MVP

Occurrence / sighting data (where animals were seen, not tracked)

┌──────────────────────────────────┬────────────────────────────────────────────────────┐
│ Source │ Format │
├──────────────────────────────────┼────────────────────────────────────────────────────┤
│ GBIF (aggregates museums, │ Darwin Core Archive: a zip of TSV files plus │
│ iNaturalist, eBird, surveys) │ meta.xml. A JSON API is also available. │
├──────────────────────────────────┼────────────────────────────────────────────────────┤
│ iNaturalist │ JSON API, or via GBIF │
├──────────────────────────────────┼────────────────────────────────────────────────────┤
│ eBird │ Tab-delimited text (EBD); requires a data request │
├──────────────────────────────────┼────────────────────────────────────────────────────┤
│ Camera traps (Wildlife Insights, │ Camtrap DP: CSVs plus datapackage.json, with │
│ LILA) │ images separate │
├──────────────────────────────────┼────────────────────────────────────────────────────┤
│ Acoustic recorders (AudioMoth) │ WAV files → detections through BirdNET → CSV │
└──────────────────────────────────┴────────────────────────────────────────────────────┘

This category matters for the README's rule that "extinction/disappearance needs observation-effort evidence". Camera-trap and survey data carry effort (trap-nights, checklist duration). GPS tracking does not. It probably needs a 4th family, such as occurrence_observations, with presence/absence and effort fields. Watch for sensitive species: GBIF often blurs or generalizes their coordinates.

Satellite / remote sensing (raster)

┌────────────────────────┬─────────────────────────┬────────────────────────────────────┐
│ Source │ What it gives │ Format │
├────────────────────────┼─────────────────────────┼────────────────────────────────────┤
│ Sentinel-2 (10 m, │ Optical bands → │ COG GeoTIFF through STAC APIs │
│ 5-day revisit) │ NDVI/EVI │ (Planetary Computer, Earth │
│ │ │ Search); also SAFE/JP2 │
├────────────────────────┼─────────────────────────┼────────────────────────────────────┤
│ Landsat 8/9 (30 m, │ │ │
│ 16-day revisit, │ Long-history vegetation │ COG through STAC │
│ archive back to 1980s) │ │ │
├────────────────────────┼─────────────────────────┼────────────────────────────────────┤
│ MODIS (250 m–1 km, │ Ready-made NDVI/EVI │ HDF4-EOS originally; COG on │
│ daily) │ composites (MOD13Q1) │ Planetary Computer │
├────────────────────────┼─────────────────────────┼────────────────────────────────────┤
│ │ Sees through clouds; │ │
│ Sentinel-1 SAR │ detects flooding and │ COG / SAFE │
│ │ surface moisture │ │
├────────────────────────┼─────────────────────────┼────────────────────────────────────┤
│ VIIRS/MODIS FIRMS │ Active fire points │ CSV/shapefile/JSON, near real time │
└────────────────────────┴─────────────────────────┴────────────────────────────────────┘

Gotchas: scale factors (MODIS NDVI is stored ×10000), nodata values, and cloud masks (Sentinel-2 SCL band, Landsat QA_PIXEL). The README's vegetation_observations already includes cloud/quality fields for this reason.

Weather / climate (gridded and station)

┌───────────────────────┬───────────────────────────────────┬──────────────────────────┐
│ Source │ Resolution │ Format │
├───────────────────────┼───────────────────────────────────┼──────────────────────────┤
│ CHIRPS rainfall │ ~5 km, daily, from 1981 │ GeoTIFF, NetCDF │
├───────────────────────┼───────────────────────────────────┼──────────────────────────┤
│ GPM IMERG rainfall │ ~10 km, 30-min │ HDF5 / NetCDF4 │
├───────────────────────┼───────────────────────────────────┼──────────────────────────┤
│ ERA5 / ERA5-Land │ ~9–31 km, hourly: temperature, │ NetCDF or GRIB through │
│ reanalysis │ soil moisture, wind, │ the Copernicus CDS API │
│ │ precipitation │ │
├───────────────────────┼───────────────────────────────────┼──────────────────────────┤
│ GHCN-Daily weather │ Point stations │ Fixed-width .dly text, │
│ stations │ │ or CSV │
├───────────────────────┼───────────────────────────────────┼──────────────────────────┤
│ Local station loggers │ Point │ CSV with vendor headers │
│ (HOBO, Campbell) │ │ │
└───────────────────────┴───────────────────────────────────┴──────────────────────────┘

Gotchas: NetCDF time is stored as "days since 1900-01-01" (CF conventions). Accumulation intervals are a trap: ERA5 precipitation accumulates differently in different products. The README's warning about "daily totals vs rates" applies here directly.

Static context layers (change slowly or never)

- Elevation: Copernicus DEM / SRTM, as GeoTIFF
- Land cover: ESA WorldCover, Dynamic World, as GeoTIFF
- Surface water: JRC Global Surface Water, as GeoTIFF
- Protected areas: WDPA, as Shapefile or GeoPackage
- Roads/rivers/fences: OpenStreetMap, as PBF, or GeoJSON through Overpass

The README doesn't model these yet. They are probably a context_layers concept: a versioned raster or vector that you sample, with no observation time series.

Field and customer uploads (later)

- Ground IoT sensors (soil moisture, water quality, temperature): CSV, vendor JSON APIs, sometimes MQTT streams
- Drone surveys: orthomosaic GeoTIFFs, LAS/LAZ point clouds
- Ad-hoc user files: Excel, KML/KMZ, GPX, zipped shapefiles, GeoJSON

2. Formats the Normalize lane actually has to parse

┌─────────────┬─────────────────────────────────┬───────────────────────────────────────┐
│ Shape │ Formats │ Library (matches the Python stack) │
├─────────────┼─────────────────────────────────┼───────────────────────────────────────┤
│ Tabular │ CSV, TSV, Excel, DwC-A, Camtrap │ pandas/polars, python-dwca-reader, │
│ │ DP │ frictionless │
├─────────────┼─────────────────────────────────┼───────────────────────────────────────┤
│ API JSON │ Movebank, GBIF, iNat, STAC │ httpx + pydantic │
├─────────────┼─────────────────────────────────┼───────────────────────────────────────┤
│ Gridded │ GeoTIFF/COG, NetCDF, HDF4/HDF5, │ rasterio, xarray (+ cfgrib, h5netcdf) │
│ raster │ GRIB, Zarr │ │
├─────────────┼─────────────────────────────────┼───────────────────────────────────────┤
│ Vector │ GeoJSON, Shapefile, GeoPackage, │ geopandas / pyogrio │
│ │ KML, GPX │ │
├─────────────┼─────────────────────────────────┼───────────────────────────────────────┤
│ Media │ JPEG (camera trap), WAV │ Keep as files and ingest derived │
│ │ (acoustic) │ detections only │
└─────────────┴─────────────────────────────────┴───────────────────────────────────────┘

3. Recommendation for scope

- MVP (what the README needs): Movebank GPS CSV, CHIRPS daily rainfall, and MODIS or Sentinel-2 NDVI through STAC. These fill all three existing families.
- Next: GBIF DwC-A for occurrence plus effort, ERA5 for temperature and soil moisture, and DEM and land cover as static covariates. FIRMS is a cheap, high-value addition because fire strongly drives movement.
- Defer: accelerometer, geolocators, acoustic, camera-trap images, drone, and IoT streams. Each needs its own processing pipeline before it produces records.

Two contract decisions come out of this, and the lanes should agree on both early. First, add an occurrence/effort family. Second, decide how static context layers are versioned and sampled in Recipe. Both are missing from the current README. I can draft them into the shared-contracts section if you want.

✻ Baked for 45s · done 3:39 PM

❯ but like what are the data formats that we are gonna have to worry about, were thinking of simplifying or processing things into csv

such as
❯ what is a COG
❯ select statistics that we will look at, then lets doa 1km grid, how much table space does
1 sentienl2 tile take up?

⏺ Statistics to compute

    I'd keep the MVP to three indices, all from bands Sentinel-2 provides:

    ┌───────┬───────────────────────┬──────────────────────────────────────────────────────┐
    │ Index │ Formula (Sentinel-2   │                 Question it answers                  │
    │       │        bands)         │                                                      │
    ├───────┼───────────────────────┼──────────────────────────────────────────────────────┤
    │ NDVI  │ (B8 − B4) / (B8 + B4) │ How green and productive is the vegetation?          │
    ├───────┼───────────────────────┼──────────────────────────────────────────────────────┤
    │ MNDWI │ (B3 − B11) / (B3 +    │ Is there open water or wetland?                      │
    │       │ B11)                  │                                                      │
    ├───────┼───────────────────────┼──────────────────────────────────────────────────────┤
    │ NDMI  │ (B8 − B11) / (B8 +    │ How much moisture is in the vegetation? Useful for   │
    │       │ B11)                  │ dry-period questions.                                │
    └───────┴───────────────────────┴──────────────────────────────────────────────────────┘

    NDVI and MNDWI cover the README's habitat and wetland examples. NDMI is the one I'd drop
    first if you want fewer.

    Per cell, per index, per acquisition:

- mean: the main value
- std: lets Analysis flag mixed-cover cells
- valid_pixel_fraction: the share of cloud-free pixels
- pixel_count: how many valid pixels went into the mean

  Skip the median, min, and max in the MVP. They add columns without answering a question we
  have.

  Rainfall comes from a separate source (CHIRPS), so it is not part of this table.

  Table size for one Sentinel-2 tile

  These are my estimates from the tile geometry and typical Postgres row overhead. I haven't
  measured them. Treat them as rough.

  Cell count

- A tile covers about 110 × 110 km, so a 1 km grid gives about 12,000 cells.
- Each cell holds about 10,000 pixels at 10 m. The 20 m band (B11) is resampled to match.

  Per acquisition (one satellite pass over the tile)

- Long format, one row per cell per index: 12,000 × 3 = ~36,000 rows.
- Each row is about 100 bytes in Jump to bottom (click) ↓ ad.

─────────────────────────────────────────────────────────────────────────────────────────────
❯ keep both versions, draft the segment design
─────────────────────────────────────────────────────────────────────────────────────────────
⏵⏵ auto mode on (shift+tab to cycle) · ← for agents
this for the rasters, but

Yes, the "flatten everything to rows" approach works for almost every format. In practice, "to CSV" means "every source becomes rows keyed by cell_id + time (or point + time)". The formats split into five groups, and each group has its own conversion problems.

Formats to handle, grouped by how they become rows

1. Already tabular: parse and clean

┌─────────────────┬──────────────────────┬─────────────────────────────────────────────┐
│ Format │ Seen in │ Things to handle │
├─────────────────┼──────────────────────┼─────────────────────────────────────────────┤
│ │ Movebank, FIRMS, │ Timestamps with no timezone, mixed │
│ CSV / TSV │ logger exports, user │ delimiters, decimal commas, extra header │
│ │ uploads │ lines, units only in the column name │
├─────────────────┼──────────────────────┼─────────────────────────────────────────────┤
│ │ Vendor collar │ Excel date serials, merged cells, several │
│ Excel (.xlsx) │ exports, user │ sheets │
│ │ uploads │ │
├─────────────────┼──────────────────────┼─────────────────────────────────────────────┤
│ Darwin Core │ GBIF, eBird │ TSV files plus a meta.xml that defines the │
│ Archive (zip) │ │ columns. Read the XML; don't guess. │
├─────────────────┼──────────────────────┼─────────────────────────────────────────────┤
│ Camtrap DP │ Camera traps │ 3 linked CSVs (deployments, media, │
│ (zip) │ │ observations) to join │
├─────────────────┼──────────────────────┼─────────────────────────────────────────────┤
│ Fixed-width │ GHCN-Daily .dly │ One row = one station-month with 31 day │
│ text │ │ slots, so you must unpivot it │
└─────────────────┴──────────────────────┴─────────────────────────────────────────────┘

2. JSON APIs: flatten

Movebank, GBIF, iNaturalist, STAC catalogs. The issues are nested objects, pagination, and rate limits. Usually simple.

3. Gridded rasters: zonal stats onto the 1 km grid (your pasted approach)

┌────────────────┬──────────────────────────────────────────────────────────────────────┐
│ Format │ Seen in │
├────────────────┼──────────────────────────────────────────────────────────────────────┤
│ GeoTIFF / COG │ Sentinel-2, Landsat, CHIRPS, DEM, land cover │
├────────────────┼──────────────────────────────────────────────────────────────────────┤
│ JPEG2000 │ Raw Sentinel-2 SAFE packages. Avoid: use the COG versions through │
│ (.jp2) │ STAC. │
├────────────────┼──────────────────────────────────────────────────────────────────────┤
│ NetCDF │ CHIRPS, ERA5, IMERG │
├────────────────┼──────────────────────────────────────────────────────────────────────┤
│ HDF4 / HDF5 │ MODIS (original), IMERG │
├────────────────┼──────────────────────────────────────────────────────────────────────┤
│ GRIB │ ERA5 (choose NetCDF in the download instead) │
└────────────────┴──────────────────────────────────────────────────────────────────────┘

rasterio and xarray read all of them, so a single zonal-stats function covers all rasters. The method differs by data type, not by file format:

- Continuous, finer than 1 km (Sentinel-2 10 m, DEM 30 m): mean/std/valid fraction, as in your design.
- Continuous, coarser than 1 km (CHIRPS ~5 km, ERA5 ~9 km): each 1 km cell gets the value of the coarse pixel that contains it. This is fine, but neighboring cells will have identical values. Analysis must not treat 25 cells as 25 independent rainfall measurements. Store the source resolution on the row.
- Categorical (land cover, Sentinel-2 SCL cloud mask): a mean has no meaning here. "Mean land cover = 37.4" is garbage. Store the fraction of the cell in each class instead (e.g. frac_tree, frac_water, frac_cropland).
- Static (DEM, land cover): compute once per cell, with no time column. Use a separate cell_attributes table.

4. Vector: compute per-cell attributes, or keep as points

┌──────────────────────────────────────────┬───────────────────────────────────────┐
│ Format │ Seen in │
├──────────────────────────────────────────┼───────────────────────────────────────┤
│ Shapefile (a zip of .shp/.dbf/.shx/.prj) │ WDPA protected areas, government data │
├──────────────────────────────────────────┼───────────────────────────────────────┤
│ GeoPackage, GeoJSON │ Newer sources, OSM extracts │
├──────────────────────────────────────────┼───────────────────────────────────────┤
│ KML/KMZ, GPX │ User uploads, handheld GPS │
└──────────────────────────────────────────┴───────────────────────────────────────┘

Points become lat/lon columns, with a cell_id assigned by point-in-cell. Polygons and lines become per-cell values: frac_protected, dist_to_road_km, dist_to_river_km. Don't put geometry into CSV as text unless you have to.

5. Media: never a table themselves

Camera-trap JPEGs and acoustic WAVs. Keep the files in the raw archive and ingest only the derived detections (species, time, confidence, effort) as rows.

Two decisions this design requires

1. Pick the grid once, before any lane writes cell_ids. Sentinel-2 tiles use UTM zones. A "1 km grid" built per tile won't align across zone boundaries, and cells will overlap or leave gaps. Use one global grid for everything:

- An equal-area grid (e.g. EASE-Grid 2.0 at 1 km). Cells are true squares of equal area.
- Or H3 resolution 8 (~0.74 km² hexagons). Joins and neighbor lookups are easy, but cells aren't squares.

I'd lean H3 or EASE. Either way, it belongs in contracts/.

2. Use CSV for export, not for storage. Keep "table" as the mental model, but write Parquet files (the README already plans Parquet for feature datasets). CSV loses types: timezones, null vs empty string, and float precision. At ~36k rows per index per acquisition, it's also 5–10× larger. Parquet has the same rows and columns, and pandas and DuckDB read it directly. Generate CSV only when a user downloads data.

Resulting tables

- grid_cells: cell_id, geometry, centroid
- cell_attributes (static): cell_id, elevation, slope, land-cover fractions, frac_protected, distances
- cell_observations (time series, long format): cell_id, time interval, source, variable, stat, value, valid_fraction, source_resolution
- animal_locations: point rows with raw lat/lon plus cell_id
- occurrences: sightings and detections with effort fields

Your message cut off after "but". What was the rest? If it was about the non-raster sources, the groups above cover them. If it was something else, tell me.

✻ Sautéed for 30s · done 3:42 PM

❯ whats darwin core archive?

Darwin Core Archive (DwC-A) is the standard zip format for biodiversity records. It has two parts.

1. Darwin Core: a standard set of column names. It is maintained by TDWG, a biodiversity standards body. Every publisher uses the same names for the same fields, for example:

┌──────────────────────────────────┬────────────────────────────────────────────────────┐
│ Term │ Meaning │
├──────────────────────────────────┼────────────────────────────────────────────────────┤
│ scientificName │ Antidorcas marsupialis │
├──────────────────────────────────┼────────────────────────────────────────────────────┤
│ eventDate │ When the observation happened (ISO 8601) │
├──────────────────────────────────┼────────────────────────────────────────────────────┤
│ decimalLatitude / │ WGS84 coordinates │
│ decimalLongitude │ │
├──────────────────────────────────┼────────────────────────────────────────────────────┤
│ coordinateUncertaintyInMeters │ How precise the location is │
├──────────────────────────────────┼────────────────────────────────────────────────────┤
│ basisOfRecord │ HumanObservation, MachineObservation, │
│ │ PreservedSpecimen, … │
├──────────────────────────────────┼────────────────────────────────────────────────────┤
│ occurrenceStatus │ present or absent │
├──────────────────────────────────┼────────────────────────────────────────────────────┤
│ individualCount │ How many animals │
├──────────────────────────────────┼────────────────────────────────────────────────────┤
│ samplingProtocol / │ How the data was collected │
│ samplingEffort │ │
└──────────────────────────────────┴────────────────────────────────────────────────────┘

2. The archive: a zip that packages those records.
   download.zip
   ├── occurrence.txt ← the main table (tab-separated, one row per record)
   ├── multimedia.txt ← optional extra tables, linked to the main one by ID
   ├── meta.xml ← says which column in which file is which Darwin Core term
   └── eml.xml ← dataset metadata: title, authors, license, citation

meta.xml maps column positions to terms:
<core rowType="http://rs.tdwg.org/dwc/terms/Occurrence">
<files><location>occurrence.txt</location></files>
<field index="0" term="http://rs.tdwg.org/dwc/terms/occurrenceID"/>
<field index="1" term="http://rs.tdwg.org/dwc/terms/scientificName"/>
<field index="2" term="http://rs.tdwg.org/dwc/terms/eventDate"/>
...
</core>

Why it's useful for us

GBIF uses DwC-A to combine thousands of sources, including museums, iNaturalist, eBird, and national surveys. All of them arrive with the same column names, so one Normalize mapping covers every GBIF dataset. Movebank is different: there, each study's CSV can need its own mapping.

For a simpler start, GBIF also offers a "Simple CSV" download. It is a single tab-separated file with the most common columns already flattened. Use that for the MVP, and switch to full DwC-A only if you need the extension tables or the per-dataset license metadata.

Things to handle

- eventDate can be a range, e.g. 2023-05-01/2023-05-07. Keep the interval start and end, the same way as the rainfall intervals.
- coordinateUncertaintyInMeters: some records are only accurate to tens of km. Drop or flag records whose uncertainty is larger than your 1 km cell.
- basisOfRecord: museum specimens from 1920 and live camera-trap detections sit in the same table. Filter by this field.
- occurrenceStatus = absent: absence records exist and mean "looked but didn't find". Don't treat them as sightings.
- Sensitive species: coordinates are sometimes deliberately blurred. The dataGeneralizations field tells you when.
- Licenses can differ per record (CC0, CC-BY, CC-BY-NC). This matters for the README's "cache only where terms allow" rule.
- Citation: GBIF gives each download a DOI, and you should cite it in the evidence reports.

✻ Brewed for 15s · done 3:43 PM

❯ im just trynna think like if the fetching agent is gonna get alot of type of data, should i havea something route that data to a processing pipeline, one for satelite data, one for animal tracking, for darwincore

Yes. But route on what the source is, not on the file extension, and keep the router as a plain lookup table, not an agent.

Why not route by file type

A .csv could be Movebank tracking, FIRMS fire points, GBIF Simple CSV, or a weather logger. Each needs completely different handling. A .nc could be rainfall or temperature. The extension doesn't tell you enough.

The Fetch connector already knows exactly what it downloaded, because it called the Movebank API on purpose. So the connector writes that information into the RawManifest, and routing becomes a dictionary lookup:

RawManifest { source_id: "movebank", format: "csv", kind: "tracking", ... }
│
▼
dispatcher: NORMALIZERS[(source_id, format)]
│
├── raster pipeline ───── sentinel2, chirps, era5, dem, worldcover
│ → zonal stats on grid → cell_observations / cell_attributes
├── tracking pipeline ─── movebank, vendor collar CSVs
│ → animal_locations
├── occurrence pipeline ─ gbif (DwC-A / Simple CSV), inat, camtrap DP
│ → occurrences
├── vector pipeline ───── wdpa, osm
│ → cell_attributes
└── no match ──────────── LLM proposes a mapping → quarantine until validated

NORMALIZERS = {
("movebank", "csv"): normalize_movebank_csv,
("gbif", "dwca"): normalize_gbif_dwca,
("gbif", "simple_csv"): normalize_gbif_simple_csv,
("chirps", "geotiff"): normalize_chirps,
("sentinel2", "cog"): normalize_sentinel2,
}

Design the pipelines as layers, not silos

A separate pipeline per source duplicates code. A single generic pipeline can't handle the real differences. Use two layers:

1. Shared engines, one per data shape. These do the heavy work: a raster zonal-stats engine (one implementation for Sentinel-2, CHIRPS, ERA5, and DEM), a tabular parse/clean engine, and a vector-to-grid engine.
2. Thin per-source adapters on top. Each adapter is configuration plus a little code: band math for Sentinel-2, the -9999 nodata value for CHIRPS, Argos location classes for Movebank, basisOfRecord filtering for GBIF.

Adding a new raster source then means writing about 30 lines of adapter, not a new pipeline.

Run rasters on a separate worker queue

Tabular normalization takes seconds. Zonal stats over a Sentinel-2 tile takes minutes and needs a lot of memory. If both share one queue, a single satellite job blocks the tracking data that a user is waiting for. The README already plans a durable job table, so add a job_type column and run separate workers per type, at minimum raster vs tabular. This also supports the README's hot path / background path split.

Ownership

- Fetch (lane 1) decides nothing about processing. It only sets source_id, format, and kind correctly on the manifest.
- The dispatcher and the adapters belong to Normalize (lane 2).
- Unknown sources go through the README's existing path, an LLM-proposed mapping plus validation, and never through a guessing router.

The manifest fields source_id, format, and kind become part of the shared contract. Add them to contracts/ before lane 1 and lane 2 start coding.

✻ Cooked for 18s · done 3:45 PM

❯ because at the end of the day we want columns with date and time for every measruement that we can pass of to the next layer

Yes, that's the right goal. One catch: a single timestamp column isn't enough, because the sources measure time in different ways. Every row needs a time interval, not a time point.

Why one timestamp breaks

┌──────────────────────┬────────────────────────────────────────────────────────────────┐
│ Source │ What "time" actually means │
├──────────────────────┼────────────────────────────────────────────────────────────────┤
│ GPS fix │ An instant: 2024-03-05 14:30:00Z │
├──────────────────────┼────────────────────────────────────────────────────────────────┤
│ Sentinel-2 NDVI │ An instant (acquisition), but the value is one snapshot of a │
│ │ cell │
├──────────────────────┼────────────────────────────────────────────────────────────────┤
│ CHIRPS rainfall │ A total over a day: "12 mm between 00:00 and 24:00" │
├──────────────────────┼────────────────────────────────────────────────────────────────┤
│ MODIS NDVI composite │ The best pixel over 16 days. You don't know which day. │
├──────────────────────┼────────────────────────────────────────────────────────────────┤
│ GBIF sighting │ Sometimes exact, sometimes only a day, sometimes a range │
│ │ (2023-05-01/2023-05-07) │
├──────────────────────┼────────────────────────────────────────────────────────────────┤
│ Weather station │ A day in local time, not a UTC day │
│ daily max │ │
├──────────────────────┼────────────────────────────────────────────────────────────────┤
│ DEM / land cover │ No time. Valid "until the next version". │
└──────────────────────┴────────────────────────────────────────────────────────────────┘

If CHIRPS rainfall gets a single timestamp of 2024-03-05 00:00, Recipe can't tell whether that 12 mm fell before or after an animal's 14:30 GPS fix. That causes exactly the leakage bug the README warns about.

A shared time core for every row

Every canonical table, whatever the family, carries the same time columns:

┌────────────────┬───────────────────────────────────────────────────────────────────────┐
│ Column │ Meaning │
├────────────────┼───────────────────────────────────────────────────────────────────────┤
│ time_start │ UTC, start of what the measurement covers │
├────────────────┼───────────────────────────────────────────────────────────────────────┤
│ time_end │ UTC, end. For an instant, it equals time_start. │
├────────────────┼───────────────────────────────────────────────────────────────────────┤
│ time_precision │ second / day / composite / static. Tells Recipe how far it can trust │
│ │ the time. │
├────────────────┼───────────────────────────────────────────────────────────────────────┤
│ │ When the value became known. CHIRPS final data is published weeks │
│ available_at │ after the rain falls. A forecast with a cutoff of March 5 can't use a │
│ │ value that was available only on March 25. │
└────────────────┴───────────────────────────────────────────────────────────────────────┘

Store everything in UTC. Convert local-time sources (station days, user CSVs with no timezone) during normalization, or quarantine them. Never guess the timezone.

The full column set

Keep the README's separate families, but give them a common core so Recipe can treat them uniformly:

── common core (every table) ───────────────────────────
time_start, time_end, time_precision, available_at
cell_id ← always set, from the shared grid
source_id, dataset_version_id, mapping_version_id
quality_flag

── family-specific columns ─────────────────────────────
animal_locations: animal_id, lon, lat, fix_quality, location_error_m
occurrences: species, lon, lat, coord_uncertainty_m, status (present/absent), effort
cell_observations: variable, stat, value, unit, valid_fraction, source_resolution_m
cell_attributes: variable, value, unit (time_precision = static)

Example rows:

┌───────────────────┬─────────────┬─────────────┬─────────┬──────────────────┬─────────┐
│ family │ time_start │ time_end │ cell_id │ variable / │ value │
│ │ │ │ │ entity │ │
├───────────────────┼─────────────┼─────────────┼─────────┼──────────────────┼─────────┤
│ animal_locations │ 2024-03-05 │ 2024-03-05 │ 88a2…f │ animal │ lon/lat │
│ │ 14:30Z │ 14:30Z │ │ movebank:123:A7 │ │
├───────────────────┼─────────────┼─────────────┼─────────┼──────────────────┼─────────┤
│ cell_observations │ 2024-03-05 │ 2024-03-06 │ 88a2…f │ rainfall_mm / │ 12.0 │
│ │ 00:00Z │ 00:00Z │ │ sum │ │
├───────────────────┼─────────────┼─────────────┼─────────┼──────────────────┼─────────┤
│ cell_observations │ 2024-03-04 │ 2024-03-04 │ 88a2…f │ ndvi / mean │ 0.41 │
│ │ 08:21Z │ 08:21Z │ │ │ │
├───────────────────┼─────────────┼─────────────┼─────────┼──────────────────┼─────────┤
│ cell_attributes │ — │ — │ 88a2…f │ elevation_m / │ 1340 │
│ │ │ │ │ mean │ │
└───────────────────┴─────────────┴─────────────┴─────────┴──────────────────┴─────────┘

What Recipe can do with these columns

cell_id plus the time interval is the universal join key. "Total rainfall in the animal's cell over the 7 days before each GPS fix" becomes one generic operation: join on cell_id, filter time_end <= fix_time AND available_at <= cutoff, aggregate. The same operation works for any variable from any source. That gives you the hand-off you described: every measurement as rows with time and location, in a shape Recipe never needs to special-case.

I recommend putting these four time columns plus cell_id into contracts/ as the required core for every canonical family. Lanes 1, 2, and 3 should agree on them before any lane writes code.

✻ Baked for 20s ·

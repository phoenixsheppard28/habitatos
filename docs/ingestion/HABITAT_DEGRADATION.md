# Habitat degradation

Status: implemented (P1). Section 11 lists what is implemented and what is deferred.

This document describes how the pipeline measures habitat degradation per 1 km cell.
It follows the shared rules in [README.md](README.md).
This topic adds no new family. It adds raw and derived variables to `cell_observations`.

## 1. Purpose and the direct answer

The question is: "Can we measure habitat degradation from what we have?"
The answer is: partly yes. The current sources give a good productivity signal. They do not give land cover or human pressure.

Degradation is an interpretation. The pipeline stores indicators. Analysis decides if a cell is degraded.

### What the current data measures now

The current sources are MODIS MOD13Q1 (NDVI, EVI, 16-day, from 2000-02-18), CHIRPS daily rainfall, and Sentinel-2 L2A (from 2015-06-27).

| Indicator | Inputs | Why it is useful |
| --- | --- | --- |
| Long-term NDVI and EVI trend | MODIS, 2000 to now | 25 years of productivity at 250 m. It covers the 2010–2013 tracking period. |
| Rain-use efficiency (RUE) | MODIS + CHIRPS | Productivity per mm of rain. A long decrease can show degradation. |
| RESTREND residual trend | MODIS + CHIRPS | The NDVI trend after the removal of the rainfall effect. It separates human or grazing effects from drought. |
| Seasonal amplitude | MODIS | A smaller green-up in the rains can show loss of grass cover. |
| Dry-season NDVI floor | MODIS | A low floor that decreases each year is a proxy for bare soil. |
| Productivity state | MODIS | Recent years compared with the baseline distribution of the same cell. |

### What is weak in the current data

- Sentinel-2 starts in June 2015. Sentinel-2 cannot show the 2010–2013 period.
- NDVI saturates in dense vegetation. EVI saturates less. Savanna grassland is not dense, so this risk is small in the focus area.
- Cloud removes many 16-day composites in the two rainy seasons. The `pixel_reliability` mask then removes the pixel.
- MODIS at 250 m mixes fences, small farms and grass inside one pixel.
- CHIRPS is 0.05° (about 5.5 km). Local rain differences inside one CHIRPS pixel are not visible.
- RUE and RESTREND are reliable only where NDVI follows rainfall. In wet or irrigated areas, the rainfall relation is weak.
- Fire also decreases NDVI for some months. Without burned-area data, a fire looks like degradation.

### What needs new inputs

| Need | New input |
| --- | --- |
| 30 m vegetation and bare-soil indices for 2010–2013 | Landsat Collection 2 Level-2 |
| Land cover classes (cropland, built area, bare ground) per year | ESA CCI Land Cover (1992–2020), Impact Observatory annual LULC (2017–2023), ESA WorldCover (2020, 2021) |
| Fire, so that a burn is not read as degradation | MODIS MCD64A1 burned area |
| Productivity in physical units | MODIS MOD17A3HGF annual NPP |
| Human pressure (settlement, roads, farms) | Global Human Modification (gHM), WorldPop, VIIRS night lights |
| Fences and roads (fragmentation) | OSM lines in `site_features`, see WATER_POINTS.md |
| Livestock pressure | Gridded Livestock of the World (GLW4) |

## 2. Indicators

Priority P1 is the first build. P2 is the second build. P3 is optional.

| Indicator | Variable name(s) | Unit | Inputs | Method | Time semantics | Raw or derived | Priority |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Landsat vegetation index | `ndvi`, `ndmi` | `index` | `landsat_c2_l2` | Zonal mean of clear pixels | `instant`, scene time | raw | P1 |
| Landsat bare-soil index | `bare_soil_index` | `index` | `landsat_c2_l2` | BSI formula, zonal mean | `instant` | raw | P1 |
| Land cover fraction | `landcover_fraction_<class>` | `fraction` | `esa_cci_lc`, `io_lulc_annual`, `esa_worldcover` | Class fraction per cell | `composite`, map year | raw | P1 (P2 for WorldCover) |
| Burned fraction | `burned_fraction` | `fraction` | `modis_mcd64a1` | Fraction of mapped pixels with a burn date | `composite`, month | raw | P1 |
| Annual NPP | `npp_annual` | `kgC/m2` | `modis_mod17a3hgf` | Zonal mean | `composite`, year | raw | P2 |
| Human modification | `human_modification`, `human_modification_<threat>` | `index` | `ghm` | Zonal mean | `composite`, 5-year step | raw | P2 |
| Annual NDVI summary | `ndvi_annual_mean`, `ndvi_annual_integral` | `index` | `modis_mod13q1` | Mean and sum of valid composites | `composite`, year | derived | P1 |
| Seasonal amplitude | `ndvi_seasonal_amplitude` | `index` | `modis_mod13q1` | P90 − P10 of the year | `composite`, year | derived | P1 |
| Dry-season floor | `ndvi_dry_floor` | `index` | `modis_mod13q1` | P10 of the year | `composite`, year | derived | P1 |
| Annual rainfall | `rain_annual_mm` | `mm` | `chirps` | Sum of daily values | `composite`, year | derived | P1 |
| NDVI trend | `ndvi_trend_slope`, `ndvi_trend_p_value` | `index/year`, `probability` | annual summary | Sen slope, Mann-Kendall | `composite`, analysis window | derived | P1 |
| Rain-use efficiency | `rue_mean`, `rue_trend_slope`, `rue_trend_p_value` | `index/mm`, `index/mm/year`, `probability` | annual summary + rain | Ratio, then trend | `composite`, analysis window | derived | P1 |
| RESTREND | `restrend_slope`, `restrend_p_value`, `ndvi_rain_r2` | `index/year`, `probability`, `ratio` | annual summary + rain | Regression residual trend | `composite`, analysis window | derived | P1 |
| Productivity state | `productivity_state_change` | `class` | annual summary | Trends.Earth state method | `composite`, analysis window | derived | P2 |
| Land cover change | `landcover_fraction_change_<class>` | `fraction` | land cover fractions | Last year − first year | `composite`, analysis window | derived | P2 |
| Road and fence density | `road_density_m_per_km2`, `fence_density_m_per_km2` | `m/km2` | `site_features` lines | Line length per cell | `static` or `composite` | derived | P2 |

## 3. New raw sources

All Planetary Computer (PC) collection ids below are verified with `https://planetarycomputer.microsoft.com/api/stac/v1/collections/<id>`.
PC access needs no account. The connector signs asset URLs, as for Sentinel-2.

| `source_id` | Provider | Content | Resolution | Time coverage | Access | License | Priority |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `landsat_c2_l2` | USGS, PC `landsat-c2-l2` | Surface reflectance, QA_PIXEL | 30 m | 1982-08-22 to now | PC STAC | USGS data policy (public domain) | P1 |
| `esa_cci_lc` | ESA CCI / C3S, PC `esa-cci-lc` | 22 LCCS classes per year | 300 m | 1992–2020 on PC | PC STAC | ESA CCI LC licence (not verified: reuse terms) | P1 |
| `io_lulc_annual` | Impact Observatory, Esri, Microsoft, PC `io-lulc-annual-v02` | 9 classes per year | 10 m | 2017–2023 | PC STAC | CC-BY-4.0 | P1 |
| `modis_mcd64a1` | NASA LP DAAC, PC `modis-64A1-061` | Monthly burn date | 500 m | 2000-11 to now | PC STAC | NASA open data | P1 |
| `esa_worldcover` | ESA, PC `esa-worldcover` | 11 classes | 10 m | 2020, 2021 | PC STAC | CC-BY-4.0 | P2 |
| `modis_mod17a3hgf` | NASA LP DAAC, PC `modis-17A3HGF-061` | Annual gap-filled NPP and GPP | 500 m | 2000 to now | PC STAC | NASA open data | P2 |
| `ghm` | Theobald et al., Zenodo 10.5281/zenodo.14449495 | Human modification 0–1, 8 threat layers | 300 m | 1990–2020, every 5 years | HTTPS download, no login | CC-BY-4.0 | P2 |
| `worldpop` | WorldPop, hub.worldpop.org | People per pixel | 100 m | 2000–2020 | HTTPS, no login | CC-BY-4.0 | P3 |
| `viirs_vnl` | Earth Observation Group, Colorado School of Mines | Annual night light radiance | about 500 m (not verified) | 2012 to now | Login for download (not verified) | CC-BY-4.0 | P3 |
| `glw4` | FAO, Harvard Dataverse 10.7910/DVN/LHBICE | Cattle per pixel | 5 arc-minutes (about 10 km) | 2015 only (2020 at FAO catalog) | HTTPS, no login | CC-BY-4.0 (not verified for the 2015 cattle file) | P3 |
| `hansen_gfc` | UMD / Google, GFC-2024-v1.12 | Tree cover 2000, loss year | 30 m | 2000–2024 | HTTPS from Google Cloud Storage | CC-BY-4.0 | P3 |
| `modis_mcd12q1` | NASA LP DAAC | IGBP land cover | 500 m | 2001 to about 2022 (not verified: last year) | Earthdata login; not on PC | NASA open data | P3 |

Sources that we do not use:

- Dynamic World. It is available only in Google Earth Engine (`GOOGLE/DYNAMICWORLD/V1`). An Earth Engine account is necessary. It starts in 2015, so it misses 2010–2013. IO annual LULC gives a similar 10 m product on PC.
- MCD12Q1 at P1. It is not on PC (`modis-12Q1-061` does not exist). ESA CCI gives the same years at 300 m without login.
- Hansen GFC at P1. It measures tree cover loss. The focus area is grassland and open savanna, so loss of tree cover is rare there. Use Hansen for forest regions.
- `io-biodiversity` (Biodiversity Intactness, 2017–2020, 100 m) is a model output, not an observation. Analysis can use it as a comparison only.

## 4. Raw sources

All PC raster sources use the existing STAC connector (`src/habitat/fetch/connectors/stac.py`).
Each source gets one `describe_<source>` function and one asset map.
Each normalizer uses `iter_aligned_blocks` from `raster_io.py` and `aggregate_blocks` from `zonal.py`.

### 4.1 `landsat_c2_l2` (P1)

- STAC collection: `landsat-c2-l2`. Asset host: `landsateuwest.blob.core.windows.net`. Add the host to `ASSET_HOSTS`.
- Assets: `blue`, `red`, `nir08`, `swir16`, `qa_pixel`. All are 30 m.
- Scale for reflectance (from the STAC `raster:bands`): `reflectance = DN × 0.0000275 − 0.2`. DN `0` is nodata.
- QA_PIXEL bits (from the STAC `classification:bitfields`): 0 fill, 1 dilated cloud, 3 cloud, 4 cloud shadow, 5 snow, 6 clear, 7 water.
- A pixel is valid only when bit 6 (clear) is 1 and bits 0, 1, 3, 4 and 5 are 0.
- Remove reflectance values outside 0 to 1 after the scale. Saturated pixels give such values.
- Variables: `ndvi` (nir08, red), `ndmi` (nir08, swir16), `bare_soil_index`.
- `bare_soil_index = ((swir16 + red) − (nir08 + blue)) / ((swir16 + red) + (nir08 + blue))`. Reference: Rikimaru et al. 2002 (not verified).
- `source_key`: `landsat_c2_l2:<item id>:<bbox key>`, as for Sentinel-2.
- `available_at`: the item property `created`. The STAC item has no USGS publication time. Quarantine an item without `created`.
- `processing_version`: `<landsat:collection_number>.<landsat:collection_category>`, for example `02.T1`.
- `time_precision`: `instant`. `time_start` = `time_end` = item `datetime`.
- `properties`: `platform`, `wrs_path`, `wrs_row`, `cloud_cover_land`.
- Search filter: `eo:cloud_cover <= 80`, the same default as Sentinel-2.
- `MAPPING_VERSION = "landsat-c2-l2-v1"`. `SOURCE_RESOLUTION_M = 30.0`.

Limits:

- In 2010–2013 over Athi-Kaputiei (WRS path 168, row 061), PC returns Landsat 5 and Landsat 7 items.
- Landsat 5 stopped normal imaging in November 2011. Landsat 8 data starts in April 2013.
- 2012 therefore has only Landsat 7 data. Since the SLC failure on 2003-05-31, each Landsat 7 scene loses about 22% of its pixels in stripes.
- The zonal rule `MIN_VALID_FRACTION = 0.5` sets `value = null` for cells with too many stripe pixels. The row stays, with `low_valid_fraction`.
- TM, ETM+ and OLI have different band responses. Do not mix platforms in one trend without a cross-sensor correction. Roy et al. 2016 give such coefficients (not verified). The correction is a derived step, not a raw step.

### 4.2 `esa_cci_lc` (P1)

- STAC collection: `esa-cci-lc`. Asset host: `landcoverdata.blob.core.windows.net`.
- Assets: `lccs_class` (uint8, nodata 0), `processed_flag`, `current_pixel_state`.
- Item example: `C3S-LC-L4-LCCS-Map-300m-P1Y-2020-v2.1.1-S45E000`. Each item is one year and one tile.
- `source_key`: `esa_cci_lc:<item id>:<bbox key>`.
- `available_at`: item `created`.
- `processing_version`: `esa_cci_lc:version`, for example `v2.1.1`. Versions 2.0.7 and 2.1.1 differ in method (not verified). Keep both values apart.
- `time_precision`: `composite`. `time_start` and `time_end` are the map year.
- Pixel mask: use only pixels with `processed_flag = 1`.
- The raster CRS is EPSG:4326. `CellAccumulator` needs a projected CRS. See section 6 for the warp.

### 4.3 `io_lulc_annual` (P1)

- STAC collection: `io-lulc-annual-v02`. Asset host: `ai4edataeuwest.blob.core.windows.net`.
- Asset: `data`. Class values: 0 no data, 1 water, 2 trees, 4 flooded vegetation, 5 crops, 7 built area, 8 bare ground, 9 snow/ice, 10 clouds, 11 rangeland.
- Items are UTM tiles per year, for example `37M-2023`. The years are 2017 to 2023.
- `source_key`: `io_lulc_annual:<item id>:<bbox key>`.
- `available_at`: item `created`. The items have no `created` property. Use the publication date of the V2 dataset (not verified: date). Never use the download time.
- `processing_version`: `v02`.
- `time_precision`: `composite`. `time_start` and `time_end` are the map year.
- Class 10 (clouds) is not a land class. Treat class 10 and class 0 as nodata.

### 4.4 `modis_mcd64a1` (P1)

- STAC collection: `modis-64A1-061`. Asset host: `modiseuwest.blob.core.windows.net`. The host is already allowed.
- Assets: `Burn_Date`, `Burn_Date_Uncertainty`, `QA`. All are 500 m, sinusoidal.
- `Burn_Date` values (Giglio, C6.1 user guide): 1–366 burn day, 0 unburned land, −1 unmapped, −2 water.
- `burned_fraction` per pixel: 1.0 for 1–366, 0.0 for 0, NaN for −1 and −2.
- `source_key`: `modis_mcd64a1:<item id>:<bbox key>`.
- `available_at`: item `created`.
- `processing_version`: `<collection>.<production time>` from the item id, as `modis_processing_version` does.
- `time_precision`: `composite`. `time_start` and `time_end` are the month.
- Product `mcd64a1-061`. The item id starts with `MCD64A1.`.

Limits: MCD64A1 misses small burns below about 500 m. EVENTS.md also stores fires as `point_events`. This source gives the per-cell monthly fraction only.

### 4.5 `esa_worldcover` (P2)

- STAC collection: `esa-worldcover`. Asset host: `ai4edataeuwest.blob.core.windows.net`.
- Asset: `map` (uint8, nodata 0). Optional asset `input_quality`.
- Items: 2020 (`v100`) and 2021 (`v200`). The two maps use different algorithms. Do not compute change between them.
- `processing_version`: `esa_worldcover:product_version`, for example `2.0.0`.
- `available_at`: item `created`. `time_precision`: `composite`, map year.
- Use WorldCover as a 10 m reference to check the other land cover maps. It is not a time series.

### 4.6 `modis_mod17a3hgf` (P2)

- STAC collection: `modis-17A3HGF-061`. Asset: `Npp_500m` (scale 0.0001, kg C/m²), `Npp_QC_500m`.
- The collection mixes Terra (`MOD17A3HGF.`) and Aqua (`MYD17A3HGF.`). Keep only Terra, as for MOD13Q1.
- Values above 32760 are fill values (not verified: exact range). Set them to NaN.
- `variable`: `npp_annual`, unit `kgC/m2`. `time_precision`: `composite`, the year.
- `processing_version` and `available_at` follow the MOD13Q1 rules.

### 4.7 `ghm` (P2)

- Source: Zenodo record 10.5281/zenodo.14449495, "Global human modification datasets of terrestrial ecosystems from 1990 to 2020", v1.
- Files: Cloud Optimized GeoTIFF, EPSG:4326, 300 m, years 1990, 1995, 2000, 2005, 2010, 2015, 2020.
- Layers: AA (all threats), AG, BU, EX, FR, HA, NS, PO, TI.
- Variables: `human_modification` (AA) and `human_modification_<code>` in lower case, for example `human_modification_ti`.
- The files are large (up to about 9.5 GB). The connector reads only the AOI window over HTTPS range requests (not verified: Zenodo range support).
- A new connector `src/habitat/fetch/connectors/zenodo_cog.py` is necessary. It writes the clipped window, as `clip_to_bbox` does.
- `source_key`: `ghm:<file name>:<bbox key>`. `processing_version`: the file version tag, for example `HMv20240801`.
- `available_at`: the Zenodo publication date, 2024-12-13.
- `time_precision`: `composite`. `time_start` and `time_end` are the year of the layer.

## 5. Derived indicators

The pipeline stores derived values in `cell_observations` with a `_derived` `source_id`:

| `source_id` | `mapping_version` | Variables |
| --- | --- | --- |
| `vegetation_annual_derived` | `vegetation-annual-v1` | `ndvi_annual_mean`, `ndvi_annual_integral`, `ndvi_seasonal_amplitude`, `ndvi_dry_floor`, `rain_annual_mm` |
| `vegetation_trend_derived` | `vegetation-trend-v1` | NDVI trend, RUE and RESTREND variables |
| `landcover_change_derived` | `landcover-change-v1` | `landcover_fraction_change_<class>`, `productivity_state_change` |

### 5.1 Inputs and windows

- Read inputs from `current_cell_observations`, for one dataset version per input series.
- Use only rows with `quality_flag` `ok` or `preliminary`. Count the other rows as missing.
- The default year is the calendar year. A caller can give another season window, for example a rainfall year.
- The default analysis window is 2001–2024 for MODIS. GPG v2 for SDG 15.3.1 recommends 16 years for the trend.
- The default baseline for RESTREND regression and for productivity state is 2001–2015.

### 5.2 Formulas

For cell c and year y, with n valid 16-day composites:

```text
ndvi_annual_mean[c,y]      = mean(NDVI of valid composites)
ndvi_annual_integral[c,y]  = ndvi_annual_mean[c,y] × 23           # 23 composites per year
ndvi_seasonal_amplitude    = P90(NDVI) − P10(NDVI) of the year
ndvi_dry_floor             = P10(NDVI) of the year
rain_annual_mm[c,y]        = sum of daily CHIRPS rainfall_mm

rue[c,y]                   = ndvi_annual_integral[c,y] / rain_annual_mm[c,y]
rue_mean                   = mean of rue over the window
rue_trend_slope            = Sen slope of rue against year

ndvi_trend_slope           = Sen slope of ndvi_annual_integral against year
*_p_value                  = two-sided Mann-Kendall p-value of the same series

fit on the baseline years:  ndvi_annual_integral = a + b × rain_annual_mm
ndvi_rain_r2               = R² of that fit
residual[c,y]              = observed − (a + b × rain_annual_mm[c,y])
restrend_slope             = OLS slope of residual against year, over the window
restrend_p_value           = p-value of that slope
```

Method references:

- RUE: Le Houérou 1984 (not verified); Prince, De Colstoun and Kravitz 1998, Global Change Biology 4: 359–374 (not verified: pages).
- RESTREND: Evans and Geerken 2004, Journal of Arid Environments; Wessels et al. 2007, Journal of Arid Environments 68(2): 271–297.
- Limits of RESTREND: Wessels et al. 2012, Remote Sensing of Environment (not verified: volume) ("Limits to detectability of land degradation by trend analysis of vegetation index data").
- Trends.Earth uses annual NDVI integrals, linear regression with a Mann-Kendall test (p ≤ 0.05), and RESTREND, RUE or WUE as climate correction.
- Trends.Earth state: baseline NDVI in 10 percentile classes. A loss of 2 classes or more in the last 3 years marks possible degradation.
- Trends.Earth combines productivity, land cover and soil carbon with "one out, all out". We store the parts. Analysis applies the rule.

### 5.3 Minimum data rules

| Rule | Action |
| --- | --- |
| Fewer than 16 of 23 valid composites in a year | `ndvi_annual_*` = null, flag `low_valid_fraction` |
| A year has preliminary CHIRPS days | Compute, flag `preliminary` |
| Fewer than 10 valid years in the window | Trend values = null, flag `short_series` |
| 10 to 15 valid years | Compute, flag `short_series` |
| RESTREND fit with `b <= 0` or fit p-value > 0.05 | `restrend_*` = null, flag `weak_rain_relation`; keep `ndvi_rain_r2` |
| Rain-use ratio with `rain_annual_mm < 50` | `rue` for that year = null (not verified: threshold choice) |
| Inputs from more than one Landsat platform without correction | Do not compute a Landsat trend |

`valid_fraction` of a derived row is valid years / window years. `pixel_count` is the number of valid input rows.

### 5.4 Storage

- `time_start` and `time_end` are the analysis window. An annual value has the year as its window.
- `time_precision` is `composite`. `stat` is `trend`, `ratio`, `mean`, `sum` or `percentile`.
- `available_at` is the latest `available_at` of all input rows. Then a Recipe point-in-time join never sees a value before its inputs exist.
- `source_resolution_m` is the coarsest input resolution. RUE uses CHIRPS, so the value is 5566.0.
- `product_status` is `preliminary` when any input row is preliminary.

### 5.5 Provenance and recomputation

- The pipeline writes a `RawManifest` for each derived run. The manifest has `kind = "derived"` (a new `SourceItem.kind` value).
- `properties.inputs` lists each input as `{dataset_id, dataset_version, mapping_version, variable}`.
- `processing_version` is a short hash of the sorted input list and the method parameters.
- `source_item_id` is `<method>:<window start>:<window end>:<processing_version>`.
- `ingest_batches.raw_manifest` keeps the manifest. Analysis reads the input versions from there.
- Recompute when an input series gets a new version, or when `mapping_version` changes.
- A new run with the same window supersedes the old batch, as for raw sources.
- Migration 010 adds the interval length to the partition key of the current views. Two windows with the same start and different ends are then both current.

## 6. Normalizer design

### 6.1 Categorical rasters

- Do not compute a mean of class codes.
- For each common class k, make an array with 1.0 where the pixel is class k, 0.0 where it is another valid class, NaN where it is nodata.
- `aggregate_blocks` then gives the mean of that array. The mean is the class fraction. `stat` is `fraction`.
- Each source maps its codes to the common classes in a fixed table. The table is part of `mapping_version`.

| Common class | ESA CCI codes | IO LULC codes | WorldCover codes |
| --- | --- | --- | --- |
| `tree` | 50–90, 100, 160, 170 | 2 | 10, 95 |
| `rangeland` | 40, 110, 120–122, 130, 150–153 | 11 | 20, 30 |
| `cropland` | 10–12, 20, 30 | 5 | 40 |
| `wetland` | 180 | 4 | 90 |
| `built` | 190 | 7 | 50 |
| `bare` | 140, 200–202 | 8 | 60, 100 |
| `water` | 210 | 1 | 80 |
| `snow` | 220 | 9 | 70 |

- `rangeland` joins shrub and grass, because IO LULC has no split. Bush encroachment then shows as a change in `tree` only.
- An unknown class code in the AOI is a QuarantineError. A class code mapping is never a guess.

### 6.2 Geographic rasters

- ESA CCI, WorldCover and gHM use EPSG:4326. `CellAccumulator.add_block` rejects a geographic CRS.
- Add `iter_warped_blocks` to `raster_io.py`. It opens the asset through a rasterio `WarpedVRT` in the grid CRS.
- Use nearest resampling for classes. Use the native pixel size in metres at the AOI centre.

### 6.3 `quality_flag` values

| Value | Meaning |
| --- | --- |
| `ok` | No issue |
| `low_valid_fraction` | Valid fraction below 0.5 (existing) |
| `preliminary` | Preliminary input (existing) |
| `slc_off` | Landsat 7 scene after 2003-05-31 |
| `tier2_geometry` | Landsat Collection 2 Tier 2 item |
| `short_series` | Fewer than 16 valid years in a trend window |
| `weak_rain_relation` | RESTREND fit not valid |
| `cross_version_inputs` | A derived window mixes two `processing_version` values of one input |

### 6.4 QuarantineError cases

- A required asset is missing.
- The Landsat item has no `created` property, or no `landsat:collection_number`.
- The Landsat item is Collection 1, or the scale and offset in `raster:bands` differ from 0.0000275 and −0.2.
- A land cover raster contains a class code that the mapping table does not list.
- The MCD64A1 `Burn_Date` contains a value outside −2 to 366.
- The gHM file name has no year or no layer code.
- A derived run has no input dataset version for one of its inputs.
- A derived run gets inputs with different units for one variable.

## 7. Fetch agent changes

New `data_kinds`:

| Data kind | Sources |
| --- | --- |
| `vegetation_observations` | add `landsat_c2_l2` |
| `surface_reflectance` | add `landsat_c2_l2` |
| `land_cover` | `esa_cci_lc`, `io_lulc_annual`, `esa_worldcover`, `modis_mcd12q1` |
| `fire_observations` | `modis_mcd64a1` |
| `productivity` | `modis_mod17a3hgf` |
| `human_pressure` | `ghm`, `worldpop`, `viirs_vnl`, `glw4` |
| `habitat_degradation` | the three `_derived` sources |

Tool changes:

- `fetch_environment`: add `landsat_c2_l2`, `esa_cci_lc`, `io_lulc_annual`, `esa_worldcover`, `modis_mcd64a1` and `modis_mod17a3hgf` to the source list. Update the docstring.
- Land cover maps are annual. One map per year and tile is one item. The default `max_items = 1` gives one year only. The agent must ask for more years.
- For Landsat over 2010–2013, the agent must give `max_items` per year. Landsat gives about 2 scenes per month per path and row.
- Add the tool `derive_habitat_indicators(bbox, window_start, window_end, baseline_start, baseline_end)`. It runs the derived step on stored rows. It downloads nothing.
- The derived tool returns the input dataset versions and the list of flags. It never returns a "degraded" label.
- Add the new asset hosts to `ASSET_HOSTS`: `landsateuwest.blob.core.windows.net`, `landcoverdata.blob.core.windows.net`, `ai4edataeuwest.blob.core.windows.net`.

## 8. Tests

- `tests/test_indices.py`: Landsat scale and offset; QA_PIXEL mask for each bit; `bare_soil_index` on known values.
- `tests/test_zonal.py`: class fraction arrays; nodata gives NaN, not 0. `class_fractions` is in `zonal.py`.
- `tests/test_landcover.py`: a 3 × 3 cell fixture with known classes gives the expected fractions. An unknown code raises `QuarantineError`.
- `tests/test_stac.py`: `describe_landsat`, `describe_esa_cci_lc`, `describe_io_lulc`, `describe_mcd64a1` on recorded STAC item JSON.
- `tests/test_stac.py`: a Landsat item without `created` raises `QuarantineError`.
- `tests/test_derived.py`: synthetic NDVI and rain series with a known slope. Check `ndvi_trend_slope`, `restrend_slope` and `rue_mean`.
- `tests/test_derived.py`: a series with no rainfall relation gives `weak_rain_relation`. A 9-year series gives `short_series` and null values.
- `tests/test_derived.py`: `available_at` of a derived row is the maximum of its inputs.
- `tests/test_series.py`: two derived windows with the same start and different ends are both current after the migration.
- `tests/test_live.py`: one Landsat scene of 2011 over path 168, row 061, and one ESA CCI tile for 2012.

## 9. Risks and open questions

- Degradation is an interpretation. A lower NDVI has many possible causes: drought, fire, grazing, cropping, or a wet year before the window. The pipeline stores indicators. Analysis decides.
- RESTREND assumes a linear rainfall relation that does not change. A strong degradation can change the relation itself. TSS-RESTREND (Burrell et al. 2017) handles breaks (not verified: method details).
- RUE decreases when rainfall increases, even without degradation (not verified: source). Analysis must not use RUE alone.
- Athi-Kaputiei has two rainy seasons per year (not verified). A calendar year mixes both seasons. A per-season window can be necessary.
- CHIRPS at 5.5 km is much coarser than MODIS at 250 m. Many MODIS cells share one rainfall value.
- ESA CCI at 300 m is too coarse for small farms and fences. IO LULC is 10 m but starts in 2017.
- The land cover maps use different methods. A change between two products is not a land change.
- ESA CCI on PC ends in 2020. Later years are in the Copernicus Climate Data Store (not verified: years and access).
- Fences are poorly mapped in OSM. A low `fence_density_m_per_km2` does not show that a cell has no fences.
- The ESA CCI licence on PC is "proprietary". Confirm that `reuse_allowed = true` is correct before publication.
- The IO LULC items have no `created` date. Confirm the dataset publication date.
- `cell_observations` has no `attributes` column and no `source_record_id`. The provenance of derived values is in `ingest_batches.raw_manifest`. Confirm that this is sufficient for Analysis.
- `published_at` in `stac.py` never uses the retrieval time. The connector skips an item without a publication date.

## 10. Build steps

1. Do the shared code changes in [README.md](README.md). This topic needs only `SourceItem.kind = "derived"` in addition.
2. Add migration `010_current_rows_time_end.sql`. Add `time_end` to the partition key of `current_cell_observations` and `recipe_cell_observations`.
3. Add `landsat_c2_l2`: asset map, `describe_landsat`, QA mask in `indices.py`, normalizer, registry entry, tests.
4. Add `iter_warped_blocks` to `raster_io.py` and the class fraction helper `class_fractions` to `zonal.py`. The foundation did this step.
5. Add `esa_cci_lc` and `io_lulc_annual` with the mapping tables. Add tests.
6. Add `modis_mcd64a1`. Add tests.
7. Add the derived step in `src/habitat/derive/vegetation.py`: annual summaries first, then trends, RUE and RESTREND.
8. Add the `derive_habitat_indicators` tool and the new `data_kinds` to `src/habitat/fetch/tools.py`.
9. Add a section for each new source to `SOURCES.md`.
10. Run the live test for 2010–2013 over Athi-Kaputiei. Compare the Landsat NDVI with MODIS NDVI per cell.
11. Add the P2 sources: `esa_worldcover`, `modis_mod17a3hgf`, `ghm`, and the line densities from `site_features`.

## 11. Implementation status

### Implemented (P1)

| Part | Files |
| --- | --- |
| `landsat_c2_l2` | `src/habitat/fetch/connectors/landsat.py`, `src/habitat/normalize/sources/landsat.py` |
| `esa_cci_lc`, `io_lulc_annual` | `src/habitat/fetch/connectors/landcover.py`, `src/habitat/normalize/sources/landcover.py` |
| `modis_mcd64a1` | `src/habitat/fetch/connectors/burned_area.py`, `src/habitat/normalize/sources/burned_area.py` |
| `vegetation_annual_derived`, `vegetation_trend_derived` | `src/habitat/derive/vegetation.py`, `trends.py`, `run.py`, `registry.py` |
| Agent tool `derive_habitat_indicators`, new sources in `fetch_environment` | `src/habitat/fetch/tools.py`, `src/habitat/fetch/service.py` |
| Recipe view `recipe_habitat_indicators`, family `habitat_indicators` | `migrations/015_habitat_degradation.sql`, `src/habitat/recipe_inputs.py` |

The P1 raw variables are `ndvi`, `ndmi`, `bare_soil_index`, `landcover_fraction_<class>` and `burned_fraction`.
The P1 derived variables are the annual summaries, `rain_annual_mm`, the NDVI trend, the RUE variables and the RESTREND variables.

### Differences from the design

- The IO LULC items have no `created` date. `available_at` is the creation time of the file in the Planetary Computer storage. The connector reads the `x-ms-creation-time` header, else `Last-Modified`. CHIRPS uses `Last-Modified` in the same way.
- The IO LULC search returns the tiles of UTM zones 1 and 60 for every bbox. The connector keeps only the tiles of the UTM zones of the bbox.
- The ESA CCI connector fetches `lccs_class` and `processed_flag` only. The MCD64A1 connector fetches `Burn_Date` only. The normalizers do not use the other assets.
- The fetch agent gets the new sources only when it names them. The default sources of `fetch_environment` do not change.
- `cell_observations` keeps one row per cell and variable in each batch. Thus `vegetation_annual_derived` writes one batch for each year. `vegetation_trend_derived` writes one batch for each window.
- `source_item_id` of a derived batch also contains the bbox: `<method>:<start>:<end>:<bbox key>:<processing_version>`.
- A new run supersedes only the earlier runs of the same method, period and bbox. Two runs with overlapping bboxes can give two current values for one cell.
- `rain_annual_mm` is null when one day of the year is missing. A partial sum is too low.
- The `short_series` rule also applies to `rue_mean`.
- `cross_version_inputs` compares the collection part of `processing_version`, for example `061`. The MODIS production time is different in each composite.
- `stat` is `trend` for the p-values and for `ndvi_rain_r2`.
- RESTREND needs 3 or more baseline years with NDVI and rain. With fewer years, or with constant rain, all RESTREND values are null with the flag `weak_rain_relation`.
- The Recipe lane reads the indicators from the new family `habitat_indicators`. A row has the variable name, the unit and the statistic.

### Deferred

- The P2 sources: `esa_worldcover`, `modis_mod17a3hgf` and `ghm`.
- The P2 derived values: `productivity_state_change`, `landcover_change_derived` and the road and fence densities.
- The P3 sources.
- A Landsat trend and the cross-sensor correction for Landsat platforms. The pipeline stores Landsat values per scene only.
- Build step 10: the comparison of Landsat NDVI with MODIS NDVI over Athi-Kaputiei for 2010–2013.
- A historical mode that ignores the `available_at` cutoff. The Recipe lane owns this change.

## References

- [Landsat C2 L2 STAC collection](https://planetarycomputer.microsoft.com/api/stac/v1/collections/landsat-c2-l2)
- [ESA CCI LC STAC collection](https://planetarycomputer.microsoft.com/api/stac/v1/collections/esa-cci-lc)
- [IO annual LULC v02 STAC collection](https://planetarycomputer.microsoft.com/api/stac/v1/collections/io-lulc-annual-v02)
- [ESA WorldCover STAC collection](https://planetarycomputer.microsoft.com/api/stac/v1/collections/esa-worldcover)
- [MODIS MCD64A1 STAC collection](https://planetarycomputer.microsoft.com/api/stac/v1/collections/modis-64A1-061)
- [MODIS MOD17A3HGF STAC collection](https://planetarycomputer.microsoft.com/api/stac/v1/collections/modis-17A3HGF-061)
- [MCD64A1 C6.1 user guide](https://lpdaac.usgs.gov/documents/1006/MCD64_User_Guide_V61.pdf)
- [MCD12Q1 v061](https://lpdaac.usgs.gov/products/mcd12q1v061/)
- [Global human modification 1990–2020](https://zenodo.org/records/14449495)
- [Hansen GFC 2000–2024 download](https://storage.googleapis.com/earthenginepartners-hansen/GFC-2024-v1.12/download.html)
- [Dynamic World in Earth Engine](https://developers.google.com/earth-engine/datasets/catalog/GOOGLE_DYNAMICWORLD_V1)
- [WorldPop global 2000–2020](https://hub.worldpop.org/Global1_2000-2020)
- [VIIRS night lights, EOG](https://eogdata.mines.edu/products/vnl/)
- [GLW4 cattle 2015](https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/LHBICE)
- [Trends.Earth SDG 15.3.1](http://docs.trends.earth/en/latest/for_users/features/unccdreporting.html)
- [Good Practice Guidance SDG 15.3.1 v2](https://www.unccd.int/sites/default/files/relevant-links/2021-03/Indicator_15.3.1_GPG_v2_29Mar_Advanced-version.pdf)
- [Wessels et al. 2007](https://www.sciencedirect.com/science/article/abs/pii/S014019630600190X)
- [Wessels et al. 2012, limits to detectability](https://www.sciencedirect.com/science/article/abs/pii/S0034425712002581)
- [TSS-RESTREND, Burrell et al. 2017](https://www.sciencedirect.com/science/article/abs/pii/S0034425717302171)

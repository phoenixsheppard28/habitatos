import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from habitat.archive.store import ArtifactStore
from habitat.contracts import BBox, RawManifest
from habitat.grid import Grid
from habitat.normalize.indices import sentinel2_indices
from habitat.normalize.raster_io import RasterBlock, iter_aligned_blocks
from habitat.normalize.rows import NormalizedBatch, QuarantineError, to_cell_observations
from habitat.normalize.water_indices import MIN_WATER_PIXELS, WATER_VARIABLES, sentinel2_ndci, sentinel2_ndti
from habitat.normalize.zonal import aggregate_blocks

MAPPING_VERSION = "sentinel2-l2a-v2"
REQUIRED_ASSETS = ("green", "red", "nir", "swir16", "scl")
WATER_ASSETS = ("green", "red", "swir16", "scl")
UNITS = {"ndvi": "index", "mndwi": "index", "ndmi": "index", "ndti": "index", "ndci": "index"}
SOURCE_RESOLUTION_M = 10.0
REDEDGE_RESOLUTION_M = 20.0
# A water proxy needs only one valid water pixel; `few_water_pixels` flags a small count.
WATER_MIN_VALID_FRACTION = 0.0


def normalize_sentinel2(
    manifest: RawManifest, store: ArtifactStore, grid: Grid, aoi: BBox | None = None
) -> NormalizedBatch:
    item = manifest.extensions
    missing = [name for name in REQUIRED_ASSETS if name not in item.assets]
    if missing:
        raise QuarantineError(f"{item.source_item_id}: missing assets {missing}")

    if "boa_add_offset" not in item.properties:
        raise QuarantineError(f"{item.source_item_id}: unknown BOA_ADD_OFFSET; reflectance scale is ambiguous")
    boa_add_offset = float(item.properties["boa_add_offset"])

    def compute(block: RasterBlock):
        bands = block.bands
        return sentinel2_indices(
            b3=bands["green"], b4=bands["red"], b8=bands["nir"], b11=bands["swir16"], scl=bands["scl"],
            boa_add_offset=boa_add_offset,
        )

    def compute_ndti(block: RasterBlock):
        bands = block.bands
        return {"ndti": sentinel2_ndti(bands["green"], bands["red"], bands["swir16"], bands["scl"], boa_add_offset)}

    def compute_ndci(block: RasterBlock):
        bands = block.bands
        return {
            "ndci": sentinel2_ndci(
                bands["green"], bands["red"], bands["rededge"], bands["swir16"], bands["scl"], boa_add_offset
            )
        }

    assets = {name: str(store.open(manifest, name)) for name in REQUIRED_ASSETS}
    stats = aggregate_blocks(grid, iter_aligned_blocks(assets, reference="red", aoi=aoi), compute)
    water_assets = {name: assets[name] for name in WATER_ASSETS}
    ndti = aggregate_blocks(
        grid, iter_aligned_blocks(water_assets, reference="red", aoi=aoi), compute_ndti, WATER_MIN_VALID_FRACTION
    )
    stats = pd.concat([stats, ndti], ignore_index=True) if not ndti.empty else stats
    tables = [to_cell_observations(stats, manifest, grid, MAPPING_VERSION, "mean", UNITS, SOURCE_RESOLUTION_M).table]

    if "rededge" in item.assets:
        rededge_assets = {**water_assets, "rededge": str(store.open(manifest, "rededge"))}
        ndci = aggregate_blocks(
            grid, iter_aligned_blocks(rededge_assets, reference="rededge", aoi=aoi), compute_ndci,
            WATER_MIN_VALID_FRACTION,
        )
        tables.append(
            to_cell_observations(ndci, manifest, grid, MAPPING_VERSION, "mean", UNITS, REDEDGE_RESOLUTION_M).table
        )

    return NormalizedBatch(with_few_water_pixel_flags(pa.concat_tables(tables)), MAPPING_VERSION)


def with_few_water_pixel_flags(table: pa.Table) -> pa.Table:
    water = pc.is_in(table["variable"], pa.array(sorted(WATER_VARIABLES)))
    few = pc.and_(water, pc.less(table["pixel_count"], MIN_WATER_PIXELS))
    few = pc.and_(few, pc.equal(table["quality_flag"], "ok"))
    flags = pc.if_else(few, "few_water_pixels", table["quality_flag"])

    index = table.schema.get_field_index("quality_flag")
    return table.set_column(index, table.schema.field(index), flags)

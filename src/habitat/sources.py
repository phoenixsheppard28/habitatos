from collections.abc import Callable
from dataclasses import dataclass

from habitat.archive.store import ArtifactStore
from habitat.contracts import BBox, RawManifest
from habitat.fetch.connectors import Connector
from habitat.fetch.connectors.burned_area import PRODUCT as MCD64A1_PRODUCT
from habitat.fetch.connectors.burned_area import fetch_mcd64a1
from habitat.fetch.connectors.chirps import PRODUCT as CHIRPS_PRODUCT
from habitat.fetch.connectors.chirps import STORAGE_FORMAT as CHIRPS_FORMAT
from habitat.fetch.connectors.chirps import fetch_chirps
from habitat.fetch.connectors.fixture import PRODUCT as FIXTURE_PRODUCT
from habitat.fetch.connectors.fixture import fetch_fixture
from habitat.fetch.connectors.landsat import PRODUCT as LANDSAT_PRODUCT
from habitat.fetch.connectors.landsat import fetch_landsat
from habitat.fetch.connectors.landcover import ESA_CCI_PRODUCT, IO_LULC_PRODUCT, fetch_esa_cci_lc, fetch_io_lulc
from habitat.fetch.connectors.movebank_repository import PRODUCT as REPOSITORY_PRODUCT
from habitat.fetch.connectors.movebank_repository import fetch_movebank_repository
from habitat.fetch.connectors.movebank_study import PRODUCT as STUDY_PRODUCT
from habitat.fetch.connectors.movebank_study import fetch_movebank_study
from habitat.fetch.connectors.stac import MODIS_PRODUCT, SENTINEL2_PRODUCT, fetch_modis, fetch_sentinel2
from habitat.fetch.connectors.stac import STORAGE_FORMAT as STAC_FORMAT
from habitat.fetch.connectors.zenodo import PRODUCT as ZENODO_PRODUCT
from habitat.fetch.connectors.zenodo import fetch_zenodo
from habitat.grid import Grid
from habitat.normalize.rows import NormalizedBatch
from habitat.normalize.sources.burned_area import normalize_mcd64a1
from habitat.normalize.sources.chirps import normalize_chirps
from habitat.normalize.sources.landsat import normalize_landsat
from habitat.normalize.sources.landcover import normalize_esa_cci_lc, normalize_io_lulc
from habitat.normalize.sources.modis import normalize_modis
from habitat.normalize.sources.movebank import normalize_movebank, normalize_movebank_study
from habitat.normalize.sources.sentinel2 import normalize_sentinel2

Normalizer = Callable[[RawManifest, ArtifactStore, Grid, BBox | None], NormalizedBatch]


@dataclass(frozen=True)
class Source:
    source_id: str
    product: str
    description: str
    data_kinds: frozenset[str]
    fetch: Connector
    normalizer: Normalizer | None
    storage_format: str
    needs_area_and_dates: bool = False
    needs_item: bool = False


SOURCES: dict[str, Source] = {
    source.source_id: source
    for source in [
        Source(
            "sentinel2", SENTINEL2_PRODUCT,
            "Sentinel-2 L2A NDVI, MNDWI and NDMI per 1 km cell and acquisition",
            frozenset({"surface_reflectance", "vegetation_observations", "water_observations"}),
            fetch_sentinel2, normalize_sentinel2, STAC_FORMAT, needs_area_and_dates=True,
        ),
        Source(
            "modis_mod13q1", MODIS_PRODUCT,
            "MODIS Terra MOD13Q1 16-day NDVI and EVI per 1 km cell",
            frozenset({"vegetation_observations"}),
            fetch_modis, normalize_modis, STAC_FORMAT, needs_area_and_dates=True,
        ),
        Source(
            "chirps", CHIRPS_PRODUCT,
            "CHIRPS v2.0 daily rainfall per 1 km cell",
            frozenset({"rainfall_observations"}),
            fetch_chirps, normalize_chirps, CHIRPS_FORMAT, needs_area_and_dates=True,
        ),
        Source(
            "landsat_c2_l2", LANDSAT_PRODUCT,
            "Landsat Collection 2 Level-2 NDVI, NDMI and bare soil index per 1 km cell and acquisition",
            frozenset({"surface_reflectance", "vegetation_observations"}),
            fetch_landsat, normalize_landsat, STAC_FORMAT, needs_area_and_dates=True,
        ),
        Source(
            "esa_cci_lc", ESA_CCI_PRODUCT,
            "ESA CCI annual land cover as class fractions per 1 km cell, 1992-2020",
            frozenset({"land_cover"}),
            fetch_esa_cci_lc, normalize_esa_cci_lc, STAC_FORMAT, needs_area_and_dates=True,
        ),
        Source(
            "io_lulc_annual", IO_LULC_PRODUCT,
            "Impact Observatory 10 m annual land use and land cover as class fractions per 1 km cell, 2017-2023",
            frozenset({"land_cover"}),
            fetch_io_lulc, normalize_io_lulc, STAC_FORMAT, needs_area_and_dates=True,
        ),
        Source(
            "modis_mcd64a1", MCD64A1_PRODUCT,
            "MODIS MCD64A1 monthly burned area as the burned fraction per 1 km cell",
            frozenset({"fire_observations"}),
            fetch_mcd64a1, normalize_mcd64a1, STAC_FORMAT, needs_area_and_dates=True,
        ),
        Source(
            "movebank_repository", REPOSITORY_PRODUCT,
            "Animal GPS fixes from published Movebank Data Repository packages",
            frozenset({"animal_locations"}),
            fetch_movebank_repository, normalize_movebank, "csv", needs_item=True,
        ),
        Source(
            "movebank_study", STUDY_PRODUCT,
            "Animal GPS fixes from the Movebank direct-read API, by study",
            frozenset({"animal_locations"}),
            fetch_movebank_study, normalize_movebank_study, "csv", needs_item=True,
        ),
        Source(
            "zenodo", ZENODO_PRODUCT,
            "One file of an open Zenodo research record; no canonical mapping",
            frozenset({"external_research_data"}),
            fetch_zenodo, None, "any", needs_item=True,
        ),
        Source(
            "fixture", FIXTURE_PRODUCT,
            "Synthetic demo data for development and tests; never published",
            frozenset({"animal_locations", "rainfall_observations"}),
            fetch_fixture, None, "csv", needs_item=True,
        ),
    ]
}


def get_source(source_id: str) -> Source | None:
    return SOURCES.get(source_id)


def sources_for_kind(data_kind: str) -> list[Source]:
    return [source for source in SOURCES.values() if data_kind in source.data_kinds]

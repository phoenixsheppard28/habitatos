from collections.abc import Callable
from dataclasses import dataclass

from habitat.archive.store import ArtifactStore
from habitat.contracts import BBox, RawManifest
from habitat.fetch.connectors import Connector
from habitat.fetch.connectors.chirps import PRODUCT as CHIRPS_PRODUCT
from habitat.fetch.connectors.chirps import STORAGE_FORMAT as CHIRPS_FORMAT
from habitat.fetch.connectors.chirps import fetch_chirps
from habitat.fetch.connectors.fixture import PRODUCT as FIXTURE_PRODUCT
from habitat.fetch.connectors.fixture import fetch_fixture
from habitat.fetch.connectors.firms import MODIS as FIRMS_MODIS
from habitat.fetch.connectors.firms import STORAGE_FORMAT as FIRMS_FORMAT
from habitat.fetch.connectors.firms import VIIRS_SNPP as FIRMS_VIIRS
from habitat.fetch.connectors.firms import fetch_firms_modis, fetch_firms_viirs
from habitat.fetch.connectors.gbif_occurrence import PRODUCT as GBIF_OCCURRENCE_PRODUCT
from habitat.fetch.connectors.gbif_occurrence import STORAGE_FORMAT as GBIF_OCCURRENCE_FORMAT
from habitat.fetch.connectors.gbif_occurrence import fetch_gbif_occurrence
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
from habitat.normalize.sources.chirps import normalize_chirps
from habitat.normalize.sources.firms import normalize_firms
from habitat.normalize.sources.gbif_occurrence import normalize_gbif_occurrence
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
        Source(
            "firms_modis", FIRMS_MODIS.product,
            "NASA FIRMS MODIS Terra and Aqua active fire detections (standard product), one row per fire pixel",
            frozenset({"fire_events"}),
            fetch_firms_modis, normalize_firms, FIRMS_FORMAT, needs_area_and_dates=True,
        ),
        Source(
            "firms_viirs", FIRMS_VIIRS.product,
            "NASA FIRMS VIIRS S-NPP 375 m active fire detections (standard product), one row per fire pixel",
            frozenset({"fire_events"}),
            fetch_firms_viirs, normalize_firms, FIRMS_FORMAT, needs_area_and_dates=True,
        ),
        Source(
            "gbif_occurrence", GBIF_OCCURRENCE_PRODUCT,
            "GBIF species occurrence records (includes iNaturalist and eBird); presence-only, one row per record",
            frozenset({"species_occurrences", "wildlife_mortality_events"}),
            fetch_gbif_occurrence, normalize_gbif_occurrence, GBIF_OCCURRENCE_FORMAT, needs_area_and_dates=True,
        ),
    ]
}


def get_source(source_id: str) -> Source | None:
    return SOURCES.get(source_id)


def sources_for_kind(data_kind: str) -> list[Source]:
    return [source for source in SOURCES.values() if data_kind in source.data_kinds]

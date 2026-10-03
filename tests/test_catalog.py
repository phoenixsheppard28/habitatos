from datetime import UTC, datetime

import pytest
from shapely.geometry import box

from habitat.catalog.store import LocalCatalog
from habitat.catalog.tags import deterministic_tags, merge_tags, validate_ai_tags
from habitat.contracts import Coverage, DatasetVersion, SearchFilters, StorageRef, Tag, TagOrigin, TaxonRef

SPRINGBOK = TaxonRef(gbif_key=2441057, name="Antidorcas marsupialis")
ETOSHA = box(15.5, -19.5, 17.0, -18.5)


def dataset(dataset_id, family, footprint, start, end, species=(), tags=(), version=1, summary=None):
    return DatasetVersion(
        dataset_id=dataset_id, version=version, created_at=datetime(2026, 10, 3, tzinfo=UTC), access_scope="public",
        family=family, source_id="test", description=f"{family} test data", status="ready",
        storage=StorageRef(uri=f"artifact://{dataset_id}/{version}", format="parquet"), row_grain="row",
        row_count=10, raw_artifact_refs=[], mapping_version="v1",
        coverage=Coverage(bbox=footprint.bounds, start=start, end=end), footprint_wkt=footprint.wkt,
        species=list(species), tags=list(tags), summary=summary,
    )


@pytest.fixture
def catalog(tmp_path):
    catalog = LocalCatalog(tmp_path / "catalog.json")
    catalog.register_dataset(dataset(
        "springbok-etosha", "animal_locations", box(15.8, -19.2, 16.6, -18.8),
        datetime(2019, 1, 1, tzinfo=UTC), datetime(2019, 12, 31, tzinfo=UTC), species=[SPRINGBOK],
        tags=[Tag(key="topic", value="drought", origin=TagOrigin.AI), Tag(key="study_design", value="gps_collar")],
    ))
    catalog.register_dataset(dataset(
        "zebra-kenya", "animal_locations", box(36.0, -1.5, 37.0, -0.5),
        datetime(2019, 1, 1, tzinfo=UTC), datetime(2019, 12, 31, tzinfo=UTC),
        species=[TaxonRef(gbif_key=2440902, name="Equus quagga")],
    ))
    catalog.register_dataset(dataset(
        "chirps-etosha", "cell_observations", box(15.0, -20.0, 17.5, -18.0),
        datetime(2018, 1, 1, tzinfo=UTC), datetime(2024, 12, 31, tzinfo=UTC),
    ))
    return catalog


def search(catalog, **filters):
    return [m.dataset.dataset_id for m in catalog.search_datasets(SearchFilters(access_scope=["public"], **filters))]


def test_search_by_geography_and_dates(catalog):
    found = search(
        catalog, region_wkt=ETOSHA.wkt,
        start=datetime(2019, 6, 1, tzinfo=UTC), end=datetime(2019, 9, 1, tzinfo=UTC),
    )

    assert set(found) == {"springbok-etosha", "chirps-etosha"}
    assert found[0] == "chirps-etosha"


def test_search_by_family_and_species(catalog):
    assert search(catalog, family=["animal_locations"], species=[SPRINGBOK]) == ["springbok-etosha"]


def test_dates_outside_coverage_do_not_match(catalog):
    found = search(catalog, family=["animal_locations"], start=datetime(2022, 1, 1, tzinfo=UTC), end=datetime(2022, 6, 1, tzinfo=UTC))

    assert found == []


def test_ai_tags_can_be_excluded(catalog):
    drought = [Tag(key="topic", value="drought")]

    assert search(catalog, tags_any=drought) == ["springbok-etosha"]
    assert search(catalog, tags_any=drought, include_ai_tags=False) == []


def test_matches_report_the_matched_tags(catalog):
    matches = catalog.search_datasets(
        SearchFilters(access_scope=["public"], tags_all=[Tag(key="study_design", value="gps_collar")])
    )

    assert [t.value for t in matches[0].matched_tags] == ["gps_collar"]


def test_unknown_footprint_is_kept_but_marked_unknown(catalog):
    unknown = dataset("unknown-coverage", "occurrences", box(0, 0, 1, 1), datetime(2019, 1, 1, tzinfo=UTC), datetime(2019, 2, 1, tzinfo=UTC))
    unknown.footprint_wkt = None
    catalog.register_dataset(unknown)

    match = next(m for m in catalog.search_datasets(SearchFilters(access_scope=["public"], region_wkt=ETOSHA.wkt))
                 if m.dataset.dataset_id == "unknown-coverage")

    assert match.spatial_overlap is None


def test_published_versions_are_immutable(catalog):
    with pytest.raises(ValueError, match="immutable"):
        catalog.register_dataset(dataset("zebra-kenya", "animal_locations", box(0, 0, 1, 1), None, None))


def test_ai_tags_outside_the_vocabulary_are_dropped():
    tags = validate_ai_tags([Tag(key="habitat", value="savanna"), Tag(key="habitat", value="jungle_paradise"), Tag(key="mood", value="happy")])

    assert [(t.key, t.value, t.origin) for t in tags] == [("habitat", "savanna", TagOrigin.AI)]


def test_ai_tags_never_override_deterministic_keys():
    computed = deterministic_tags("chirps", ["rainfall_mm"], datetime(2019, 1, 1, tzinfo=UTC), datetime(2020, 2, 1, tzinfo=UTC), None)
    ai = [Tag(key="study_design", value="camera_trap", origin=TagOrigin.AI), Tag(key="topic", value="drought", origin=TagOrigin.AI)]

    merged = merge_tags(computed, ai)

    assert ("study_design", "gridded_climate") in {(t.key, t.value) for t in merged}
    assert ("study_design", "camera_trap") not in {(t.key, t.value) for t in merged}
    assert {"2019", "2020"} <= {t.value for t in merged if t.key == "year"}


def test_region_layers_become_tags():
    tags = deterministic_tags(
        "chirps", [], None, None, ETOSHA,
        region_layers={"country": [("NA", box(11, -29, 25, -17)), ("AO", box(11, -17, 24, -4))]},
    )

    assert [t.value for t in tags if t.key == "country"] == ["NA"]

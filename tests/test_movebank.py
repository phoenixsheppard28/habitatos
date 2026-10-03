from datetime import UTC, datetime

import pytest

from conftest import make_manifest
from habitat.catalog.publish import publish_series_version
from habitat.catalog.store import PostgresCatalog
from habitat.contracts import SearchFilters, TaxonRef
from habitat.archive.store import LocalArtifactStore
from habitat.normalize.router import normalize
from habitat.normalize.rows import QuarantineError, series_id
from habitat.storage.series import SeriesStore

GPS_CSV = """event-id,visible,timestamp,location-long,location-lat,gps:dop,sensor-type,individual-taxon-canonical-name,tag-local-identifier,individual-local-identifier,study-name
1,true,2011-03-01 06:00:00.000,36.95,-1.45,4.6,"gps","Connochaetes taurinus","2829","Naboisho","Wildebeest"
2,true,2011-03-01 07:00:00.000,36.96,-1.45,7.2,"gps","Connochaetes taurinus","2829","Naboisho","Wildebeest"
3,false,2011-03-01 08:00:00.000,36.97,-1.46,40.0,"gps","Connochaetes taurinus","2829","Naboisho","Wildebeest"
4,true,2011-03-01 06:00:00.000,36.90,-1.40,,"gps","Connochaetes taurinus","2846","Olope","Wildebeest"
5,true,2011-03-01 07:00:00.000,,,,"gps","Connochaetes taurinus","2846","Olope","Wildebeest"
"""

REFERENCE_CSV = """tag-id,animal-id,animal-taxon,deploy-on-date,deploy-off-date,animal-sex,animal-life-stage,study-site
2829,Naboisho,Connochaetes taurinus,2010-05-28 06:00:00.000,2012-06-21 23:59:00.000,f,9 years,Athi-Kaputiei
2846,Olope,Connochaetes taurinus,2010-05-25 18:01:00.000,2011-08-11 23:59:00.000,m,10 years,Athi-Kaputiei
"""

WILDEBEEST = TaxonRef(gbif_key=2441105, name="Connochaetes taurinus")


@pytest.fixture
def package(tmp_path):
    locations = tmp_path / "wildebeest-gps.csv"
    reference = tmp_path / "wildebeest-reference-data.csv"
    locations.write_text(GPS_CSV)
    reference.write_text(REFERENCE_CSV)

    return make_manifest(
        "movebank_repository",
        {"locations": str(locations), "reference": str(reference)},
        datetime(2011, 3, 1, 6, tzinfo=UTC),
        datetime(2011, 3, 1, 8, tzinfo=UTC),
        item_id="10255/move.1095/wildebeest-gps.csv",
        product="movebank-data-repository",
        available_at=datetime(2020, 12, 1, tzinfo=UTC),
        properties={"study_id": "208413731"},
        storage_format="csv",
    )


def test_fixes_become_animal_locations_with_namespaced_ids(package, grid):
    batch = normalize(package, LocalArtifactStore(), grid)
    rows = batch.table.to_pylist()

    assert batch.family == "animal_locations"
    assert len(rows) == 4
    assert rows[0]["entity_id"] == "movebank:208413731:Naboisho"
    assert rows[0]["observed_at"] == datetime(2011, 3, 1, 6, tzinfo=UTC)
    assert rows[0]["cell_id"].startswith("E1K-r")
    assert [row["quality_flag"] for row in rows] == ["ok", "ok", "marked_outlier", "ok"]
    assert '"gps:dop":4.6' in rows[0]["attributes"]


def test_reference_data_describes_each_animal(package, grid):
    entities = {e.local_identifier: e for e in normalize(package, LocalArtifactStore(), grid).entities}

    assert entities["Olope"].sex == "m"
    assert entities["Olope"].deploy_off == datetime(2011, 8, 11, 23, 59, tzinfo=UTC)
    assert entities["Naboisho"].taxon_name == "Connochaetes taurinus"


def test_a_file_without_coordinates_is_quarantined(tmp_path, grid):
    broken = tmp_path / "broken.csv"
    broken.write_text("event-id,timestamp,individual-local-identifier\n1,2011-03-01 06:00:00,A\n")
    package = make_manifest(
        "movebank_repository", {"locations": str(broken)}, datetime(2011, 3, 1, tzinfo=UTC),
        properties={"study_id": "1"}, storage_format="csv",
    )

    with pytest.raises(QuarantineError, match="location-long"):
        normalize(package, LocalArtifactStore(), grid)


def test_tracking_rows_are_queryable_and_searchable_by_species(database, package, grid):
    store = SeriesStore(database, grid)
    catalog = PostgresCatalog(database)
    batch = normalize(package, LocalArtifactStore(), grid)
    for entity in batch.entities:
        entity.gbif_taxon_key = WILDEBEEST.gbif_key

    first = store.append_batch(series_id(package, grid), package, batch)
    again = store.append_batch(series_id(package, grid), package, batch)
    published = publish_series_version(store, catalog, grid, first.series_id, "movebank_repository", "wildebeest", "public")

    assert first.appended and not again.appended
    locations = database.execute(
        "SELECT entity_id, dataset_version, ST_X(geometry) FROM current_animal_locations ORDER BY observed_at, entity_id"
    ).fetchall()
    assert locations[0] == ("movebank:208413731:Naboisho", 1, 36.95)
    assert len(locations) == 4

    assert published.species == [WILDEBEEST]
    assert published.row_count == 4
    found = catalog.search_datasets(SearchFilters(access_scope=["public"], species=[WILDEBEEST]))
    assert [m.dataset.dataset_id for m in found] == [first.series_id]

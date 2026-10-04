from contextlib import contextmanager
from datetime import UTC, datetime

import pyarrow as pa
import pytest
from psycopg.rows import dict_row

from conftest import make_manifest
from habitat import web
from habitat.catalog.publish import publish_series_version
from habitat.catalog.store import PostgresCatalog
from habitat.contracts import SearchFilters, TaxonRef
from habitat.archive.store import LocalArtifactStore
from habitat.db import MIGRATIONS_DIR
from habitat.ingest import IngestOutcome, Workspace, publish_changed
from habitat.normalize.router import normalize
from habitat.normalize.rows import QuarantineError, series_id, series_id_for
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


@pytest.mark.parametrize("source_id", ["movebank_repository", "movebank_study"])
def test_studies_have_separate_series_but_files_from_one_study_share_a_series(package, grid, source_id):
    package.extensions.source_id = source_id
    other_study = package.model_copy(deep=True)
    other_study.extensions.properties["study_id"] = "3609"
    another_file = package.model_copy(deep=True)
    another_file.extensions.source_item_id = "10255/move.1095/another-gps.csv"

    assert series_id(package, grid) != series_id(other_study, grid)
    assert series_id(package, grid) == series_id(another_file, grid)


def test_existing_studies_become_separate_catalog_entries_and_snapshots(database, package, grid, monkeypatch):
    store = SeriesStore(database, grid)
    catalog = PostgresCatalog(database)
    original_id = series_id_for(package.extensions.source_id, package.extensions.product, grid)
    other = package.model_copy(deep=True)
    other.extensions.source_item_id = "10255/move.3609/black-bear.csv"
    other.extensions.properties = {"study_id": "3609", "title": "Data from: Black bear movement"}
    package.extensions.properties["title"] = "Data from: Wildebeest movement"

    for manifest in [package, other]:
        batch = normalize(manifest, LocalArtifactStore(), grid)
        batch.table = batch.table.set_column(
            batch.table.schema.get_field_index("dataset_id"), batch.table.schema.field("dataset_id"),
            pa.array([original_id] * batch.table.num_rows),
        )
        for entity in batch.entities:
            entity.gbif_taxon_key = WILDEBEEST.gbif_key if manifest is package else 2433407
            entity.taxon_name = WILDEBEEST.name if manifest is package else "Ursus americanus"
        store.append_batch(original_id, manifest, batch)
        publish_series_version(store, catalog, grid, original_id, "movebank_repository", "Combined tracking", "public")

    database.execute((MIGRATIONS_DIR / "010_movebank_study_datasets.sql").read_text())

    @contextmanager
    def connection():
        with database.cursor(row_factory=dict_row) as cursor:
            yield cursor

    monkeypatch.setattr(web, "connection", connection)
    web.dataset_snapshot.cache_clear()
    try:
        datasets = {dataset["dataset_id"]: dataset for dataset in web.catalog()["datasets"]}
        assert set(datasets) == {series_id(package, grid), series_id(other, grid)}
        assert datasets[series_id(package, grid)]["description"] == "Wildebeest movement"
        assert datasets[series_id(other, grid)]["description"] == "Black bear movement"
        assert datasets[series_id(package, grid)]["coverage"]["species"] == [WILDEBEEST.name]
        assert datasets[series_id(other, grid)]["coverage"]["species"] == ["Ursus americanus"]
        assert all(dataset["row_count"] == 4 for dataset in datasets.values())

        for manifest in [package, other]:
            snapshot = web.dataset_features(series_id(manifest, grid))
            assert snapshot["total_records"] == 2
            assert {feature["properties"]["entity_id"].split(":")[1] for feature in snapshot["features"]} == {
                manifest.extensions.properties["study_id"]}
            assert store.latest_version(series_id(manifest, grid)).has_item(
                manifest.extensions.source_item_id, manifest.extensions.processing_version, "final")

        assert database.execute(
            "SELECT count(*) FROM recipe_animal_locations WHERE dataset_id = %s AND dataset_version = '2'",
            (original_id,),
        ).fetchone()[0] == 8
        assert {row[0] for row in database.execute("SELECT DISTINCT dataset_id FROM current_animal_locations")} == {
            series_id(package, grid), series_id(other, grid)}
    finally:
        web.dataset_snapshot.cache_clear()


def test_new_studies_publish_with_their_own_titles(database, package, grid):
    store = SeriesStore(database, grid)
    package.extensions.properties["title"] = "Data from: Wildebeest movement"
    batch = normalize(package, LocalArtifactStore(), grid)
    result = store.append_batch(series_id(package, grid), package, batch)

    published = publish_changed([IngestOutcome(package, result)], Workspace(database, grid))

    assert published == [series_id(package, grid)]
    assert PostgresCatalog(database).latest(published[0]).description == "Wildebeest movement"

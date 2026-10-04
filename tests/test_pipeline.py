from datetime import UTC, datetime

import httpx
import pytest

from conftest import make_manifest
from fixtures import movebank_package
from habitat.archive import Archive
from habitat.archive.index import PostgresArtifactIndex
from habitat.catalog.store import PostgresCatalog
from habitat.contracts import SearchFilters
from habitat.ingest import Workspace
from habitat.pipeline import build_request, cleanup_published_raw_files, process, run


def movebank_and_gbif(request: httpx.Request) -> httpx.Response:
    if request.url.host == "api.gbif.org":
        if request.url.path.endswith("/species/match"):
            return httpx.Response(200, json={"matchType": "EXACT", "rank": "SPECIES", "usageKey": 2441105,
                                             "scientificName": "Connochaetes taurinus"})
        return httpx.Response(200, json={"results": []})
    return movebank_package.handler()(request)


@pytest.fixture
def workspace(database, grid):
    return Workspace(database, grid)


def package_request():
    return build_request("movebank_repository", None, None, None, movebank_package.PACKAGE_UUID, None)


def test_fetch_normalize_append_publish_then_nothing_new(workspace, database, mock_http):
    mock_http(movebank_and_gbif)

    first = run(package_request(), use_agent=False, workspace=workspace)
    second = run(package_request(), use_agent=False, workspace=workspace)

    assert first.status == "ok"
    assert [o.status for o in first.outcomes] == ["appended"]
    assert first.published == [first.outcomes[0].series_id]
    assert workspace.archive.store.list_files() == []
    assert [o.status for o in second.outcomes] == ["already_present"]
    assert second.published == []

    assert database.execute("SELECT count(*) FROM animal_locations").fetchone()[0] == 4
    assert database.execute("SELECT count(*) FROM raw_artifacts").fetchone()[0] == 1
    (manifest,) = database.execute("SELECT manifest FROM raw_artifacts").fetchone()
    assert manifest["storage"]["uri"].startswith("artifact://")
    found = PostgresCatalog(database).search_datasets(SearchFilters(access_scope=["public"]))
    assert [m.dataset.version for m in found] == [1]
    assert found[0].dataset.species[0].gbif_key == 2441105

    item = first.fetch.output.raw_artifacts[0].extensions
    published = PostgresArtifactIndex(database).find_published(item.source_id, item.source_item_id,
                                                              item.processing_version, item.product_status.value, "public")
    assert published == first.fetch.output.raw_artifacts[0]
    assert PostgresArtifactIndex(database).find_published(item.source_id, item.source_item_id,
                                                         item.processing_version, item.product_status.value, "private") is None


def test_insufficient_data_never_publishes(workspace, database, mock_http):
    mock_http(lambda request: httpx.Response(404))

    result = run(package_request(), use_agent=False, workspace=workspace)

    assert result.status == "insufficient_data"
    assert result.outcomes == [] and result.published == []
    assert database.execute("SELECT count(*) FROM datasets").fetchone()[0] == 0


def test_unknown_source_is_quarantined_with_a_reason(workspace):
    manifest = make_manifest("landsat", {}, datetime(2024, 1, 1, tzinfo=UTC))

    outcome, ingest = process(manifest, workspace, None)

    assert ingest is None
    assert outcome.status == "quarantined"
    assert "unknown source_id 'landsat'" in outcome.reason


def test_fixture_data_is_quarantined_not_published(workspace, database):
    result = run(build_request("fixture", None, None, None, "fixture-movement-001", None), False, workspace)

    assert result.status == "ok"
    assert [o.status for o in result.outcomes] == ["quarantined"]
    assert "no canonical mapping" in result.outcomes[0].reason
    assert result.published == []
    assert database.execute("SELECT count(*) FROM datasets").fetchone()[0] == 0
    assert workspace.archive.store.list_files()


def test_a_corrupt_archive_file_is_quarantined(workspace):
    archive = Archive(index=PostgresArtifactIndex(workspace.connection))
    workspace.archive = archive
    [manifest] = run(build_request("fixture", None, None, None, "fixture-rainfall-001", None), False, workspace).fetch.output.raw_artifacts
    archive.store.open(manifest, "data").write_text("changed")

    outcome, _ = process(manifest, workspace, None)

    assert outcome.status == "quarantined" and "checksum" in outcome.reason


def test_the_area_of_an_agent_request_comes_from_the_manifest():
    from habitat.pipeline import fetched_area

    manifest = make_manifest("chirps", {}, datetime(2024, 1, 1, tzinfo=UTC), properties={"requested_bbox": [1, 2, 3, 4]})

    assert fetched_area(manifest) == (1, 2, 3, 4)
    assert fetched_area(make_manifest("chirps", {}, datetime(2024, 1, 1, tzinfo=UTC))) is None


def test_failed_publication_keeps_raw_files_and_retry_publishes_existing_rows(workspace, database, mock_http, monkeypatch):
    from habitat import pipeline

    mock_http(movebank_and_gbif)
    publish = pipeline.publish_changed

    def unavailable(*args):
        raise RuntimeError("catalog unavailable")

    monkeypatch.setattr(pipeline, "publish_changed", unavailable)
    with pytest.raises(RuntimeError, match="catalog unavailable"):
        run(package_request(), use_agent=False, workspace=workspace)
    assert workspace.archive.store.list_files()
    assert database.execute("SELECT count(*) FROM animal_locations").fetchone()[0] == 4
    assert database.execute("SELECT count(*) FROM datasets").fetchone()[0] == 0

    monkeypatch.setattr(pipeline, "publish_changed", publish)
    retried = run(package_request(), use_agent=False, workspace=workspace)

    assert retried.outcomes[0].status == "already_present"
    assert retried.published == [retried.outcomes[0].series_id]
    assert workspace.archive.store.list_files() == []
    assert database.execute("SELECT count(*) FROM animal_locations").fetchone()[0] == 4


def test_raw_files_survive_a_database_rollback(workspace, database, mock_http):
    mock_http(movebank_and_gbif)

    with pytest.raises(RuntimeError, match="rollback"):
        with database.transaction():
            run(package_request(), use_agent=False, workspace=workspace)
            assert workspace.archive.store.list_files()
            assert cleanup_published_raw_files(workspace) == {"artifacts": 0, "bytes": 0}
            raise RuntimeError("rollback")

    assert workspace.archive.store.list_files()
    assert database.execute("SELECT count(*) FROM animal_locations").fetchone()[0] == 0
    assert database.execute("SELECT count(*) FROM datasets").fetchone()[0] == 0


def test_cleanup_failure_keeps_the_published_dataset_available(workspace, database, mock_http, monkeypatch):
    mock_http(movebank_and_gbif)
    remove = workspace.archive.store.remove

    def failed_cleanup(manifest):
        raise OSError("disk unavailable")

    monkeypatch.setattr(workspace.archive.store, "remove", failed_cleanup)
    result = run(package_request(), use_agent=False, workspace=workspace)

    assert result.published
    assert workspace.archive.store.list_files()
    assert database.execute("SELECT count(*) FROM animal_locations").fetchone()[0] == 4

    monkeypatch.setattr(workspace.archive.store, "remove", remove)
    removed = cleanup_published_raw_files(workspace)
    assert removed["artifacts"] == 1
    assert removed["bytes"] > 0
    assert workspace.archive.store.list_files() == []

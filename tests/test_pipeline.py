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
from habitat.pipeline import build_request, process, run


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
    assert [o.status for o in second.outcomes] == ["already_present"]
    assert second.published == []

    assert database.execute("SELECT count(*) FROM animal_locations").fetchone()[0] == 4
    assert database.execute("SELECT count(*) FROM raw_artifacts").fetchone()[0] == 1
    (manifest,) = database.execute("SELECT manifest FROM raw_artifacts").fetchone()
    assert manifest["storage"]["uri"].startswith("artifact://")
    found = PostgresCatalog(database).search_datasets(SearchFilters(access_scope=["public"]))
    assert [m.dataset.version for m in found] == [1]
    assert found[0].dataset.species[0].gbif_key == 2441105


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

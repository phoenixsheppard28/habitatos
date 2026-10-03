from habitat.archive import Archive
from habitat.contracts import RawManifest
from habitat.fetch.connectors import ConnectorRequest
from habitat.fetch.connectors.fixture import FIXTURES_DIR, fetch_fixture, search_fixtures
from habitat.sources import SOURCES


def test_search_finds_movement_fixture():
    hits = search_fixtures("antelope movement", species=["example-antelope"])

    assert "fixture-movement-001" in {hit["dataset_id"] for hit in hits}


def test_search_respects_species_filter():
    assert search_fixtures("rainfall", species=["example-antelope"]) == []


def test_fixture_is_archived_once_with_a_source_item():
    archive = Archive()

    [first] = fetch_fixture(ConnectorRequest(item="fixture-movement-001"), archive).manifests
    [second] = fetch_fixture(ConnectorRequest(item="fixture-movement-001"), archive).manifests

    assert first == second
    assert RawManifest.model_validate(first.model_dump(mode="json")) == first
    assert first.storage.uri == "artifact://fixture-movement-001/1"
    assert first.storage.format == SOURCES["fixture"].storage_format
    assert first.checksum.startswith("sha256:")
    assert archive.store.open(first, "data").read_bytes() == (FIXTURES_DIR / "sample_tracks.csv").read_bytes()


def test_unknown_fixture_is_a_typed_error():
    result = fetch_fixture(ConnectorRequest(item="nope"), Archive())

    assert [e.code for e in result.errors] == ["not_found"]

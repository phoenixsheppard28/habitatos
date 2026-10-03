from datetime import UTC, datetime

import pytest

from conftest import make_manifest
from habitat import config
from habitat.archive import Archive, ChecksumMismatch, LocalArtifactStore
from habitat.archive.index import DuplicateSourceKey, MemoryArtifactIndex, PostgresArtifactIndex
from habitat.archive.paths import contained, safe_name

UNSAFE_NAMES = ["../../evil", "/etc/passwd", "..", "", ".", "a\\..\\..\\evil"]


def archived(archive: Archive, artifact_id="scene-1", files=None, source_key="test:scene-1"):
    version, stored = archive.put(artifact_id, files or {"B04.tif": b"red", "SCL.tif": b"scl"}, "geotiff")
    manifest = make_manifest("sentinel2", {"red": "B04.tif", "scl": "SCL.tif"}, datetime(2024, 2, 17, tzinfo=UTC))
    manifest = manifest.model_copy(update={
        "artifact_id": artifact_id, "version": version, "storage": stored.storage, "checksum": stored.checksum,
        "extensions": manifest.extensions.model_copy(update={"source_key": source_key}),
    })
    return archive.record(manifest)


def files_below(path):
    return {p for p in path.rglob("*") if p.is_file()}


@pytest.mark.parametrize("name", UNSAFE_NAMES)
def test_safe_name_never_leaves_one_path_component(name):
    try:
        result = safe_name(name)
    except ValueError:
        return

    assert "/" not in result and result not in ("", ".", "..")


@pytest.mark.parametrize("name", ["../../evil", "/etc/passwd"])
def test_remote_file_names_are_reduced_to_a_base_name(tmp_path, name):
    store = LocalArtifactStore()
    store.put("artifact-1", "1", {name: b"x"}, "csv")

    written = files_below(tmp_path)
    assert all(path.is_relative_to(config.settings().data_dir) for path in written)
    assert {path.name for path in written} == {safe_name(name)}


@pytest.mark.parametrize("name", ["..", "", "."])
def test_remote_file_names_without_a_base_name_are_rejected(tmp_path, name):
    with pytest.raises(ValueError):
        LocalArtifactStore().put("artifact-1", "1", {name: b"x"}, "csv")

    assert files_below(tmp_path) == set()


@pytest.mark.parametrize("artifact_id", UNSAFE_NAMES)
def test_unsafe_artifact_ids_are_rejected(tmp_path, artifact_id):
    with pytest.raises(ValueError):
        LocalArtifactStore().put(artifact_id, "1", {"file.csv": b"x"}, "csv")

    assert files_below(tmp_path) == set()


def test_an_artifact_uri_cannot_point_outside_the_archive(tmp_path):
    manifest = make_manifest("fixture", {"data": "../../../../etc/passwd"}, datetime(2024, 1, 1, tzinfo=UTC))

    with pytest.raises((ValueError, FileNotFoundError)):
        LocalArtifactStore().open(manifest, "data")


def test_contained_rejects_an_escape(tmp_path):
    with pytest.raises(ValueError):
        contained(tmp_path / "root", "..", "outside")


def test_one_artifact_holds_several_files_and_a_stable_checksum():
    archive = Archive()
    manifest = archived(archive)

    assert manifest.storage.uri == "artifact://scene-1/1"
    assert len(manifest.checksum.split(",")) == 2
    assert all(part.startswith("sha256:") for part in manifest.checksum.split(","))
    assert archive.resolve(manifest)["red"].read_bytes() == b"red"


def test_a_changed_byte_gives_a_checksum_mismatch_on_resolve():
    archive = Archive()
    manifest = archived(archive)
    path = archive.store.open(manifest, "red")
    path.write_bytes(b"rex")

    with pytest.raises(ChecksumMismatch):
        archive.resolve(manifest)
    assert archive.cached(manifest.extensions.source_key) is None


def test_a_part_file_is_not_a_complete_artifact():
    store = LocalArtifactStore()
    directory = store.directory("scene-1", "1")
    directory.mkdir(parents=True)
    (directory / "B04.tif.part").write_bytes(b"half")

    assert not store.exists("scene-1", "1")

    store.put("scene-1", "1", {"B04.tif": b"red"}, "geotiff")
    assert store.exists("scene-1", "1")
    assert [p.name for p in store.files("scene-1", "1")] == ["B04.tif"]


def test_a_part_file_next_to_complete_files_makes_the_artifact_incomplete():
    archive = Archive()
    manifest = archived(archive)
    (archive.store.directory("scene-1", "1") / "B08.tif.part").write_bytes(b"half")

    assert archive.cached(manifest.extensions.source_key) is None


def test_the_same_source_key_is_downloaded_once():
    archive = Archive()
    downloads = []

    def fetch(source_key):
        if (cached := archive.cached(source_key)) is not None:
            return cached
        downloads.append(source_key)
        return archived(archive, source_key=source_key)

    first = fetch("test:scene-1")
    second = fetch("test:scene-1")

    assert downloads == ["test:scene-1"]
    assert second == first


def test_a_corrupt_cache_entry_is_downloaded_again_as_a_new_version():
    archive = Archive()
    first = archived(archive)
    archive.store.open(first, "red").write_bytes(b"corrupt")

    assert archive.cached("test:scene-1") is None
    second = archived(archive)

    assert second.version == "2"
    assert archive.cached("test:scene-1") == second


def test_memory_index_rejects_a_duplicate_source_key():
    index = MemoryArtifactIndex()
    manifest = make_manifest("fixture", {}, datetime(2024, 1, 1, tzinfo=UTC))
    index.record(manifest)

    with pytest.raises(DuplicateSourceKey):
        index.record(manifest)


def test_raw_artifacts_round_trip(database):
    index = PostgresArtifactIndex(database)
    archive = Archive(index=index)
    manifest = archived(archive)

    assert index.find_by_source_key("test:scene-1") == manifest
    assert archive.cached("test:scene-1") == manifest
    row = database.execute("SELECT artifact_id, version, source_id, storage_uri FROM raw_artifacts").fetchone()
    assert row == ("scene-1", "1", "sentinel2", "artifact://scene-1/1")


def test_raw_artifacts_rejects_a_duplicate_source_key(database):
    index = PostgresArtifactIndex(database)
    manifest = archived(Archive(index=index))
    duplicate = manifest.model_copy(update={"artifact_id": "other", "storage": manifest.storage.model_copy(update={"uri": "artifact://other/1"})})

    with pytest.raises(DuplicateSourceKey):
        index.record(duplicate)

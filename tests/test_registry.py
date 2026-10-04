import subprocess

from habitat.config import PROJECT_ROOT
from habitat.sources import SOURCES

EXPECTED = {"sentinel2", "modis_mod13q1", "chirps", "movebank_repository", "movebank_study", "zenodo", "fixture"}
EXPECTED |= {"wqp", "gemstat", "cgls_lwq"}


def test_every_source_id_has_a_connector():
    assert set(SOURCES) == EXPECTED
    assert all(callable(source.fetch) for source in SOURCES.values())
    assert all(source.source_id == key for key, source in SOURCES.items())


def test_sources_without_mapping_are_only_zenodo_and_fixture():
    assert {key for key, source in SOURCES.items() if source.normalizer is None} == {"zenodo", "fixture"}


def test_every_source_with_a_normalizer_has_a_storage_format():
    for source in SOURCES.values():
        if source.normalizer is not None:
            assert source.storage_format and source.storage_format != "any"


def test_no_old_source_ids_remain():
    pattern = r"[\"']\(sentinel-2\|modis\)[\"']"
    found = subprocess.run(
        ["grep", "-rn", pattern, "src", "tests"], cwd=PROJECT_ROOT, capture_output=True, text=True
    ).stdout

    assert found == ""

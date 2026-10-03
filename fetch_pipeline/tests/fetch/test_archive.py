from __future__ import annotations

from fetch.archive import register_raw_artifact, sha256_bytes
from fetch.connectors.fixture import download_fixture
from fetch.models import Coverage, Rights, SourceRef


def test_checksum_stable() -> None:
    content = b"hello habitat"
    assert sha256_bytes(content) == sha256_bytes(content)


def test_download_fixture_writes_manifest(isolated_data_dir) -> None:
    manifest = download_fixture("fixture-movement-001")
    assert hasattr(manifest, "artifact_id")
    assert manifest.checksum.startswith("sha256:")
    assert manifest.source.name == "fixture"


def test_cache_reuses_unchanged_artifact(isolated_data_dir) -> None:
    first = download_fixture("fixture-movement-001")
    second = download_fixture("fixture-movement-001")
    assert first.artifact_id == second.artifact_id
    assert first.checksum == second.checksum


def test_changed_content_new_version(isolated_data_dir) -> None:
    source = SourceRef(name="fixture", url="https://example.org/x", study_id="s1")
    rights = Rights(license="fixture-only", retention_allowed=True, reuse_allowed=True)
    m1 = register_raw_artifact(
        content=b"version-a",
        filename="demo.csv",
        source=source,
        coverage=Coverage(),
        rights=rights,
        source_key="test:demo",
    )
    m2 = register_raw_artifact(
        content=b"version-b",
        filename="demo.csv",
        source=source,
        coverage=Coverage(),
        rights=rights,
        source_key="test:demo",
    )
    assert m1.artifact_id != m2.artifact_id
    assert m1.checksum != m2.checksum

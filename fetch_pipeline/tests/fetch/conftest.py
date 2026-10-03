from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep tests from writing to the repo data/ folder."""
    data = tmp_path / "data"
    raw = data / "raw"
    manifests = data / "manifests"
    raw.mkdir(parents=True)
    manifests.mkdir(parents=True)

    monkeypatch.setattr("fetch.paths.DATA_ROOT", data)
    monkeypatch.setattr("fetch.paths.RAW_ROOT", raw)
    monkeypatch.setattr("fetch.paths.MANIFEST_ROOT", manifests)
    monkeypatch.setattr("fetch.paths.INDEX_PATH", data / "artifact_index.json")
    return data

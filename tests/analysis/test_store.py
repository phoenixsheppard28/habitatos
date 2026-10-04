import json
from pathlib import Path

import pandas as pd
import pytest

from analysis.store import ArtifactStore, StorageError


def test_round_trip_json_and_filtered_read(tmp_path):
    store = ArtifactStore(tmp_path)
    frame = pd.DataFrame({"animal_id": ["A", "A", "B"], "km": [1.0, 2.0, 3.0]})
    frame.to_parquet(tmp_path / "tracks.parquet", index=False)
    uri = store.write_json("models/one.json", {"presented": False, "mae": 1.5})

    loaded = json.loads(Path(store.resolve(uri)).read_text())
    assert loaded == {"mae": 1.5, "presented": False}
    filtered = store.read_dataset({"uri": "artifact://tracks.parquet", "format": "parquet"}, filters={"animal_id": "A"})
    assert list(filtered["km"]) == [1.0, 2.0]
    projected = store.read_dataset({"uri": "artifact://tracks.parquet", "format": "parquet"}, columns=["km"])
    assert list(projected.columns) == ["km"]


def test_rejects_paths_outside_the_artifact_root(tmp_path):
    store = ArtifactStore(tmp_path)
    (tmp_path / "inside.parquet").write_bytes(b"")
    for uri in (
        "artifact://../pyproject.toml",
        "artifact://foo/../../pyproject.toml",
        "artifact:///tmp/secret.parquet",
        "https://example.com/data.parquet",
        "artifact://",
    ):
        with pytest.raises(StorageError):
            store.resolve(uri)


def test_missing_artifact_and_bad_format_are_storage_errors(tmp_path):
    store = ArtifactStore(tmp_path)
    with pytest.raises(StorageError, match="artifact not found"):
        store.read_dataset({"uri": "artifact://missing.parquet", "format": "parquet"})
    (tmp_path / "note.json").write_text("{}\n")
    with pytest.raises(StorageError, match="unsupported table format"):
        store.read_dataset({"uri": "artifact://note.json", "format": "json"})
    with pytest.raises(StorageError, match="unknown filter column"):
        pd.DataFrame({"animal_id": ["A"]}).to_parquet(tmp_path / "one.parquet", index=False)
        store.read_dataset({"uri": "artifact://one.parquet", "format": "parquet"}, filters={"missing": "A"})


def test_write_cannot_escape_the_root(tmp_path):
    store = ArtifactStore(tmp_path)
    with pytest.raises(StorageError):
        store.write_json("../outside.json", {"ok": True})

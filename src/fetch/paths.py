from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = REPO_ROOT / "data"
RAW_ROOT = DATA_ROOT / "raw"
MANIFEST_ROOT = DATA_ROOT / "manifests"
INDEX_PATH = DATA_ROOT / "artifact_index.json"


def ensure_data_dirs() -> None:
    RAW_ROOT.mkdir(parents=True, exist_ok=True)
    MANIFEST_ROOT.mkdir(parents=True, exist_ok=True)

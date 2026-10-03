"""Read and write versioned artifacts through artifact:// URIs.

Paths stay inside the store root. A relative segment or an absolute path is
rejected before the file is opened.
"""

import json
from pathlib import Path

from contracts.models import StorageRef


class StorageError(Exception):
    pass


class ArtifactStore:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def read_dataset(self, storage, columns=None, filters=None):
        import pandas as pd

        ref = storage if isinstance(storage, StorageRef) else StorageRef.model_validate(storage)
        if ref.format != "parquet":
            raise StorageError(f"unsupported table format: {ref.format}")
        frame = pd.read_parquet(self.resolve(ref.uri), columns=columns)
        if filters:
            for key, value in filters.items():
                if key not in frame.columns:
                    raise StorageError(f"unknown filter column: {key}")
                frame = frame.loc[frame[key] == value]
        return frame

    def write_json(self, relative: str, payload: dict) -> str:
        path = self._relative(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            encoded = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise StorageError("artifact JSON must be finite") from exc
        path.write_text(encoded + "\n")
        return f"artifact://{relative}"

    def resolve(self, uri: str) -> Path:
        if not uri.startswith("artifact://"):
            raise StorageError("storage uri must use artifact://")
        path = self._relative(uri.removeprefix("artifact://"))
        if not path.is_file():
            raise StorageError(f"artifact not found: {uri}")
        return path

    def _relative(self, relative: str) -> Path:
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or not relative:
            raise StorageError("storage uri escapes the artifact root")
        resolved = (self.root / path).resolve()
        if not resolved.is_relative_to(self.root):
            raise StorageError("storage uri escapes the artifact root")
        return resolved

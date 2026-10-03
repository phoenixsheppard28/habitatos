"""Local development persistence; replace through the Stage 2 adapter later."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .errors import RecipeError


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def cache_key(recipe, query, datasets, execution_version):
    return digest({"recipe": recipe.model_dump(mode="json"),
        "query": query.model_dump(mode="json"),
        "inputs": [datasets[ref.key].model_dump(mode="json") for ref in recipe.inputs.values()],
        "execution_version": execution_version})


class LocalArtifactStore:
    """Scoped fixture/development store. URI resolution is explicit, never guessed."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, kind, identity, scope):
        path = self.root / digest(scope) / kind
        path.mkdir(parents=True, exist_ok=True)
        return path / (digest(identity) + ".json")

    def _write(self, path, value, *, immutable=False):
        raw = canonical(value)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".write-")
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if immutable:
                try:
                    os.link(tmp, path)
                except FileExistsError:
                    if json.loads(path.read_text()) != value:
                        raise RecipeError("IMMUTABLE_VERSION_CONFLICT", "recipe ID/version already has different content")
            else:
                os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def save_recipe(self, recipe):
        identity = [recipe.recipe_id, recipe.version]
        path = self._path("recipes", identity, recipe.access_scope)
        value = recipe.model_dump(mode="json")
        self._write(path, value, immutable=True)

    def recipe(self, reference, scope):
        path = self._path("recipes", [reference["recipe_id"], reference["version"]], scope)
        if not path.exists():
            raise RecipeError("ARTIFACT_UNREADABLE", "parent recipe is unavailable in this access scope")
        return json.loads(path.read_text())

    def save_context(self, query, candidates):
        self._write(self._path("contexts", query.query_id, query.access_scope),
                    {"query": query.model_dump(mode="json"), "candidates": candidates})

    def context(self, query):
        path = self._path("contexts", query.query_id, query.access_scope)
        if not path.exists():
            return None
        value = json.loads(path.read_text())
        if value["query"] != query.model_dump(mode="json"):
            raise RecipeError("QUERY_VERSION_CONFLICT", "query ID reused with different query constraints")
        return value

    def save_failure(self, request_id, scope, report):
        self._write(self._path("failures", request_id, scope), report)

    def lookup(self, key, scope):
        path = self._path("artifacts", key, scope)
        if not path.exists():
            return None
        value = json.loads(path.read_text())
        if value["artifact"]["access_scope"] != scope or value["artifact"]["cache_key"] != key:
            raise RecipeError("ARTIFACT_UNREADABLE", "cached artifact scope/key mismatch")
        parquet = path.with_suffix(".parquet")
        if not parquet.exists() or hashlib.sha256(parquet.read_bytes()).hexdigest() != value["checksum"]:
            raise RecipeError("ARTIFACT_UNREADABLE", "cached artifact is missing or corrupt")
        return value

    def publish(self, key, recipe, rows, report):
        import pyarrow as pa
        import pyarrow.parquet as pq
        scope = recipe.access_scope
        path = self._path("artifacts", key, scope)
        parquet = path.with_suffix(".parquet")
        types = {"string": pa.string(), "integer": pa.int64(), "number": pa.float64(),
                 "boolean": pa.bool_(), "timestamp": pa.timestamp("us", tz="UTC"),
                 "geometry": pa.string(), "json": pa.string()}
        schema = pa.schema([pa.field(c.name, types[c.type], nullable=c.nullable) for c in recipe.output.columns],
                           metadata={b"recipe.columns": canonical([c.model_dump(mode="json") for c in recipe.output.columns]).encode()})
        converted = [{c.name: canonical(row[c.name]) if c.type in {"geometry", "json"} and row[c.name] is not None else row[c.name]
                      for c in recipe.output.columns} for row in rows]
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".parquet-")
        os.close(fd)
        try:
            pq.write_table(pa.Table.from_pylist(converted, schema=schema), tmp)
            os.replace(tmp, parquet)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        artifact = {"schema_version": "1.0", "artifact_id": "features-"+key, "version": "1",
            "created_at": datetime.now(timezone.utc).isoformat(), "access_scope": scope,
            "recipe_ref": {"recipe_id": recipe.recipe_id, "version": recipe.version},
            "input_dataset_refs": [r.model_dump(mode="json") for r in recipe.inputs.values()],
            "cache_key": key, "storage": {"uri": "artifact://features-"+key+"/1", "format": "parquet"},
            "row_grain": recipe.output.row_grain, "columns": [c.model_dump(mode="json") for c in recipe.output.columns],
            "row_count": len(rows), "validation_report_ref": "artifact://report-"+key+"/1"}
        value = {"artifact": artifact, "report": report,
                 "checksum": hashlib.sha256(parquet.read_bytes()).hexdigest()}
        self._write(path, value)
        return value

    def resolve_artifact(self, storage, *, scope):
        uri = storage.get("uri", "")
        prefix = "artifact://features-"
        if not uri.startswith(prefix) or not uri.endswith("/1"):
            raise RecipeError("ARTIFACT_UNREADABLE", "unknown local artifact URI")
        key = uri[len(prefix):-2]
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise RecipeError("ARTIFACT_UNREADABLE", "invalid artifact key")
        if self.lookup(key, scope) is None:
            raise RecipeError("ARTIFACT_UNREADABLE", "artifact not found in access scope")
        return self._path("artifacts", key, scope).with_suffix(".parquet")

    def read_dataset(self, storage, *, scope, columns=None, filters=None):
        import pyarrow.parquet as pq
        return pq.read_table(self.resolve_artifact(storage, scope=scope), columns=columns, filters=filters)

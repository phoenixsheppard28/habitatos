"""Shared fixture builder for analysis tests."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from analysis.store import ArtifactStore
from contracts.models import (
    ColumnSpec,
    DatasetRef,
    FeatureArtifact,
    QuerySpec,
    RecipeRef,
    RecipeView,
    Rights,
    StorageRef,
)

FIXTURES = Path(__file__).parent / "analysis" / "fixtures"
REGION = {
    "type": "Polygon",
    "coordinates": [[[0, 0], [30, 0], [30, 10], [0, 10], [0, 0]]],
}


def replace_frame(tmp_path: Path, request: dict, frame: pd.DataFrame, name: str = "movement.parquet") -> dict:
    frame.to_parquet(tmp_path / name, index=False)
    artifact = request["input"]["feature_artifact"]
    artifact["row_count"] = int(len(frame))
    artifact["storage"]["uri"] = f"artifact://{name}"
    return request


def movement_frame() -> pd.DataFrame:
    frame = pd.read_csv(FIXTURES / "movement.csv")
    frame["date"] = pd.to_datetime(frame["date"], utc=True)
    return frame


def movement_request(tmp_path: Path, **query_overrides):
    frame = movement_frame()
    frame.to_parquet(tmp_path / "movement.parquet", index=False)
    created = datetime(2026, 1, 5, tzinfo=timezone.utc)
    artifact = FeatureArtifact(
        artifact_id="feature-movement",
        version="1",
        created_at=created,
        access_scope="public",
        recipe_ref=RecipeRef(recipe_id="recipe-movement", version="1"),
        input_dataset_refs=[DatasetRef(dataset_id="dataset-movement", version="1")],
        storage=StorageRef(uri="artifact://movement.parquet", format="parquet"),
        row_grain="entity_day",
        row_count=len(frame),
        columns=[
            ColumnSpec(name="animal_id", type="string", nullable=False, role="entity_id"),
            ColumnSpec(name="date", type="timestamp", nullable=False, role="event_time"),
            ColumnSpec(name="lon", type="number", nullable=False, unit="degrees", role="longitude"),
            ColumnSpec(name="lat", type="number", nullable=False, unit="degrees", role="latitude"),
            ColumnSpec(name="km_moved", type="number", nullable=True, unit="km", role="daily_displacement"),
            ColumnSpec(name="cell_id", type="string", nullable=True, role="cell_id"),
            ColumnSpec(
                name="rain_mm",
                type="number",
                nullable=True,
                unit="mm",
                role="rainfall",
                description="rainfall",
            ),
        ],
        coverage={"species": ["antelope"], "start": "2026-01-01T00:00:00Z", "end": "2026-01-04T00:00:00Z"},
        rights=Rights(license="fixture-only", attribution="Demo fixture", retention_allowed=True, reuse_allowed=True),
    )
    recipe = RecipeView(recipe_id="recipe-movement", version="1")
    query_fields = {
        "query_id": "query-001",
        "question": "Identify a notable pattern in tracked antelope movement in this region.",
        "task_type": "historical",
        "species": ["antelope"],
        "region": REGION,
        "time_range": {"start": "2026-01-01T00:00:00Z", "end": "2026-01-04T00:00:00Z"},
        "access_scope": "public",
    }
    query_fields.update(query_overrides)
    query = QuerySpec.model_validate(query_fields)
    request = {
        "contract_version": "1.0",
        "request_id": "request-001",
        "query_id": "query-001",
        "access_scope": "public",
        "input": {
            "query": query.model_dump(mode="json"),
            "recipe": recipe.model_dump(mode="json"),
            "feature_artifact": artifact.model_dump(mode="json"),
        },
    }
    return json.loads(json.dumps(request)), ArtifactStore(tmp_path)

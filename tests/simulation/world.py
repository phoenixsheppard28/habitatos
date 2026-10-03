"""Stand-ins for Fetch, Normalize, and Recipe.

These handlers are test fixtures. They move a local table through the same
stage order the coordinator will use when those lanes exist, then call the
real analysis service.
"""

import copy
from datetime import datetime, timezone

import pandas as pd

from analysis.service import run
from analysis.store import ArtifactStore
from contracts.models import (
    ColumnSpec,
    DatasetRef,
    FeatureArtifact,
    RecipeRef,
    RecipeView,
    Rights,
    StorageRef,
)


class SimulatedStudy:
    def __init__(self, root, frame: pd.DataFrame, *, species: str = "antelope"):
        self.root = root
        self.store = ArtifactStore(root)
        self.frame = frame.copy()
        self.species = species
        self.calls: list[str] = []
        self.access_scope = "public"
        self.fetch_empty = False
        self.quarantine_all = False
        self.respect_cutoff = True
        self.habitat_only = False
        self.include_species_role = False
        self.recipe_failures_remaining = 0

    def handlers(self) -> dict:
        return {
            "fetch": self.fetch,
            "normalize": self.normalize,
            "recipe": self.recipe,
            "analysis": self.analysis,
        }

    def fetch(self, request: dict) -> dict:
        self.calls.append("fetch")
        if self.fetch_empty:
            return _envelope(
                request,
                "insufficient_data",
                {
                    "result": {
                        "status": "insufficient_data",
                        "findings": [],
                        "metrics": {},
                        "report": "No source study overlaps this question.",
                    },
                    "raw_artifacts": [],
                },
            )
        _write(self.root / "raw" / "study.parquet", self.frame)
        return _envelope(
            request,
            "ok",
            {
                "raw_artifacts": [
                    {
                        "artifact_id": "raw-study",
                        "uri": "artifact://raw/study.parquet",
                        "source": {"name": "Movebank", "study_id": "study-demo"},
                        "row_count": int(len(self.frame)),
                    }
                ]
            },
        )

    def normalize(self, request: dict) -> dict:
        self.calls.append("normalize")
        raw = request["extensions"]["stage_outputs"]["fetch"]["output"]["raw_artifacts"][0]
        frame = pd.read_parquet(self.store.resolve(raw["uri"]))
        if self.quarantine_all:
            return _envelope(
                request,
                "insufficient_data",
                {
                    "result": {
                        "status": "insufficient_data",
                        "findings": [],
                        "metrics": {"quarantined_rows": int(len(frame))},
                        "report": "Every raw row failed validation.",
                    },
                    "datasets": [],
                },
            )
        kept = frame
        if "animal_id" in frame.columns and not self.habitat_only:
            kept = frame.loc[frame["animal_id"].notna()].copy()
        _write(self.root / "normalized" / "animal_locations.parquet", kept)
        return _envelope(
            request,
            "ok",
            {
                "datasets": [
                    {
                        "dataset_id": "dataset-sim",
                        "version": "1",
                        "family": "rainfall_observations" if self.habitat_only else "animal_locations",
                        "uri": "artifact://normalized/animal_locations.parquet",
                        "row_count": int(len(kept)),
                        "quarantined_rows": int(len(frame) - len(kept)),
                    }
                ]
            },
        )

    def recipe(self, request: dict) -> dict:
        self.calls.append("recipe")
        if self.recipe_failures_remaining:
            self.recipe_failures_remaining -= 1
            raise RuntimeError("recipe store busy")
        dataset = request["extensions"]["stage_outputs"]["normalize"]["output"]["datasets"][0]
        frame = pd.read_parquet(self.store.resolve(dataset["uri"]))
        _write(self.root / "features" / "entity_day.parquet", frame)
        query = request["input"]["query"]
        forecast = query.get("forecast")
        recipe = RecipeView(
            recipe_id="recipe-sim",
            version="1",
            cutoff=forecast["cutoff"] if forecast else None,
            features_respect_cutoff=bool(forecast) and self.respect_cutoff,
        )
        artifact = FeatureArtifact(
            artifact_id="feature-sim",
            version="1",
            created_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
            access_scope=self.access_scope,
            recipe_ref=RecipeRef(recipe_id="recipe-sim", version="1"),
            input_dataset_refs=[DatasetRef(dataset_id=dataset["dataset_id"], version=dataset["version"])],
            storage=StorageRef(uri="artifact://features/entity_day.parquet", format="parquet"),
            row_grain="entity_day",
            row_count=int(len(frame)),
            columns=_columns(self),
            coverage=_coverage(frame, self.species),
            rights=Rights(
                license="fixture-only",
                attribution="Simulated study",
                retention_allowed=True,
                reuse_allowed=True,
            ),
        )
        return _envelope(
            request,
            "ok",
            {
                "recipe": recipe.model_dump(mode="json"),
                "feature_artifact": artifact.model_dump(mode="json"),
            },
        )

    def analysis(self, request: dict) -> dict:
        self.calls.append("analysis")
        published = request["extensions"]["stage_outputs"]["recipe"]["output"]
        prepared = copy.deepcopy(request)
        prepared.pop("extensions", None)
        prepared["input"]["recipe"] = published["recipe"]
        prepared["input"]["feature_artifact"] = published["feature_artifact"]
        return run(prepared, store=self.store)


def _columns(study: SimulatedStudy) -> list[ColumnSpec]:
    if study.habitat_only:
        columns = [
            ColumnSpec(name="date", type="timestamp", nullable=False, role="event_time"),
            ColumnSpec(
                name="rain_mm",
                type="number",
                nullable=True,
                unit="mm",
                role="rainfall",
                description="rainfall",
            ),
        ]
    else:
        columns = [
            ColumnSpec(name="animal_id", type="string", nullable=False, role="entity_id"),
            ColumnSpec(name="date", type="timestamp", nullable=False, role="event_time"),
            ColumnSpec(name="lon", type="number", nullable=True, unit="degrees", role="longitude"),
            ColumnSpec(name="lat", type="number", nullable=True, unit="degrees", role="latitude"),
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
        ]
    if study.include_species_role:
        columns.append(ColumnSpec(name="species", type="string", nullable=False, role="species"))
    return columns


def _coverage(frame: pd.DataFrame, species: str) -> dict:
    start = pd.Timestamp(frame["date"].min())
    end = pd.Timestamp(frame["date"].max())
    if start.tzinfo is None:
        start = start.tz_localize("UTC")
        end = end.tz_localize("UTC")
    return {
        "species": [species],
        "start": start.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _write(path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


def _envelope(request: dict, status: str, output: dict) -> dict:
    return {
        "contract_version": request["contract_version"],
        "request_id": request["request_id"],
        "query_id": request["query_id"],
        "access_scope": request["access_scope"],
        "status": status,
        "output": output,
        "warnings": [],
        "error": None,
        "extensions": {},
    }

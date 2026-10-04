"""JSON-serializable contracts shared across lanes.

Lane 2 owns this package. These models are the analysis lane's v1 proposal:
required fields match the README, and extra fields are ignored.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

GEOMETRY_TYPES = {
    "Point",
    "MultiPoint",
    "LineString",
    "MultiLineString",
    "Polygon",
    "MultiPolygon",
}
COLUMN_TYPES = {
    "string",
    "integer",
    "number",
    "boolean",
    "timestamp",
    "geometry",
    "json",
}


class APIModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class TimeRange(APIModel):
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def starts_before_end(self) -> "TimeRange":
        if self.start >= self.end:
            raise ValueError("time_range.start must be before time_range.end")
        return self


class ComparisonWindow(APIModel):
    name: str
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def starts_before_end(self) -> "ComparisonWindow":
        if self.start >= self.end:
            raise ValueError("comparison window start must be before end")
        return self


class BoundaryLayer(APIModel):
    name: str
    geometry: dict[str, Any]


class ForecastRequest(APIModel):
    cutoff: datetime
    horizon_days: int = Field(ge=1, le=30)
    target: str = "next_day_displacement"
    scenario: dict[str, Any] | None = None


class AnalysisOptions(APIModel):
    method: Literal["auto", "summary", "trend", "correlation", "distribution", "statistics", "comparison"] = "auto"
    variables: list[str] = Field(default_factory=list, max_length=8,
                                 description="Numeric column names or roles from the prepared table, in axis order.")


class QuerySpec(APIModel):
    query_id: str
    question: str
    task_type: Literal["historical", "forecast", "discovery"]
    species: list[str]
    region: dict[str, Any]
    time_range: TimeRange
    forecast: ForecastRequest | None = None
    comparison_windows: list[ComparisonWindow] | None = None
    analysis: AnalysisOptions = Field(default_factory=AnalysisOptions)
    access_scope: str
    analysis_method: Literal["movement", "residence_time"] = "movement"
    max_tracking_gap_hours: float = Field(default=6, gt=0, le=168)
    extensions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def two_windows(self) -> "QuerySpec":
        if self.comparison_windows is not None and len(self.comparison_windows) != 2:
            raise ValueError("comparison_windows must contain two windows")
        return self

    @field_validator("species")
    @classmethod
    def species_names(cls, value: list[str]) -> list[str]:
        if not value or any(not isinstance(name, str) or not name.strip() for name in value):
            raise ValueError("species must be a non-empty list of names")
        return value

    @field_validator("region")
    @classmethod
    def region_geometry(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value.get("type") not in GEOMETRY_TYPES:
            raise ValueError("region.type must be a GeoJSON geometry")
        return value


class ColumnSpec(APIModel):
    name: str
    type: str
    nullable: bool
    unit: str | None = None
    role: str | None = None
    description: str | None = None

    @field_validator("type")
    @classmethod
    def known_type(cls, value: str) -> str:
        if value not in COLUMN_TYPES:
            raise ValueError(f"unsupported column type: {value}")
        return value


class StorageRef(APIModel):
    uri: str
    format: Literal["parquet", "json"]


class RecipeRef(APIModel):
    recipe_id: str
    version: str


class RecipeView(RecipeRef):
    cutoff: datetime | None = None
    features_respect_cutoff: bool = False


class DatasetRef(APIModel):
    dataset_id: str
    version: str


class Rights(APIModel):
    license: str | None = None
    attribution: str | None = None
    retention_allowed: bool | None = None
    reuse_allowed: bool | None = None


class FeatureArtifact(APIModel):
    schema_version: str = "1.0"
    artifact_id: str
    version: str
    created_at: datetime
    access_scope: str
    recipe_ref: RecipeRef
    input_dataset_refs: list[DatasetRef]
    storage: StorageRef
    row_grain: str
    columns: list[ColumnSpec]
    row_count: int | None = None
    validation_report_ref: str | None = None
    coverage: dict[str, Any] | None = None
    rights: Rights | None = None
    sampling_grain: Literal["animal_fix", "animal_day"] | None = None


class AnalysisInput(APIModel):
    query: QuerySpec
    recipe: RecipeView
    feature_artifact: FeatureArtifact
    boundaries: list[BoundaryLayer] = Field(default_factory=list)


class AnalysisRequest(APIModel):
    contract_version: Literal["1.0"]
    request_id: str
    query_id: str
    access_scope: str
    input: AnalysisInput

"""Lane-local models; Stage 2 can map its shared contracts into these adapters."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Name = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=63)]
DataType = Literal["string", "integer", "number", "boolean", "timestamp", "geometry", "json"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Ref(Model):
    dataset_id: str = Field(min_length=1)
    version: str = Field(min_length=1)

    @property
    def key(self):
        return self.dataset_id, self.version


class Column(Model):
    name: Name
    type: DataType
    description: str = Field(min_length=1)
    nullable: bool = True
    unit: str | None = None
    role: str | None = None
    derived_from: list[str] = Field(default_factory=list)


class Coverage(Model):
    start: datetime | None = None
    end: datetime | None = None
    bbox: tuple[float, float, float, float] | None = None
    species: list[str] | None = None

    @field_validator("start", "end")
    @classmethod
    def timezone_required(cls, value):
        if value is not None and value.utcoffset() is None:
            raise ValueError("timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def valid_bounds(self):
        if self.start and self.end and self.start > self.end:
            raise ValueError("coverage start must precede end")
        if self.bbox:
            w, s, e, n = self.bbox
            if not (-180 <= w <= 180 and -180 <= e <= 180 and -90 <= s <= n <= 90):
                raise ValueError("invalid WGS84 bounding box")
        return self


class DatasetVersion(Ref):
    access_scope: str
    status: Literal["ready", "quarantined"]
    family: str
    description: str
    row_grain: str
    columns: list[Column] = Field(min_length=1)
    storage: dict[str, Any]
    mapping_version: str
    validation_report_ref: str
    coverage: Coverage = Field(default_factory=Coverage)
    row_count: int | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_columns(self):
        if len({c.name for c in self.columns}) != len(self.columns):
            raise ValueError("duplicate dataset columns")
        return self


class QuerySpec(Model):
    query_id: str
    question: str = Field(min_length=1)
    task_type: Literal["historical", "forecast", "discovery"]
    access_scope: str
    time_range: Coverage
    region: dict[str, Any]
    species: list[str] = Field(default_factory=list)
    forecast_cutoff: datetime | None = None
    extensions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_query(self):
        if not self.time_range.start or not self.time_range.end:
            raise ValueError("absolute start/end dates are required")
        if self.forecast_cutoff and self.forecast_cutoff.utcoffset() is None:
            raise ValueError("forecast cutoff requires a timezone")
        if self.task_type == "forecast" and not self.forecast_cutoff:
            raise ValueError("forecast queries require an absolute cutoff")
        return self


class Requirement(Model):
    requirement_id: str
    description: str
    filters: dict[str, Any] = Field(default_factory=dict)
    semantic_text: str
    required: bool = True
    required_columns: list[str] = Field(default_factory=list)
    expected_units: dict[str, str] = Field(default_factory=dict)
    # Explicit history needed in addition to the query's output interval.
    lookback_seconds: int = Field(default=0, ge=0, le=315576000)
    species_specific: bool = False


class Step(Model):
    id: Name
    operation_version: Literal["1"] = "1"


class Select(Step):
    operation: Literal["select"]
    input: Name
    columns: dict[Name, Name]  # output name -> source column


class Predicate(Model):
    column: Name
    operator: Literal["eq", "ne", "lt", "le", "gt", "ge", "in", "is_null", "not_null"]
    value: Any = None


class Filter(Step):
    operation: Literal["filter"]
    input: Name
    predicates: list[Predicate] = Field(min_length=1)


class TimeBucket(Step):
    operation: Literal["time_bucket"]
    input: Name
    column: Name
    output: Name
    period: Literal["day", "month", "year"]
    timezone: Literal["UTC"] = "UTC"


class Aggregation(Model):
    column: Name
    method: Literal["sum", "mean", "min", "max", "count"]
    output: Name


class Aggregate(Step):
    operation: Literal["aggregate"]
    input: Name
    group_by: list[Name] = Field(min_length=1)
    aggregations: list[Aggregation] = Field(min_length=1)


class JoinBase(Step):
    left: Name
    right: Name
    right_columns: dict[Name, Name]  # output name -> right source column
    type: Literal["left", "inner"] = "left"


class Join(JoinBase):
    operation: Literal["join"]
    keys: dict[Name, Name]  # left key -> right key
    cardinality: Literal["one_to_one", "many_to_one"]


class SpatialJoin(JoinBase):
    operation: Literal["spatial_join"]
    left_geometry: Name
    right_geometry: Name
    right_tie_break: Name
    predicate: Literal["covers"] = "covers"
    crs: Literal["EPSG:4326"] = "EPSG:4326"


class AsOfJoin(JoinBase):
    operation: Literal["asof_join"]
    keys: dict[Name, Name]
    left_time: Name
    right_time: Name
    right_tie_break: Name
    tolerance_seconds: int = Field(gt=0, le=315576000)
    right_available_at: Name | None = None
    direction: Literal["backward"] = "backward"


class Window(JoinBase):
    operation: Literal["window_aggregate"]
    type: Literal["left"] = "left"
    keys: dict[Name, Name]
    left_time: Name
    right_start: Name
    right_end: Name
    right_available_at: Name | None = None
    value_column: Name
    output: Name
    window_seconds: int = Field(gt=0, le=315576000)
    minimum_coverage: float = Field(default=1, gt=0, le=1)
    # Complete source intervals only; source overlaps fail validation.
    method: Literal["sum"] = "sum"


Operation = Annotated[
    Select | Filter | TimeBucket | Aggregate | Join | SpatialJoin | AsOfJoin | Window,
    Field(discriminator="operation"),
]


class Output(Model):
    step: Name
    row_grain: str = Field(min_length=1)
    keys: list[Name] = Field(min_length=1)
    description: str = Field(min_length=1)
    intended_use: str = Field(min_length=1)
    columns: list[Column] = Field(min_length=1)
    # All result rows must be within the query interval at this explicit time.
    time_column: Name


class RecipeSpec(Model):
    schema_version: Literal["1.0"] = "1.0"
    recipe_id: str
    version: str
    query_ref: str
    access_scope: str
    parent_recipe_ref: dict[str, str] | None = None
    inputs: dict[Name, Ref]
    steps: list[Operation] = Field(min_length=1, max_length=64)
    output: Output
    rationale: str = ""


class Clarification(Model):
    question: str
    reason: str
    options: list[str] = Field(default_factory=list)


class PlanningDecision(Model):
    recipe: RecipeSpec | None = None
    clarification: Clarification | None = None
    unmet_requirements: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def one_outcome(self):
        if sum((self.recipe is not None, self.clarification is not None,
                bool(self.unmet_requirements))) != 1:
            raise ValueError("planner must return exactly one outcome")
        return self

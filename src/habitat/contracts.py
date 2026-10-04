import json
from datetime import datetime
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import Any, Literal

import pyarrow as pa
from pydantic import BaseModel, Field, field_validator

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts"

BBox = tuple[float, float, float, float]
ARTIFACT_URI_PREFIX = "artifact://"
ContractVersion = Literal["1.0"]
FetchStatus = Literal["ok", "partial", "pending", "insufficient_data", "error"]
TaskType = Literal["historical", "forecast", "discovery"]


class TimePrecision(StrEnum):
    INSTANT = "instant"
    DAY = "day"
    COMPOSITE = "composite"
    STATIC = "static"


class ProductStatus(StrEnum):
    PRELIMINARY = "preliminary"
    FINAL = "final"


class SourceRef(BaseModel):
    name: str
    url: str | None = None
    study_id: str | None = None


class StorageRef(BaseModel):
    uri: str
    format: str


class Coverage(BaseModel):
    species: list[str] = Field(default_factory=list)
    bbox: BBox | None = None
    start: datetime | None = None
    end: datetime | None = None


class Rights(BaseModel):
    license: str | None = None
    retention_allowed: bool | None = None
    reuse_allowed: bool | None = None
    attribution: str | None = None


SourceItemKind = Literal["raster", "tabular", "vector", "derived"]


class SourceItem(BaseModel):
    """Typed `extensions` of a RawManifest for one source item: a raster scene, a tabular file, a vector file,
    or a run that derives values from other series."""

    source_id: str
    product: str
    source_item_id: str
    source_key: str
    kind: SourceItemKind = "raster"
    time_start: datetime
    time_end: datetime
    time_precision: TimePrecision
    available_at: datetime
    processing_version: str
    product_status: ProductStatus = ProductStatus.FINAL
    assets: dict[str, str]
    properties: dict[str, Any] = Field(default_factory=dict)


class RawManifest(BaseModel):
    artifact_id: str
    version: str
    created_at: datetime
    access_scope: str
    source: SourceRef
    storage: StorageRef
    checksum: str
    retrieved_at: datetime
    coverage: Coverage
    rights: Rights
    extensions: SourceItem

    @field_validator("storage")
    @classmethod
    def storage_is_an_artifact_uri(cls, storage: StorageRef) -> StorageRef:
        if not storage.uri.startswith(ARTIFACT_URI_PREFIX):
            raise ValueError(f"storage.uri must start with {ARTIFACT_URI_PREFIX}, got {storage.uri!r}")
        return storage


class TimeRange(BaseModel):
    start: str | None = None
    end: str | None = None


class QuerySpec(BaseModel):
    query_id: str
    question: str
    task_type: TaskType
    species: list[str] = Field(default_factory=list)
    region: dict[str, Any] | None = None
    time_range: TimeRange = Field(default_factory=TimeRange)


class FetchRequirements(BaseModel):
    """What the coordinator still needs retrieved. Dates are inclusive YYYY-MM-DD days."""

    species: list[str] = Field(default_factory=list)
    data_kinds: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    bbox: list[float] | None = None
    start: str | None = None
    end: str | None = None
    package: str | None = None


class FetchError(BaseModel):
    code: str
    message: str
    retryable: bool = False


class FetchRequestInput(BaseModel):
    query: QuerySpec
    requirements: FetchRequirements = Field(default_factory=FetchRequirements)


class FetchRequest(BaseModel):
    contract_version: ContractVersion = "1.0"
    request_id: str
    query_id: str
    access_scope: str = "public"
    input: FetchRequestInput


class FetchResponseOutput(BaseModel):
    raw_artifacts: list[RawManifest] = Field(default_factory=list)


class FetchResponse(BaseModel):
    contract_version: ContractVersion = "1.0"
    request_id: str
    query_id: str
    access_scope: str
    status: FetchStatus
    output: FetchResponseOutput = Field(default_factory=FetchResponseOutput)
    warnings: list[str] = Field(default_factory=list)
    error: FetchError | None = None
    extensions: dict[str, Any] = Field(default_factory=dict)


class TagOrigin(StrEnum):
    DETERMINISTIC = "deterministic"
    AI = "ai"


class Tag(BaseModel):
    key: str
    value: str
    origin: TagOrigin = TagOrigin.DETERMINISTIC
    model: str | None = None
    confidence: float | None = None
    evidence: str | None = None

    def matches(self, other: "Tag") -> bool:
        return self.key == other.key and self.value == other.value


class TaxonRef(BaseModel):
    gbif_key: int
    name: str


class DatasetVersion(BaseModel):
    dataset_id: str
    version: int
    created_at: datetime
    access_scope: str
    family: str
    source_id: str
    description: str
    status: Literal["ready", "quarantined"]
    storage: StorageRef
    row_grain: str
    row_count: int
    raw_artifact_refs: list[str]
    mapping_version: str
    validation_report_ref: str | None = None
    coverage: Coverage
    footprint_wkt: str | None = None
    variables: list[str] = Field(default_factory=list)
    species: list[TaxonRef] = Field(default_factory=list)
    tags: list[Tag] = Field(default_factory=list)
    summary: str | None = None


class SearchFilters(BaseModel):
    access_scope: list[str]
    family: list[str] | None = None
    region_wkt: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    species: list[TaxonRef] | None = None
    variables: list[str] | None = None
    tags_all: list[Tag] | None = None
    tags_any: list[Tag] | None = None
    include_ai_tags: bool = True
    text: str | None = None
    status: Literal["ready", "quarantined"] = "ready"


class DatasetMatch(BaseModel):
    dataset: DatasetVersion
    spatial_overlap: float | None
    temporal_overlap: float | None
    matched_tags: list[Tag]

    @property
    def score(self) -> float:
        spatial = 1.0 if self.spatial_overlap is None else self.spatial_overlap
        temporal = 1.0 if self.temporal_overlap is None else self.temporal_overlap
        return spatial * temporal


UTC_TIMESTAMP = pa.timestamp("us", tz="UTC")

CELL_OBSERVATIONS_SCHEMA = pa.schema(
    [
        pa.field("cell_id", pa.string(), nullable=False),
        pa.field("time_start", UTC_TIMESTAMP, nullable=False),
        pa.field("time_end", UTC_TIMESTAMP, nullable=False),
        pa.field("time_precision", pa.string(), nullable=False),
        pa.field("available_at", UTC_TIMESTAMP, nullable=False),
        pa.field("source_id", pa.string(), nullable=False),
        pa.field("source_item_id", pa.string(), nullable=False),
        pa.field("processing_version", pa.string(), nullable=False),
        pa.field("product_status", pa.string(), nullable=False),
        pa.field("dataset_id", pa.string(), nullable=False),
        pa.field("mapping_version", pa.string(), nullable=False),
        pa.field("quality_flag", pa.string(), nullable=False),
        pa.field("variable", pa.string(), nullable=False),
        pa.field("stat", pa.string(), nullable=False),
        pa.field("value", pa.float64(), nullable=True),
        pa.field("std", pa.float64(), nullable=True),
        pa.field("unit", pa.string(), nullable=False),
        pa.field("valid_fraction", pa.float64(), nullable=False),
        pa.field("pixel_count", pa.int64(), nullable=False),
        pa.field("source_resolution_m", pa.float64(), nullable=False),
    ]
)

ANIMAL_LOCATIONS_SCHEMA = pa.schema(
    [
        pa.field("source_record_id", pa.string(), nullable=False),
        pa.field("dataset_id", pa.string(), nullable=False),
        pa.field("entity_id", pa.string(), nullable=False),
        pa.field("tag_id", pa.string(), nullable=True),
        pa.field("observed_at", UTC_TIMESTAMP, nullable=False),
        pa.field("available_at", UTC_TIMESTAMP, nullable=False),
        pa.field("longitude", pa.float64(), nullable=False),
        pa.field("latitude", pa.float64(), nullable=False),
        pa.field("cell_id", pa.string(), nullable=False),
        pa.field("sensor_type", pa.string(), nullable=False),
        pa.field("quality_flag", pa.string(), nullable=False),
        pa.field("mapping_version", pa.string(), nullable=False),
        pa.field("attributes", pa.string(), nullable=False),
    ]
)


ANIMAL_ENTITIES_SCHEMA = pa.schema(
    [
        pa.field("entity_id", pa.string(), nullable=False),
        pa.field("source_id", pa.string(), nullable=False),
        pa.field("study_id", pa.string(), nullable=False),
        pa.field("local_identifier", pa.string(), nullable=False),
        pa.field("taxon_name", pa.string(), nullable=True),
        pa.field("gbif_taxon_key", pa.int64(), nullable=True),
        pa.field("sex", pa.string(), nullable=True),
        pa.field("life_stage", pa.string(), nullable=True),
        pa.field("deploy_on", UTC_TIMESTAMP, nullable=True),
        pa.field("deploy_off", UTC_TIMESTAMP, nullable=True),
        pa.field("study_site", pa.string(), nullable=True),
        pa.field("attributes", pa.string(), nullable=False),
    ]
)

SITE_FEATURES_SCHEMA = pa.schema(
    [
        pa.field("source_record_id", pa.string(), nullable=False),
        pa.field("dataset_id", pa.string(), nullable=False),
        pa.field("source_id", pa.string(), nullable=False),
        pa.field("source_item_id", pa.string(), nullable=False),
        pa.field("processing_version", pa.string(), nullable=False),
        pa.field("mapping_version", pa.string(), nullable=False),
        pa.field("feature_id", pa.string(), nullable=False),
        pa.field("feature_class", pa.string(), nullable=False),
        pa.field("feature_type", pa.string(), nullable=False),
        pa.field("origin", pa.string(), nullable=False),
        pa.field("permanence", pa.string(), nullable=False),
        pa.field("status", pa.string(), nullable=False),
        pa.field("name", pa.string(), nullable=True),
        pa.field("time_start", UTC_TIMESTAMP, nullable=False),
        pa.field("time_end", UTC_TIMESTAMP, nullable=False),
        pa.field("time_precision", pa.string(), nullable=False),
        pa.field("available_at", UTC_TIMESTAMP, nullable=False),
        pa.field("longitude", pa.float64(), nullable=True),
        pa.field("latitude", pa.float64(), nullable=True),
        pa.field("cell_id", pa.string(), nullable=True),
        pa.field("geometry", pa.binary(), nullable=False),
        pa.field("coordinate_uncertainty_m", pa.float64(), nullable=True),
        pa.field("quality_flag", pa.string(), nullable=False),
        pa.field("attributes", pa.string(), nullable=False),
    ]
)


class AnimalEntity(BaseModel):
    entity_id: str
    source_id: str
    study_id: str
    local_identifier: str
    taxon_name: str | None = None
    gbif_taxon_key: int | None = None
    sex: str | None = None
    life_stage: str | None = None
    deploy_on: datetime | None = None
    deploy_off: datetime | None = None
    study_site: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


@cache
def load_contract(name: str) -> dict[str, Any]:
    return json.loads((CONTRACTS_DIR / name).read_text())

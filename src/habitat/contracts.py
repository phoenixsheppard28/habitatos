import json
from datetime import datetime
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import Any, Literal

import pyarrow as pa
from pydantic import BaseModel, Field

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts"

BBox = tuple[float, float, float, float]


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


class RasterItem(BaseModel):
    """Typed `extensions` of a RawManifest for one gridded source item."""

    source_id: str
    product: str
    source_item_id: str
    kind: Literal["raster"] = "raster"
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
    checksum: str | None
    retrieved_at: datetime
    coverage: Coverage
    rights: Rights
    extensions: RasterItem


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


@cache
def load_contract(name: str) -> dict[str, Any]:
    return json.loads((CONTRACTS_DIR / name).read_text())

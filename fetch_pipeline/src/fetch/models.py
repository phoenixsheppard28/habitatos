"""Contract v1 types for Fetch lane (mirrors README handoff)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ContractVersion = Literal["1.0"]
FetchStatus = Literal["ok", "partial", "pending", "insufficient_data", "error"]
TaskType = Literal["historical", "forecast", "discovery"]


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
    """What the coordinator still needs retrieved."""

    species: list[str] = Field(default_factory=list)
    data_kinds: list[str] = Field(default_factory=list)
    bbox: list[float] | None = None
    start: str | None = None
    end: str | None = None


class SourceRef(BaseModel):
    name: str
    url: str
    study_id: str | None = None


class StorageRef(BaseModel):
    uri: str
    format: str


class Coverage(BaseModel):
    species: list[str] = Field(default_factory=list)
    bbox: list[float] | None = None
    start: str | None = None
    end: str | None = None


class Rights(BaseModel):
    license: str | None = None
    retention_allowed: bool | None = None
    reuse_allowed: bool | None = None
    attribution: str | None = None


class RawManifest(BaseModel):
    artifact_id: str
    version: str
    created_at: datetime
    access_scope: str = "public"
    source: SourceRef
    storage: StorageRef
    checksum: str
    retrieved_at: datetime
    coverage: Coverage = Field(default_factory=Coverage)
    rights: Rights = Field(default_factory=Rights)
    extensions: dict[str, Any] = Field(default_factory=dict)


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

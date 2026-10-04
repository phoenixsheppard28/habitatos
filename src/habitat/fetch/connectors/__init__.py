import math
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from habitat.archive import Archive
from habitat.archive.index import AlreadyIngested, never_ingested
from habitat.contracts import BBox, FetchError, RawManifest


@dataclass(frozen=True)
class ConnectorRequest:
    """One retrieval from one source. `item` names one dataset: a package, study, record or fixture id.
    `parameters` limits a water-quality station source to these vocabulary parameters; empty means all."""

    bbox: BBox | None = None
    start: date | None = None
    end: date | None = None
    item: str | None = None
    access_scope: str = "public"
    max_items: int = 10
    max_days: int = 31
    max_bytes: int = 2 * 1024**3
    max_file_bytes: int = 512 * 1024**2
    cloud_cover: float = 80.0
    parameters: tuple[str, ...] = ()


@dataclass
class ConnectorResult:
    manifests: list[RawManifest] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[FetchError] = field(default_factory=list)

    def fail(self, code: str, message: str, retryable: bool = False) -> "ConnectorResult":
        self.errors.append(FetchError(code=code, message=message, retryable=retryable))
        return self


class Connector(Protocol):
    def __call__(
        self, request: ConnectorRequest, archive: Archive, already_ingested: AlreadyIngested = never_ingested
    ) -> ConnectorResult: ...


def validate_area_and_dates(request: ConnectorRequest) -> tuple[date, date]:
    """Reject a malformed request before any network call."""
    if request.bbox is None or request.start is None or request.end is None:
        raise ValueError("this source needs a bbox, a start date and an end date")

    if len(request.bbox) != 4 or not all(math.isfinite(value) for value in request.bbox):
        raise ValueError("bbox must contain four finite WGS84 coordinates")

    west, south, east, north = request.bbox
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("bbox must be west,south,east,north; split a region that crosses the antimeridian")

    if request.start > request.end:
        raise ValueError("start must be on or before end")

    if not 1 <= request.max_items <= 100 or not 1 <= request.max_days <= 366 or request.max_bytes <= 0:
        raise ValueError("max_items must be 1..100, max_days 1..366, and max_bytes positive")

    if request.max_file_bytes <= 0 or not 0 <= request.cloud_cover <= 100:
        raise ValueError("max_file_bytes must be positive and cloud_cover 0..100")

    return request.start, request.end

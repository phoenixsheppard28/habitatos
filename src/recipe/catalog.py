"""Stage 2 owns search/indexing; this module unions and assesses its results."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Protocol

from shapely.geometry import shape

from .errors import RecipeError
from .models import DatasetVersion, QuerySpec, Requirement


@dataclass
class SearchPage:
    datasets: list[DatasetVersion]
    truncated: bool = False
    scores: dict[tuple[str, str], float] = field(default_factory=dict)


class Catalog(Protocol):
    def search_metadata(self, filters: dict, *, query: QuerySpec, limit: int) -> SearchPage: ...
    def search_semantic(self, text: str, *, query: QuerySpec, limit: int) -> SearchPage: ...
    def authorize(self, dataset: DatasetVersion, query: QuerySpec) -> bool: ...
    def readable(self, dataset: DatasetVersion) -> bool: ...
    def inspect(self, dataset: DatasetVersion, query: QuerySpec) -> DatasetVersion: ...


@dataclass
class Candidate:
    dataset: DatasetVersion
    retrievals: list[dict[str, Any]] = field(default_factory=list)
    requirements: set[str] = field(default_factory=set)


def discover(catalog: Catalog, query: QuerySpec, requirements: list[Requirement], limit=100):
    """Broad union; never impose an extra score/tag cutoff in the Recipe lane."""
    candidates = {}
    warnings = []
    for req in requirements:
        for method, page in (
            ("metadata", catalog.search_metadata(req.filters, query=query, limit=limit)),
            ("semantic", catalog.search_semantic(req.semantic_text, query=query, limit=limit)),
        ):
            if page.truncated:
                warnings.append(f"{req.requirement_id}: {method} results truncated at {limit}")
            for ds in page.datasets:
                # Apply hard controls even if a search adapter returned bad hits.
                if ds.status != "ready" or not catalog.authorize(ds, query):
                    continue
                candidate = candidates.setdefault(ds.key, Candidate(ds))
                if candidate.dataset != ds:
                    raise RecipeError("CATALOG_CONFLICT", "same immutable version has conflicting descriptors")
                candidate.requirements.add(req.requirement_id)
                candidate.retrievals.append({"method": method, "requirement_id": req.requirement_id,
                    "filters": req.filters if method == "metadata" else None,
                    "semantic_score": page.scores.get(ds.key)})
    return list(candidates.values()), warnings


@dataclass
class Eligibility:
    status: str
    reasons: list[str]


def _longitude_parts(w, e):
    return [(w, e)] if w <= e else [(w, 180), (-180, e)]


def _bbox_overlap(a, b):
    if a[3] < b[1] or b[3] < a[1]:
        return False
    return any(x <= v and u <= y for x, y in _longitude_parts(a[0], a[2])
               for u, v in _longitude_parts(b[0], b[2]))


def eligibility(catalog: Catalog, query: QuerySpec, ds: DatasetVersion, req: Requirement):
    hard, unknown = [], []
    if not catalog.authorize(ds, query):
        hard.append("access denied")
    if ds.status != "ready":
        hard.append("dataset is not validated/ready")
    if not catalog.readable(ds):
        hard.append("storage is unreadable")
    columns = {c.name: c for c in ds.columns}
    roles = {c.role for c in ds.columns}
    if not ("event_time" in roles or {"interval_start", "interval_end"} <= roles):
        unknown.append("time-field semantics unavailable")
    if not ("geometry" in roles or {"longitude", "latitude"} <= roles):
        unknown.append("location-field semantics unavailable")
    for name in req.required_columns:
        if name not in columns:
            hard.append(f"required column missing: {name}")
    for name, unit in req.expected_units.items():
        if name not in columns or columns[name].unit is None:
            unknown.append(f"unit unknown: {name}")
        elif columns[name].unit != unit:
            hard.append(f"unit mismatch: {name}")
    coverage = ds.coverage
    start = query.time_range.start - timedelta(seconds=req.lookback_seconds)
    if coverage.start is None or coverage.end is None:
        unknown.append("temporal coverage unknown")
    elif coverage.end < start or coverage.start > query.time_range.end:
        hard.append("temporal coverage does not overlap")
    try:
        bounds = shape(query.region).bounds
        if len(bounds) != 4 or shape(query.region).is_empty:
            raise ValueError("empty region")
    except (ValueError, TypeError, KeyError) as exc:
        raise RecipeError("INVALID_QUERY", "region must be a nonempty GeoJSON geometry") from exc
    if coverage.bbox is None:
        unknown.append("geographic coverage unknown")
    elif not _bbox_overlap(coverage.bbox, bounds):
        hard.append("geographic coverage does not overlap")
    if req.species_specific and query.species:
        if coverage.species is None:
            unknown.append("species coverage unknown")
        elif not set(query.species).intersection(coverage.species):
            hard.append("species coverage does not overlap")
    return Eligibility("ineligible" if hard else "unresolved" if unknown else "eligible", hard + unknown)


class CallbackCatalog:
    """Integration adapter: map any Stage 2 tag/search layout through callbacks."""

    def __init__(self, *, metadata_search, semantic_search, authorize, readable, inspect):
        self.search_metadata = metadata_search
        self.search_semantic = semantic_search
        self.authorize = authorize
        self.readable = readable
        self.inspect = inspect

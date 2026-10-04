import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pyarrow as pa
import shapely
from shapely.geometry.base import BaseGeometry

from habitat.contracts import SITE_FEATURES_SCHEMA, RawManifest
from habitat.grid import Grid
from habitat.normalize.rows import SITE_FEATURES, NormalizedBatch, series_id
from habitat.normalize.sources.movebank import cell_ids_for_points

FOREVER = datetime(9999, 12, 31, tzinfo=UTC)
IMPRECISE_LOCATION_M = 500.0
FLAG_ORDER = (
    "feature_id_missing", "geometry_repaired", "location_imprecise", "permanence_unknown", "status_unknown",
)
CLASSES_WITH_STATUS = frozenset({"water_point"})


@dataclass
class SiteFeature:
    """One version of one feature as a source shows it. `reasons` holds source-specific quality reasons."""

    source_record_id: str
    feature_id: str
    feature_class: str
    feature_type: str
    origin: str
    permanence: str
    status: str
    geometry: BaseGeometry
    time_start: datetime
    available_at: datetime
    name: str | None = None
    coordinate_uncertainty_m: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)


def to_site_features(
    features: list[SiteFeature], manifest: RawManifest, grid: Grid, mapping_version: str
) -> NormalizedBatch:
    item = manifest.extensions
    rows = []
    for feature in features:
        geometry, repaired = valid_geometry(feature.geometry)
        is_point = geometry.geom_type == "Point"
        rows.append({
            "source_record_id": feature.source_record_id,
            "dataset_id": series_id(manifest, grid),
            "source_id": item.source_id,
            "source_item_id": item.source_item_id,
            "processing_version": item.processing_version,
            "mapping_version": mapping_version,
            "feature_id": feature.feature_id,
            "feature_class": feature.feature_class,
            "feature_type": feature.feature_type,
            "origin": feature.origin,
            "permanence": feature.permanence,
            "status": feature.status,
            "name": feature.name,
            "time_start": feature.time_start,
            "time_end": FOREVER,
            "time_precision": item.time_precision.value,
            "available_at": feature.available_at,
            "longitude": geometry.x if is_point else None,
            "latitude": geometry.y if is_point else None,
            "cell_id": point_cell_id(grid, geometry) if is_point else None,
            "geometry": shapely.to_wkb(geometry),
            "coordinate_uncertainty_m": feature.coordinate_uncertainty_m,
            "quality_flag": quality_flag(feature, repaired),
            "attributes": json.dumps(feature.attributes, sort_keys=True, default=str),
        })

    table = pa.Table.from_pylist(rows, schema=SITE_FEATURES_SCHEMA)
    return NormalizedBatch(table, mapping_version, family=SITE_FEATURES)


def valid_geometry(geometry: BaseGeometry) -> tuple[BaseGeometry, bool]:
    if geometry.is_valid:
        return geometry, False

    return shapely.make_valid(geometry), True


def point_cell_id(grid: Grid, point: BaseGeometry) -> str:
    return str(cell_ids_for_points(grid, np.array([point.x]), np.array([point.y]))[0])


def quality_flag(feature: SiteFeature, geometry_repaired: bool) -> str:
    reasons = set(feature.reasons)
    if geometry_repaired:
        reasons.add("geometry_repaired")
    if (feature.coordinate_uncertainty_m or 0) > IMPRECISE_LOCATION_M:
        reasons.add("location_imprecise")
    if feature.permanence == "unknown":
        reasons.add("permanence_unknown")
    if feature.status == "unknown" and feature.feature_class in CLASSES_WITH_STATUS:
        reasons.add("status_unknown")

    return next((flag for flag in FLAG_ORDER if flag in reasons), "ok")

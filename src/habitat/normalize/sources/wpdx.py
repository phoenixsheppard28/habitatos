import json
from datetime import UTC, datetime
from typing import Any

from shapely.geometry import Point

from habitat.archive.store import ArtifactStore
from habitat.contracts import BBox, RawManifest
from habitat.grid import Grid
from habitat.normalize.rows import NormalizedBatch, QuarantineError
from habitat.normalize.site_features import SiteFeature, to_site_features

MAPPING_VERSION = "wpdx-plus-v1"
WATER_SOURCES = {
    "Borehole/Tubewell": ("borehole", "permanent"),
    "Protected Well": ("well", "unknown"),
    "Unprotected Well": ("well", "unknown"),
    "Undefined Well": ("well", "unknown"),
    "Protected Spring": ("spring", "unknown"),
    "Undefined Spring": ("spring", "unknown"),
    "Piped Water": ("tap", "permanent"),
    "Sand or Sub-surface Dam": ("sand_dam", "seasonal"),
    "Rainwater Harvesting": ("rainwater_tank", "seasonal"),
    "Delivered Water": ("delivered", "intermittent"),
}
STATUSES = {
    "Functional": "functional",
    "Functional, needs repair": "functional_needs_repair",
    "Functional, not in use": "functional_not_in_use",
    "Non-Functional": "non_functional",
    "Non-Functional, dry season": "non_functional_dry_season",
    "Abandoned/Decommissioned": "abandoned",
}
ATTRIBUTE_FIELDS = (
    "install_year", "water_tech_clean", "management_clean", "source", "dataset_title", "clean_adm1", "clean_adm2",
    "clean_adm3",
)
NATURAL_TYPES = frozenset({"spring"})


def normalize_wpdx(manifest: RawManifest, store: ArtifactStore, grid: Grid, aoi: BBox | None = None) -> NormalizedBatch:
    item = manifest.extensions
    if "water_points" not in item.assets:
        raise QuarantineError(f"{item.source_item_id}: missing asset 'water_points'")

    rows = json.loads(store.open(manifest, "water_points").read_text())
    return to_site_features([to_feature(row) for row in rows], manifest, grid, MAPPING_VERSION)


def to_feature(row: dict[str, Any]) -> SiteFeature:
    row_id = row.get("row_id")
    if row.get("lat_deg") in (None, "") or row.get("lon_deg") in (None, ""):
        raise QuarantineError(f"WPdx row {row_id}: no coordinates")
    for required in ("report_date", "updated"):
        if not row.get(required):
            raise QuarantineError(f"WPdx row {row_id}: no {required}")

    feature_type, permanence = feature_type_and_permanence(row.get("water_source_clean"), row_id)
    status = water_point_status(row.get("status_clean"), row_id)
    if status == "non_functional_dry_season":
        permanence = "seasonal"

    wpdx_id = row.get("wpdx_id")
    return SiteFeature(
        source_record_id=str(row_id),
        feature_id=f"wpdx:{wpdx_id}" if wpdx_id else f"wpdx:row:{row_id}",
        feature_class="water_point",
        feature_type=feature_type,
        origin="natural" if feature_type in NATURAL_TYPES else "artificial",
        permanence=permanence,
        status=status,
        geometry=Point(float(row["lon_deg"]), float(row["lat_deg"])),
        time_start=floating_utc(row["report_date"]),
        available_at=floating_utc(row["updated"]),
        attributes={name: row[name] for name in ATTRIBUTE_FIELDS if row.get(name) not in (None, "")},
        reasons=[] if wpdx_id else ["feature_id_missing"],
    )


def feature_type_and_permanence(water_source: str | None, row_id: str | None = None) -> tuple[str, str]:
    if not water_source:
        return "unknown", "unknown"
    if water_source not in WATER_SOURCES:
        raise QuarantineError(f"WPdx row {row_id}: water_source_clean {water_source!r} is not in the mapping table")

    return WATER_SOURCES[water_source]


def water_point_status(status: str | None, row_id: str | None) -> str:
    if not status:
        return "unknown"
    if status not in STATUSES:
        raise QuarantineError(f"WPdx row {row_id}: status_clean {status!r} is not in the mapping table")

    return STATUSES[status]


def floating_utc(value: str) -> datetime:
    """Socrata gives floating timestamps without a zone. The pipeline reads them as UTC (not verified)."""
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

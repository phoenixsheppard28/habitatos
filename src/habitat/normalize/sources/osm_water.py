import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from shapely.geometry import LineString, MultiLineString, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import linemerge, polygonize, unary_union

from habitat.archive.store import ArtifactStore
from habitat.contracts import BBox, RawManifest
from habitat.grid import Grid
from habitat.normalize.rows import NormalizedBatch, QuarantineError
from habitat.normalize.site_features import SiteFeature, to_site_features

MAPPING_VERSION = "osm-water-v1"
AREA_TAGS = {("natural", "water"), ("natural", "wetland"), ("landuse", "reservoir")}
METADATA_KEYS = ("version", "changeset")


@dataclass(frozen=True)
class TagRow:
    feature_class: str
    feature_type: str
    origin: str
    permanence: str | None = None


NATURAL_WATER = {
    "lake": TagRow("lake", "lake", "natural"),
    "oxbow": TagRow("lake", "oxbow", "natural"),
    "lagoon": TagRow("lake", "lagoon", "natural"),
    "pond": TagRow("pan", "pond", "natural"),
    "reservoir": TagRow("reservoir", "reservoir", "artificial", "unknown"),
    "basin": TagRow("reservoir", "basin", "artificial", "unknown"),
    "river": TagRow("river", "river", "natural"),
    "stream": TagRow("river", "stream", "natural"),
    "canal": TagRow("river", "canal", "artificial", "unknown"),
}
WATERWAYS = {
    "river": TagRow("river", "river", "natural"),
    "stream": TagRow("river", "stream", "natural"),
    "canal": TagRow("river", "canal", "artificial", "unknown"),
    "dam": TagRow("dam", "dam", "artificial", "unknown"),
    "weir": TagRow("dam", "weir", "artificial", "unknown"),
}
WATER_POINTS = {
    ("man_made", "water_tap"): TagRow("water_point", "tap", "artificial", "permanent"),
    ("amenity", "drinking_water"): TagRow("water_point", "tap", "artificial", "permanent"),
    ("amenity", "water_point"): TagRow("water_point", "tap", "artificial", "permanent"),
    ("man_made", "reservoir_covered"): TagRow("water_point", "reservoir_covered", "artificial", "unknown"),
    ("man_made", "dam"): TagRow("dam", "dam", "artificial", "unknown"),
    ("natural", "spring"): TagRow("water_point", "spring", "natural", "unknown"),
}
OPERATIONAL_STATUS = {
    "operational": "functional",
    "yes": "functional",
    "needs_maintenance": "functional_needs_repair",
    "needs_repair": "functional_needs_repair",
    "closed": "non_functional",
    "broken": "non_functional",
    "non_functional": "non_functional",
    "not_operational": "non_functional",
    "no": "non_functional",
}


def normalize_osm_water(
    manifest: RawManifest, store: ArtifactStore, grid: Grid, aoi: BBox | None = None
) -> NormalizedBatch:
    item = manifest.extensions
    if "features" not in item.assets:
        raise QuarantineError(f"{item.source_item_id}: missing asset 'features'")

    document = json.loads(store.open(manifest, "features").read_text())
    if not (document.get("osm3s") or {}).get("timestamp_osm_base"):
        raise QuarantineError(f"{item.source_item_id}: the response has no osm3s.timestamp_osm_base")

    features = [to_feature(element) for element in document.get("elements", [])]
    return to_site_features(features, manifest, grid, MAPPING_VERSION)


def to_feature(element: dict[str, Any]) -> SiteFeature:
    key = f"{element['type']}/{element['id']}"
    if not element.get("timestamp"):
        raise QuarantineError(f"{key}: the element has no timestamp, so its publication time is unknown")

    tags = element.get("tags", {})
    row = tag_row(tags)
    if row is None:
        raise QuarantineError(f"{key}: the tags {tags} match no row of the water tag table")

    mapped_at = parse_time(element["timestamp"])
    return SiteFeature(
        source_record_id=f"{key}@v{element['version']}",
        feature_id=f"osm:{key}",
        feature_class=row.feature_class,
        feature_type=row.feature_type,
        origin=row.origin,
        permanence=row.permanence or permanence(tags),
        status=status(tags),
        geometry=geometry(element, is_area(tags)),
        time_start=mapped_at,
        available_at=mapped_at,
        name=tags.get("name"),
        attributes={"tags": tags, **{name: element[name] for name in METADATA_KEYS if name in element}},
    )


def tag_row(tags: dict[str, str]) -> TagRow | None:
    """The first matching row wins: water points before water bodies, so a tagged well on a lake stays a well."""
    if tags.get("man_made") == "water_well":
        return TagRow("water_point", "borehole" if "pump" in tags else "well", "artificial", "unknown")

    for (key, value), row in WATER_POINTS.items():
        if tags.get(key) == value:
            return row

    if tags.get("natural") == "wetland":
        return TagRow("wetland", tags.get("wetland", "wetland"), "natural", "unknown")

    if tags.get("landuse") == "reservoir":
        return NATURAL_WATER["reservoir"]

    if tags.get("natural") == "water":
        if "water" not in tags:
            return TagRow("lake", "water", "unknown")
        return NATURAL_WATER.get(tags["water"])

    return WATERWAYS.get(tags.get("waterway"))


def permanence(tags: dict[str, str]) -> str:
    if tags.get("intermittent") == "yes" or tags.get("seasonal") == "yes":
        return "seasonal"
    if tags.get("intermittent") == "no":
        return "permanent"
    return "unknown"


def status(tags: dict[str, str]) -> str:
    if tags.get("abandoned") == "yes" or any(key.startswith("abandoned:") for key in tags):
        return "abandoned"
    if tags.get("disused") == "yes" or any(key.startswith("disused:") for key in tags):
        return "non_functional"
    return OPERATIONAL_STATUS.get(tags.get("operational_status", ""), "unknown")


def is_area(tags: dict[str, str]) -> bool:
    return tags.get("area") == "yes" or any(tags.get(key) == value for key, value in AREA_TAGS)


def geometry(element: dict[str, Any], area: bool) -> BaseGeometry:
    key = f"{element['type']}/{element['id']}"
    if element["type"] == "node":
        return Point(element["lon"], element["lat"])

    if element["type"] == "way":
        coordinates = way_coordinates(element.get("geometry"), key)
        if area and len(coordinates) >= 4 and coordinates[0] == coordinates[-1]:
            return Polygon(coordinates)
        return LineString(coordinates)

    lines = [
        (member.get("role"), LineString(way_coordinates(member.get("geometry"), key)))
        for member in element.get("members", [])
        if member.get("type") == "way" and len(member.get("geometry") or []) >= 2
    ]
    if not lines:
        raise QuarantineError(f"{key}: the relation has no member way with a geometry")

    if area:
        return multipolygon(lines, key)

    merged = linemerge([line for _, line in lines])
    return merged if isinstance(merged, MultiLineString) else MultiLineString([merged])


def multipolygon(lines: list[tuple[str, LineString]], key: str) -> BaseGeometry:
    outer = unary_union(list(polygonize([line for role, line in lines if role != "inner"])))
    inner = unary_union(list(polygonize([line for role, line in lines if role == "inner"])))
    if outer.is_empty:
        raise QuarantineError(f"{key}: the outer member ways do not form a closed ring")

    return outer.difference(inner)


def way_coordinates(points: list[dict] | None, key: str) -> list[tuple[float, float]]:
    """Overpass gives null for a node outside the response; such a way has no complete geometry."""
    if not points or any(point is None for point in points):
        raise QuarantineError(f"{key}: the way has no complete geometry")

    return [(point["lon"], point["lat"]) for point in points]


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

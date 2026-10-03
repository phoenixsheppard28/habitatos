"""Share of a tracked path that falls inside a named boundary.

Distance is equirectangular kilometers. The inside fraction of a segment is
measured in that same local frame, so a segment that only clips a boundary
is not scored with degree-length.
"""

import math

import pandas as pd
from shapely import make_valid
from shapely.geometry import LineString, MultiPolygon, Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform


def route_overlap(frame: pd.DataFrame, roles: dict, boundaries: list) -> dict:
    entity = roles["entity_id"].name
    event_time = roles["event_time"].name
    lon = roles["longitude"].name
    lat = roles["latitude"].name
    paths = _paths(frame, entity, event_time, lon, lat)
    layers = []
    findings = []
    warnings = []
    for boundary in boundaries:
        geometry = _geometry(boundary.geometry)
        if geometry is None:
            warnings.append(f"Boundary {boundary.name} is not a usable polygon and was skipped.")
            continue
        animals = []
        points_inside = 0
        points = 0
        path_km = 0.0
        inside_km = 0.0
        for animal, coords in paths:
            animal_points = sum(1 for coordinate in coords if geometry.covers(Point(coordinate)))
            animal_km, animal_inside = _lengths(coords, geometry)
            points += len(coords)
            points_inside += animal_points
            path_km += animal_km
            inside_km += animal_inside
            animals.append(
                {
                    "entity_id": animal,
                    "n_points": len(coords),
                    "n_points_inside": animal_points,
                    "path_km": animal_km,
                    "path_km_inside": animal_inside,
                    "path_share": 0.0 if animal_km == 0 else animal_inside / animal_km,
                }
            )
        share = 0.0 if path_km == 0 else inside_km / path_km
        layers.append(
            {
                "name": boundary.name,
                "n_points": points,
                "n_points_inside": points_inside,
                "point_share": 0.0 if points == 0 else points_inside / points,
                "path_km": path_km,
                "path_km_inside": inside_km,
                "path_share": share,
                "animals": animals,
            }
        )
        findings.append(
            f"{inside_km:.2f} km of {path_km:.2f} km of tracked path falls inside {boundary.name} "
            f"({points_inside} of {points} fixes). This is the tracked sample, not a population."
        )
    return {"findings": findings, "metrics": {"boundaries": layers}, "warnings": warnings, "limitations": []}


def _geometry(payload: dict) -> BaseGeometry | None:
    try:
        geometry = shape(payload)
    except Exception:
        return None
    if geometry.is_empty:
        return None
    if not geometry.is_valid:
        geometry = make_valid(geometry)
    if geometry.geom_type == "GeometryCollection":
        parts = [part for part in geometry.geoms if part.geom_type in {"Polygon", "MultiPolygon"}]
        if not parts:
            return None
        geometry = parts[0] if len(parts) == 1 else MultiPolygon(_polygon_parts(parts))
    if geometry.geom_type not in {"Polygon", "MultiPolygon"} or geometry.is_empty:
        return None
    return geometry


def _polygon_parts(parts) -> list:
    polygons = []
    for part in parts:
        if part.geom_type == "Polygon":
            polygons.append(part)
        else:
            polygons.extend(part.geoms)
    return polygons


def _paths(frame, entity, event_time, lon, lat):
    paths = []
    for animal, group in frame.groupby(entity, sort=True):
        coordinates = []
        for _, row in group.sort_values(event_time).iterrows():
            if pd.isna(row[lon]) or pd.isna(row[lat]):
                continue
            coordinates.append((float(row[lon]), float(row[lat])))
        if coordinates:
            paths.append((str(animal), coordinates))
    return paths


def _lengths(coords: list[tuple[float, float]], geometry: BaseGeometry) -> tuple[float, float]:
    total = 0.0
    inside = 0.0
    for start, end in zip(coords, coords[1:]):
        length = _segment_km(*start, *end)
        total += length
        if length == 0:
            continue
        local_segment = LineString([(0.0, 0.0), _local_xy(end[0], end[1], start[0], start[1])])
        local_boundary = transform(lambda x, y, z=None, origin=start: _local_xy(x, y, origin[0], origin[1]), geometry)
        if local_segment.length == 0 or not local_segment.intersects(local_boundary):
            continue
        crossed = local_segment.intersection(local_boundary)
        inside += length * (crossed.length / local_segment.length)
    return total, inside


def _local_xy(lon: float, lat: float, origin_lon: float, origin_lat: float) -> tuple[float, float]:
    mid_lat = math.radians(origin_lat)
    x = (lon - origin_lon) * math.cos(mid_lat) * 111.32
    y = (lat - origin_lat) * 110.574
    return x, y


def _segment_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    mid_lat = math.radians((lat1 + lat2) / 2)
    dx = (lon2 - lon1) * math.cos(mid_lat) * 111.32
    dy = (lat2 - lat1) * 110.574
    return math.hypot(dx, dy)

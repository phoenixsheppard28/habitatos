"""Monthly per-cell water variables, derived from `site_features` and monthly surface water.

See docs/ingestion/WATER_POINTS.md, section "Derived cell variables".
"""

import argparse
import hashlib
import json
import logging
import math
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import shapely
from psycopg.rows import dict_row
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as transform_geometry

from habitat.contracts import (
    CELL_OBSERVATIONS_SCHEMA,
    BBox,
    Coverage,
    FetchRequest,
    RawManifest,
    Rights,
    SourceItem,
    SourceRef,
    TimePrecision,
)
from habitat.fetch.connectors import jrc_gsw_monthly, osm_overpass, wpdx
from habitat.fetch.connectors.water_derived import PRODUCT, STORAGE_FORMAT
from habitat.fetch.connectors.stac import SENTINEL2_RIGHTS, bbox_key
from habitat.grid import Grid, parse_cell_id, transformer
from habitat.ingest import IngestOutcome, Workspace
from habitat.normalize.rows import NormalizedBatch, series_id_for, to_cell_observations
from habitat.normalize.sources.movebank import cell_ids_for_points
from habitat.storage.series import AppendResult

logger = logging.getLogger(__name__)

SOURCE_ID = "water_derived"
MAPPING_VERSION = "water-derived-v1"
SOURCE_RESOLUTION_M = 1000.0
SEARCH_RADIUS_M = 20_000.0
DENSITY_RADIUS_M = 5_000.0
DENSITY_AREA_KM2 = math.pi * (DENSITY_RADIUS_M / 1000) ** 2
DUPLICATE_DISTANCE_M = 50.0
MIN_VALID_FRACTION = 0.5
METRES_PER_DEGREE = 111_320.0

SURFACE_WATER_SOURCE = "jrc_gsw_monthly"
MNDWI_SOURCE = "sentinel2"
FEATURE_SOURCES = frozenset({"osm_overpass", "wpdx"})
INPUT_SOURCES = FEATURE_SOURCES | {SURFACE_WATER_SOURCE}
UNAVAILABLE_STATUSES = frozenset({"non_functional", "abandoned"})
SOURCE_RIGHTS = {
    "osm_overpass": osm_overpass.RIGHTS,
    "wpdx": wpdx.RIGHTS,
    SURFACE_WATER_SOURCE: jrc_gsw_monthly.RIGHTS,
    MNDWI_SOURCE: SENTINEL2_RIGHTS,
}

BACKFILLED = "feature_backfilled"
STATE_UNKNOWN = "seasonal_state_unknown"
BEYOND_RADIUS = "beyond_search_radius"
EDGE_EFFECT = "edge_effect"
LOW_VALID_FRACTION = "low_valid_fraction"
FLAG_ORDER = (BEYOND_RADIUS, EDGE_EFFECT, STATE_UNKNOWN, BACKFILLED, LOW_VALID_FRACTION)

ALL_WATER = "distance_to_water_m"
DISTANCES: dict[str, Callable[["Feature"], bool]] = {
    ALL_WATER: lambda feature: True,
    "distance_to_permanent_water_m": lambda feature: feature.permanence == "permanent",
    "distance_to_natural_water_m": lambda feature: feature.origin == "natural",
    "distance_to_artificial_water_m": lambda feature: feature.origin == "artificial",
}
DENSITY = "water_point_density"
UNITS = {**{variable: "m" for variable in DISTANCES}, DENSITY: "count_per_km2"}


@dataclass
class Feature:
    feature_id: str
    series_id: str
    batch_key: str
    source_id: str
    feature_type: str
    origin: str
    permanence: str
    status: str
    time_start: datetime
    valid_until: datetime
    time_precision: str
    available_at: datetime
    attributes: dict[str, Any]
    geometry: BaseGeometry
    reasons: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class InputBatch:
    series_id: str
    batch_key: str
    dataset_version: int
    mapping_version: str
    variable: str
    source_id: str
    available_at: datetime
    requested_bbox: BBox | None


@dataclass
class Month:
    start: datetime
    end: datetime

    @classmethod
    def of(cls, day: date) -> "Month":
        first = day.replace(day=1)
        return cls(
            datetime.combine(first, time.min, tzinfo=UTC), datetime.combine(next_month(first), time.min, tzinfo=UTC)
        )


def next_month(month: date) -> date:
    return date(month.year + month.month // 12, month.month % 12 + 1, 1)


def months_between(start: date, end: date) -> list[date]:
    months, month = [], start.replace(day=1)
    while month <= end:
        months.append(month)
        month = next_month(month)
    return months


def derive_water(
    workspace: Workspace, bbox: BBox, start: date, end: date, access_scope: str = "public"
) -> list[IngestOutcome]:
    outcomes = [derive_water_month(workspace, bbox, month, access_scope) for month in months_between(start, end)]
    return [outcome for outcome in outcomes if outcome is not None]


def derive_water_month(
    workspace: Workspace, bbox: BBox, month: date, access_scope: str = "public"
) -> IngestOutcome | None:
    """Recompute one bbox and month. Unchanged inputs give the same batch key, so nothing new is appended."""
    connection, grid = workspace.connection, workspace.grid
    period = Month.of(month)
    search_area = buffered(bbox, SEARCH_RADIUS_M)
    search_cells = cells_in(grid, search_area)

    features = current_features(connection, search_area, access_scope)
    surface = surface_water_rows(connection, search_cells, period, access_scope)
    inputs = input_batches(connection, search_area, features, surface, access_scope)
    if not inputs:
        return None

    series = series_id_for(SOURCE_ID, PRODUCT, grid)
    item_id = f"{bbox_key(bbox)}:{period.start:%Y-%m}"
    available_at = latest_available_at(features, surface, inputs)
    version = processing_version(available_at, inputs)
    live = live_batches(connection, series, item_id)
    if (unchanged := live.get(version)) is not None:
        key, manifest = unchanged
        return IngestOutcome(manifest, AppendResult(series, workspace.store.latest_version(series).version, key, False))

    manifest = derived_manifest(workspace, bbox, period, item_id, version, available_at, inputs, access_scope)
    batch = water_batch(grid, bbox, period, features, surface, inputs, manifest)
    superseded = tuple(key for key, _ in live.values())
    return IngestOutcome(manifest, workspace.store.append_batch(series, manifest, batch, supersedes=superseded))


def buffered(bbox: BBox, metres: float) -> BBox:
    west, south, east, north = bbox
    lat_step = metres / METRES_PER_DEGREE
    lon_step = metres / (METRES_PER_DEGREE * max(math.cos(math.radians(max(abs(south), abs(north)))), 0.01))
    return max(west - lon_step, -180), max(south - lat_step, -90), min(east + lon_step, 180), min(north + lat_step, 90)


def cells_in(grid: Grid, bbox: BBox) -> list[str]:
    rows, cols = grid.cells_in_bbox(bbox)
    return grid.cell_ids(rows, cols).tolist()


def current_features(connection, search_area: BBox, access_scope: str) -> list[Feature]:
    """The current version of each feature per series, as in recipe_site_features, without the catalog."""
    west, south, east, north = search_area
    with connection.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            """
            SELECT feature_id, series_id, batch_key, source_id, feature_type, origin, permanence, status, time_start,
                   least(time_end, lead(time_start) OVER (PARTITION BY series_id, feature_id ORDER BY time_start))
                       AS valid_until,
                   time_precision, available_at, attributes, ST_AsBinary(geometry) AS geometry
            FROM (
                SELECT f.*, row_number() OVER (
                    PARTITION BY f.series_id, f.feature_id, f.time_start
                    ORDER BY b.added_in_version DESC, f.available_at DESC, f.source_record_id DESC
                ) AS current_rank
                FROM site_features f
                JOIN ingest_batches b USING (series_id, batch_key)
                WHERE b.superseded_in_version IS NULL
                  AND b.raw_manifest->>'access_scope' = %(scope)s
                  AND f.geometry && ST_MakeEnvelope(%(west)s, %(south)s, %(east)s, %(north)s, 4326)
            ) ranked
            WHERE current_rank = 1
            """,
            {"scope": access_scope, "west": west, "south": south, "east": east, "north": north},
        ).fetchall()

    clip = box(*search_area)
    features = []
    for row in rows:
        geometry = shapely.from_wkb(bytes(row.pop("geometry")))
        clipped = geometry if geometry.geom_type == "Point" else geometry.intersection(clip)
        if not clipped.is_empty:
            features.append(Feature(**row, geometry=clipped))
    return features


def surface_water_rows(connection, cells: list[str], period: Month, access_scope: str) -> pd.DataFrame:
    with connection.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            """
            SELECT o.cell_id, o.source_id, o.variable, o.value, o.valid_fraction, o.available_at, o.series_id,
                   o.batch_key
            FROM current_cell_observations o
            JOIN ingest_batches b USING (series_id, batch_key)
            WHERE o.cell_id = ANY(%(cells)s)
              AND b.raw_manifest->>'access_scope' = %(scope)s
              AND ((o.source_id = %(surface)s AND o.time_start = %(start)s
                    AND o.variable IN ('surface_water_fraction', 'distance_to_surface_water_m'))
                   OR (o.source_id = %(mndwi)s AND o.variable = 'mndwi'
                       AND o.time_start >= %(start)s AND o.time_start < %(end)s))
            """,
            {"cells": cells, "scope": access_scope, "surface": SURFACE_WATER_SOURCE, "mndwi": MNDWI_SOURCE,
             "start": period.start, "end": period.end},
        ).fetchall()

    columns = ["cell_id", "source_id", "variable", "value", "valid_fraction", "available_at", "series_id", "batch_key"]
    return pd.DataFrame(rows, columns=columns)


def input_batches(
    connection, search_area: BBox, features: list[Feature], surface: pd.DataFrame, access_scope: str
) -> list[InputBatch]:
    """Feature batches that cover the search area, also without a feature in it, and every batch of a used row."""
    used = {(feature.series_id, feature.batch_key) for feature in features}
    used |= set(zip(surface["series_id"], surface["batch_key"]))
    variables = {(feature.series_id, feature.batch_key): {"site_features"} for feature in features}
    for (series, key), group in surface.groupby(["series_id", "batch_key"]):
        variables[(series, key)] = set(group["variable"])

    with connection.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            """
            SELECT b.series_id, b.batch_key, b.mapping_version, b.raw_manifest, s.latest_version
            FROM ingest_batches b
            JOIN series s USING (series_id)
            WHERE b.superseded_in_version IS NULL
              AND b.raw_manifest->>'access_scope' = %(scope)s
              AND (s.family = 'site_features' OR (b.series_id, b.batch_key) IN (
                       SELECT * FROM unnest(%(series)s::text[], %(keys)s::text[])))
            """,
            {"scope": access_scope, "series": [s for s, _ in used], "keys": [k for _, k in used]},
        ).fetchall()

    area = box(*search_area)
    batches = []
    for row in rows:
        item = row["raw_manifest"]["extensions"]
        requested = item.get("properties", {}).get("requested_bbox")
        pair = (row["series_id"], row["batch_key"])
        if pair not in used and not (requested and box(*requested).intersects(area)):
            continue

        for variable in sorted(variables.get(pair, {"site_features"})):
            batches.append(InputBatch(
                series_id=row["series_id"], batch_key=row["batch_key"], dataset_version=row["latest_version"],
                mapping_version=row["mapping_version"], variable=variable, source_id=item["source_id"],
                available_at=datetime.fromisoformat(item["available_at"]),
                requested_bbox=tuple(requested) if requested else None,
            ))
    return sorted(batches, key=lambda batch: (batch.series_id, batch.batch_key, batch.variable))


def latest_available_at(features: list[Feature], surface: pd.DataFrame, inputs: list[InputBatch]) -> datetime:
    """The latest publication of an input row. Without a row, the latest publication of an input batch."""
    times = [feature.available_at for feature in features] + list(surface["available_at"])
    return max(times) if times else max(batch.available_at for batch in inputs)


def processing_version(available_at: datetime, inputs: list[InputBatch]) -> str:
    keys = sorted({f"{batch.series_id}|{batch.batch_key}" for batch in inputs})
    digest = hashlib.sha256("\n".join(keys).encode()).hexdigest()[:8]
    return f"{available_at.astimezone(UTC):%Y-%m-%dT%H:%M:%SZ}+{digest}"


def live_batches(connection, series: str, item_id: str) -> dict[str, tuple[str, RawManifest]]:
    """Live derived batches of one bbox and month, by processing version."""
    rows = connection.execute(
        "SELECT processing_version, batch_key, raw_manifest FROM ingest_batches "
        "WHERE series_id = %s AND source_item_id = %s AND superseded_in_version IS NULL",
        (series, item_id),
    ).fetchall()
    return {version: (key, RawManifest.model_validate(manifest)) for version, key, manifest in rows}


def derived_manifest(
    workspace: Workspace,
    bbox: BBox,
    period: Month,
    item_id: str,
    version: str,
    available_at: datetime,
    inputs: list[InputBatch],
    access_scope: str,
) -> RawManifest:
    """The manifest points to a small JSON artifact that lists the input batches."""
    batch_keys = sorted({f"{batch.series_id}|{batch.batch_key}" for batch in inputs})
    listed = [
        {"dataset_id": batch.series_id, "dataset_version": batch.dataset_version,
         "mapping_version": batch.mapping_version, "variable": batch.variable, "batch_key": batch.batch_key}
        for batch in inputs
    ]
    body = json.dumps({"item": item_id, "batch_keys": batch_keys, "inputs": listed}, indent=1).encode()
    source_key = f"{SOURCE_ID}:{item_id}:{version}"
    artifact_id = f"water-derived-{hashlib.sha256(source_key.encode()).hexdigest()[:16]}"
    artifact_version, stored = workspace.archive.put(artifact_id, {"inputs.json": body}, STORAGE_FORMAT)

    created_at = datetime.now(UTC)
    manifest = RawManifest(
        artifact_id=artifact_id,
        version=artifact_version,
        created_at=created_at,
        access_scope=access_scope,
        source=SourceRef(name="Water variables derived from site_features and monthly surface water"),
        storage=stored.storage,
        checksum=stored.checksum,
        retrieved_at=created_at,
        coverage=Coverage(bbox=bbox, start=period.start, end=period.end),
        rights=combined_rights({batch.source_id for batch in inputs}),
        extensions=SourceItem(
            source_id=SOURCE_ID,
            product=PRODUCT,
            source_item_id=item_id,
            source_key=source_key,
            kind="derived",
            time_start=period.start,
            time_end=period.end,
            time_precision=TimePrecision.COMPOSITE,
            available_at=available_at,
            processing_version=version,
            assets={"inputs": "inputs.json"},
            properties={"inputs": listed, "requested_bbox": list(bbox), "search_radius_m": SEARCH_RADIUS_M},
        ),
    )
    return workspace.archive.record(manifest)


def combined_rights(source_ids: Iterable[str]) -> Rights:
    """ODbL is share-alike, so it is the most restrictive license of the inputs when OSM is one of them."""
    rights = [SOURCE_RIGHTS[source] for source in sorted(source_ids) if source in SOURCE_RIGHTS]
    licenses = {right.license for right in rights}
    return Rights(
        license="ODbL-1.0" if "ODbL-1.0" in licenses else "; ".join(sorted(licenses)) or None,
        retention_allowed=all(right.retention_allowed for right in rights),
        reuse_allowed=all(right.reuse_allowed for right in rights),
        attribution="; ".join(right.attribution for right in rights),
    )


def water_batch(
    grid: Grid,
    bbox: BBox,
    period: Month,
    features: list[Feature],
    surface: pd.DataFrame,
    inputs: list[InputBatch],
    manifest: RawManifest,
) -> NormalizedBatch:
    cells = cells_in(grid, bbox)
    local = local_projection(bbox)
    to_local = transformer("EPSG:4326", local).transform
    cell_points = cell_centroids(grid, cells, to_local)

    observed = observed_water(surface)
    available = deduplicated([f for f in features if is_available(f, period, observed, grid)], to_local)
    geometries = [transform_geometry(to_local, feature.geometry) for feature in available]
    edge = edge_distances(cell_points, coverage_area(inputs), to_local)
    valid = valid_fractions(grid, cell_points, surface, bbox, to_local)
    surface_distance = surface_distances(cells, surface)

    # Without a feature source only the surface water is known; a zero density or a missing artificial water
    # point would then be a guess.
    has_features = any(batch.source_id in FEATURE_SOURCES for batch in inputs)
    variables = DISTANCES if has_features else {ALL_WATER: DISTANCES[ALL_WATER]}
    distances = pd.concat(
        [
            distance_rows(variable, keep, cells, cell_points, available, geometries, edge, valid,
                          surface_distance if variable == ALL_WATER else None)
            for variable, keep in variables.items()
        ],
        ignore_index=True,
    )
    tables = [
        flagged(to_cell_observations(distances, manifest, grid, MAPPING_VERSION, "centroid", UNITS,
                                     SOURCE_RESOLUTION_M).table, distances["flag"]),
    ]
    if has_features:
        density = density_rows(cells, cell_points, available, geometries, edge, valid)
        tables.append(flagged(to_cell_observations(density, manifest, grid, MAPPING_VERSION, "density", UNITS,
                                                   SOURCE_RESOLUTION_M).table, density["flag"]))

    return NormalizedBatch(pa.concat_tables(tables), MAPPING_VERSION)


def local_projection(bbox: BBox) -> str:
    """An azimuthal equidistant projection at the bbox centre. EASE-Grid 2.0 is equal-area, not equidistant."""
    west, south, east, north = bbox
    return f"+proj=aeqd +lat_0={(south + north) / 2:.6f} +lon_0={(west + east) / 2:.6f} +datum=WGS84 +units=m +no_defs"


def cell_centroids(grid: Grid, cells: list[str], to_local) -> np.ndarray:
    rows, cols = np.array([parse_cell_id(cell) for cell in cells]).T
    x, y = grid.cell_centres_xy(rows, cols)
    lon, lat = transformer(grid.crs, "EPSG:4326").transform(x, y)
    local_x, local_y = to_local(lon, lat)
    return shapely.points(np.asarray(local_x), np.asarray(local_y))


def observed_water(surface: pd.DataFrame) -> dict[str, bool]:
    """Water seen in a cell this month: JRC first; Sentinel-2 MNDWI > 0 only where JRC has no value."""
    observed = {}
    mndwi = surface[(surface["source_id"] == MNDWI_SOURCE) & surface["value"].notna()]
    for cell, values in mndwi.groupby("cell_id")["value"]:
        observed[cell] = bool((values > 0).any())

    fractions = surface[(surface["variable"] == "surface_water_fraction") & surface["value"].notna()]
    observed.update({cell: value > 0 for cell, value in zip(fractions["cell_id"], fractions["value"])})
    return observed


def is_available(feature: Feature, period: Month, observed: dict[str, bool], grid: Grid) -> bool:
    """Apply the availability rules of the design. Sets the reasons of a feature that counts as available."""
    if feature.valid_until <= period.start:
        return False

    if feature.time_start >= period.end:
        start = documented_start(feature.attributes)
        backfill_natural = feature.origin == "natural" and feature.time_precision == TimePrecision.STATIC.value
        if not (backfill_natural or (start is not None and start < period.end)):
            return False
        feature.reasons.add(BACKFILLED)

    if feature.status in UNAVAILABLE_STATUSES:
        return False

    if feature.permanence == "permanent":
        return True

    states = [observed[cell] for cell in feature_cells(grid, feature.geometry) if cell in observed]
    if any(states):
        return True
    if states:
        return False

    feature.reasons.add(STATE_UNKNOWN)
    return True


def documented_start(attributes: dict[str, Any]) -> datetime | None:
    """The first day of the documented construction or installation year, if the source gives one."""
    tags = attributes.get("tags") or {}
    for value in (attributes.get("install_year"), tags.get("start_date"), tags.get("construction_date")):
        if match := re.match(r"\s*(\d{4})", str(value or "")):
            return datetime(int(match.group(1)), 1, 1, tzinfo=UTC)
    return None


def feature_cells(grid: Grid, geometry: BaseGeometry) -> list[str]:
    if geometry.geom_type == "Point":
        return cell_ids_for_points(grid, np.array([geometry.x]), np.array([geometry.y])).tolist()

    projected = transform_geometry(transformer("EPSG:4326", grid.crs).transform, geometry)
    min_x, min_y, max_x, max_y = projected.bounds
    top, left = grid.rows_cols_from_xy(np.array([min_x]), np.array([max_y]))
    bottom, right = grid.rows_cols_from_xy(np.array([max_x]), np.array([min_y]))
    rows, cols = np.meshgrid(np.arange(top[0], bottom[0] + 1), np.arange(left[0], right[0] + 1), indexing="ij")
    rows, cols = rows.ravel(), cols.ravel()

    x0 = grid.origin_x_m + cols * grid.cell_size_m
    y1 = grid.origin_y_m - rows * grid.cell_size_m
    hits = shapely.intersects(shapely.box(x0, y1 - grid.cell_size_m, x0 + grid.cell_size_m, y1), projected)
    return grid.cell_ids(rows[hits], cols[hits]).tolist()


def deduplicated(features: list[Feature], to_local) -> list[Feature]:
    """Two sources often map one water point. A point of another series with the same type within 50 m is the
    same point; the first source id wins."""
    kept: list[Feature] = []
    kept_points: list[tuple[Feature, BaseGeometry]] = []
    for feature in sorted(features, key=lambda feature: (feature.source_id, feature.feature_id)):
        if feature.geometry.geom_type != "Point":
            kept.append(feature)
            continue

        point = transform_geometry(to_local, feature.geometry)
        duplicate = any(
            other.feature_type == feature.feature_type and other.series_id != feature.series_id
            and other_point.distance(point) <= DUPLICATE_DISTANCE_M
            for other, other_point in kept_points
        )
        if not duplicate:
            kept.append(feature)
            kept_points.append((feature, point))
    return kept


def coverage_area(inputs: list[InputBatch]) -> BaseGeometry | None:
    """The area that the input batches were fetched for. Water outside it is unknown, not absent."""
    boxes = [box(*batch.requested_bbox) for batch in inputs if batch.requested_bbox]
    return shapely.union_all(boxes) if boxes else None


def edge_distances(cell_points: np.ndarray, coverage: BaseGeometry | None, to_local) -> np.ndarray:
    if coverage is None:
        return np.full(len(cell_points), np.inf)

    local = transform_geometry(to_local, coverage)
    inside = shapely.contains(local, cell_points)
    return np.where(inside, shapely.distance(local.boundary, cell_points), 0.0)


def valid_fractions(grid: Grid, cell_points: np.ndarray, surface: pd.DataFrame, bbox: BBox, to_local) -> np.ndarray:
    """The mean observed share of JRC pixels in the cells within the search radius. 1.0 without JRC input."""
    fractions = surface[surface["variable"] == "surface_water_fraction"]
    if fractions.empty:
        return np.ones(len(cell_points))

    search_cells = cells_in(grid, buffered(bbox, SEARCH_RADIUS_M))
    observed = dict(zip(fractions["cell_id"], fractions["valid_fraction"]))
    shares = np.array([observed.get(cell, 0.0) for cell in search_cells])

    near_cell, near_search = shapely.STRtree(cell_centroids(grid, search_cells, to_local)).query(
        cell_points, predicate="dwithin", distance=SEARCH_RADIUS_M
    )
    totals = np.bincount(near_cell, weights=shares[near_search], minlength=len(cell_points))
    counts = np.bincount(near_cell, minlength=len(cell_points))
    return totals / np.maximum(counts, 1)


def surface_distances(cells: list[str], surface: pd.DataFrame) -> np.ndarray:
    rows = surface[(surface["variable"] == "distance_to_surface_water_m") & surface["value"].notna()]
    known = dict(zip(rows["cell_id"], rows["value"]))
    return np.array([known.get(cell, np.nan) for cell in cells], dtype=float)


def distance_rows(
    variable: str,
    keep: Callable[[Feature], bool],
    cells: list[str],
    cell_points: np.ndarray,
    available: list[Feature],
    geometries: list[BaseGeometry],
    edge: np.ndarray,
    valid: np.ndarray,
    surface_distance: np.ndarray | None,
) -> pd.DataFrame:
    chosen = [index for index, feature in enumerate(available) if keep(feature)]
    distance = np.full(len(cells), np.inf)
    reasons: list[set[str]] = [set() for _ in cells]
    counts = np.zeros(len(cells), dtype=np.int64)
    if chosen:
        tree = shapely.STRtree([geometries[index] for index in chosen])
        (cell_index, tree_index), found = tree.query_nearest(cell_points, return_distance=True, all_matches=False)
        distance[cell_index] = found
        for cell, nearest in zip(cell_index, tree_index):
            reasons[cell] = set(available[chosen[nearest]].reasons)
        near_cell, _ = tree.query(cell_points, predicate="dwithin", distance=SEARCH_RADIUS_M)
        counts = np.bincount(near_cell, minlength=len(cells))

    if surface_distance is not None:
        closer = np.nan_to_num(surface_distance, nan=np.inf) < distance
        distance[closer] = surface_distance[closer]
        for cell in np.flatnonzero(closer):
            reasons[cell] = set()

    within = distance <= SEARCH_RADIUS_M
    return pd.DataFrame({
        "cell_id": cells,
        "variable": variable,
        "value": np.where(within, distance, np.nan),
        "std": None,
        "valid_fraction": valid,
        "pixel_count": counts,
        "flag": [
            quality_flag(reasons[i], within[i], edge[i], min(distance[i], SEARCH_RADIUS_M), valid[i])
            for i in range(len(cells))
        ],
    })


def density_rows(
    cells: list[str],
    cell_points: np.ndarray,
    available: list[Feature],
    geometries: list[BaseGeometry],
    edge: np.ndarray,
    valid: np.ndarray,
) -> pd.DataFrame:
    points = [index for index, feature in enumerate(available) if feature.geometry.geom_type == "Point"]
    counts = np.zeros(len(cells), dtype=np.int64)
    reasons: list[set[str]] = [set() for _ in cells]
    if points:
        near_cell, near_point = shapely.STRtree([geometries[index] for index in points]).query(
            cell_points, predicate="dwithin", distance=DENSITY_RADIUS_M
        )
        counts = np.bincount(near_cell, minlength=len(cells))
        for cell, point in zip(near_cell, near_point):
            reasons[cell] |= available[points[point]].reasons

    return pd.DataFrame({
        "cell_id": cells,
        "variable": DENSITY,
        "value": counts / DENSITY_AREA_KM2,
        "std": None,
        "valid_fraction": valid,
        "pixel_count": counts,
        "flag": [quality_flag(reasons[i], True, edge[i], DENSITY_RADIUS_M, valid[i]) for i in range(len(cells))],
    })


def quality_flag(reasons: set[str], within: bool, edge: float, reach_m: float, valid_fraction: float) -> str:
    """A nearer water outside the fetched area is possible when the area edge is closer than the value."""
    flags = set(reasons)
    if not within:
        flags.add(BEYOND_RADIUS)
    if edge < reach_m:
        flags.add(EDGE_EFFECT)
    if valid_fraction < MIN_VALID_FRACTION:
        flags.add(LOW_VALID_FRACTION)
    return next((flag for flag in FLAG_ORDER if flag in flags), "ok")


def flagged(table: pa.Table, flags: pd.Series) -> pa.Table:
    index = CELL_OBSERVATIONS_SCHEMA.get_field_index("quality_flag")
    return table.set_column(index, CELL_OBSERVATIONS_SCHEMA.field(index), pa.array(flags.tolist(), pa.string()))


def derive_after_ingest(
    request: FetchRequest, ingests: list[IngestOutcome], workspace: Workspace
) -> list[IngestOutcome]:
    """Recompute the water variables when a fetch appended a water input. The request gives the area and dates;
    without them, the fetched area and the fetched JRC months."""
    appended = [
        outcome.manifest for outcome in ingests
        if outcome.append and outcome.append.appended and outcome.manifest.extensions.source_id in INPUT_SOURCES
    ]
    if not appended:
        return []

    requirements = request.input.requirements
    bbox = tuple(requirements.bbox) if requirements.bbox else fetched_bbox(appended)
    if requirements.start and requirements.end:
        start, end = date.fromisoformat(requirements.start[:10]), date.fromisoformat(requirements.end[:10])
    else:
        months = [m.extensions.time_start.date() for m in appended if m.extensions.source_id == SURFACE_WATER_SOURCE]
        if not months:
            logger.info("water derive skipped: the request gives no dates and no JRC month was fetched")
            return []
        start, end = min(months), max(months)

    if bbox is None:
        logger.info("water derive skipped: the request gives no bbox")
        return []

    return derive_water(workspace, bbox, start, end, request.access_scope)


def fetched_bbox(manifests: list[RawManifest]) -> BBox | None:
    boxes = [m.extensions.properties.get("requested_bbox") for m in manifests]
    boxes = [b for b in boxes if b]
    if not boxes:
        return None

    return min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)


def main() -> None:
    from habitat.db import connect
    from habitat.grid import default_grid
    from habitat.ingest import publish_changed
    from habitat.pipeline import parse_bbox

    parser = argparse.ArgumentParser(description="Derive the monthly water variables per 1 km cell.")
    parser.add_argument("--bbox", type=parse_bbox, required=True, help="west,south,east,north in WGS84")
    parser.add_argument("--start", type=date.fromisoformat, required=True, help="first day, YYYY-MM-DD")
    parser.add_argument("--end", type=date.fromisoformat, required=True, help="last day, YYYY-MM-DD")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    with connect() as connection:
        workspace = Workspace(connection, default_grid())
        outcomes = derive_water(workspace, args.bbox, args.start, args.end)
        published = publish_changed(outcomes, workspace)

    for outcome in outcomes:
        state = "appended" if outcome.append.appended else "unchanged"
        print(f"{outcome.manifest.extensions.source_item_id}: {state}")
    for series in published:
        print(f"published: {series}")


if __name__ == "__main__":
    main()

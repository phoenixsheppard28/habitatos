"""Bounded retrieval of water features, water points and monthly surface water for one area and date range."""

from datetime import date
from typing import Any

from habitat.derive.water import buffered
from habitat.fetch.connectors import ConnectorRequest, validate_area_and_dates
from habitat.fetch.connectors import jrc_gsw_monthly, osm_overpass
from habitat.fetch.connectors.stac import bbox_key
from habitat.fetch.service import run_connector

WATER_SOURCES = ("osm_overpass", "wpdx", "jrc_gsw_monthly")
MAX_BUFFER_KM = 100
OSM_HISTORY_START = osm_overpass.OLDEST_SNAPSHOT.date()
JRC_MONTHLY_END = date(2021, 12, 31)


def buffered_bbox(bbox: list[float], buffer_km: float) -> tuple[float, float, float, float]:
    """The bbox grown by `buffer_km` on each side, so that distances near the edge see the water beyond it."""
    return buffered(tuple(bbox), buffer_km * 1000)


def fetch_water(
    bbox: list[float],
    start: str,
    end: str,
    *,
    sources: list[str] | None = None,
    buffer_km: float = 20,
    max_items: int = 12,
    discover_only: bool = False,
) -> dict[str, Any]:
    sources = list(dict.fromkeys(sources or WATER_SOURCES))
    unknown = set(sources) - set(WATER_SOURCES)
    if unknown:
        raise ValueError(f"sources must be some of {', '.join(WATER_SOURCES)}; got {sorted(unknown)}")
    if not 0 <= buffer_km <= MAX_BUFFER_KM:
        raise ValueError(f"buffer_km must be 0..{MAX_BUFFER_KM}")

    first, last = date.fromisoformat(start), date.fromisoformat(end)
    validate_area_and_dates(ConnectorRequest(bbox=tuple(bbox), start=first, end=last))
    request = ConnectorRequest(bbox=buffered_bbox(bbox, buffer_km), start=first, end=last, max_items=max_items)
    validate_area_and_dates(request)

    warnings = history_warnings(sources, first, last)
    if discover_only:
        return {
            "status": "discovered", "buffered_bbox": list(request.bbox),
            "discovered": planned_items(request, sources), "warnings": warnings,
        }

    artifacts, errors = [], []
    for source_id in sources:
        result = run_connector(source_id, request)
        artifacts.extend(result.manifests)
        warnings.extend(result.warnings)
        errors.extend(f"{source_id}: {error.message}" for error in result.errors)

    status = ("partial" if warnings or errors else "ok") if artifacts else "insufficient_data"
    return {
        "status": status,
        "buffered_bbox": list(request.bbox),
        "raw_artifacts": [
            {"artifact_id": a.artifact_id, "source_id": a.extensions.source_id, "storage": a.storage.uri,
             "coverage": a.coverage.model_dump(mode="json")}
            for a in artifacts
        ],
        "warnings": list(dict.fromkeys(warnings)) + errors,
        "limitations": [
            "OSM dates are mapping dates, not construction dates. WPdx records human water supply, not wildlife "
            "water. JRC monthly water misses pans and troughs smaller than 30 m.",
        ],
    }


def history_warnings(sources: list[str], first: date, last: date) -> list[str]:
    warnings = []
    if "osm_overpass" in sources and first < OSM_HISTORY_START:
        warnings.append(
            "osm_overpass: OSM history starts 2012-09-12; features before that date come from a later snapshot"
        )
    if "jrc_gsw_monthly" in sources and last > JRC_MONTHLY_END:
        warnings.append("jrc_gsw_monthly: JRC monthly history v1.4 ends 2021-12; later months are not fetched")
    return warnings


def planned_items(request: ConnectorRequest, sources: list[str]) -> list[dict[str, str]]:
    """What a fetch would ask for. Discovery makes no network call."""
    planned = []
    if "osm_overpass" in sources:
        snapshot = osm_overpass.snapshot_time(request.end)
        state = f"{snapshot:%Y-%m-%d}" if snapshot else "current"
        planned.append({"source_id": "osm_overpass", "item": f"{bbox_key(request.bbox)}@{state}"})
    if "wpdx" in sources:
        planned.append({"source_id": "wpdx", "item": f"eqje-vguj:{bbox_key(request.bbox)}"})
    if "jrc_gsw_monthly" in sources:
        months = [
            month for month in jrc_gsw_monthly.months_between(request.start, request.end)
            if jrc_gsw_monthly.FIRST_MONTH <= month <= jrc_gsw_monthly.LAST_MONTH
        ]
        planned += [
            {"source_id": "jrc_gsw_monthly", "item": jrc_gsw_monthly.item_id(month, offsets)}
            for month in months
            for offsets in jrc_gsw_monthly.tile_offsets(request.bbox)
        ][: request.max_items]
    return planned

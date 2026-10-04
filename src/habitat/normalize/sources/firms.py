import json

import numpy as np
import pandas as pd

from habitat.archive.store import ArtifactStore
from habitat.contracts import BBox, RawManifest
from habitat.grid import Grid
from habitat.normalize.events import quality_flags, to_point_events, within_bbox
from habitat.normalize.rows import NormalizedBatch, QuarantineError

MAPPING_VERSION = "firms-csv-v1"
REQUIRED_COLUMNS = [
    "latitude", "longitude", "scan", "track", "acq_date", "acq_time", "satellite", "instrument", "confidence",
    "version", "frp", "daynight", "type",
]
BRIGHTNESS_COLUMNS = {"MODIS": ("brightness", "bright_t31"), "VIIRS": ("bright_ti4", "bright_ti5")}
VIIRS_CONFIDENCE = {"l", "n", "h"}
# Not verified against a FIRMS threshold; see docs/ingestion/EVENTS.md section 4.
MODIS_LOW_CONFIDENCE_BELOW = 30
VEGETATION_FIRE_TYPE = 0


def normalize_firms(manifest: RawManifest, store: ArtifactStore, grid: Grid, aoi: BBox | None = None) -> NormalizedBatch:
    """FIRMS standard product CSV to point_events. `acq_date` and `acq_time` are UTC.

    https://www.earthdata.nasa.gov/data/tools/firms/faq
    """
    detections = pd.read_csv(store.open(manifest, "detections"), dtype=str, keep_default_na=False)
    missing = [column for column in REQUIRED_COLUMNS if column not in detections.columns]
    if missing:
        raise QuarantineError(f"FIRMS file has no column(s) {missing}")

    if (detections["latitude"] == "").any() or (detections["longitude"] == "").any():
        raise QuarantineError("a FIRMS detection has no coordinates")

    unknown_instruments = set(detections["instrument"]) - set(BRIGHTNESS_COLUMNS)
    if unknown_instruments:
        raise QuarantineError(f"unknown FIRMS instrument(s) {sorted(unknown_instruments)}")

    missing = [
        column for instrument in set(detections["instrument"]) for column in BRIGHTNESS_COLUMNS[instrument]
        if column not in detections.columns
    ]
    if missing:
        raise QuarantineError(f"FIRMS file has no column(s) {sorted(missing)}")

    detections = detections.assign(
        longitude=detections["longitude"].astype(float), latitude=detections["latitude"].astype(float),
        source_longitude=detections["longitude"], source_latitude=detections["latitude"],
    )
    requested = manifest.extensions.properties.get("requested_bbox")
    detections = within_bbox(detections, tuple(requested) if requested else aoi)

    confidence = parsed_confidence(detections)
    observed_at = pd.to_datetime(
        detections["acq_date"] + detections["acq_time"].str.zfill(4), format="%Y-%m-%d%H%M", utc=True
    )
    fire_type = detections["type"].astype(int)
    rows = pd.DataFrame(
        {
            "source_record_id": detections["satellite"] + ":" + detections["acq_date"] + "T"
            + detections["acq_time"].str.zfill(4) + "Z:" + detections["source_latitude"] + ":"
            + detections["source_longitude"],
            "event_type": "active_fire",
            "occurrence_status": "present",
            "sampling_design": "systematic",
            "time_start": observed_at,
            "time_end": observed_at,
            "time_precision": "instant",
            "available_at": pd.Timestamp(manifest.extensions.available_at),
            "longitude": detections["longitude"],
            "latitude": detections["latitude"],
            "coordinate_uncertainty_m": 500 * np.hypot(detections["scan"].astype(float), detections["track"].astype(float)),
            "value": detections["frp"].astype(float),
            "unit": "MW",
            "basis": "satellite_detection",
            "method": detections["instrument"] + " " + detections["version"],
        },
        index=detections.index,
    )
    rows["attributes"] = [
        json.dumps(attributes(detection, confidence_value, kind))
        for (_, detection), confidence_value, kind in zip(detections.iterrows(), confidence, fire_type)
    ]
    low_confidence = pd.Series(
        [
            value == "l" if instrument == "VIIRS" else value < MODIS_LOW_CONFIDENCE_BELOW
            for value, instrument in zip(confidence, detections["instrument"])
        ],
        index=detections.index,
        dtype=bool,
    )
    rows["quality_flag"] = quality_flags(
        rows, {"low_confidence": low_confidence, "non_vegetation_fire": fire_type != VEGETATION_FIRE_TYPE}
    )

    return to_point_events(rows, manifest, grid, MAPPING_VERSION)


def parsed_confidence(detections: pd.DataFrame) -> list[int | str]:
    """MODIS gives 0–100, VIIRS gives l, n or h. Any other value is outside the documented encoding."""
    values = []
    for value, instrument in zip(detections["confidence"], detections["instrument"]):
        if instrument == "VIIRS" and value in VIIRS_CONFIDENCE:
            values.append(value)
        elif instrument == "MODIS" and value.isdigit() and 0 <= int(value) <= 100:
            values.append(int(value))
        else:
            raise QuarantineError(f"FIRMS {instrument} confidence {value!r} is outside the documented encoding")
    return values


def attributes(detection: pd.Series, confidence: int | str, fire_type: int) -> dict:
    brightness, brightness_31 = BRIGHTNESS_COLUMNS[detection["instrument"]]
    return {
        "satellite": detection["satellite"],
        "confidence": confidence,
        "daynight": detection["daynight"],
        "type": int(fire_type),
        brightness: float(detection[brightness]),
        brightness_31: float(detection[brightness_31]),
    }

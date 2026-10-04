"""Water Quality Portal WQX 3.0 `fullPhysChem` result CSV to site_observations.

https://www.waterqualitydata.us/wqx3/ and the WQX 3.0 domain values.
"""

import hashlib
import logging
import re
from datetime import UTC, date, datetime, timedelta

import pandas as pd

from habitat.archive.store import ArtifactStore
from habitat.contracts import BBox, RawManifest, TimePrecision
from habitat.grid import Grid
from habitat.normalize.rows import NormalizedBatch, QuarantineError, series_id
from habitat.normalize.water_quality import (
    NOT_APPLICABLE,
    VOCABULARY,
    censored_values,
    conversion,
    converted,
    first_flag,
    in_area,
    json_records,
    normalize_unit,
    out_of_range,
    resolve_parameter,
    site_observations_batch,
)

logger = logging.getLogger(__name__)

MAPPING_VERSION = "wqp-wqx3-v1"

CHARACTERISTICS: dict[str, tuple[str, ...]] = {
    "Dissolved oxygen (DO)": ("dissolved_oxygen", "dissolved_oxygen_saturation"),
    "Dissolved oxygen saturation": ("dissolved_oxygen_saturation",),
    "Biochemical oxygen demand, standard conditions": ("bod5",),
    "Chemical oxygen demand": ("cod",),
    "Nitrate": ("nitrate_n",),
    "Ammonia": ("ammonium_n",),
    "Ammonia and ammonium": ("ammonium_n",),
    "Total Nitrogen, mixed forms": ("total_nitrogen",),
    "Phosphorus": ("total_phosphorus",),
    "Orthophosphate": ("orthophosphate_p",),
    "Escherichia coli": ("ecoli", "ecoli_mpn"),
    "Fecal Coliform": ("faecal_coliforms",),
    "Specific conductance": ("conductivity",),
    "Turbidity": ("turbidity", "turbidity_fnu"),
    "Total suspended solids": ("total_suspended_solids",),
    "Total dissolved solids": ("total_dissolved_solids",),
    "pH": ("ph",),
    "Temperature, water": ("water_temperature",),
    "Chlorophyll a": ("chlorophyll_a",),
    "Fluoride": ("fluoride",),
    "Lead": ("lead",),
    "Cadmium": ("cadmium",),
    "Chromium": ("chromium",),
    "Arsenic": ("arsenic",),
    "Mercury": ("mercury",),
}

FRACTIONS = {
    "total": "total",
    "unfiltered": "total",
    "unfiltered, field": "total",
    "dissolved": "dissolved",
    "filtered, lab": "dissolved",
    "filtered, field": "dissolved",
    "filtered field and/or lab": "dissolved",
    "suspended": "suspended",
}

LOCATION_TYPES = {
    "stream": "river",
    "river/stream": "river",
    "river/stream intermittent": "river",
    "river/stream perennial": "river",
    "river/stream ephemeral": "river",
    "tidal stream": "river",
    "lake": "lake",
    "lake, reservoir, impoundment": "reservoir",
    "reservoir": "reservoir",
    "wetland": "wetland",
    "canal": "canal",
    "canal drainage": "canal",
    "canal irrigation": "canal",
    "spring": "spring",
    "well": "groundwater",
}

# WQX 3.0 ResultDetectionCondition domain values.
LEFT_CENSORED = {
    "not detected", "below detection limit", "below reporting limit", "below method detection limit",
    "present below quantification limit", "detected not quantified",
}
RIGHT_CENSORED = {"above operating range", "present above quantification limit", "above reporting limit"}
CONTAMINATION_CONDITIONS = {"systematic contamination"}
FINAL_STATUSES = {"final", "accepted"}
SUSPECT_STATUSES = {"rejected"}

# Standard offsets of the WQX time zone codes, in hours from UTC.
TIME_ZONES = {
    "UTC": 0, "GMT": 0, "AST": -4, "ADT": -3, "EST": -5, "EDT": -4, "CST": -6, "CDT": -5, "MST": -7, "MDT": -6,
    "PST": -8, "PDT": -7, "AKST": -9, "AKDT": -8, "HST": -10, "SST": -11, "ChST": 10,
}

# NAD83 and WGS84 differ by less than 2 m in the conterminous USA (NOAA NGS). That is far below the 1 km grid,
# so the coordinates are used as they are, with this uncertainty.
DATUM_UNCERTAINTY_M = {"WGS84": 0.0, "NAD83": 2.0}
FEET_TO_M = 0.3048
SQUARE_MILES_TO_KM2 = 2.589988110336

MAPPED_COLUMNS = {
    "Location_Identifier", "Location_LatitudeStandardized", "Location_LongitudeStandardized",
    "Location_HorzCoordStandardizedDatum", "Activity_StartDate", "Activity_StartTime", "Activity_StartTimeZone",
    "Result_Characteristic", "Result_Measure", "Result_MeasureUnit", "Result_MethodSpeciation",
    "Result_ResultDetectionCondition", "DetectionLimit_MeasureA", "DetectionLimit_TypeA",
    "DetectionLimit_MeasureUnitA", "Activity_DepthHeightMeasure", "Activity_DepthHeightMeasureUnit",
    "ResultAnalyticalMethod_Identifier", "Result_MeasureIdentifier", "Result_MeasureStatusIdentifier",
    "LastChangeDate",
}
LONG_TEXT_COLUMNS = {"ResultAnalyticalMethod_Description", "Activity_Comment", "Result_Comment"}
CENSORED_TEXT = re.compile(r"^\s*([<>])\s*([0-9.]+)\s*$")


def normalize_wqp(manifest: RawManifest, store: ArtifactStore, grid: Grid, aoi: BBox | None = None) -> NormalizedBatch:
    results = pd.read_csv(store.open(manifest, "results"), dtype=str, keep_default_na=False, na_values=[""])
    stations = (
        pd.read_csv(store.open(manifest, "stations"), dtype=str, keep_default_na=False, na_values=[""])
        if "stations" in manifest.extensions.assets
        else None
    )
    missing = sorted(MAPPED_COLUMNS - {"LastChangeDate"} - set(results.columns))
    if missing:
        raise QuarantineError(f"WQP result file has no column(s) {missing}; expected the WQX 3.0 fullPhysChem profile")

    mapped = results["Result_Characteristic"].isin(CHARACTERISTICS)
    if not mapped.any():
        raise QuarantineError("WQP result file has no mapped parameter")
    if (~mapped).any():
        logger.info("wqp: dropped %d rows of unmapped characteristics", int((~mapped).sum()))
    results = results[mapped]

    sites = monitoring_sites(results, stations)
    results = results[results["Location_Identifier"].isin(sites.loc[in_area(sites, aoi), "local_site_id"])]
    if results.empty:
        raise QuarantineError("WQP result file has no sample of a mapped parameter inside the area")

    rows = observation_rows(results, manifest, grid)
    return site_observations_batch(rows, sites, grid, MAPPING_VERSION)


def monitoring_sites(results: pd.DataFrame, stations: pd.DataFrame | None) -> pd.DataFrame:
    location_columns = [column for column in results.columns if column.startswith("Location_")]
    sites = results[location_columns].drop_duplicates("Location_Identifier").set_index("Location_Identifier")
    if stations is not None:
        extra = stations.drop_duplicates("Location_Identifier").set_index("Location_Identifier")
        sites = sites.combine_first(extra.reindex(sites.index))

    datums = sites["Location_HorzCoordStandardizedDatum"].fillna("")
    unknown = sorted(set(datums) - set(DATUM_UNCERTAINTY_M))
    if unknown:
        raise QuarantineError(f"WQP coordinate datum(s) {unknown} have no documented transformation to WGS84")

    location_types = sites["Location_Type"].fillna("")
    return pd.DataFrame(
        {
            "site_id": "wqp:" + sites.index,
            "source_id": "wqp",
            "local_site_id": sites.index,
            "site_name": sites["Location_Name"],
            "water_body_type": location_types.str.lower().map(LOCATION_TYPES).fillna("other"),
            "longitude": pd.to_numeric(sites["Location_LongitudeStandardized"]),
            "latitude": pd.to_numeric(sites["Location_LatitudeStandardized"]),
            "coordinate_uncertainty_m": coordinate_uncertainty(sites, datums),
            "upstream_area_km2": optional_measure(
                sites, "Location_DrainageAreaMeasure", {"sq mi": SQUARE_MILES_TO_KM2, "km2": 1.0}
            ),
            "elevation_m": optional_measure(sites, "Location_VerticalMeasure", {"ft": FEET_TO_M, "m": 1.0}),
            "attributes": json_records(
                pd.DataFrame(
                    {
                        "location_type": location_types,
                        "state": sites.get("Location_StatePostalCode"),
                        "county": sites.get("Location_CountyName"),
                        "huc8": sites.get("Location_HUCEightDigitCode"),
                        "huc12": sites.get("Location_HUCTwelveDigitCode"),
                        "datum": datums,
                    }
                )
            ),
        }
    ).reset_index(drop=True)


def optional_measure(frame: pd.DataFrame, column: str, factors: dict[str, float]) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(None, index=frame.index, dtype="float64")

    values = pd.to_numeric(frame[column], errors="coerce")
    units = frame.get(column.replace("Measure", "MeasureUnit"), pd.Series(index=frame.index, dtype=object))
    return values * units.fillna("").str.lower().map(factors)


def coordinate_uncertainty(sites: pd.DataFrame, datums: pd.Series) -> pd.Series:
    accuracy = optional_measure(sites, "Location_HorzAccuracyMeasure", {"ft": FEET_TO_M, "m": 1.0})
    datum_error = datums.map(DATUM_UNCERTAINTY_M)
    return accuracy.where(accuracy.notna(), datum_error.where(datum_error > 0))


def observation_rows(results: pd.DataFrame, manifest: RawManifest, grid: Grid) -> pd.DataFrame:
    item = manifest.extensions
    speciation = results["Result_MethodSpeciation"].fillna("")
    reported = results["Result_Measure"].fillna("")
    text_censoring = reported.str.extract(CENSORED_TEXT)

    limit_unit = with_speciation(results["DetectionLimit_MeasureUnitA"], speciation)
    unit = with_speciation(results["Result_MeasureUnit"], speciation).where(
        results["Result_MeasureUnit"].notna() | (reported != ""), limit_unit
    )
    parameters = pd.Series(
        [resolve_parameter(CHARACTERISTICS[name], text) for name, text in zip(results["Result_Characteristic"], unit)],
        index=results.index,
    )

    condition = results["Result_ResultDetectionCondition"].fillna("").str.lower()
    censored = pd.Series("none", index=results.index)
    censored[condition.isin(LEFT_CENSORED) | (text_censoring[0] == "<")] = "left"
    censored[condition.isin(RIGHT_CENSORED) | (text_censoring[0] == ">")] = "right"

    rows = pd.DataFrame(
        {
            "parameter": parameters,
            "value": pd.to_numeric(reported.where(text_censoring[1].isna(), text_censoring[1]), errors="coerce"),
            "unit_source": unit,
            "censored": censored,
        },
        index=results.index,
    )
    rows["value"] = converted(rows, "value", "unit_source")
    rows["detection_limit"] = detection_limits(results, rows, limit_unit)
    rows["value"], rows["detection_limit"], no_limit = censored_values(rows)

    not_numeric = rows["value"].isna() & (censored == "none")
    if not_numeric.any():
        logger.info("wqp: dropped %d rows without a numeric value", int(not_numeric.sum()))

    status = results["Result_MeasureStatusIdentifier"].fillna("").str.lower()
    final = status.isin(FINAL_STATUSES)
    time_start, time_end, precision = sample_times(results)
    available_at = change_dates(results).fillna(pd.Timestamp(item.available_at))
    measure_type = results.get("Result_MeasureType", pd.Series("", index=results.index)).fillna("").str.lower()

    rows = rows.assign(
        source_record_id=record_ids(results),
        dataset_id=series_id(manifest, grid),
        site_id="wqp:" + results["Location_Identifier"],
        time_start=time_start,
        time_end=time_end,
        time_precision=precision,
        available_at=available_at,
        source_id=item.source_id,
        source_item_id=item.source_item_id,
        processing_version=item.processing_version,
        product_status=final.map({True: "final", False: "preliminary"}),
        mapping_version=MAPPING_VERSION,
        fraction=fractions(results, parameters),
        detection_limit_type=results["DetectionLimit_TypeA"].map(detection_limit_type),
        sample_depth_m=optional_measure(results, "Activity_DepthHeightMeasure", {"m": 1.0, "ft": FEET_TO_M}),
        method=results["ResultAnalyticalMethod_Identifier"],
        attributes=attributes(results),
    )
    rows["quality_flag"] = first_flag(
        rows.index,
        [
            ("contamination", condition.isin(CONTAMINATION_CONDITIONS)),
            ("suspect", status.isin(SUSPECT_STATUSES)),
            ("out_of_range", out_of_range(rows)),
            ("censored_no_limit", no_limit),
            ("estimated", measure_type == "estimated"),
            ("preliminary", ~final),
        ],
    )
    return rows[~not_numeric]


def with_speciation(units: pd.Series, speciation: pd.Series) -> pd.Series:
    units = units.fillna("")
    return (units + " " + speciation).str.strip().where(speciation != "", units)


def detection_limits(results: pd.DataFrame, rows: pd.DataFrame, limit_unit: pd.Series) -> pd.Series:
    """The A limit, converted with the factor of its own unit. A limit in a unit without a factor is kept only in
    `attributes`, unless the row is censored: then the limit is the value, and the unit must be known."""
    raw = pd.to_numeric(results["DetectionLimit_MeasureA"], errors="coerce")
    limits = pd.Series(float("nan"), index=results.index)
    for index in raw.dropna().index:
        parameter = rows.at[index, "parameter"]
        if normalize_unit(limit_unit[index]) in VOCABULARY[parameter].factors or rows.at[index, "censored"] != "none":
            limits[index] = conversion(parameter, limit_unit[index]).apply(float(raw[index]))
    return limits


def detection_limit_type(text) -> str | None:
    text = str(text or "").lower()
    if "detection" in text:
        return "LOD"
    if "quantitation" in text or "quantification" in text:
        return "LOQ"
    if "reporting" in text:
        return "reporting"
    return None


def fractions(results: pd.DataFrame, parameters: pd.Series) -> pd.Series:
    source = results["Result_SampleFraction"].fillna("").str.lower().map(FRACTIONS).fillna(NOT_APPLICABLE)
    has_fraction = parameters.map(lambda name: VOCABULARY[name].has_fraction)
    return source.where(has_fraction, NOT_APPLICABLE)


def sample_times(results: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    """An instant in UTC when the time and a known zone exist, else the UTC day of the local date."""
    day = pd.to_datetime(results["Activity_StartDate"], format="%Y-%m-%d").dt.tz_localize("UTC")
    offset_hours = results["Activity_StartTimeZone"].map(TIME_ZONES)
    local = pd.to_datetime(
        results["Activity_StartDate"] + " " + results["Activity_StartTime"].fillna(""), format="%Y-%m-%d %H:%M:%S",
        errors="coerce",
    )
    instant = (local - pd.to_timedelta(offset_hours, unit="h")).dt.tz_localize("UTC")
    has_instant = instant.notna()

    time_start = instant.where(has_instant, day)
    time_end = instant.where(has_instant, day + timedelta(days=1))
    precision = has_instant.map({True: TimePrecision.INSTANT.value, False: TimePrecision.DAY.value})
    return time_start, time_end, precision


def parse_change_date(text) -> datetime | None:
    """`LastChangeDate` is either a date or a text such as `Fri Jan 31 08:59:46 UTC 2025`."""
    if not isinstance(text, str) or not text.strip():
        return None

    try:
        return datetime.strptime(text.strip(), "%a %b %d %H:%M:%S UTC %Y").replace(tzinfo=UTC)
    except ValueError:
        return datetime.combine(date.fromisoformat(text.strip()[:10]), datetime.min.time(), tzinfo=UTC)


def change_dates(results: pd.DataFrame) -> pd.Series:
    if "LastChangeDate" not in results.columns:
        return pd.Series(pd.NaT, index=results.index, dtype="datetime64[us, UTC]")
    return pd.to_datetime(results["LastChangeDate"].map(parse_change_date), utc=True)


def record_ids(results: pd.DataFrame) -> pd.Series:
    fallback = results[["Activity_ActivityIdentifier", "Result_Characteristic", "Result_SampleFraction"]].fillna("")
    hashed = fallback.agg("|".join, axis=1).map(lambda text: "sha256:" + hashlib.sha256(text.encode()).hexdigest()[:24])
    return results["Result_MeasureIdentifier"].fillna(hashed)


def attributes(results: pd.DataFrame) -> list[str]:
    rest = results[
        [
            column for column in results.columns
            if column not in MAPPED_COLUMNS and column not in LONG_TEXT_COLUMNS and not column.startswith("Location_")
        ]
    ].copy()
    rest["local_start_time"] = results["Activity_StartTime"]
    rest["local_time_zone"] = results["Activity_StartTimeZone"]
    rest["source_unit"] = results["Result_MeasureUnit"]
    rest["source_value"] = results["Result_Measure"]
    return json_records(rest)

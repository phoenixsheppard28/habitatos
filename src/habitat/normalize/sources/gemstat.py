"""UNEP GEMS/Water Global Freshwater Quality Archive (Zenodo) to site_observations.

The format is in `README_output_format.txt` of the archive. All CSV files are Latin-1 text.
"""

import hashlib
import io
import logging
import zipfile
from collections.abc import Iterable
from datetime import timedelta

import pandas as pd

from habitat.archive.store import ArtifactStore
from habitat.contracts import BBox, RawManifest, TimePrecision
from habitat.grid import Grid
from habitat.normalize.rows import NormalizedBatch, QuarantineError, series_id
from habitat.normalize.water_quality import (
    NOT_APPLICABLE,
    VOCABULARY,
    censored_values,
    converted,
    first_flag,
    in_area,
    json_records,
    out_of_range,
    site_observations_batch,
)

logger = logging.getLogger(__name__)

MAPPING_VERSION = "gemstat-gfqa-v1"
ENCODING = "latin-1"
STATION_FILE = "GEMStat_station_metadata.csv"

# Data files that hold the mapped codes. The other files hold pesticides and other substances outside the vocabulary.
DATA_FILES = (
    "Arsenic.csv", "Cadmium.csv", "Chromium.csv", "Dissolved_Gas.csv", "Electrical_Conductance.csv", "Fluoride.csv",
    "Lead.csv", "Mercury.csv", "Optical.csv", "Other_Nitrogen.csv", "Oxidized_Nitrogen.csv", "Oxygen_Demand.csv",
    "Phosphorus.csv", "Pigment.csv", "Temperature.csv", "Water.csv", "pH.csv",
)


def metal_codes(symbol: str, parameter: str) -> dict[str, tuple[str, str, str]]:
    return {
        f"{symbol}-Dis": (parameter, "dissolved", ""),
        f"{symbol}-Tot": (parameter, "total", ""),
        f"{symbol}-Sus": (parameter, "suspended", ""),
    }


# Code: (parameter, fraction, speciation). The speciation completes the unit, because a code such as NO3N is
# reported as nitrogen in plain mg/l. BOD is not mapped: GEMStat does not give its incubation time, so it is not
# known to be BOD5. Bacteria are not mapped: the unit 1/100 ml does not tell cfu from MPN.
CODES: dict[str, tuple[str, str, str]] = {
    "O2-Dis": ("dissolved_oxygen", NOT_APPLICABLE, ""),
    "O2-Dis-Sat": ("dissolved_oxygen_saturation", NOT_APPLICABLE, ""),
    "COD": ("cod", NOT_APPLICABLE, ""),
    "NO3N": ("nitrate_n", NOT_APPLICABLE, "as N"),
    "NH4N": ("ammonium_n", NOT_APPLICABLE, "as N"),
    "TN": ("total_nitrogen", "total", "as N"),
    "TP": ("total_phosphorus", "total", "as P"),
    "TDP": ("total_phosphorus", "dissolved", "as P"),
    "DRP": ("orthophosphate_p", "dissolved", "as P"),
    "EC": ("conductivity", NOT_APPLICABLE, ""),
    "TURB": ("turbidity", NOT_APPLICABLE, ""),
    "TSS": ("total_suspended_solids", NOT_APPLICABLE, ""),
    "TDS": ("total_dissolved_solids", NOT_APPLICABLE, ""),
    "pH": ("ph", NOT_APPLICABLE, ""),
    "TEMP": ("water_temperature", NOT_APPLICABLE, ""),
    "Chl-a": ("chlorophyll_a", NOT_APPLICABLE, ""),
    "F-Dis": ("fluoride", "dissolved", ""),
    "F-Tot": ("fluoride", "total", ""),
    **metal_codes("Pb", "lead"),
    **metal_codes("Cd", "cadmium"),
    **metal_codes("Cr", "chromium"),
    **metal_codes("As", "arsenic"),
    **metal_codes("Hg", "mercury"),
}

WATER_TYPES = {
    "River station": "river",
    "Lake station": "lake",
    "Reservoir station": "reservoir",
    "Groundwater station": "groundwater",
    "Wetland station": "wetland",
}

DATA_QUALITY = {
    "Good": "ok",
    "Fair": "ok",
    "Unknown": "ok",
    "Estimated": "estimated",
    "Pending review": "suspect",
    "Suspect": "suspect",
    "Contamination": "contamination",
}

# README_output_format.txt: 00:00 or 12:00 is the default when the provider reported no time.
DEFAULT_TIMES = {"00:00", "12:00"}
MAPPED_COLUMNS = {
    "GEMS Station Number", "Sample Date", "Sample Time", "Depth", "Parameter Code", "Analysis Method Code",
    "Value Flags", "Value", "Unit", "Data Quality",
}


def normalize_gemstat(
    manifest: RawManifest, store: ArtifactStore, grid: Grid, aoi: BBox | None = None
) -> NormalizedBatch:
    with zipfile.ZipFile(store.open(manifest, "archive")) as archive:
        sites = monitoring_sites(read_csv(archive, STATION_FILE))
        wanted = set(sites.loc[in_area(sites, aoi), "local_site_id"])
        samples = pd.concat(
            [frame[frame["GEMS Station Number"].isin(wanted)] for frame in data_frames(archive)], ignore_index=True
        )

    if samples.empty:
        raise QuarantineError("GEMStat archive has no samples of mapped parameters for these stations")

    mapped = samples["Parameter Code"].isin(CODES)
    if not mapped.any():
        raise QuarantineError("GEMStat archive has no mapped parameter")
    if (~mapped).any():
        logger.info("gemstat: dropped %d rows of unmapped parameter codes", int((~mapped).sum()))

    rows = observation_rows(samples[mapped], manifest, grid)
    return site_observations_batch(rows, sites, grid, MAPPING_VERSION)


def read_csv(archive: zipfile.ZipFile, name: str) -> pd.DataFrame:
    with archive.open(name) as raw:
        return pd.read_csv(io.TextIOWrapper(raw, encoding=ENCODING), dtype=str, keep_default_na=False, na_values=[""])


def data_frames(archive: zipfile.ZipFile) -> Iterable[pd.DataFrame]:
    names = set(archive.namelist())
    for name in DATA_FILES:
        if name in names:
            yield read_csv(archive, name)


def monitoring_sites(stations: pd.DataFrame) -> pd.DataFrame:
    water_types = stations["Water Type"].map(WATER_TYPES).fillna("other")
    return pd.DataFrame(
        {
            "site_id": "gemstat:" + stations["GEMS Station Number"],
            "source_id": "gemstat",
            "local_site_id": stations["GEMS Station Number"],
            "site_name": stations["Station Identifier"],
            "water_body_type": water_types,
            "water_body_name": stations["Water Body Name"],
            "longitude": pd.to_numeric(stations["Longitude"], errors="coerce"),
            "latitude": pd.to_numeric(stations["Latitude"], errors="coerce"),
            "upstream_area_km2": pd.to_numeric(stations["Upstream Basin Area"], errors="coerce"),
            "elevation_m": pd.to_numeric(stations["Elevation"], errors="coerce"),
            "attributes": json_records(
                pd.DataFrame(
                    {
                        "country": stations["Country Name"],
                        "local_station_number": stations["Local Station Number"],
                        "water_type": stations["Water Type"],
                        "main_basin": stations["Main Basin"],
                        "monitoring_type": stations["Monitoring Type"],
                        "responsible_agency": stations["Responsible Collection Agency"],
                    }
                )
            ),
        }
    )


def observation_rows(samples: pd.DataFrame, manifest: RawManifest, grid: Grid) -> pd.DataFrame:
    item = manifest.extensions
    codes = samples["Parameter Code"].map(CODES)
    parameters = codes.str[0]
    fraction = codes.str[1].where(parameters.map(lambda name: VOCABULARY[name].has_fraction), NOT_APPLICABLE)
    unit = (samples["Unit"].fillna("") + " " + codes.str[2]).str.strip()

    flags = samples["Value Flags"].fillna("")
    censored = flags.map({"<": "left", ">": "right"}).fillna("none")
    rows = pd.DataFrame(
        {
            "parameter": parameters,
            "fraction": fraction,
            "value": pd.to_numeric(samples["Value"], errors="coerce"),
            "unit_source": unit,
            "censored": censored,
            "detection_limit": None,
        },
        index=samples.index,
    )
    rows["value"] = converted(rows, "value", "unit_source")
    rows["value"], rows["detection_limit"], no_limit = censored_values(rows)

    day = pd.to_datetime(samples["Sample Date"], format="%Y-%m-%d").dt.tz_localize("UTC")
    local_time = samples["Sample Time"].fillna("")
    quality = samples["Data Quality"].map(DATA_QUALITY).fillna("ok")
    rows = rows.assign(
        source_record_id=record_ids(samples),
        dataset_id=series_id(manifest, grid),
        site_id="gemstat:" + samples["GEMS Station Number"],
        time_start=day,
        time_end=day + timedelta(days=1),
        time_precision=TimePrecision.DAY.value,
        available_at=pd.Timestamp(item.available_at),
        source_id=item.source_id,
        source_item_id=item.source_item_id,
        processing_version=item.processing_version,
        product_status=item.product_status.value,
        mapping_version=MAPPING_VERSION,
        sample_depth_m=pd.to_numeric(samples["Depth"], errors="coerce"),
        method=samples["Analysis Method Code"],
        attributes=attributes(samples, local_time),
    )
    rows["quality_flag"] = first_flag(
        rows.index,
        [
            ("contamination", quality == "contamination"),
            ("suspect", quality == "suspect"),
            ("out_of_range", out_of_range(rows)),
            ("censored_no_limit", no_limit),
            ("estimated", (quality == "estimated") | (flags == "~")),
            ("time_default", local_time.isin(DEFAULT_TIMES)),
        ],
    )
    return rows


def record_ids(samples: pd.DataFrame) -> pd.Series:
    key = samples[
        ["GEMS Station Number", "Sample Date", "Sample Time", "Depth", "Parameter Code", "Analysis Method Code"]
    ].fillna("")
    return key.agg("|".join, axis=1).map(lambda text: "gemstat:" + hashlib.sha256(text.encode()).hexdigest()[:24])


def attributes(samples: pd.DataFrame, local_time: pd.Series) -> list[str]:
    rest = samples[[column for column in samples.columns if column not in MAPPED_COLUMNS]].copy()
    rest["local_sample_time"] = local_time
    rest["parameter_code"] = samples["Parameter Code"]
    rest["source_unit"] = samples["Unit"]
    rest["value_flag"] = samples["Value Flags"]
    rest["data_quality"] = samples["Data Quality"]
    return json_records(rest)

"""Ogutu et al. 2016, PLOS ONE, S4 Data: DRSRS aerial survey estimates per Kenya rangeland county."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
from pyproj import Geod
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from habitat.archive.store import ArtifactStore
from habitat.contracts import BBox, RawManifest
from habitat.grid import Grid
from habitat.normalize.population import population_batch
from habitat.normalize.rows import NormalizedBatch, QuarantineError

SOURCE_ID = "ogutu_kenya_rangelands"
MAPPING_VERSION = "ogutu-s4-v1"
HEADER_ROW_INDEX = 2
MISSING_TEXT = "."
EXCEL_EPOCH = datetime(1899, 12, 30, tzinfo=UTC)
PREDICTION_LEVEL = 0.95

COUNTY = "County"
COUNTY_NUMBER = "County number"
COUNTY_AREA = "Area of County (Km2)"
SURVEY_CODE = "Survey Code"
END_DATE = "End Date of survey"
SPECIES = "Species"
SPECIES_NUMBER = "Species number"
COUNTED = "Actual number counted"
SURVEY_ESTIMATE = "Population size estimated from survey"
SURVEY_SE = "Standard Error estimated from survey"
WITHOUT_OUTLIERS = "Population estimate with 22 outliers removed"
OVERALL_MEAN = "Overall Mean population size"
LOG_MEAN = "Logarithm of mean population size"
MODEL_ESTIMATE = "Population size estimate from model"
MODEL_LOWER = "Lower 95% Prediction Limit for population size estimate"
MODEL_UPPER = "Upper 95% Prediction Limit for population size estimate"
COLUMNS = [
    COUNTY, COUNTY_NUMBER, COUNTY_AREA, SURVEY_CODE, END_DATE, SPECIES, SPECIES_NUMBER, COUNTED, SURVEY_ESTIMATE,
    SURVEY_SE, WITHOUT_OUTLIERS, OVERALL_MEAN, LOG_MEAN, MODEL_ESTIMATE, MODEL_LOWER, MODEL_UPPER,
]
NUMBER_COLUMNS = [
    COUNTY_NUMBER, COUNTY_AREA, SPECIES_NUMBER, COUNTED, SURVEY_ESTIMATE, SURVEY_SE, WITHOUT_OUTLIERS, OVERALL_MEAN,
    LOG_MEAN, MODEL_ESTIMATE, MODEL_LOWER, MODEL_UPPER,
]

# Scientific name and GBIF backbone key of each species name in the file. A group of species has no key.
# Cattle in Kenya are mostly zebu; the GBIF backbone keeps zebu inside Bos taurus.
# "Burchell's zebra" is the plains zebra, and "Oryx" is the beisa oryx, the oryx of Kenya.
SPECIES_TAXA: dict[str, tuple[str, int | None]] = {
    "Sheep and goats": ("Sheep and goats", None),
    "Cattle": ("Bos taurus", 2441022),
    "Camel": ("Camelus dromedarius", 9055455),
    "Donkey": ("Equus asinus", 2440891),
    "Ostrich": ("Struthio camelus", 2495150),
    "Grant's gazelle": ("Nanger granti", 7261447),
    "Warthog": ("Phacochoerus africanus", 2441212),
    "Giraffe": ("Giraffa camelopardalis", 2441205),
    "Impala": ("Aepyceros melampus", 2441144),
    "Gerenuk": ("Litocranius walleri", 2441093),
    "Burchell's zebra": ("Equus quagga", 2440892),
    "Oryx": ("Oryx beisa", 5706393),
    "Lesser kudu": ("Tragelaphus imberbis", 5220177),
    "Eland": ("Taurotragus oryx", 5220167),
    "Elephant": ("Loxodonta africana", 2435350),
    "Buffalo": ("Syncerus caffer", 2441034),
    "Waterbuck": ("Kobus ellipsiprymnus", 5220160),
    "Hartebeest": ("Alcelaphus buselaphus", 5220185),
    "Thomson's gazelle": ("Eudorcas thomsonii", 7261427),
    "Grevy's zebra": ("Equus grevyi", 2440894),
    "Topi": ("Damaliscus lunatus", 2441041),
    "Wildebeest": ("Connochaetes taurinus", 2441105),
}

# The S4 county "Machakos" is the former Machakos district: its area of 14,225 km2 equals the sum of the
# geoBoundaries counties Machakos (6,220 km2) and Makueni (7,987 km2).
COUNTY_BOUNDARY_SHAPES = {
    "Machakos": ["Machakos", "Makueni"],
    "Elgeyo Marakwet": ["Elgeyo-Marakwet"],
}


def normalize_ogutu_kenya_rangelands(
    manifest: RawManifest, store: ArtifactStore, grid: Grid, aoi: BBox | None = None
) -> NormalizedBatch:
    survey = read_survey_table(store.open(manifest, "data"))
    boundaries = read_boundaries(store.open(manifest, "boundaries"))

    properties = manifest.extensions.properties
    areas = county_areas(survey, boundaries, properties.get("boundary_source"))
    records = survey_records(survey)
    mapping_version = f"{MAPPING_VERSION}+{properties.get('boundary_version', 'no-boundaries')}"
    return population_batch(records, areas, manifest, grid, mapping_version)


def read_survey_table(path: Path) -> pd.DataFrame:
    frame = pd.read_excel(path, header=HEADER_ROW_INDEX, dtype=object, engine="openpyxl")
    frame.columns = [str(column).strip() for column in frame.columns]
    missing = [column for column in COLUMNS if column not in frame.columns]
    if missing:
        raise QuarantineError(f"S4 file lacks the columns {missing}")

    # The row under the header repeats short column names and has no county.
    frame = frame.loc[frame[COUNTY].notna() & frame[SPECIES].notna(), COLUMNS].reset_index(drop=True)
    if frame.empty:
        raise QuarantineError("S4 file has no data rows")

    for column in NUMBER_COLUMNS:
        frame[column] = parse_numbers(frame[column], column)
    frame[END_DATE] = frame[END_DATE].map(excel_date)
    frame[SURVEY_CODE] = pd.Series([survey_code(value) for value in frame[SURVEY_CODE]], dtype=object)
    frame[COUNTY] = frame[COUNTY].str.strip()
    frame[SPECIES] = frame[SPECIES].str.strip()
    unknown = sorted(set(frame[SPECIES]) - SPECIES_TAXA.keys())
    if unknown:
        raise QuarantineError(f"species names without a taxon mapping: {unknown}")

    return frame


def survey_period(path: Path) -> tuple[datetime, datetime]:
    """First and last survey day. It reads only the date column, so a file with other faults still gets archived."""
    frame = pd.read_excel(path, header=HEADER_ROW_INDEX, dtype=object, engine="openpyxl")
    frame.columns = [str(column).strip() for column in frame.columns]
    if END_DATE not in frame.columns:
        raise QuarantineError(f"S4 file lacks the column {END_DATE!r}")

    dates = [excel_date(value) for value in frame[END_DATE] if is_date(value)]
    if not dates:
        raise QuarantineError("S4 file has no survey dates")

    return min(dates), max(dates)


def is_date(value) -> bool:
    return isinstance(value, datetime) or (isinstance(value, int | float) and not pd.isna(value))


def parse_numbers(values: pd.Series, column: str) -> pd.Series:
    text = values.map(lambda value: value.strip() if isinstance(value, str) else value)
    missing = text.isna() | text.isin([MISSING_TEXT, ""])
    numbers = pd.to_numeric(text.where(~missing), errors="coerce")
    bad = numbers.isna() & ~missing
    if bad.any():
        raise QuarantineError(f"{column}: values are not a number: {sorted(set(text[bad].astype(str)))[:5]}")

    return numbers.astype("float64")


def excel_date(value) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC)
    if isinstance(value, int | float) and not pd.isna(value):
        return EXCEL_EPOCH + timedelta(days=int(value))
    raise QuarantineError(f"survey date {value!r} does not parse")


def survey_code(value) -> str | None:
    """Codes are YYNN. The file keeps numeric codes as numbers, so 0703 (2007) is the number 703."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, int | float):
        return f"{int(value):04d}"
    return str(value).strip()


def read_boundaries(path: Path) -> dict[str, BaseGeometry]:
    collection = json.loads(path.read_text())
    return {feature["properties"]["shapeName"]: shape(feature["geometry"]) for feature in collection["features"]}


def county_areas(
    survey: pd.DataFrame, boundaries: dict[str, BaseGeometry], geometry_source: str | None
) -> pd.DataFrame:
    geod = Geod(ellps="WGS84")
    areas = []
    for number, county in survey.groupby(COUNTY_NUMBER):
        name = county[COUNTY].mode().iloc[0]
        source_areas = county[COUNTY_AREA].dropna()
        shape_names = COUNTY_BOUNDARY_SHAPES.get(name, [name])
        located = all(shape_name in boundaries for shape_name in shape_names)
        geometry = unary_union([boundaries[shape_name] for shape_name in shape_names]) if located else None
        attributes = {
            "county_number": int(number),
            "boundary_shapes": shape_names,
            "source_area_km2_values": sorted(source_areas.unique().tolist()),
        }
        if geometry is not None:
            attributes["geometry_area_km2"] = round(abs(geod.geometry_area_perimeter(geometry)[0]) / 1e6, 1)

        areas.append({
            "area_id": county_area_id(number),
            "source_id": SOURCE_ID,
            "area_name": name,
            "area_type": "admin_unit",
            "area_km2": float(source_areas.mode().iloc[0]) if not source_areas.empty else None,
            "geometry_wkt": geometry.wkt if geometry is not None else None,
            "geometry_source": geometry_source if geometry is not None else None,
            "valid_from": None,
            "valid_to": None,
            "attributes": attributes,
        })
    return pd.DataFrame(areas)


def county_area_id(county_number: float) -> str:
    return f"ke_county:{int(county_number)}"


def survey_records(survey: pd.DataFrame) -> pd.DataFrame:
    """One survey row gives a survey estimate, a model estimate, or both."""
    records = []
    for row in survey.to_dict("records"):
        taxon_name, taxon_key = SPECIES_TAXA[row[SPECIES]]
        survey_or_day = row[SURVEY_CODE] or f"d{row[END_DATE]:%Y%m%d}"
        common = {
            "area_id": county_area_id(row[COUNTY_NUMBER]),
            "taxon_name": taxon_name,
            "gbif_taxon_key": taxon_key,
            "time_start": row[END_DATE],
            "time_end": row[END_DATE],
            "metric": "population_estimate",
            "unit": "individuals",
            "effort_value": None,
            "effort_unit": None,
            "read_from_figure": False,
        }
        series_attributes = {
            "survey_code": row[SURVEY_CODE],
            "source_species": row[SPECIES],
            "overall_mean": row[OVERALL_MEAN],
            "log_mean": row[LOG_MEAN],
        }
        record_id = f"{int(row[COUNTY_NUMBER])}:{survey_or_day}:{int(row[SPECIES_NUMBER])}"
        has_survey = not pd.isna(row[SURVEY_ESTIMATE])

        if has_survey:
            records.append(common | {
                "source_record_id": f"{record_id}:survey",
                "method": "aerial_sample",
                "value": row[SURVEY_ESTIMATE],
                "se": row[SURVEY_SE],
                "ci_low": None, "ci_high": None, "ci_level": None,
                "source_outlier": row[WITHOUT_OUTLIERS] != row[SURVEY_ESTIMATE],
                "attributes": series_attributes | {
                    "animals_counted_in_strips": whole_number(row[COUNTED]),
                    "estimate_without_outliers": row[WITHOUT_OUTLIERS],
                },
            })
        if not pd.isna(row[MODEL_ESTIMATE]):
            records.append(common | {
                "source_record_id": f"{record_id}:model",
                "method": "model",
                "value": row[MODEL_ESTIMATE],
                "se": None,
                "ci_low": row[MODEL_LOWER], "ci_high": row[MODEL_UPPER], "ci_level": PREDICTION_LEVEL,
                "source_outlier": False,
                "attributes": series_attributes | {"without_survey": not has_survey},
            })

    frame = pd.DataFrame(records)
    return frame.assign(source_record_id=numbered_duplicates(frame["source_record_id"]))


def numbered_duplicates(record_ids: pd.Series) -> pd.Series:
    """The file repeats a few county, survey and species rows. The second row gets the suffix #2, and so on."""
    occurrence = record_ids.groupby(record_ids).cumcount() + 1
    return record_ids.where(occurrence == 1, record_ids + "#" + occurrence.astype(str))


def whole_number(value: float) -> int | None:
    return None if pd.isna(value) else int(value)

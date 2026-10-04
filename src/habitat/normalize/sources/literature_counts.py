"""Population values that a person typed from a paper or a census report, one reviewed CSV file per publication.

Check a file before a commit:

    uv run python -m habitat.normalize.sources.literature_counts reference/literature_counts/<citation_key>.csv
"""

import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, Field

from habitat.archive.store import ArtifactStore
from habitat.catalog.taxa import resolve_taxon
from habitat.contracts import BBox, RawManifest
from habitat.grid import Grid
from habitat.normalize.population import AREA_TYPES, METHODS, METRIC_UNITS, population_batch
from habitat.normalize.rows import NormalizedBatch, QuarantineError

SOURCE_ID = "literature_counts"
MAPPING_VERSION = "literature-counts-v1"
AREA_ID_PREFIX = "literature:"
DATE_FORMAT = "%Y-%m-%d"

REQUIRED_COLUMNS = [
    "record_id", "area_id", "area_name", "area_type", "taxon_name", "time_start", "time_end", "metric", "method",
    "value", "unit", "page", "table_or_figure",
]
OPTIONAL_COLUMNS = [
    "area_km2", "longitude", "latitude", "gbif_taxon_key", "se", "ci_low", "ci_high", "ci_level", "effort_value",
    "effort_unit", "protocol", "read_from_figure", "notes",
]
NUMBER_COLUMNS = ["area_km2", "longitude", "latitude", "value", "se", "ci_low", "ci_high", "ci_level", "effort_value"]
AREA_COLUMNS = ["area_name", "area_type", "area_km2", "longitude", "latitude"]
BOOLEAN_TEXT = {"": False, "false": False, "true": True}


class LiteratureMetadata(BaseModel):
    """The sidecar `<citation_key>.json` of a literature file."""

    citation_key: str = Field(pattern=r"^[a-z0-9_]+$")
    citation: str
    doi: str | None = None
    published: date
    license: str
    reuse_allowed: bool = False
    access_scope: str = "literature-review"
    entered_by: list[str] = Field(default_factory=list)
    checked_by: list[str] = Field(default_factory=list)
    notes: str | None = None


def read_literature_file(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False).apply(lambda column: column.str.strip())


def read_metadata(path: Path) -> LiteratureMetadata:
    return LiteratureMetadata.model_validate_json(path.read_text())


def literature_problems(frame: pd.DataFrame) -> list[str]:
    """Every problem of a literature file. An empty list means the file can be normalized without a guess."""
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        return [f"missing columns {missing}"]

    unknown = sorted(set(frame.columns) - set(REQUIRED_COLUMNS) - set(OPTIONAL_COLUMNS))
    problems = [f"unknown columns {unknown}"] if unknown else []
    frame = frame.reindex(columns=REQUIRED_COLUMNS + OPTIONAL_COLUMNS, fill_value="")
    for index, row in frame.iterrows():
        problems += [f"line {index + 2}: {problem}" for problem in row_problems(row)]

    duplicates = sorted(set(frame.loc[frame["record_id"].duplicated(), "record_id"]))
    if duplicates:
        problems.append(f"record_id is not unique: {duplicates}")

    for area_id, definitions in frame.groupby("area_id")[AREA_COLUMNS]:
        if len(definitions.drop_duplicates()) > 1:
            problems.append(f"area {area_id} has more than one definition of {AREA_COLUMNS}")

    return problems


def row_problems(row: pd.Series) -> list[str]:
    problems = [f"{column} is empty" for column in REQUIRED_COLUMNS if not row[column]]
    numbers = {}
    for column in NUMBER_COLUMNS:
        try:
            numbers[column] = float(row[column]) if row[column] else None
        except ValueError:
            problems.append(f"{column} {row[column]!r} is not a number")

    if row["gbif_taxon_key"] and not row["gbif_taxon_key"].isdigit():
        problems.append(f"gbif_taxon_key {row['gbif_taxon_key']!r} is not a whole number")

    dates = {}
    for column in ("time_start", "time_end"):
        try:
            dates[column] = datetime.strptime(row[column], DATE_FORMAT)
        except ValueError:
            problems.append(f"{column} {row[column]!r} is not a YYYY-MM-DD date")
    if len(dates) == 2 and dates["time_start"] > dates["time_end"]:
        problems.append("time_start is after time_end")

    if row["metric"] not in METRIC_UNITS:
        problems.append(f"unknown metric {row['metric']!r}; allowed: {sorted(METRIC_UNITS)}")
    elif row["unit"] not in METRIC_UNITS[row["metric"]]:
        problems.append(f"unit {row['unit']!r} is not allowed for metric {row['metric']!r}")
    if row["method"] not in METHODS:
        problems.append(f"unknown method {row['method']!r}; allowed: {sorted(METHODS)}")
    if row["area_type"] not in AREA_TYPES:
        problems.append(f"unknown area_type {row['area_type']!r}; allowed: {sorted(AREA_TYPES)}")
    if row["read_from_figure"].lower() not in BOOLEAN_TEXT:
        problems.append(f"read_from_figure {row['read_from_figure']!r} is not true or false")

    return problems + number_problems(numbers)


def number_problems(numbers: dict[str, float | None]) -> list[str]:
    problems = []
    value, low, high = numbers.get("value"), numbers.get("ci_low"), numbers.get("ci_high")
    if any(numbers.get(column) is not None and numbers[column] < 0 for column in ("value", "se", "area_km2")):
        problems.append("value, se or area_km2 is negative")
    if low is not None and high is not None and low > high:
        problems.append("ci_low is greater than ci_high")
    elif value is not None and ((low is not None and value < low) or (high is not None and value > high)):
        problems.append("value is outside ci_low and ci_high")
    if numbers.get("ci_level") is not None and not 0 < numbers["ci_level"] < 1:
        problems.append("ci_level must be between 0 and 1, for example 0.95")

    longitude, latitude = numbers.get("longitude"), numbers.get("latitude")
    if (longitude is None) != (latitude is None):
        problems.append("give both longitude and latitude, or neither")
    elif longitude is not None and not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
        problems.append("longitude or latitude is out of range")

    return problems


def normalize_literature_counts(
    manifest: RawManifest, store: ArtifactStore, grid: Grid, aoi: BBox | None = None
) -> NormalizedBatch:
    frame = read_literature_file(store.open(manifest, "data"))
    problems = literature_problems(frame)
    if problems:
        raise QuarantineError("; ".join(problems[:10]))

    citation_key = manifest.extensions.source_item_id
    frame = frame.reindex(columns=REQUIRED_COLUMNS + OPTIONAL_COLUMNS, fill_value="")
    return population_batch(
        literature_records(frame, citation_key), literature_areas(frame, citation_key), manifest, grid,
        MAPPING_VERSION,
    )


def literature_records(frame: pd.DataFrame, citation_key: str) -> pd.DataFrame:
    keys = taxon_keys(frame)
    return pd.DataFrame({
        "source_record_id": citation_key + ":" + frame["record_id"],
        "area_id": AREA_ID_PREFIX + frame["area_id"],
        "taxon_name": frame["taxon_name"],
        "gbif_taxon_key": [
            int(key) if key else keys[name] for name, key in zip(frame["taxon_name"], frame["gbif_taxon_key"])
        ],
        "time_start": frame["time_start"].map(utc_day),
        "time_end": frame["time_end"].map(utc_day),
        "metric": frame["metric"],
        "method": frame["method"],
        "unit": frame["unit"],
        **{column: numbers(frame[column]) for column in ("value", "se", "ci_low", "ci_high", "ci_level")},
        "effort_value": numbers(frame["effort_value"]),
        "effort_unit": empty_as_none(frame["effort_unit"]),
        "protocol": empty_as_none(frame["protocol"]),
        "source_outlier": False,
        "read_from_figure": frame["read_from_figure"].str.lower().map(BOOLEAN_TEXT),
        "attributes": [
            {
                "citation_key": citation_key, "page": row.page, "table_or_figure": row.table_or_figure,
                "read_from_figure": BOOLEAN_TEXT[row.read_from_figure.lower()], "notes": row.notes or None,
            }
            for row in frame.itertuples()
        ],
    })


def taxon_keys(frame: pd.DataFrame) -> dict[str, int | None]:
    """GBIF keys for the names that the file gives without a key. A name that does not resolve has no key."""
    keys = {}
    for name in sorted(set(frame.loc[frame["gbif_taxon_key"] == "", "taxon_name"])):
        resolution = resolve_taxon(name)
        keys[name] = resolution.taxa[0].gbif_key if resolution.status == "resolved" else None
    return keys


def literature_areas(frame: pd.DataFrame, citation_key: str) -> pd.DataFrame:
    areas = frame.drop_duplicates("area_id").reset_index(drop=True)
    located = (areas["longitude"] != "") & (areas["latitude"] != "")
    return pd.DataFrame({
        "area_id": AREA_ID_PREFIX + areas["area_id"],
        "source_id": SOURCE_ID,
        "area_name": areas["area_name"],
        "area_type": areas["area_type"],
        "area_km2": numbers(areas["area_km2"]),
        "geometry_wkt": ("POINT (" + areas["longitude"] + " " + areas["latitude"] + ")").where(located, None),
        "geometry_source": pd.Series(f"{SOURCE_ID}:{citation_key}", index=areas.index).where(located, None),
        "valid_from": None,
        "valid_to": None,
        "attributes": [{"citation_key": citation_key} for _ in range(len(areas))],
    })


def numbers(column: pd.Series) -> pd.Series:
    return pd.to_numeric(column.mask(column == ""), errors="raise").astype("float64")


def empty_as_none(column: pd.Series) -> pd.Series:
    return column.astype(object).where(column != "", None)


def utc_day(text: str) -> datetime:
    return datetime.strptime(text, DATE_FORMAT).replace(tzinfo=UTC)


def main(paths: list[str]) -> int:
    failed = False
    for path in paths:
        problems = literature_problems(read_literature_file(Path(path)))
        metadata_path = Path(path).with_suffix(".json")
        try:
            read_metadata(metadata_path)
        except (OSError, ValueError) as error:
            problems.append(f"metadata file {metadata_path.name}: {error}")

        print(f"{path}: {'ok' if not problems else f'{len(problems)} problem(s)'}")
        for problem in problems:
            print(f"  {problem}")
        failed = failed or bool(problems)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

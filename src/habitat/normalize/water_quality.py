"""Shared steps of the water-quality station normalizers: the parameter vocabulary, unit factors, censored values,
quality flags and the `site_observations` and `monitoring_sites` tables. See docs/ingestion/WATER_POLLUTION.md."""

import json
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass

import pandas as pd
import pyarrow as pa

from habitat.contracts import MONITORING_SITES_SCHEMA, SITE_OBSERVATIONS_SCHEMA
from habitat.grid import Grid
from habitat.normalize.rows import MONITORING_SITES, SITE_OBSERVATIONS, NormalizedBatch, QuarantineError
from habitat.normalize.sources.movebank import cell_ids_for_points

WATER_BODY_TYPES = ("river", "lake", "reservoir", "wetland", "canal", "spring", "groundwater", "other")
FRACTIONS = ("total", "dissolved", "suspended", "not_applicable")
NOT_APPLICABLE = "not_applicable"

# Molar masses in g/mol (IUPAC standard atomic weights): N 14.007, O 15.999, H 1.008, P 30.974.
N_PER_NO3 = 14.007 / 62.004
N_PER_NH4 = 14.007 / 18.038
N_PER_NH3 = 14.007 / 17.031
P_PER_PO4 = 30.974 / 94.971
FAHRENHEIT_SCALE = 5 / 9


class UnknownUnit(QuarantineError):
    """The unit has no documented factor to the canonical unit. A conversion would be a guess."""


@dataclass(frozen=True)
class Conversion:
    scale: float = 1.0
    offset: float = 0.0

    def apply(self, value: float) -> float:
        return value * self.scale + self.offset


SAME = Conversion()


@dataclass(frozen=True)
class Parameter:
    """`factors` maps a normalized source unit to the conversion into `unit`. `has_fraction` is False for a property
    of the water itself, such as pH or temperature, where filtered and unfiltered do not apply."""

    unit: str
    factors: dict[str, Conversion]
    has_fraction: bool = True
    minimum: float | None = 0.0
    maximum: float | None = None


def same(*units: str) -> dict[str, Conversion]:
    return {unit: SAME for unit in units}


MICROGRAMS_PER_LITRE = {**same("ug/l"), "mg/l": Conversion(1000.0)}

VOCABULARY: dict[str, Parameter] = {
    "dissolved_oxygen": Parameter("mg/L", same("mg/l"), has_fraction=False),
    "dissolved_oxygen_saturation": Parameter("%", same("%", "% saturatn"), has_fraction=False),
    "bod5": Parameter("mg/L", same("mg/l")),
    "cod": Parameter("mg/L", same("mg/l")),
    "nitrate_n": Parameter("mg/L as N", {**same("mg/l as n"), "mg/l as no3": Conversion(N_PER_NO3)}),
    "ammonium_n": Parameter(
        "mg/L as N",
        {**same("mg/l as n"), "mg/l as nh4": Conversion(N_PER_NH4), "mg/l as nh3": Conversion(N_PER_NH3)},
    ),
    "total_nitrogen": Parameter("mg/L as N", same("mg/l", "mg/l as n")),
    "total_phosphorus": Parameter(
        "mg/L as P", {**same("mg/l", "mg/l as p"), "ug/l": Conversion(0.001), "ug/l as p": Conversion(0.001)}
    ),
    "orthophosphate_p": Parameter("mg/L as P", {**same("mg/l as p"), "mg/l as po4": Conversion(P_PER_PO4)}),
    "ecoli": Parameter("cfu/100mL", same("cfu/100ml"), has_fraction=False),
    "ecoli_mpn": Parameter("MPN/100mL", same("mpn/100ml"), has_fraction=False),
    "faecal_coliforms": Parameter("cfu/100mL", same("cfu/100ml"), has_fraction=False),
    # 1 µmho = 1 µS by definition of the siemens.
    "conductivity": Parameter(
        "uS/cm at 25 C",
        {**same("us/cm", "umho/cm", "us/cm @25c"), "ms/cm": Conversion(1000.0), "ms/m": Conversion(10.0)},
        has_fraction=False,
    ),
    "turbidity": Parameter("NTU", same("ntu"), has_fraction=False),
    "turbidity_fnu": Parameter("FNU", same("fnu"), has_fraction=False),
    "total_suspended_solids": Parameter("mg/L", same("mg/l"), has_fraction=False),
    "total_dissolved_solids": Parameter("mg/L", same("mg/l"), has_fraction=False),
    "ph": Parameter(
        "pH", same("", "none", "std units", "ph units", "standard units", "---"), has_fraction=False, maximum=14.0
    ),
    "water_temperature": Parameter(
        "deg C",
        {"deg c": SAME, "deg f": Conversion(FAHRENHEIT_SCALE, -32 * FAHRENHEIT_SCALE), "k": Conversion(1.0, -273.15)},
        has_fraction=False,
        minimum=None,
    ),
    "chlorophyll_a": Parameter("ug/L", {**same("ug/l", "mg/m3"), "mg/l": Conversion(1000.0)}),
    "fluoride": Parameter("mg/L", same("mg/l")),
    "lead": Parameter("ug/L", MICROGRAMS_PER_LITRE),
    "cadmium": Parameter("ug/L", MICROGRAMS_PER_LITRE),
    "chromium": Parameter("ug/L", MICROGRAMS_PER_LITRE),
    "arsenic": Parameter("ug/L", MICROGRAMS_PER_LITRE),
    "mercury": Parameter("ug/L", {**MICROGRAMS_PER_LITRE, "ng/l": Conversion(0.001)}),
}


def normalize_unit(unit) -> str:
    if unit is None or (isinstance(unit, float) and math.isnan(unit)):
        return ""

    text = str(unit).strip().lower().replace("µ", "u").replace("μ", "u")
    text = text.replace("°c", "deg c").replace("°f", "deg f")
    return re.sub(r"\s+", " ", text)


def conversion(parameter: str, unit) -> Conversion:
    factor = VOCABULARY[parameter].factors.get(normalize_unit(unit))
    if factor is None:
        raise UnknownUnit(f"unit {unit!r} of {parameter} has no documented factor to {VOCABULARY[parameter].unit}")
    return factor


def convert(parameter: str, value: float | None, unit) -> float | None:
    factor = conversion(parameter, unit)
    if value is None or math.isnan(value):
        return None
    return factor.apply(value)


def resolve_parameter(candidates: Iterable[str], unit) -> str:
    """Some source codes cover two parameters that differ in method, for example turbidity in NTU or FNU.
    The unit tells them apart. Never convert between them."""
    key = normalize_unit(unit)
    for parameter in candidates:
        if key in VOCABULARY[parameter].factors:
            return parameter
    raise UnknownUnit(f"unit {unit!r} has no documented factor for any of {sorted(candidates)}")


def converted(frame: pd.DataFrame, column: str, unit_column: str) -> pd.Series:
    """Convert one value column row by row. The unit of a row without a value is not checked."""
    return pd.Series(
        [
            None if pd.isna(value) else conversion(parameter, unit).apply(float(value))
            for parameter, value, unit in zip(frame["parameter"], frame[column], frame[unit_column])
        ],
        index=frame.index,
        dtype="float64",
    )


def censored_values(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    """`value`, `detection_limit` and the rows without a limit, after the censoring rules.

    A censored row gets its limit as the value and as the detection limit. The limit is the detection limit, else the
    reported value. A limit of 0 is no limit: a value below the limit is never written as 0.
    """
    censored = frame["censored"] != "none"
    limit = frame["detection_limit"].astype("float64")
    reported = frame["value"].astype("float64")
    limit = limit.where(limit > 0, reported.where(reported > 0))

    values = reported.where(~censored, limit)
    limits = frame["detection_limit"].astype("float64").where(~censored, limit)
    return values, limits, censored & limit.isna()


def out_of_range(frame: pd.DataFrame) -> pd.Series:
    """A value that is not physically possible, for example pH above 14 or a negative concentration."""
    minimum = frame["parameter"].map(lambda name: VOCABULARY[name].minimum).astype("float64")
    maximum = frame["parameter"].map(lambda name: VOCABULARY[name].maximum).astype("float64")
    values = frame["value"].astype("float64")
    return ((values < minimum) | (values > maximum)).fillna(False).astype(bool)


def first_flag(index: pd.Index, conditions: list[tuple[str, pd.Series]]) -> pd.Series:
    """One flag per row: the first condition in the list that is true, else `ok`."""
    flags = pd.Series("ok", index=index, dtype=object)
    for flag, condition in reversed(conditions):
        flags[condition.reindex(index, fill_value=False).astype(bool)] = flag
    return flags


def check_coordinates(sites: pd.DataFrame) -> None:
    longitude, latitude = sites["longitude"], sites["latitude"]
    missing = longitude.isna() | latitude.isna() | ((longitude == 0) & (latitude == 0))
    outside = ~longitude.between(-180, 180) | ~latitude.between(-90, 90)
    bad = sites.loc[missing | outside, "local_site_id"].tolist()
    if bad:
        raise QuarantineError(f"station(s) {bad[:5]} have missing or impossible coordinates")


def in_area(frame: pd.DataFrame, aoi) -> pd.Series:
    if aoi is None:
        return pd.Series(True, index=frame.index)

    west, south, east, north = aoi
    return frame["longitude"].between(west, east) & frame["latitude"].between(south, north)


def json_records(frame: pd.DataFrame) -> list[str]:
    """One JSON object per row with the non-empty values."""
    return [
        json.dumps({key: value for key, value in record.items() if not pd.isna(value) and value != ""})
        for record in frame.astype(object).to_dict("records")
    ]


def sites_table(sites: pd.DataFrame, grid: Grid) -> pa.Table:
    sites = sites.copy()
    check_coordinates(sites)

    unknown = sorted(set(sites["water_body_type"]) - set(WATER_BODY_TYPES))
    if unknown:
        raise QuarantineError(f"water body types {unknown} are not in the vocabulary")

    sites["cell_id"] = cell_ids_for_points(grid, sites["longitude"].to_numpy(), sites["latitude"].to_numpy())
    for column in MONITORING_SITES_SCHEMA.names:
        if column not in sites.columns:
            sites[column] = None

    return pa.Table.from_pandas(
        sites[MONITORING_SITES_SCHEMA.names], schema=MONITORING_SITES_SCHEMA, preserve_index=False
    )


def observations_table(rows: pd.DataFrame, sites: pa.Table) -> pa.Table:
    unknown_fractions = sorted(set(rows["fraction"]) - set(FRACTIONS))
    if unknown_fractions:
        raise QuarantineError(f"fractions {unknown_fractions} are not in the vocabulary")

    site_columns = sites.select(["site_id", "longitude", "latitude", "cell_id"]).to_pandas()
    rows = rows.drop(columns=["longitude", "latitude", "cell_id"], errors="ignore").merge(
        site_columns, on="site_id", how="left", validate="many_to_one"
    )
    rows["unit"] = rows["parameter"].map(lambda name: VOCABULARY[name].unit)
    for column in SITE_OBSERVATIONS_SCHEMA.names:
        if column not in rows.columns:
            rows[column] = None

    rows = rows.drop_duplicates("source_record_id", keep="last")
    return pa.Table.from_pandas(
        rows[SITE_OBSERVATIONS_SCHEMA.names], schema=SITE_OBSERVATIONS_SCHEMA, preserve_index=False
    )


def site_observations_batch(
    rows: pd.DataFrame, sites: pd.DataFrame, grid: Grid, mapping_version: str
) -> NormalizedBatch:
    used_sites = sites[sites["site_id"].isin(rows["site_id"])].drop_duplicates("site_id")
    site_rows = sites_table(used_sites, grid)
    return NormalizedBatch(
        observations_table(rows, site_rows),
        mapping_version,
        family=SITE_OBSERVATIONS,
        references={MONITORING_SITES: site_rows},
    )

import math

import pandas as pd
import pytest

from habitat.normalize.rows import QuarantineError
from habitat.normalize.water_quality import (
    VOCABULARY,
    UnknownUnit,
    censored_values,
    convert,
    first_flag,
    normalize_unit,
    out_of_range,
    resolve_parameter,
)


@pytest.mark.parametrize(
    "parameter, value, unit, expected",
    [
        ("dissolved_oxygen", 8.6, "mg/l", 8.6),
        ("dissolved_oxygen_saturation", 89.4, "%", 89.4),
        ("bod5", 3.0, "mg/l", 3.0),
        ("cod", 12.0, "mg/l", 12.0),
        ("nitrate_n", 1.0, "mg/l as N", 1.0),
        ("nitrate_n", 1.0, "mg/l as NO3", 14.007 / 62.004),
        ("ammonium_n", 1.0, "mg/l as N", 1.0),
        ("ammonium_n", 1.0, "mg/l as NH4", 14.007 / 18.038),
        ("ammonium_n", 1.0, "mg/l as NH3", 14.007 / 17.031),
        ("total_nitrogen", 2.0, "mg/l", 2.0),
        ("total_phosphorus", 0.2, "mg/l", 0.2),
        ("total_phosphorus", 200.0, "µg/l", 0.2),
        ("orthophosphate_p", 1.0, "mg/l as P", 1.0),
        ("orthophosphate_p", 1.0, "mg/l as PO4", 30.974 / 94.971),
        ("ecoli", 151.0, "cfu/100ml", 151.0),
        ("ecoli_mpn", 23.0, "MPN/100mL", 23.0),
        ("faecal_coliforms", 40.0, "cfu/100ml", 40.0),
        ("conductivity", 806.0, "uS/cm", 806.0),
        ("conductivity", 0.806, "mS/cm", 806.0),
        ("conductivity", 80.6, "mS/m", 806.0),
        ("conductivity", 806.0, "umho/cm", 806.0),
        ("turbidity", 7.7, "NTU", 7.7),
        ("turbidity_fnu", 6.26, "FNU", 6.26),
        ("total_suspended_solids", 15.0, "mg/l", 15.0),
        ("total_dissolved_solids", 300.0, "mg/l", 300.0),
        ("ph", 7.2, None, 7.2),
        ("ph", 7.2, "None", 7.2),
        ("ph", 7.2, "std units", 7.2),
        ("ph", 7.2, "pH units", 7.2),
        ("ph", 7.2, "standard units", 7.2),
        ("ph", 7.2, "---", 7.2),
        ("water_temperature", 26.0, "deg C", 26.0),
        ("water_temperature", 26.0, "°C", 26.0),
        ("water_temperature", 212.0, "deg F", 100.0),
        ("water_temperature", 300.0, "K", 26.85),
        ("chlorophyll_a", 5.0, "µg/l", 5.0),
        ("chlorophyll_a", 5.0, "mg/m3", 5.0),
        ("chlorophyll_a", 0.005, "mg/l", 5.0),
        ("fluoride", 1.5, "mg/l", 1.5),
        ("lead", 33.0, "ug/L", 33.0),
        ("lead", 0.017, "mg/l", 17.0),
        ("cadmium", 0.001, "mg/l", 1.0),
        ("chromium", 0.001, "mg/l", 1.0),
        ("arsenic", 0.001, "mg/l", 1.0),
        ("mercury", 0.001, "mg/l", 1.0),
        ("mercury", 50.0, "ng/l", 0.05),
    ],
)
def test_each_documented_factor(parameter, value, unit, expected):
    assert convert(parameter, value, unit) == pytest.approx(expected)


def test_every_parameter_has_its_canonical_unit_as_an_accepted_unit():
    for name, parameter in VOCABULARY.items():
        assert parameter.factors, name
        assert parameter.unit


def test_unit_spellings_are_normalized():
    assert normalize_unit(" µg/L ") == normalize_unit("ug/l") == normalize_unit("μg/l")
    assert normalize_unit("°C") == "deg c"
    assert normalize_unit(float("nan")) == ""


def test_an_unknown_unit_is_a_quarantine_reason():
    with pytest.raises(UnknownUnit) as error:
        convert("lead", 1.0, "ppm")

    assert isinstance(error.value, QuarantineError)
    assert "ppm" in str(error.value)


def test_ntu_is_never_converted_to_fnu_and_cfu_never_to_mpn():
    with pytest.raises(UnknownUnit):
        convert("turbidity", 1.0, "FNU")
    with pytest.raises(UnknownUnit):
        convert("ecoli", 1.0, "MPN/100ml")


def test_the_unit_selects_the_parameter_among_candidates():
    assert resolve_parameter(("turbidity", "turbidity_fnu"), "FNU") == "turbidity_fnu"
    assert resolve_parameter(("ecoli", "ecoli_mpn"), "MPN/100mL") == "ecoli_mpn"
    assert resolve_parameter(("dissolved_oxygen", "dissolved_oxygen_saturation"), "%") == (
        "dissolved_oxygen_saturation"
    )
    with pytest.raises(UnknownUnit):
        resolve_parameter(("turbidity", "turbidity_fnu"), "NTRU")


def test_a_missing_value_stays_missing():
    assert convert("lead", None, "mg/l") is None
    assert convert("lead", float("nan"), "mg/l") is None


def test_a_censored_value_is_the_limit_and_never_zero():
    frame = pd.DataFrame(
        {
            "censored": ["left", "left", "left", "right", "none"],
            "value": [0.5, None, 0.0, None, 0.0],
            "detection_limit": [None, 0.03, None, 2420.0, None],
        }
    )

    values, limits, no_limit = censored_values(frame)

    assert values.fillna(-1).tolist() == [0.5, 0.03, -1, 2420.0, 0.0]
    assert limits.fillna(-1).tolist() == [0.5, 0.03, -1, 2420.0, -1]
    assert no_limit.tolist() == [False, False, True, False, False]


def test_out_of_range_values():
    frame = pd.DataFrame(
        {"parameter": ["ph", "ph", "lead", "water_temperature", "lead"], "value": [15.0, 7.0, -1.0, -0.5, None]}
    )

    assert out_of_range(frame).tolist() == [True, False, True, False, False]


def test_the_first_matching_flag_wins():
    index = pd.RangeIndex(3)
    flags = first_flag(
        index,
        [
            ("suspect", pd.Series([True, False, False], index=index)),
            ("estimated", pd.Series([True, True, False], index=index)),
        ],
    )

    assert flags.tolist() == ["suspect", "estimated", "ok"]


def test_molar_mass_factors_are_exact_ratios():
    assert math.isclose(convert("nitrate_n", 62.004, "mg/l as NO3"), 14.007)
    assert math.isclose(convert("orthophosphate_p", 94.971, "mg/l as PO4"), 30.974)

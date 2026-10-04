import json

import pandas as pd
import pytest

from analysis.service import run
from support import movement_request, replace_frame


def residence_request(tmp_path, hours=(0, 1, 2, 6), vegetation=(0.2, 0.2, 0.8, 0.8)):
    request, store = movement_request(tmp_path, analysis_method="residence_time")
    artifact = request["input"]["feature_artifact"]
    artifact["sampling_grain"] = "animal_fix"
    artifact["row_grain"] = "one row per animal fix"
    artifact["columns"] = [column for column in artifact["columns"] if column["role"] not in {"daily_displacement", "rainfall"}]
    artifact["columns"].append({"name": "ndvi", "type": "number", "nullable": True,
                                 "unit": "1", "role": "vegetation_index", "description": "NDVI"})
    frame = pd.DataFrame({
        "animal_id": ["bear-a"] * len(hours),
        "date": [pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(hours=hour) for hour in hours],
        "lon": [10.0] * len(hours), "lat": [0.0] * len(hours), "cell_id": ["a"] * len(hours),
        "ndvi": vegetation,
    })
    replace_frame(tmp_path, request, frame)

    return request, store, frame


def test_residence_uses_elapsed_time_instead_of_fix_counts(tmp_path):
    request, store, _ = residence_request(tmp_path)

    response = run(request, store=store)

    assert response["status"] == "ok", response
    metrics = response["output"]["result"]["metrics"]["residence"]
    assert metrics["tracked_hours"] == 6
    assert metrics["above_median_hours"] == 4.5
    assert metrics["at_or_below_median_hours"] == 1.5
    assert metrics["above_median_share"] == 0.75
    assert metrics["time_weighted_vegetation_index"] == pytest.approx(0.65)
    assert metrics["n_intervals"] == 3
    assert metrics["per_animal"][0]["tracked_hours"] == 6
    assert response["output"]["analysis_spec"]["method"] == "residence_time"
    json.dumps(response, allow_nan=False)


def test_long_gaps_and_terminal_fixes_do_not_add_residence_time(tmp_path):
    request, store, _ = residence_request(tmp_path, (0, 1, 2, 6, 19), (0.2, 0.2, 0.8, 0.8, 0.2))

    response = run(request, store=store)

    metrics = response["output"]["result"]["metrics"]["residence"]
    assert metrics["tracked_hours"] == 6
    assert metrics["excluded_gap_intervals"] == 1
    assert metrics["excluded_gap_hours"] == 13
    assert metrics["above_median_share"] == 0.75
    assert any("longer than 6" in warning for warning in response["warnings"])


def test_missing_vegetation_does_not_bridge_over_an_unmeasured_fix(tmp_path):
    request, store, _ = residence_request(tmp_path, vegetation=(0.2, 0.2, None, 0.8))

    metrics = run(request, store=store)["output"]["result"]["metrics"]["residence"]

    assert metrics["tracked_hours"] == 1
    assert metrics["excluded_unmeasured_intervals"] == 2
    assert metrics["excluded_unmeasured_hours"] == 5
    assert metrics["above_median_hours"] == 0


def test_nullable_numeric_values_exclude_unmeasured_intervals(tmp_path):
    request, store, frame = residence_request(tmp_path, vegetation=(0.2, 0.2, None, 0.8))
    frame["ndvi"] = frame["ndvi"].astype("Float64")
    replace_frame(tmp_path, request, frame)

    metrics = run(request, store=store)["output"]["result"]["metrics"]["residence"]

    assert metrics["tracked_hours"] == 1
    assert metrics["excluded_unmeasured_intervals"] == 2


def test_expired_modis_values_do_not_contribute_time(tmp_path):
    request, store, frame = residence_request(tmp_path)
    for name, role in (("composite_start", "vegetation_valid_from"), ("composite_end", "vegetation_valid_until")):
        request["input"]["feature_artifact"]["columns"].append({
            "name": name, "type": "timestamp", "nullable": True, "role": role,
        })
    frame["composite_start"] = "2025-12-31T00:00:00Z"
    frame["composite_end"] = "2026-01-01T01:30:00Z"
    replace_frame(tmp_path, request, frame)

    metrics = run(request, store=store)["output"]["result"]["metrics"]["residence"]

    assert metrics["tracked_hours"] == 1
    assert metrics["excluded_unmeasured_hours"] == 5


def test_intervals_do_not_cross_between_animals(tmp_path):
    request, store, frame = residence_request(tmp_path)
    frame["animal_id"] = ["bear-a", "bear-a", "bear-b", "bear-b"]
    replace_frame(tmp_path, request, frame)

    metrics = run(request, store=store)["output"]["result"]["metrics"]["residence"]

    assert metrics["tracked_hours"] == 5
    assert metrics["n_intervals"] == 2
    assert metrics["n_animals"] == 2
    assert [animal["tracked_hours"] for animal in metrics["per_animal"]] == [1, 4]


def test_query_boundary_does_not_extrapolate_to_the_next_fix(tmp_path):
    request, store, _ = residence_request(tmp_path)
    request["input"]["query"]["time_range"]["end"] = "2026-01-01T04:00:00Z"

    metrics = run(request, store=store)["output"]["result"]["metrics"]["residence"]

    assert metrics["tracked_hours"] == 2


def test_daily_summaries_cannot_substitute_for_residence_fixes(tmp_path):
    request, store, _ = residence_request(tmp_path)
    request["input"]["feature_artifact"]["sampling_grain"] = "animal_day"

    response = run(request, store=store)

    assert response["status"] == "insufficient_data"
    assert response["output"]["result"]["code"] == "fix_grain_required"


def test_duplicate_fix_timestamps_are_rejected(tmp_path):
    request, store, _ = residence_request(tmp_path, (0, 0, 2, 6))

    response = run(request, store=store)

    assert response["status"] == "error"
    assert response["error"]["code"] == "invalid_grain"


def test_no_usable_intervals_returns_insufficient_data(tmp_path):
    request, store, _ = residence_request(tmp_path, (0, 20), (0.2, 0.8))

    response = run(request, store=store)

    assert response["status"] == "insufficient_data"
    assert response["output"]["result"]["code"] == "no_residence_intervals"

import json
import re

import pandas as pd

from analysis.service import run
from tests.support import movement_frame, movement_request, replace_frame


def test_report_numbers_are_grounded(tmp_path):
    request, store = movement_request(tmp_path)
    response = run(request, store=store)
    result = response["output"]["result"]
    allowed = set(re.findall(r"\d+(?:\.\d+)?", json.dumps({"metrics": result["metrics"], "evidence": result["evidence"]})))
    allowed.update(token.split(".")[0] for token in list(allowed) if "." in token)
    used = set(re.findall(r"\d+(?:\.\d+)?", result["report"]))
    assert used <= allowed
    json.dumps(response, allow_nan=False)


def test_gaps_name_the_missing_animal_day(tmp_path):
    request, store = movement_request(tmp_path)
    gaps = run(request, store=store)["output"]["result"]["metrics"]["gaps"]
    assert gaps == [{"entity_id": "A", "date": "2026-01-03"}]


def test_paths_follow_observed_points_in_time(tmp_path):
    request, store = movement_request(tmp_path)
    features = run(request, store=store)["output"]["result"]["map"]["features"]
    lines = {item["properties"]["entity_id"]: item for item in features if item["geometry"]["type"] == "LineString"}
    assert lines["A"]["geometry"]["coordinates"] == [[10.0, 0.0], [10.1, 0.0], [10.2, 0.0]]
    assert len(lines["B"]["geometry"]["coordinates"]) == 4
    assert all(item["properties"]["kind"] == "observed" for item in features if item["geometry"]["type"] == "Point")
    assert all(row["predicted"] is False for row in run(request, store=store)["output"]["result"]["timeline"]["series"])


def test_null_displacement_is_not_zero(tmp_path):
    request, store = movement_request(tmp_path)
    frame = movement_frame()
    frame["km_moved"] = None
    replace_frame(tmp_path, request, frame)
    result = run(request, store=store)["output"]["result"]
    assert result["metrics"]["n_displacement_rows"] == 0
    assert result["metrics"]["displacement_km_total"] is None
    assert result["metrics"]["displacement_km_median"] is None
    assert "unknown" in result["report"]
    assert "total 0 km" not in result["report"]


def test_missing_coordinates_make_the_result_partial(tmp_path):
    request, store = movement_request(tmp_path)
    frame = movement_frame()
    frame.loc[0, "lat"] = None
    replace_frame(tmp_path, request, frame)
    response = run(request, store=store)
    assert response["status"] == "partial"
    result = response["output"]["result"]
    assert result["status"] == "partial"
    points = [item for item in result["map"]["features"] if item["geometry"]["type"] == "Point"]
    assert len(points) == 6
    assert result["map"]["missing_coordinates"] == 1
    assert any("no coordinates" in warning for warning in response["warnings"])


def test_time_filter_is_inclusive_and_reports_exclusions(tmp_path):
    request, store = movement_request(tmp_path)
    request["input"]["query"]["time_range"] = {
        "start": "2026-01-01T00:00:00Z",
        "end": "2026-01-03T00:00:00Z",
    }
    request["input"]["feature_artifact"]["coverage"]["end"] = "2026-03-01T00:00:00Z"
    response = run(request, store=store)
    metrics = response["output"]["result"]["metrics"]
    assert metrics["n_observations"] == 5
    assert metrics["displacement_km_total"] == 18
    assert any(warning.startswith("Excluded 2 ") for warning in response["warnings"])
    assert "2026-01-04" not in {row["date"] for row in response["output"]["result"]["timeline"]["series"]}


def test_optional_roles_are_omitted_instead_of_invented(tmp_path):
    request, store = movement_request(tmp_path)
    request["input"]["feature_artifact"]["columns"] = [
        column
        for column in request["input"]["feature_artifact"]["columns"]
        if column["role"] not in {"cell_id", "rainfall"}
    ]
    result = run(request, store=store)["output"]["result"]
    assert "revisits" not in result["metrics"]
    assert "habitat" not in result["metrics"]
    assert "Cell " not in result["report"]
    assert "caused the change" not in result["report"]
    assert result["metrics"]["displacement_km_median"] == 4


def test_thin_habitat_overlap_is_a_warning(tmp_path):
    request, store = movement_request(tmp_path)
    frame = movement_frame()
    frame.loc[frame["date"] != frame["date"].min(), "rain_mm"] = None
    replace_frame(tmp_path, request, frame)
    response = run(request, store=store)
    assert response["status"] == "ok"
    assert "habitat" not in response["output"]["result"]["metrics"]
    assert any("no habitat comparison" in warning for warning in response["warnings"])


def test_vegetation_comparison_uses_its_own_column(tmp_path):
    request, store = movement_request(tmp_path)
    frame = movement_frame()
    frame["greenness"] = [0.1, 0.8, 0.2, 0.1, 0.9, 0.3, 0.2]
    replace_frame(tmp_path, request, frame)
    request["input"]["feature_artifact"]["columns"].append(
        {
            "name": "greenness",
            "type": "number",
            "nullable": True,
            "unit": "index",
            "role": "vegetation_index",
            "description": "greenness",
        }
    )
    habitat = run(request, store=store)["output"]["result"]["metrics"]["habitat"]
    assert set(habitat) == {"rainfall", "vegetation_index"}
    assert habitat["vegetation_index"]["n_above"] >= 2
    assert habitat["vegetation_index"]["n_below"] >= 2


def test_unknown_coverage_is_warned_not_treated_as_verified(tmp_path):
    request, store = movement_request(tmp_path)
    request["input"]["feature_artifact"]["coverage"] = None
    response = run(request, store=store)
    assert response["status"] == "ok"
    assert any("coverage were not verified" in warning for warning in response["warnings"])


def test_same_calendar_day_is_invalid_even_at_different_hours(tmp_path):
    request, store = movement_request(tmp_path)
    frame = movement_frame()
    extra = frame.iloc[[1]].copy()
    extra["date"] = pd.Timestamp("2026-01-02T18:00:00Z")
    replace_frame(tmp_path, request, pd.concat([frame, extra], ignore_index=True))
    response = run(request, store=store)
    assert response["error"]["code"] == "invalid_grain"


def test_bad_values_and_descriptors_fail_before_a_finding(tmp_path):
    request, store = movement_request(tmp_path)

    broken_time = movement_frame()
    broken_time["date"] = broken_time["date"].astype(str)
    broken_time.loc[0, "date"] = "not-a-date"
    replace_frame(tmp_path, request, broken_time)
    assert run(request, store=store)["error"]["code"] == "invalid_timestamps"

    broken_number = movement_frame()
    broken_number["km_moved"] = [
        "abc" if index == 1 else (None if pd.isna(value) else str(value))
        for index, value in enumerate(broken_number["km_moved"])
    ]
    replace_frame(tmp_path, request, broken_number)
    assert run(request, store=store)["error"]["code"] == "invalid_values"

    request, store = movement_request(tmp_path)
    request["input"]["feature_artifact"]["row_count"] = 1
    assert run(request, store=store)["error"]["code"] == "artifact_row_count_mismatch"

    request, store = movement_request(tmp_path)
    frame = movement_frame().drop(columns=["km_moved"])
    replace_frame(tmp_path, request, frame)
    assert run(request, store=store)["error"]["code"] == "missing_column"


def test_request_rejections_do_not_open_a_private_or_mismatched_table(tmp_path):
    request, store = movement_request(tmp_path)
    request["input"]["feature_artifact"]["storage"]["uri"] = "artifact://missing.parquet"

    mismatched = json.loads(json.dumps(request))
    mismatched["input"]["query"]["query_id"] = "other-query"
    assert run(mismatched, store=store)["error"]["code"] == "query_id_mismatch"

    recipe = json.loads(json.dumps(request))
    recipe["input"]["recipe"]["version"] = "9"
    assert run(recipe, store=store)["error"]["code"] == "recipe_mismatch"

    schema = json.loads(json.dumps(request))
    schema["input"]["feature_artifact"]["schema_version"] = "2.0"
    assert run(schema, store=store)["error"]["code"] == "unsupported_contract"

    ambiguous = json.loads(json.dumps(request))
    ambiguous["input"]["feature_artifact"]["columns"].append(
        {"name": "other_id", "type": "string", "nullable": False, "role": "entity_id"}
    )
    assert run(ambiguous, store=store)["error"]["code"] == "ambiguous_role"

    unitless = json.loads(json.dumps(request))
    for column in unitless["input"]["feature_artifact"]["columns"]:
        if column["role"] == "daily_displacement":
            column["unit"] = None
    unit_response = run(unitless, store=store)
    assert unit_response["status"] == "insufficient_data"
    assert "daily_displacement.unit" in unit_response["output"]["result"]["metrics"]["missing_roles"]

    species = json.loads(json.dumps(request))
    species["input"]["feature_artifact"]["coverage"]["species"] = ["zebra"]
    species_response = run(species, store=store)
    assert species_response["output"]["result"]["code"] == "coverage_mismatch"
    assert species_response["output"]["result"]["metrics"]["dimension"] == "species"

    discovery = json.loads(json.dumps(request))
    discovery["input"]["query"]["task_type"] = "discovery"
    assert run(discovery, store=store)["error"]["code"] == "discovery_not_analyzed"

    assert run("not-an-object", store=store)["error"]["code"] == "invalid_request"
    assert run({}, store=store)["error"]["code"] == "unsupported_contract"


def test_a_narrower_request_can_read_public_data_but_not_another_scope(tmp_path):
    request, store = movement_request(tmp_path)
    request["access_scope"] = "org-1"
    request["input"]["query"]["access_scope"] = "org-1"
    assert run(request, store=store)["status"] == "ok"

    request["input"]["feature_artifact"]["access_scope"] = "org-2"
    assert run(request, store=store)["error"]["code"] == "access_scope_mismatch"


def test_envelope_always_has_its_contract_fields(tmp_path):
    request, store = movement_request(tmp_path)
    response = run(request, store=store)
    assert response["request_id"] == "request-001"
    assert response["query_id"] == "query-001"
    assert response["access_scope"] == "public"
    assert response["contract_version"] == "1.0"
    assert response["error"] is None
    assert response["output"]["analysis_spec"]["method"] == "movement_summary"
    assert response["output"]["model_artifact"] is None
    assert response["output"]["result"]["artifact_versions"]["feature"] == "feature-movement@1"

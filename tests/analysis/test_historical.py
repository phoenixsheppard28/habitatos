import json

from analysis.service import run
from tests.support import movement_request


def test_historical_report(tmp_path):
    request, store = movement_request(tmp_path)
    response = run(json.loads(json.dumps(request)), store=store)
    assert response["status"] == "ok"
    assert response["error"] is None
    result = response["output"]["result"]
    metrics = result["metrics"]
    assert metrics["n_animals"] == 2
    assert metrics["n_observations"] == 7
    assert metrics["n_displacement_rows"] == 5
    assert metrics["missing_displacement_rows"] == 2
    assert metrics["displacement_km_total"] == 32
    assert metrics["displacement_km_median"] == 4
    assert metrics["gap_animal_days"] == 1
    assert "Across 2 tracked animals from 2026-01-01 to 2026-01-04" in result["report"]
    assert "median daily displacement was 4 km (total 32 km)" in result["report"]
    assert "Cell w1 has 4 observations from 2 animals; 1 of those animals was recorded there" in result["report"]
    assert "When rainfall was above its median (1 mm)" in result["report"]
    assert "was 7 km (n=2)" in result["report"]
    assert "was 4 km (n=3)" in result["report"]
    assert "does not show that the environment caused the change" in result["report"]
    assert "not a wildlife census" in result["report"]
    assert "dataset-movement@1" in result["report"]
    assert "Attribution: Demo fixture." in result["report"]
    timeline = {row["date"]: row for row in result["timeline"]["series"]}
    assert timeline["2026-01-01"]["median_daily_displacement"] is None
    assert timeline["2026-01-02"]["median_daily_displacement"] == 7
    assert timeline["2026-01-03"]["n_animals"] == 1
    points = [item for item in result["map"]["features"] if item["geometry"]["type"] == "Point"]
    lines = [item for item in result["map"]["features"] if item["geometry"]["type"] == "LineString"]
    assert len(points) == 7
    assert len(lines) == 2
    assert points[0]["geometry"]["coordinates"] == [10.0, 0.0]
    assert response["warnings"] == []


def test_repeat_query_keeps_result_id(tmp_path):
    request, store = movement_request(tmp_path)
    first = run(request, store=store)["output"]["result"]
    second = run(request, store=store)["output"]["result"]
    assert first["result_id"] == second["result_id"]
    assert first["report"] == second["report"]


def test_missing_role_is_insufficient(tmp_path):
    request, store = movement_request(tmp_path)
    request["input"]["feature_artifact"]["columns"] = [
        column
        for column in request["input"]["feature_artifact"]["columns"]
        if column["role"] != "daily_displacement"
    ]
    response = run(request, store=store)
    assert response["status"] == "insufficient_data"
    assert response["output"]["result"]["code"] == "missing_roles"
    assert "daily_displacement" in response["output"]["result"]["metrics"]["missing_roles"]
    assert "32" not in response["output"]["result"]["report"]


def test_private_artifact_is_not_opened(tmp_path):
    request, store = movement_request(tmp_path)
    request["input"]["feature_artifact"]["access_scope"] = "private"
    request["input"]["feature_artifact"]["storage"]["uri"] = "artifact://missing.parquet"
    response = run(request, store=store)
    assert response["status"] == "error"
    assert response["error"]["code"] == "access_scope_mismatch"


def test_coverage_mismatch(tmp_path):
    request, store = movement_request(tmp_path)
    request["input"]["feature_artifact"]["coverage"] = {
        "species": ["antelope"],
        "start": "2020-01-01T00:00:00Z",
        "end": "2020-02-01T00:00:00Z",
    }
    request["input"]["feature_artifact"]["storage"]["uri"] = "artifact://missing.parquet"
    response = run(request, store=store)
    assert response["status"] == "insufficient_data"
    assert response["output"]["result"]["code"] == "coverage_mismatch"
    assert "displacement_km_median" not in response["output"]["result"]["metrics"]


def test_duplicate_day_is_invalid_grain(tmp_path):
    request, store = movement_request(tmp_path)
    frame = store.read_dataset(request["input"]["feature_artifact"]["storage"])
    frame = frame.copy()
    extra = frame.iloc[[0]]
    doubled = __import__("pandas").concat([frame, extra], ignore_index=True)
    doubled.to_parquet(tmp_path / "movement.parquet", index=False)
    request["input"]["feature_artifact"]["row_count"] = len(doubled)
    response = run(request, store=store)
    assert response["status"] == "error"
    assert response["error"]["code"] == "invalid_grain"


def test_rows_outside_range(tmp_path):
    request, store = movement_request(tmp_path)
    request["input"]["query"]["time_range"] = {
        "start": "2026-02-01T00:00:00Z",
        "end": "2026-02-10T00:00:00Z",
    }
    request["input"]["feature_artifact"]["coverage"] = {
        "species": ["antelope"],
        "start": "2026-01-01T00:00:00Z",
        "end": "2026-03-01T00:00:00Z",
    }
    response = run(request, store=store)
    assert response["status"] == "insufficient_data"
    assert response["output"]["result"]["code"] == "no_rows_in_range"


def test_unsupported_contract(tmp_path):
    request, store = movement_request(tmp_path)
    request["contract_version"] = "2.0"
    response = run(request, store=store)
    assert response["error"]["code"] == "unsupported_contract"

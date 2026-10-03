"""Full coordinator runs with simulated Fetch, Normalize, and Recipe."""

import copy
import json
import re
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from tests.simulation.world import SimulatedStudy
from tests.support import REGION, movement_frame
from workflow.coordinator import Coordinator
from workflow.jobs import SqliteJobStore
from workflow.monitor import MonitorRegistry

CORE = {
    "type": "Polygon",
    "coordinates": [[[9, -1], [11, -1], [11, 1], [9, 1], [9, -1]]],
}


def test_historical_question_runs_from_a_simulated_study(tmp_path):
    study = SimulatedStudy(tmp_path, movement_frame())
    coordinator = Coordinator(handlers=study.handlers())
    submitted = coordinator.submit_query(_ask())
    result = submitted["result"]
    assert study.calls == ["fetch", "normalize", "recipe", "analysis"]
    assert [stage["state"] for stage in submitted["stages"]] == ["succeeded"] * 4
    assert submitted["status"] == "complete"
    assert result["metrics"]["n_observations"] == 7
    assert result["metrics"]["displacement_km_median"] == 4
    assert result["metrics"]["displacement_km_total"] == 32
    assert result["metrics"]["gaps"] == [{"entity_id": "A", "date": "2026-01-03"}]
    assert result["evidence"]["datasets"] == [{"dataset_id": "dataset-sim", "version": "1"}]
    assert result["evidence"]["recipe_id"] == "recipe-sim"
    assert "Simulated study" in result["report"]
    assert "dataset-sim@1" in result["report"]
    lines = [item for item in result["map"]["features"] if item["geometry"]["type"] == "LineString"]
    assert {item["properties"]["entity_id"] for item in lines} == {"A", "B"}
    assert all(row["predicted"] is False for row in result["timeline"]["series"])
    _assert_report_is_grounded(result)
    json.dumps(submitted, allow_nan=False)


def test_a_corrupt_raw_row_never_reaches_the_finding(tmp_path):
    frame = movement_frame()
    corrupt = frame.iloc[[0]].copy()
    corrupt["animal_id"] = None
    corrupt["km_moved"] = 999.0
    study = SimulatedStudy(tmp_path, pd.concat([frame, corrupt], ignore_index=True))
    submitted = Coordinator(handlers=study.handlers()).submit_query(_ask())
    raw = pd.read_parquet(tmp_path / "raw" / "study.parquet")
    cleaned = pd.read_parquet(tmp_path / "normalized" / "animal_locations.parquet")
    assert len(raw) == 8
    assert len(cleaned) == 7
    assert submitted["result"]["metrics"]["displacement_km_total"] == 32
    assert "999" not in submitted["result"]["report"]


def test_fetch_and_normalize_can_stop_the_pipeline(tmp_path):
    empty = SimulatedStudy(tmp_path / "empty", movement_frame())
    empty.fetch_empty = True
    coordinator = Coordinator(handlers=empty.handlers())
    missed = coordinator.submit_query(_ask())
    coordinator.resume(missed["job_id"])
    assert empty.calls == ["fetch"]
    assert missed["status"] == "insufficient_data"
    assert "No source study" in missed["result"]["report"]

    blocked = SimulatedStudy(tmp_path / "blocked", movement_frame())
    blocked.quarantine_all = True
    quarantined = Coordinator(handlers=blocked.handlers()).submit_query(_ask())
    assert blocked.calls == ["fetch", "normalize"]
    assert quarantined["status"] == "insufficient_data"
    assert quarantined["result"]["metrics"]["quarantined_rows"] == 7


def test_recipe_failure_resumes_without_fetching_again(tmp_path):
    study = SimulatedStudy(tmp_path, movement_frame())
    study.recipe_failures_remaining = 1
    coordinator = Coordinator(handlers=study.handlers())
    failed = coordinator.submit_query(_ask())
    resumed = coordinator.resume(failed["job_id"])
    assert failed["job_status"] == "failed"
    assert failed["error"]["stage"] == "recipe"
    assert resumed["status"] == "complete"
    assert resumed["result"]["metrics"]["displacement_km_median"] == 4
    assert study.calls == ["fetch", "normalize", "recipe", "recipe", "analysis"]


def test_repeating_the_request_does_not_rebuild_the_pipeline(tmp_path):
    study = SimulatedStudy(tmp_path, movement_frame())
    coordinator = Coordinator(handlers=study.handlers())
    request = _ask()
    first = coordinator.submit_query(request)
    changed = copy.deepcopy(request)
    changed["input"]["query"]["question"] = "Has this species gone extinct in the region?"
    study.frame["km_moved"] = 0
    second = coordinator.submit_query(changed)
    assert second["job_id"] == first["job_id"]
    assert second["result"]["metrics"]["displacement_km_median"] == 4
    assert study.calls == ["fetch", "normalize", "recipe", "analysis"]


def test_relative_dates_filter_the_simulated_table(tmp_path):
    study = SimulatedStudy(tmp_path, movement_frame())
    request = _ask()
    request["input"]["query"]["time_range"] = {"relative_days": 1, "as_of": "2026-01-02T00:00:00Z"}
    submitted = Coordinator(handlers=study.handlers()).submit_query(request)
    assert submitted["query"]["time_range"]["start"].startswith("2026-01-01")
    assert submitted["query"]["time_range"]["end"].startswith("2026-01-02")
    assert submitted["result"]["metrics"]["n_observations"] == 4
    assert submitted["result"]["metrics"]["displacement_km_median"] == 7


def test_windows_and_a_boundary_survive_the_simulated_lanes(tmp_path):
    study = SimulatedStudy(tmp_path, movement_frame())
    request = _ask(
        comparison_windows=[
            {"name": "early", "start": "2026-01-01T00:00:00Z", "end": "2026-01-02T00:00:00Z"},
            {"name": "late", "start": "2026-01-03T00:00:00Z", "end": "2026-01-04T00:00:00Z"},
        ]
    )
    request["input"]["boundaries"] = [{"name": "core", "geometry": CORE}]
    result = Coordinator(handlers=study.handlers()).submit_query(request)["result"]
    early, late = result["metrics"]["comparison"]["windows"]
    overlap = result["metrics"]["overlap"]["boundaries"][0]
    by_animal = {row["entity_id"]: row for row in overlap["animals"]}
    assert early["median_displacement"] == 7
    assert early["n_displacement_rows"] == 2
    assert late["median_displacement"] == 4
    assert late["n_displacement_rows"] == 3
    assert by_animal["A"]["path_share"] == 1
    assert by_animal["B"]["path_share"] == 0
    assert "not a restoration outcome" in result["report"]
    assert "not a population" in result["report"]


def test_forecast_and_cell_use_are_scored_after_simulation(tmp_path):
    ramp = SimulatedStudy(tmp_path / "ramp", _series(days=80, value=lambda index: float(index)))
    request = _ask(**_forecast_query(days=80, cutoff_day=40))
    request["input"]["query"]["forecast"]["scenario"] = {"name": "drier", "rainfall_mm": 0}
    forecast = Coordinator(handlers=ramp.handlers()).submit_query(request)["result"]
    assert forecast["metrics"]["shown_forecast"] == "model"
    assert forecast["metrics"]["holdout_rows"] >= 30
    assert forecast["metrics"]["per_animal_models"][0]["presented"] == "model"
    assert forecast["model_artifact"]["evaluation"]["presented"] is True
    assert "does not apply it" in forecast["report"]
    assert {item["properties"]["kind"] for item in forecast["map"]["features"]} == {"last_observed"}

    cells = SimulatedStudy(tmp_path / "cells", _series(days=80, value=lambda index: 5.0, alternate_cells=True))
    cell_request = _ask(**_forecast_query(days=80, cutoff_day=40, target="cell_use"))
    cell_result = Coordinator(handlers=cells.handlers()).submit_query(cell_request)["result"]
    steps = cell_result["metrics"]["cell_forecast"][0]["steps"]
    assert cell_result["metrics"]["shown_forecast"] == "model"
    assert [step["cell_id"] for step in steps] == ["c0", "c1", "c0", "c1", "c0", "c1", "c0"]
    assert "LineString" not in {item["geometry"]["type"] for item in cell_result["map"]["features"]}

    steady = SimulatedStudy(tmp_path / "steady", _series(days=80, value=lambda index: 5.0))
    steady_request = _ask(request_id="request-steady", **_forecast_query(days=80, cutoff_day=40, target="cell_use"))
    steady_result = Coordinator(handlers=steady.handlers()).submit_query(steady_request)["result"]
    assert steady_result["metrics"]["shown_forecast"] == "baseline"
    assert steady_result["metrics"]["baseline_accuracy"] == 1


def test_a_recipe_that_ignores_the_cutoff_is_not_forecast(tmp_path):
    study = SimulatedStudy(tmp_path, _series(days=80, value=lambda index: float(index)))
    study.respect_cutoff = False
    submitted = Coordinator(handlers=study.handlers()).submit_query(_ask(**_forecast_query(days=80, cutoff_day=40)))
    assert submitted["status"] == "insufficient_data"
    assert submitted["result"]["code"] == "cutoff_not_guaranteed"
    assert submitted["result"]["model_artifact"] is None


def test_refused_questions_and_unmatched_species_produce_no_finding(tmp_path):
    extinct = SimulatedStudy(tmp_path / "extinct", movement_frame())
    request = _ask()
    request["input"]["query"]["question"] = "Has this species gone extinct in the region?"
    refused = Coordinator(handlers=extinct.handlers()).submit_query(request)
    assert extinct.calls == ["fetch", "normalize", "recipe", "analysis"]
    assert refused["status"] == "insufficient_data"
    assert refused["result"]["metrics"]["reason"] == "extinction"
    assert "map" not in refused["result"]
    assert "32" not in refused["result"]["report"]

    zebra = movement_frame()
    zebra["species"] = "zebra"
    study = SimulatedStudy(tmp_path / "zebra", zebra)
    study.include_species_role = True
    unmatched = Coordinator(handlers=study.handlers()).submit_query(_ask())
    assert unmatched["result"]["code"] == "no_rows_for_species"
    assert "displacement_km_total" not in unmatched["result"]["metrics"]


def test_habitat_only_and_partial_tracks(tmp_path):
    rain = SimulatedStudy(tmp_path / "rain", movement_frame())
    rain.habitat_only = True
    habitat = Coordinator(handlers=rain.handlers()).submit_query(_ask())["result"]
    assert habitat["metrics"]["variables"]["rainfall"]["median"] == 1
    assert habitat["metrics"]["variables"]["rainfall"]["n"] == 7
    assert "No tracked animals" in habitat["report"]
    assert "wildlife census" not in habitat["report"]

    tracks = movement_frame()
    tracks.loc[tracks.index[0], "lon"] = None
    partial = SimulatedStudy(tmp_path / "partial", tracks)
    submitted = Coordinator(handlers=partial.handlers()).submit_query(_ask(request_id="request-partial"))
    assert submitted["status"] == "partial"
    assert submitted["result"]["map"]["missing_coordinates"] == 1


def test_private_simulated_data_is_not_analyzed_for_a_public_question(tmp_path):
    study = SimulatedStudy(tmp_path, movement_frame())
    study.access_scope = "private"
    coordinator = Coordinator(handlers=study.handlers())
    submitted = coordinator.submit_query(_ask())
    coordinator.resume(submitted["job_id"])
    assert submitted["job_status"] == "failed"
    assert submitted["error"]["retryable"] is False
    assert "cannot read" in submitted["error"]["message"]
    assert study.calls.count("analysis") == 1
    assert study.calls.count("fetch") == 1


def test_simulated_job_and_monitor_survive_a_new_process(tmp_path):
    study = SimulatedStudy(tmp_path, movement_frame())
    path = tmp_path / "jobs.sqlite"
    first = Coordinator(handlers=study.handlers(), job_store=SqliteJobStore(path))
    submitted = first.submit_query(_ask())
    second = Coordinator(handlers=study.handlers(), job_store=SqliteJobStore(path))
    loaded = second.get_result(submitted["job_id"])
    assert loaded["result"]["metrics"]["displacement_km_median"] == 4
    assert second.submit_query(_ask())["job_id"] == submitted["job_id"]

    watched = SimulatedStudy(tmp_path / "watch", movement_frame())
    registry = MonitorRegistry(tmp_path / "monitors.sqlite")
    registry.register("weekly", _ask(request_id="request-watch"))
    coordinator = Coordinator(handlers=watched.handlers())
    initial = registry.run_if_changed("weekly", "checksum-v1", coordinator)
    repeat = registry.run_if_changed("weekly", "checksum-v1", coordinator)
    watched.frame = movement_frame()
    watched.frame["km_moved"] = 3.0
    changed = registry.run_if_changed("weekly", "checksum-v2", coordinator)
    assert initial["ran"] is True
    assert initial["result_changed"] is False
    assert repeat["ran"] is False
    assert changed["ran"] is True
    assert changed["result_changed"] is True
    rerun = coordinator.get_result(changed["job_id"])["result"]
    assert rerun["metrics"]["displacement_km_median"] == 3


@pytest.mark.parametrize("seed", [11, 19, 23])
def test_random_simulated_tracks_keep_the_sample_invariants(tmp_path, seed):
    rng = np.random.default_rng(seed)
    start = datetime(2026, 3, 1, tzinfo=timezone.utc)
    rows = []
    for animal in ("A", "B", "C"):
        for index in range(12):
            rows.append(
                {
                    "animal_id": animal,
                    "date": start + timedelta(days=index),
                    "lon": None if rng.random() < 0.15 else float(rng.normal(10, 0.2)),
                    "lat": None if rng.random() < 0.15 else float(rng.normal(0, 0.2)),
                    "km_moved": None if rng.random() < 0.2 else float(rng.uniform(0, 8)),
                    "cell_id": "w1",
                    "rain_mm": float(rng.uniform(0, 5)),
                }
            )
    frame = pd.DataFrame(rows)
    study = SimulatedStudy(tmp_path, frame)
    request = _ask(
        request_id=f"request-random-{seed}",
        time_range={
            "start": "2026-03-01T00:00:00Z",
            "end": (start + timedelta(days=11)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )
    result = Coordinator(handlers=study.handlers()).submit_query(request)["result"]
    values = frame["km_moved"].dropna()
    metrics = result["metrics"]
    points = [item for item in result["map"]["features"] if item["geometry"]["type"] == "Point"]
    assert metrics["n_observations"] == len(frame)
    assert metrics["displacement_km_total"] == pytest.approx(float(values.sum()))
    assert values.min() <= metrics["displacement_km_median"] <= values.max()
    assert len(points) + result["map"]["missing_coordinates"] == metrics["n_observations"]
    assert result["evidence"]["datasets"][0]["dataset_id"] == "dataset-sim"


def _ask(request_id="request-sim", **query_updates):
    query = {
        "query_id": "query-sim",
        "question": "Identify a notable pattern in tracked antelope movement in this region.",
        "task_type": "historical",
        "species": ["antelope"],
        "region": REGION,
        "time_range": {"start": "2026-01-01T00:00:00Z", "end": "2026-01-04T00:00:00Z"},
        "access_scope": "public",
    }
    query.update(query_updates)
    return {
        "contract_version": "1.0",
        "request_id": request_id,
        "query_id": "query-sim",
        "access_scope": "public",
        "input": {
            "query": query,
            "sources": [{"name": "Movebank", "study_id": "study-demo"}],
        },
    }


def _forecast_query(*, days, cutoff_day, target="next_day_displacement", horizon=7):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=days - 1)
    cutoff = start + timedelta(days=cutoff_day)
    return {
        "task_type": "forecast",
        "question": "Forecast the next-day displacement of these tracked antelopes.",
        "time_range": {
            "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "forecast": {
            "cutoff": cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "horizon_days": horizon,
            "target": target,
            "scenario": None,
        },
    }


def _series(*, days, value, alternate_cells=False):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for index in range(days):
        rows.append(
            {
                "animal_id": "A",
                "date": start + timedelta(days=index),
                "lon": 10.0,
                "lat": 0.0,
                "km_moved": value(index),
                "cell_id": ("c0" if index % 2 == 0 else "c1") if alternate_cells else "w1",
                "rain_mm": 1.0,
            }
        )
    return pd.DataFrame(rows)


def _assert_report_is_grounded(result):
    allowed = set(re.findall(r"\d+(?:\.\d+)?", json.dumps({"metrics": result["metrics"], "evidence": result["evidence"]})))
    allowed.update(token.split(".")[0] for token in list(allowed) if "." in token)
    used = set(re.findall(r"\d+(?:\.\d+)?", result["report"]))
    assert used <= allowed

import json
from datetime import UTC, datetime

import pytest

from conftest import make_manifest
from habitat.fetch import service, tools
from habitat.fetch.connectors import ConnectorResult
from habitat.fetch.connectors import cgls_lwq
from habitat.sources import sources_for_kind

BBOX = [-77.2, 38.8, -76.9, 39.0]


def test_the_new_data_kinds_name_their_sources():
    assert {s.source_id for s in sources_for_kind("water_quality_samples")} == {"wqp", "gemstat"}
    assert {s.source_id for s in sources_for_kind("water_quality_observations")} == {"sentinel2", "cgls_lwq"}


def test_the_agent_has_a_water_quality_tool():
    names = {tool.name for tool in tools.FETCH_AGENT_TOOLS}

    assert "fetch_water_quality" in names
    assert "cgls_lwq" in tools.fetch_environment.description
    assert "coverage gap" in tools.fetch_water_quality.description


def fake_connector(calls, manifests=(), warnings=()):
    def fetch(request, archive, already_ingested=None):
        calls.append(request)
        return ConnectorResult(manifests=list(manifests), warnings=list(warnings))

    return fetch


def test_station_sources_run_with_the_requested_parameters(monkeypatch):
    calls = []
    sample = make_manifest("wqp", {}, datetime(2023, 6, 1, tzinfo=UTC), item_id="wqp:x", storage_format="csv")
    monkeypatch.setattr(service, "get_source", lambda source_id: type("S", (), {"fetch": staticmethod(
        fake_connector(calls, [sample] if source_id == "wqp" else [], [] if source_id == "wqp" else ["gemstat: gap"])
    )}))

    result = service.fetch_water_quality(BBOX, "2023-06-01", "2023-06-30", parameters=["ph", "lead"])

    assert result["status"] == "partial"
    assert [a["source_id"] for a in result["raw_artifacts"]] == ["wqp"]
    assert {request.parameters for request in calls} == {("ph", "lead")}
    assert "gemstat: gap" in result["warnings"]


def test_an_empty_result_is_a_coverage_gap(monkeypatch):
    calls = []
    monkeypatch.setattr(service, "get_source", lambda source_id: type("S", (), {"fetch": staticmethod(
        fake_connector(calls, warnings=[f"{source_id}: no samples (a coverage gap, not an error)"])
    )}))

    result = service.fetch_water_quality([36.8, -1.6, 37.1, -1.3], "2011-01-01", "2011-12-31")

    assert result["status"] == "insufficient_data"
    assert any("coverage gap" in note for note in result["limitations"])


def test_unknown_station_sources_and_parameters_are_rejected():
    with pytest.raises(ValueError):
        service.fetch_water_quality(BBOX, "2023-06-01", "2023-06-30", sources=["sentinel2"])
    with pytest.raises(ValueError):
        service.fetch_water_quality(BBOX, "2023-06-01", "2023-06-30", parameters=["glyphosate"])


def test_the_tool_returns_an_error_object_for_bad_input():
    answer = json.loads(tools.fetch_water_quality.call({"bbox": BBOX, "start": "2023-06-01", "end": "2023-06-30",
                                                       "sources": ["landsat"]}))

    assert answer["status"] == "error"


def test_environment_discovery_searches_lake_water_quality(monkeypatch):
    monkeypatch.setattr(cgls_lwq, "search_cgls_lwq", lambda *args: [type("I", (), {"id": "lwq-1"})()])

    result = service.fetch_environment([36.2, -0.95, 36.5, -0.65], "2011-01-01", "2011-01-10", sources=["cgls_lwq"],
                                       discover_only=True)

    assert result["discovered"] == [{"source_id": "cgls_lwq", "item": "lwq-1"}]

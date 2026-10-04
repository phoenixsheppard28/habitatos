import json

import httpx
import pytest

from habitat.fetch import events
from habitat.fetch.tools import FETCH_AGENT_TOOLS
from habitat.normalize.sources.gbif_occurrence import MORTALITY_DATASET_KEYS
from test_firms import MODIS_CSV, VIIRS_CSV, firms_server
from test_gbif_occurrence import gbif_server, records

BBOX = [36.7, -1.6, 37.2, -1.25]
WILDEBEEST = {"matchType": "EXACT", "rank": "SPECIES", "usageKey": 2441105, "scientificName": "Connochaetes taurinus"}


def provider(species_match: dict | None = None, searches: list | None = None):
    firms = firms_server({"modis_2012_Kenya.csv": MODIS_CSV, "viirs-snpp_2012_Kenya.csv": VIIRS_CSV})
    gbif = gbif_server(records(), searches=searches)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "firms.modaps.eosdis.nasa.gov":
            return firms(request)
        if request.url.path == "/v1/species/match":
            return httpx.Response(200, json=species_match or {})
        if request.url.path == "/v1/species/search":
            return httpx.Response(200, json={"results": []})
        return gbif(request)

    return handle


def test_fires_come_from_modis_and_viirs(mock_http):
    mock_http(provider())

    result = events.fetch_events(BBOX, "2012-03-01", "2012-03-31", ["active_fire"])

    assert {artifact["source_id"] for artifact in result["raw_artifacts"]} == {"firms_modis", "firms_viirs"}
    assert result["status"] == "partial"
    assert any("clouds" in limitation for limitation in result["limitations"])


def test_species_names_are_resolved_before_the_search(mock_http):
    searches = []
    mock_http(provider(WILDEBEEST, searches))

    result = events.fetch_events(BBOX, "2012-01-01", "2013-12-31", ["species_occurrence"], species=["wildebeest"])

    assert [artifact["source_id"] for artifact in result["raw_artifacts"]] == ["gbif_occurrence"]
    assert all(params.get_list("taxonKey") == ["2441105"] for params in searches)
    assert any("presence-only" in limitation for limitation in result["limitations"])


def test_an_unresolved_species_name_fetches_nothing(mock_http):
    searches = []
    mock_http(provider({"matchType": "NONE"}, searches))

    result = events.fetch_events(BBOX, "2012-01-01", "2013-12-31", ["species_occurrence"], species=["antelopes"])

    assert result["status"] == "species_not_resolved"
    assert result["species"][0]["name"] == "antelopes"
    assert searches == []


def test_mortality_searches_each_roadkill_dataset(mock_http):
    searches = []
    mock_http(provider(searches=searches))

    events.fetch_events(BBOX, "2012-01-01", "2013-12-31", ["wildlife_mortality"])

    assert {params["datasetKey"] for params in searches} == MORTALITY_DATASET_KEYS


@pytest.mark.parametrize("event_types", [[], ["volcano"]])
def test_unknown_event_types_are_rejected_before_network(mock_http, event_types):
    calls = mock_http(lambda request: httpx.Response(500))

    with pytest.raises(ValueError, match="event_types"):
        events.fetch_events(BBOX, "2012-01-01", "2012-01-02", event_types)
    assert calls == []


def test_the_agent_has_the_fetch_events_tool(mock_http):
    calls = mock_http(lambda request: httpx.Response(500))
    [tool] = [tool for tool in FETCH_AGENT_TOOLS if tool.name == "fetch_events"]

    answer = json.loads(tool.call({"bbox": [0, 0, 0, 1], "start": "2012-01-01", "end": "2012-01-02",
                                   "event_types": ["active_fire"]}))

    assert answer["status"] == "error" and calls == []
    assert "presence-only" in tool.description

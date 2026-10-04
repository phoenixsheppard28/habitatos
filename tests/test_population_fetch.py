import json

from habitat.fetch import catalog, service, tools
from habitat.fetch.connectors.ogutu_kenya_rangelands import DATASET_ID as OGUTU
from test_ogutu_kenya_rangelands import provider

LITERATURE = "literature_counts:ogutu2013_nairobi"
KENYA = [33.9, -4.7, 41.9, 5.4]
NAMIBIA = [14.0, -20.0, 17.0, -18.0]


def dataset_ids(hits):
    return {hit["dataset_id"] for hit in hits}


def test_population_sources_are_found_by_data_kind_without_network():
    hits = catalog.search_catalog("", data_kinds=["population_counts"])

    assert dataset_ids(hits) == {OGUTU, LITERATURE}
    assert all(hit["data_kind"] == "population_counts" for hit in hits)


def test_population_search_filters_by_species_text_and_bbox():
    assert dataset_ids(catalog.search_catalog("wildebeest kenya")) == {OGUTU}
    assert dataset_ids(catalog.search_catalog("", species=["Connochaetes taurinus"], bbox=KENYA)) == {OGUTU, LITERATURE}
    assert OGUTU not in dataset_ids(catalog.search_catalog("", species=["Connochaetes taurinus"], bbox=NAMIBIA))
    assert dataset_ids(catalog.search_catalog("", species=["Equus grevyi"], data_kinds=["population_counts"])) == {OGUTU}


def test_a_data_kind_filter_also_applies_to_the_other_sources():
    hits = catalog.search_catalog("demo", data_kinds=["population_counts"])

    assert all(hit["data_kind"] == "population_counts" for hit in hits)


def test_population_dataset_ids_resolve_and_report_access():
    assert service.resolve_dataset(OGUTU) == ("ogutu_kenya_rangelands", OGUTU)
    assert service.resolve_dataset(LITERATURE) == ("literature_counts", LITERATURE)
    assert service.check_access(OGUTU)["status"] == "available"
    literature_access = service.check_access(LITERATURE)
    assert literature_access["status"] == "restricted"
    assert literature_access["access_scope"] == "literature-review"
    assert service.inspect_source(OGUTU)["rights"]["license"] == "CC-BY-4.0"
    assert service.inspect_source(LITERATURE)["found"] is True


def test_the_agent_downloads_the_ogutu_file(mock_http):
    mock_http(provider())

    manifest = service.download_dataset(OGUTU)

    assert manifest.extensions.source_id == "ogutu_kenya_rangelands"


def test_the_search_tool_takes_a_bbox_and_data_kinds():
    result = tools.search_catalog.call(
        {"query": "", "bbox": KENYA, "data_kinds": ["population_counts"], "include_internet": False}
    )

    assert dataset_ids(json.loads(result)) == {OGUTU, LITERATURE}
    assert "Counts from different methods are not comparable" in tools.search_catalog.description

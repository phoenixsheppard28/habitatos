import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import openpyxl
import pytest
from pyproj import Geod
from shapely import wkt

from habitat.archive import Archive
from habitat.archive.store import sha256_file
from habitat.catalog.publish import publish_series_version
from habitat.catalog.store import PostgresCatalog
from habitat.fetch.connectors import ConnectorRequest
from habitat.fetch.connectors.ogutu_kenya_rangelands import ITEM_ID, fetch_ogutu_kenya_rangelands
from habitat.ingest import ingest_manifest
from habitat.normalize.router import normalize
from habitat.normalize.rows import COUNT_AREAS, POPULATION_COUNTS, QuarantineError
from habitat.sources import SOURCES
from habitat.storage.series import SeriesStore

FIXTURES = Path(__file__).parent / "fixtures" / "population"
S4_SAMPLE = FIXTURES / "ogutu_s4_sample.xlsx"
BOUNDARIES_SAMPLE = FIXTURES / "geoboundaries_ken_adm1_sample.geojson"
SIGNED_S4 = "https://storage.googleapis.com/plos-corpus-prod/10.1371/journal.pone.0163249/1/pone.0163249.s004.xlsx"
BOUNDARY_MEDIA = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/ken-adm1.geojson"


def provider(s4: Path = S4_SAMPLE, boundaries: Path = BOUNDARIES_SAMPLE):
    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.url.host == "journals.plos.org":
            return httpx.Response(302, headers={"location": SIGNED_S4})
        if url == SIGNED_S4:
            return httpx.Response(200, content=s4.read_bytes())
        if request.url.host == "github.com":
            return httpx.Response(302, headers={"location": BOUNDARY_MEDIA})
        if url == BOUNDARY_MEDIA:
            return httpx.Response(200, content=boundaries.read_bytes())
        if request.url.host == "api.gbif.org":
            return httpx.Response(200, json={"matchType": "NONE", "results": []})
        return httpx.Response(404)

    return handle


@pytest.fixture
def fetched(mock_http):
    mock_http(provider())
    archive = Archive()
    [manifest] = fetch_ogutu_kenya_rangelands(ConnectorRequest(), archive).manifests
    return manifest, archive


@pytest.fixture
def rows(fetched, grid):
    manifest, archive = fetched
    return {row["source_record_id"]: row for row in normalize(manifest, archive.store, grid).table.to_pylist()}


def test_the_connector_archives_the_s4_file_and_the_county_boundaries(fetched):
    manifest, archive = fetched

    item = manifest.extensions
    assert item.source_item_id == ITEM_ID
    assert item.source_key == f"ogutu_kenya_rangelands:{ITEM_ID}:geoboundaries-9469f09"
    assert item.available_at == datetime(2016, 9, 27, tzinfo=UTC)
    assert item.processing_version == sha256_file(S4_SAMPLE)
    assert item.kind == "tabular"
    assert (item.time_start, item.time_end) == (datetime(1977, 2, 5, tzinfo=UTC), datetime(2007, 12, 9, tzinfo=UTC))
    assert set(item.assets) == {"data", "boundaries"}
    assert manifest.storage.format == SOURCES["ogutu_kenya_rangelands"].storage_format == "xlsx"
    assert manifest.access_scope == "public"
    assert manifest.rights.license == "CC-BY-4.0" and manifest.rights.reuse_allowed
    assert "10.1371/journal.pone.0163249" in manifest.rights.attribution
    assert "Connochaetes taurinus" in manifest.coverage.species
    assert archive.store.open(manifest, "boundaries").read_bytes() == BOUNDARIES_SAMPLE.read_bytes()


def test_a_second_fetch_uses_the_archive(mock_http):
    requests = mock_http(provider())
    archive = Archive()
    fetch_ogutu_kenya_rangelands(ConnectorRequest(), archive)
    count = len(requests)

    [manifest] = fetch_ogutu_kenya_rangelands(ConnectorRequest(item=ITEM_ID), archive).manifests

    assert len(requests) == count
    assert manifest.extensions.source_item_id == ITEM_ID


def test_the_connector_refuses_another_item(mock_http):
    requests = mock_http(provider())

    result = fetch_ogutu_kenya_rangelands(ConnectorRequest(item="10.1371/journal.pone.0163249.s001"), Archive())

    assert result.errors[0].code == "invalid_request"
    assert requests == []


def test_a_survey_row_gives_a_survey_estimate_and_a_model_estimate(rows):
    survey = rows["2:7701:9:survey"]
    model = rows["2:7701:9:model"]

    assert survey["time_start"] == survey["time_end"] == datetime(1977, 2, 5, tzinfo=UTC)
    assert (survey["metric"], survey["method"], survey["unit"]) == ("population_estimate", "aerial_sample", "individuals")
    assert (survey["value"], survey["se"]) == (42974.0, 12862.0)
    assert survey["area_id"] == "ke_county:2"
    assert (survey["taxon_name"], survey["gbif_taxon_key"]) == ("Connochaetes taurinus", 2441105)
    assert json.loads(survey["attributes"])["animals_counted_in_strips"] == 955
    assert survey["quality_flag"] == "interval_unknown"
    assert (model["method"], model["value"], model["ci_level"]) == ("model", 64242.25, 0.95)
    assert model["ci_low"] == 38786.04 and model["ci_high"] > model["value"]
    assert model["comparability_group"] != survey["comparability_group"]


def test_a_year_without_survey_gives_only_the_model_row(rows):
    assert "2:d19790601:9:model" in rows
    assert not any(key.startswith("2:d19790601:9:survey") for key in rows)
    assert json.loads(rows["2:d19790601:9:model"]["attributes"])["without_survey"] is True


def test_sheep_and_goats_is_kept_as_an_unresolved_taxon(rows):
    row = rows["1:7706:1:survey"]

    assert (row["taxon_name"], row["gbif_taxon_key"]) == ("Sheep and goats", None)
    assert row["quality_flag"] == "taxon_unresolved"


def test_source_outliers_zero_counts_and_duplicates_are_kept_and_flagged(rows):
    assert rows["2:9104:11:survey"]["quality_flag"] == "source_outlier"
    assert rows["2:0703:22:survey"]["quality_flag"] == "zero_count"
    assert rows["2:8301:14:survey"]["value"] == 310.0
    assert {"2:8301:14:model", "2:8301:14:model#2"} <= rows.keys()


def test_counties_take_the_s4_area_and_the_boundary_geometry(fetched, grid):
    manifest, archive = fetched

    areas = {a["area_id"]: a for a in normalize(manifest, archive.store, grid).references[COUNT_AREAS].to_pylist()}

    kajiado, machakos = areas["ke_county:2"], areas["ke_county:3"]
    assert (kajiado["area_name"], kajiado["area_type"], kajiado["area_km2"]) == ("Kajiado", "admin_unit", 21851.0)
    assert "geoBoundaries" in kajiado["geometry_source"]
    geod = Geod(ellps="WGS84")
    machakos_km2 = abs(geod.geometry_area_perimeter(wkt.loads(machakos["geometry_wkt"]))[0]) / 1e6
    assert machakos["area_km2"] == 14225.0
    assert machakos_km2 == pytest.approx(14225, rel=0.03)
    assert json.loads(machakos["attributes"])["boundary_shapes"] == ["Machakos", "Makueni"]


def test_a_county_without_a_boundary_is_unlocated(mock_http, tmp_path, grid):
    collection = json.loads(BOUNDARIES_SAMPLE.read_text())
    collection["features"] = [f for f in collection["features"] if f["properties"]["shapeName"] != "Narok"]
    boundaries = tmp_path / "boundaries.geojson"
    boundaries.write_text(json.dumps(collection))
    mock_http(provider(boundaries=boundaries))
    archive = Archive()
    [manifest] = fetch_ogutu_kenya_rangelands(ConnectorRequest(), archive).manifests

    rows = normalize(manifest, archive.store, grid).table.to_pylist()

    cattle = next(row for row in rows if row["source_record_id"] == "1:7706:4:survey")
    assert cattle["quality_flag"] == "area_unlocated"


def edited_s4(tmp_path, column: int, row: int, value) -> Path:
    workbook = openpyxl.load_workbook(S4_SAMPLE)
    workbook.active.cell(row, column).value = value
    path = tmp_path / "s4.xlsx"
    workbook.save(path)
    return path


@pytest.mark.parametrize(
    ("column", "row", "value", "message"),
    [
        (9, 3, "Animals seen", "Actual number counted"),
        (7, 5, "Unicorn", "Unicorn"),
        (10, 5, "many", "not a number"),
    ],
)
def test_a_file_that_needs_a_guess_is_quarantined(mock_http, tmp_path, grid, column, row, value, message):
    mock_http(provider(s4=edited_s4(tmp_path, column, row, value)))
    archive = Archive()
    [manifest] = fetch_ogutu_kenya_rangelands(ConnectorRequest(), archive).manifests

    with pytest.raises(QuarantineError, match=message):
        normalize(manifest, archive.store, grid)


def test_ingest_and_publish_make_the_counts_visible_to_recipe(database, grid, mock_http):
    mock_http(provider())
    archive = Archive()
    [manifest] = fetch_ogutu_kenya_rangelands(ConnectorRequest(), archive).manifests
    store = SeriesStore(database, grid)

    outcome = ingest_manifest(manifest, archive, store, grid, None)
    descriptor = publish_series_version(
        store, PostgresCatalog(database), grid, outcome.append.series_id, "ogutu_kenya_rangelands",
        SOURCES["ogutu_kenya_rangelands"].description, "public",
    )

    assert descriptor.family == POPULATION_COUNTS
    assert 2441105 in {taxon.gbif_key for taxon in descriptor.species}
    (wildebeest,) = database.execute(
        "SELECT count(*) FROM recipe_population_counts WHERE area_id = 'ke_county:2' AND gbif_taxon_key = 2441105"
    ).fetchone()
    assert wildebeest == 7
    (cells, share_sum) = database.execute(
        "SELECT count(*), sum(overlap_fraction) FROM count_area_cells WHERE area_id = 'ke_county:2'"
    ).fetchone()
    assert cells > 20000
    assert share_sum * (grid.cell_size_m / 1000) ** 2 == pytest.approx(21851, rel=0.03)

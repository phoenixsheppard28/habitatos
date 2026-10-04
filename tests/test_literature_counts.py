import csv
import hashlib
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from conftest import make_manifest
from habitat.archive import Archive
from habitat.contracts import TimePrecision
from habitat.fetch.connectors import ConnectorRequest
from habitat.fetch.connectors.literature_counts import LITERATURE_DIR, fetch_literature_counts
from habitat.normalize.router import normalize
from habitat.normalize.rows import COUNT_AREAS, QuarantineError
from habitat.normalize.sources.literature_counts import literature_problems, read_literature_file
from habitat.sources import SOURCES

EXAMPLE = LITERATURE_DIR / "ogutu2013_nairobi.csv"
EXAMPLE_METADATA = LITERATURE_DIR / "ogutu2013_nairobi.json"

# Values printed on pages 22 and 23 of Ogutu et al. 2013, The Open Conservation Biology Journal 7: 11-26.
PAPER_WILDEBEEST_TOTALS = {
    ("nairobi_np", 1): 1814, ("nairobi_np", 9): 13034,
    ("athi_kaputiei_plains", 1): 96800, ("athi_kaputiei_plains", 9): 11440,
}
PAPER_DENSITIES = {
    ("Connochaetes taurinus", "nairobi_np", 1): 15.5, ("Connochaetes taurinus", "nairobi_np", 9): 111.4,
    ("Connochaetes taurinus", "athi_kaputiei_plains", 1): 44.0, ("Connochaetes taurinus", "athi_kaputiei_plains", 9): 5.2,
    ("Equus quagga", "nairobi_np", 1): 10.4, ("Equus quagga", "nairobi_np", 9): 235.6,
    ("Equus quagga", "athi_kaputiei_plains", 1): 32.4, ("Equus quagga", "athi_kaputiei_plains", 9): 0.65,
}


def git_blob_sha(path: Path) -> str:
    content = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


def manifest_for(csv_path: Path, metadata_path: Path = EXAMPLE_METADATA):
    return make_manifest(
        "literature_counts", {"data": csv_path, "metadata": metadata_path}, datetime(1948, 1, 1, tzinfo=UTC),
        item_id="ogutu2013_nairobi", product="literature-counts", precision=TimePrecision.COMPOSITE,
        available_at=datetime(2013, 9, 20, tzinfo=UTC), storage_format="csv",
    )


def edited_copy(tmp_path: Path, row: int, **changes) -> Path:
    with EXAMPLE.open() as source:
        rows = list(csv.DictReader(source))
    rows[row] |= changes
    path = tmp_path / "ogutu2013_nairobi.csv"
    with path.open("w", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_the_example_file_has_no_problems():
    assert literature_problems(read_literature_file(EXAMPLE)) == []


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"time_start": "20/01/1948"}, "time_start"),
        ({"unit": "animals"}, "unit"),
        ({"method": "guess"}, "method"),
        ({"value": "about 15"}, "value"),
        ({"value": "-3"}, "negative"),
        ({"ci_low": "20", "ci_high": "10"}, "ci_low"),
        ({"longitude": "36.9", "latitude": ""}, "longitude and latitude"),
        ({"record_id": "wildebeest_park_1948_01_total"}, "not unique"),
        ({"area_km2": "120"}, "nairobi_np"),
        ({"page": ""}, "page"),
        ({"read_from_figure": "maybe"}, "read_from_figure"),
    ],
)
def test_the_validator_names_each_problem(tmp_path, changes, message):
    problems = literature_problems(read_literature_file(edited_copy(tmp_path, 0, **changes)))

    assert any(message in problem for problem in problems), problems
    assert all(problem.startswith("line 2") or "nairobi_np" in problem or "not unique" in problem
               for problem in problems)


def test_the_validator_names_a_missing_column(tmp_path):
    path = tmp_path / "short.csv"
    path.write_text("record_id,value\nr1,3\n")

    problems = literature_problems(read_literature_file(path))

    assert any("missing columns" in problem and "area_id" in problem for problem in problems)


def test_the_normalized_totals_match_the_paper(grid):
    rows = normalize(manifest_for(EXAMPLE), Archive().store, grid).table.to_pylist()

    totals = {
        (row["area_id"].removeprefix("literature:"), row["time_start"].month): row["value"]
        for row in rows if row["metric"] == "population_estimate"
    }
    assert totals == PAPER_WILDEBEEST_TOTALS
    densities = {
        (row["taxon_name"], row["area_id"].removeprefix("literature:"), row["time_start"].month): row["value"]
        for row in rows if row["metric"] == "density"
    }
    assert densities == PAPER_DENSITIES


def test_a_literature_row_keeps_page_figure_and_flags(grid):
    batch = normalize(manifest_for(EXAMPLE), Archive().store, grid)

    rows = {row["source_record_id"]: row for row in batch.table.to_pylist()}
    park = rows["ogutu2013_nairobi:wildebeest_park_1948_01_density"]
    plains = rows["ogutu2013_nairobi:wildebeest_plains_1948_01_density"]
    assert (park["time_start"], park["time_end"]) == (datetime(1948, 1, 1, tzinfo=UTC), datetime(1948, 1, 31, tzinfo=UTC))
    assert (park["metric"], park["method"], park["unit"]) == ("density", "compiled", "individuals_per_km2")
    assert '"page": "22"' in park["attributes"] and '"table_or_figure": "text"' in park["attributes"]
    assert park["quality_flag"] == "ok"
    assert plains["quality_flag"] == "area_unlocated"
    areas = {area["area_id"]: area for area in batch.references[COUNT_AREAS].to_pylist()}
    assert areas["literature:nairobi_np"]["geometry_wkt"] == "POINT (36.9 -1.3833)"
    assert areas["literature:athi_kaputiei_plains"]["geometry_wkt"] is None


def test_a_value_read_from_a_figure_is_flagged(tmp_path, grid):
    path = edited_copy(tmp_path, 0, read_from_figure="true", table_or_figure="Fig. 5")

    [row, *_] = normalize(manifest_for(path), Archive().store, grid).table.to_pylist()

    assert row["quality_flag"] == "digitized_from_figure"
    assert '"read_from_figure": true' in row["attributes"]


def test_a_missing_taxon_key_is_resolved_with_gbif(tmp_path, grid, mock_http):
    def gbif(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/species/match"
        return httpx.Response(200, json={"matchType": "EXACT", "rank": "SPECIES", "usageKey": 2441105,
                                         "scientificName": "Connochaetes taurinus (Burchell, 1823)"})

    requests = mock_http(gbif)
    path = edited_copy(tmp_path, 0, gbif_taxon_key="")

    [row, *_] = normalize(manifest_for(path), Archive().store, grid).table.to_pylist()

    assert row["gbif_taxon_key"] == 2441105
    assert len(requests) == 1


def test_a_file_with_a_problem_is_quarantined(tmp_path, grid):
    path = edited_copy(tmp_path, 0, unit="animals")

    with pytest.raises(QuarantineError, match="unit"):
        normalize(manifest_for(path), Archive().store, grid)


def test_the_connector_copies_the_file_and_its_metadata_without_network(mock_http):
    requests = mock_http(lambda request: httpx.Response(500))
    archive = Archive()

    [manifest] = fetch_literature_counts(ConnectorRequest(item="literature_counts:ogutu2013_nairobi"), archive).manifests

    item = manifest.extensions
    assert requests == []
    assert item.source_item_id == "ogutu2013_nairobi"
    assert item.processing_version == f"git:{git_blob_sha(EXAMPLE)}"
    assert item.source_key == (
        f"literature_counts:ogutu2013_nairobi:{git_blob_sha(EXAMPLE)}:{git_blob_sha(EXAMPLE_METADATA)}"
    )
    assert item.available_at == datetime(2013, 9, 20, tzinfo=UTC)
    assert (item.time_start, item.time_end) == (datetime(1948, 1, 1, tzinfo=UTC), datetime(1948, 9, 30, tzinfo=UTC))
    assert manifest.access_scope == "literature-review"
    assert manifest.rights.license == "CC-BY-NC-3.0" and manifest.rights.reuse_allowed is False
    assert manifest.storage.format == SOURCES["literature_counts"].storage_format
    assert archive.store.open(manifest, "data").read_bytes() == EXAMPLE.read_bytes()


def test_the_connector_refuses_an_unknown_or_unsafe_citation_key():
    assert fetch_literature_counts(ConnectorRequest(item="nobody2020"), Archive()).errors[0].code == "not_found"
    assert fetch_literature_counts(ConnectorRequest(item="../secrets"), Archive()).errors[0].code == "invalid_request"

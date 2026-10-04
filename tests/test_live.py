"""Live checks against the real providers. Run with HABITAT_LIVE_TESTS=1. They write only to a throwaway schema."""

import time
from datetime import date

import pytest

from habitat import config
from habitat.archive import Archive
from habitat.catalog.taxa import resolve_taxon
from habitat.fetch.connectors import ConnectorRequest
from habitat.fetch.connectors.literature_counts import LITERATURE_DIR
from habitat.fetch.connectors.ogutu_kenya_rangelands import fetch_ogutu_kenya_rangelands
from habitat.ingest import Workspace
from habitat.normalize.sources.literature_counts import read_literature_file
from habitat.normalize.sources.ogutu_kenya_rangelands import (
    COLUMNS,
    COUNTY,
    COUNTY_BOUNDARY_SHAPES,
    SPECIES_TAXA,
    read_boundaries,
    read_survey_table,
)
from habitat.pipeline import build_request, run

BBOX = (36.8, -1.6, 37.1, -1.3)
FULL_TILES_BYTES = 3.4 * 1024**3

pytestmark = pytest.mark.live


def archive_bytes() -> int:
    return sum(path.stat().st_size for path in config.settings().raw_dir.rglob("*") if path.is_file())


def report(name, result, started):
    print(
        f"\n[{name}] status={result.status} artifacts={len(result.fetch.output.raw_artifacts)} "
        f"outcomes={[(o.artifact_id, o.status, o.reason) for o in result.outcomes]} "
        f"published={result.published} archive_bytes={archive_bytes()} seconds={time.monotonic() - started:.1f} "
        f"warnings={result.warnings}"
    )


@pytest.fixture
def workspace(database, grid):
    return Workspace(database, grid)


def test_live_sentinel2_is_clipped(workspace):
    started = time.monotonic()

    result = run(build_request("sentinel2", BBOX, date(2024, 2, 15), date(2024, 2, 20), None, None), False, workspace)

    report("sentinel2", result, started)
    assert result.status in ("ok", "partial")
    assert result.fetch.output.raw_artifacts
    assert archive_bytes() < FULL_TILES_BYTES / 20


def test_live_modis(workspace):
    started = time.monotonic()

    result = run(build_request("modis_mod13q1", BBOX, date(2024, 2, 1), date(2024, 2, 28), None, None), False, workspace)

    report("modis_mod13q1", result, started)
    assert result.status in ("ok", "partial")
    assert all(a.artifact_id.startswith("MOD13Q1.") for a in result.fetch.output.raw_artifacts)


def test_live_chirps(workspace):
    started = time.monotonic()

    result = run(build_request("chirps", BBOX, date(2024, 2, 17), date(2024, 2, 18), None, None), False, workspace)

    report("chirps", result, started)
    assert result.status in ("ok", "partial")
    assert len(result.fetch.output.raw_artifacts) == 2


def test_live_agent_path(workspace):
    started = time.monotonic()
    question = "Find rainfall and vegetation for bbox 36.8,-1.6,37.1,-1.3 from 2024-02-15 to 2024-02-20"

    result = run(build_request(None, None, None, None, None, question), True, workspace)

    report("agent", result, started)
    print(f"[agent] summary: {result.fetch.extensions.get('agent_summary')}")
    assert result.status in ("ok", "partial")
    assert result.fetch.output.raw_artifacts


def test_live_ogutu_s4_header_and_boundaries():
    archive = Archive()
    [manifest] = fetch_ogutu_kenya_rangelands(ConnectorRequest(), archive).manifests

    survey = read_survey_table(archive.store.open(manifest, "data"))
    assert list(survey.columns) == COLUMNS
    assert survey[COUNTY].nunique() == 20
    boundaries = read_boundaries(archive.store.open(manifest, "boundaries"))
    for county in survey[COUNTY].unique():
        assert all(name in boundaries for name in COUNTY_BOUNDARY_SHAPES.get(county, [county]))


def test_live_literature_taxon_keys_match_gbif():
    for path in LITERATURE_DIR.glob("*.csv"):
        rows = read_literature_file(path)
        for name, key in set(zip(rows["taxon_name"], rows["gbif_taxon_key"])):
            resolution = resolve_taxon(name)
            assert resolution.status == "resolved" and str(resolution.taxa[0].gbif_key) == key, (path.name, name)


def test_live_ogutu_species_keys_match_gbif():
    for taxon_name, key in SPECIES_TAXA.values():
        if key is not None:
            resolution = resolve_taxon(taxon_name)
            assert resolution.status == "resolved" and resolution.taxa[0].gbif_key == key, taxon_name

import json
from datetime import UTC, datetime

import pandas as pd
import pytest
from pyproj import Geod
from shapely.geometry import Point, box

from conftest import make_manifest
from habitat.area_cells import area_cell_overlaps
from habitat.contracts import COUNT_AREAS_SCHEMA, POPULATION_COUNTS_SCHEMA, TimePrecision
from habitat.grid import default_grid, parse_cell_id
from habitat.normalize.population import check_groups, comparability_group, population_batch
from habitat.normalize.rows import COUNT_AREAS, POPULATION_COUNTS, QuarantineError

SURVEY_DAY = datetime(1977, 2, 5, tzinfo=UTC)


def area(area_id="test:park", geometry_wkt="POINT (36.9 -1.38)", name="Test park"):
    return {
        "area_id": area_id, "source_id": "test", "area_name": name, "area_type": "park", "area_km2": 117.0,
        "geometry_wkt": geometry_wkt, "geometry_source": "test", "valid_from": None, "valid_to": None,
        "attributes": {},
    }


def record(**changes):
    values = {
        "source_record_id": "r1", "area_id": "test:park", "taxon_name": "Connochaetes taurinus",
        "gbif_taxon_key": 2441105, "time_start": SURVEY_DAY, "time_end": datetime(1977, 2, 28, tzinfo=UTC),
        "metric": "population_estimate", "method": "aerial_sample", "value": 42974.0, "unit": "individuals",
        "se": 12862.0, "ci_low": None, "ci_high": None, "ci_level": None, "effort_value": None, "effort_unit": None,
        "source_outlier": False, "read_from_figure": False, "protocol": None, "attributes": {"survey_code": "7701"},
    }
    return values | changes


def batch(records, areas=None):
    manifest = make_manifest(
        "test", {}, SURVEY_DAY, item_id="item-1", product="counts", precision=TimePrecision.COMPOSITE,
        available_at=datetime(2016, 9, 27, tzinfo=UTC), storage_format="csv",
    )
    return population_batch(
        pd.DataFrame(records), pd.DataFrame(areas or [area()]), manifest, default_grid(), "test-v1"
    )


def flags(records, areas=None):
    return batch(records, areas).table.column("quality_flag").to_pylist()


def test_a_batch_has_the_population_family_and_the_count_areas_reference():
    result = batch([record()])

    assert result.family == POPULATION_COUNTS
    assert result.table.schema.equals(POPULATION_COUNTS_SCHEMA)
    assert result.references[COUNT_AREAS].schema.equals(COUNT_AREAS_SCHEMA)
    row = result.table.to_pylist()[0]
    assert row["comparability_group"] == (
        "test:test:park:Connochaetes taurinus:population_estimate:aerial_sample:individuals"
    )
    assert row["available_at"] == datetime(2016, 9, 27, tzinfo=UTC)
    assert row["dataset_id"] == "test--counts--ease2-global-1km"
    assert json.loads(row["attributes"]) == {"survey_code": "7701"}
    assert row["quality_flag"] == "ok"


def test_the_protocol_label_is_part_of_the_comparability_group():
    assert comparability_group("s", "a", "T", "count", "ground_total", "individuals", "dry-season") == (
        "s:a:T:count:ground_total:individuals:dry-season"
    )


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"gbif_taxon_key": None}, "taxon_unresolved"),
        ({"source_outlier": True}, "source_outlier"),
        ({"read_from_figure": True}, "digitized_from_figure"),
        ({"se": None}, "no_uncertainty"),
        ({"method": "ground_total", "se": None}, "ok"),
        ({"value": 0.0}, "zero_count"),
        ({"time_end": SURVEY_DAY}, "interval_unknown"),
        ({"gbif_taxon_key": None, "value": 0.0}, "taxon_unresolved"),
    ],
)
def test_each_row_gets_the_first_quality_flag_that_applies(changes, expected):
    assert flags([record(**changes)]) == [expected]


def test_an_area_without_geometry_flags_its_rows_as_unlocated():
    assert flags([record()], [area(geometry_wkt=None)]) == ["area_unlocated"]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"method": "helicopter_guess"}, "unknown method"),
        ({"metric": "biomass"}, "unknown metric"),
        ({"unit": "individuals_per_km2"}, "unit"),
        ({"value": -1.0}, "negative"),
        ({"ci_low": 10.0, "ci_high": 5.0}, "ci_low"),
        ({"time_end": datetime(1977, 1, 1, tzinfo=UTC)}, "time_start"),
        ({"area_id": "test:elsewhere"}, "no count area"),
    ],
)
def test_a_row_that_needs_a_guess_is_quarantined(changes, message):
    with pytest.raises(QuarantineError, match=message):
        batch([record(**changes)])


def test_an_area_without_a_name_and_without_coordinates_is_quarantined():
    with pytest.raises(QuarantineError, match="name"):
        batch([record()], [area(name="", geometry_wkt=None)])


def test_a_comparability_group_with_two_metrics_is_quarantined():
    rows = pd.DataFrame([
        {"comparability_group": "g", "metric": "count", "unit": "individuals", "method": "ground_total"},
        {"comparability_group": "g", "metric": "density", "unit": "individuals_per_km2", "method": "ground_total"},
    ])

    with pytest.raises(QuarantineError, match="g"):
        check_groups(rows)


def test_duplicate_record_ids_are_quarantined():
    with pytest.raises(QuarantineError, match="r1"):
        batch([record(), record()])


def test_a_point_area_gets_the_one_cell_that_contains_it(grid):
    [(cell_id, share)] = area_cell_overlaps(grid, Point(36.9, -1.38))

    assert share == 1.0
    assert grid.cell_polygon_wgs84(cell_id).contains(Point(36.9, -1.38))


def test_cell_shares_of_a_polygon_sum_to_its_area(grid):
    polygon = box(36.0, -1.2, 36.2, -1.0)
    area_km2 = abs(Geod(ellps="WGS84").geometry_area_perimeter(polygon)[0]) / 1e6

    overlaps = area_cell_overlaps(grid, polygon)

    shares = [share for _, share in overlaps]
    assert all(0 < share <= 1 for share in shares)
    assert any(share < 1 for share in shares)
    cell_km2 = (grid.cell_size_m / 1000) ** 2
    assert sum(shares) * cell_km2 == pytest.approx(area_km2, rel=0.01)
    assert len({parse_cell_id(cell_id) for cell_id, _ in overlaps}) == len(overlaps)

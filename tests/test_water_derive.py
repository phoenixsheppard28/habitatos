import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import pytest
from pyproj import Geod
from shapely.geometry import Point, Polygon

from conftest import make_manifest
from habitat.contracts import TimePrecision
from habitat.derive import water
from habitat.grid import parse_cell_id, transformer
from habitat.ingest import Workspace
from habitat.pipeline import build_request, run
from habitat.normalize.rows import series_id, series_id_for, to_cell_observations
from habitat.normalize.site_features import SiteFeature, to_site_features

BBOX = (36.90, -1.46, 36.94, -1.42)
COVERED = (36.60, -1.76, 37.24, -1.12)
MARCH, APRIL = date(2011, 3, 1), date(2011, 4, 1)
MAPPED = datetime(2010, 6, 1, tzinfo=UTC)


@pytest.fixture
def workspace(database, grid):
    return Workspace(database, grid)


def feature(record_id, geometry, **changes):
    values = dict(
        source_record_id=record_id, feature_id=f"test:{record_id}", feature_class="water_point",
        feature_type="borehole", origin="artificial", permanence="permanent", status="functional",
        geometry=geometry, time_start=MAPPED, available_at=MAPPED,
    )
    return SiteFeature(**(values | changes))


def add_features(workspace, features, item_id="features-1", covered=COVERED, source_id="osm_overpass"):
    manifest = make_manifest(
        source_id, {}, MAPPED, MAPPED, item_id=item_id, product="water-test", precision=TimePrecision.STATIC,
        available_at=max(f.available_at for f in features), properties={"requested_bbox": list(covered)},
        storage_format="json",
    )
    batch = to_site_features(features, manifest, workspace.grid, "test-v1")
    return workspace.store.append_batch(series_id(manifest, workspace.grid), manifest, batch)


def add_surface_water(workspace, month, fractions: dict[str, float], distances: dict[str, float] | None = None):
    start = datetime.combine(month, datetime.min.time(), tzinfo=UTC)
    end = datetime.combine(water.next_month(month), datetime.min.time(), tzinfo=UTC)
    manifest = make_manifest(
        "jrc_gsw_monthly", {}, start, end, item_id=f"jrc-{month:%Y_%m}", product="jrc-test",
        precision=TimePrecision.COMPOSITE, available_at=datetime(2019, 1, 5, tzinfo=UTC),
        properties={"requested_bbox": list(COVERED)},
    )
    rows = [
        {"cell_id": cell, "variable": "surface_water_fraction", "value": value, "std": None,
         "valid_fraction": 0.9, "pixel_count": 1000}
        for cell, value in fractions.items()
    ] + [
        {"cell_id": cell, "variable": "distance_to_surface_water_m", "value": value, "std": None,
         "valid_fraction": 0.9, "pixel_count": 1000}
        for cell, value in (distances or {}).items()
    ]
    batch = to_cell_observations(
        pd.DataFrame(rows), manifest, workspace.grid, "jrc-test-v1", "mean",
        {"surface_water_fraction": "fraction", "distance_to_surface_water_m": "m"}, 30,
    )
    workspace.store.append_batch(series_id(manifest, workspace.grid), manifest, batch)


def cell_of(grid, lon, lat):
    x, y = transformer("EPSG:4326", grid.crs).transform(lon, lat)
    rows, cols = grid.rows_cols_from_xy(np.array([x]), np.array([y]))
    return str(grid.cell_ids(rows, cols)[0])


def centroid(grid, cell_id):
    row, col = parse_cell_id(cell_id)
    x, y = grid.cell_centres_xy(np.array([row]), np.array([col]))
    lon, lat = transformer(grid.crs, "EPSG:4326").transform(x[0], y[0])
    return float(lon), float(lat)


def pan_around(grid, cell_id):
    """A 200 m pan around the cell centroid, so it lies in one cell and covers the centroid."""
    lon, lat = centroid(grid, cell_id)
    return Polygon([(lon - 0.001, lat - 0.001), (lon + 0.001, lat - 0.001), (lon + 0.001, lat + 0.001),
                    (lon - 0.001, lat + 0.001)])


def derived(workspace, month=MARCH, bbox=BBOX):
    water.derive_water_month(workspace, bbox, month)
    rows = workspace.connection.execute(
        "SELECT cell_id, variable, value, quality_flag, valid_fraction, pixel_count, available_at "
        "FROM current_cell_observations WHERE source_id = 'water_derived' AND time_start = %s",
        (datetime.combine(month, datetime.min.time(), tzinfo=UTC),),
    ).fetchall()
    return {(cell, variable): (value, flag, valid, count, available)
            for cell, variable, value, flag, valid, count, available in rows}


def test_distance_to_a_known_point_is_geodesic(workspace, grid):
    add_features(workspace, [feature("bh", Point(36.92, -1.44))])
    cell = cell_of(grid, 36.905, -1.455)

    rows = derived(workspace)

    lon, lat = centroid(grid, cell)
    _, _, expected = Geod(ellps="WGS84").inv(lon, lat, 36.92, -1.44)
    value, flag, valid, count, available = rows[(cell, "distance_to_water_m")]
    assert value == pytest.approx(expected, rel=0.002)
    assert flag == "ok" and valid == 1.0 and count == 1
    assert available == MAPPED
    assert rows[(cell, "distance_to_artificial_water_m")][0] == pytest.approx(expected, rel=0.002)
    assert rows[(cell, "distance_to_permanent_water_m")][0] == pytest.approx(expected, rel=0.002)
    assert rows[(cell, "distance_to_natural_water_m")][0] is None
    assert rows[(cell, "distance_to_natural_water_m")][1] == "beyond_search_radius"


def test_water_point_density_counts_points_within_5_km(workspace, grid):
    add_features(workspace, [feature("a", Point(36.92, -1.44)), feature("b", Point(36.921, -1.441)),
                             feature("c", Point(37.2, -1.44))])
    cell = cell_of(grid, 36.92, -1.44)

    rows = derived(workspace)

    assert rows[(cell, "water_point_density")][0] == pytest.approx(2 / 78.54, rel=0.001)


def test_a_seasonal_pan_is_dry_in_one_month_and_wet_in_the_next(workspace, grid):
    pan_cell = cell_of(grid, 36.916, -1.436)
    add_features(workspace, [feature("pan", pan_around(grid, pan_cell), feature_class="pan", feature_type="pond",
                                     origin="natural", permanence="seasonal", status="unknown")])
    add_surface_water(workspace, MARCH, {pan_cell: 0.0})
    add_surface_water(workspace, APRIL, {pan_cell: 0.3})

    dry = derived(workspace, MARCH)
    wet = derived(workspace, APRIL)

    assert dry[(pan_cell, "distance_to_water_m")][0] is None
    assert wet[(pan_cell, "distance_to_water_m")][0] == 0.0
    assert wet[(pan_cell, "distance_to_natural_water_m")][0] == 0.0


def test_a_seasonal_feature_without_observations_counts_as_available_with_a_flag(workspace, grid):
    pan_cell = cell_of(grid, 36.916, -1.436)
    add_features(workspace, [feature("pan", pan_around(grid, pan_cell), feature_class="pan", origin="natural",
                                     permanence="seasonal")])

    rows = derived(workspace)

    assert rows[(pan_cell, "distance_to_water_m")][:2] == (0.0, "seasonal_state_unknown")


def test_surface_water_pixels_count_as_water(workspace, grid):
    cell = cell_of(grid, 36.905, -1.455)
    add_features(workspace, [feature("bh", Point(36.93, -1.43))])
    add_surface_water(workspace, MARCH, {cell: 0.2}, {cell: 12.0})

    rows = derived(workspace)

    assert rows[(cell, "distance_to_water_m")][0] == 12.0
    assert rows[(cell, "distance_to_artificial_water_m")][0] > 1000


def test_a_natural_feature_mapped_later_is_backfilled_but_an_artificial_one_is_not(workspace, grid):
    later = datetime(2015, 6, 1, tzinfo=UTC)
    add_features(workspace, [
        feature("spring", Point(36.92, -1.44), origin="natural", feature_type="spring", time_start=later,
                available_at=later),
        feature("borehole", Point(36.905, -1.455), time_start=later, available_at=later),
    ])
    cell = cell_of(grid, 36.905, -1.455)

    rows = derived(workspace)

    assert rows[(cell, "distance_to_natural_water_m")][1] == "feature_backfilled"
    assert rows[(cell, "distance_to_artificial_water_m")][0] is None


def test_an_artificial_feature_with_an_install_year_is_backfilled_from_that_year(workspace, grid):
    later = datetime(2015, 6, 1, tzinfo=UTC)
    add_features(workspace, [feature("borehole", Point(36.905, -1.455), time_start=later, available_at=later,
                                     attributes={"install_year": "2005"})])
    cell = cell_of(grid, 36.905, -1.455)

    rows = derived(workspace)

    assert rows[(cell, "distance_to_artificial_water_m")][1] == "feature_backfilled"


def test_non_functional_and_abandoned_points_are_not_water(workspace, grid):
    add_features(workspace, [feature("broken", Point(36.92, -1.44), status="non_functional"),
                             feature("gone", Point(36.921, -1.44), status="abandoned")])

    rows = derived(workspace)

    assert all(value is None for (cell, variable), (value, *_) in rows.items() if variable == "distance_to_water_m")


def test_a_cell_near_the_edge_of_the_fetched_area_is_flagged(workspace, grid):
    add_features(workspace, [feature("bh", Point(37.5, -1.44))], covered=(36.88, -1.48, 36.96, -1.40))

    rows = derived(workspace)

    flags = {flag for (cell, variable), (_, flag, *_) in rows.items() if variable == "distance_to_water_m"}
    assert flags == {"beyond_search_radius"}

    add_features(workspace, [feature("near", Point(36.95, -1.44))], item_id="features-2",
                 covered=(36.88, -1.48, 36.96, -1.40))
    rows = derived(workspace)
    flags = {flag for (cell, variable), (_, flag, *_) in rows.items() if variable == "distance_to_water_m"}
    assert "edge_effect" in flags


def test_recomputation_supersedes_the_old_batch(workspace, grid):
    add_features(workspace, [feature("bh", Point(36.92, -1.44))])
    first = water.derive_water_month(workspace, BBOX, MARCH)
    unchanged = water.derive_water_month(workspace, BBOX, MARCH)

    add_features(workspace, [feature("bh2", Point(36.905, -1.455))], item_id="features-2")
    second = water.derive_water_month(workspace, BBOX, MARCH)

    assert first.append.appended and not unchanged.append.appended
    assert second.append.version == first.append.version + 1
    superseded = workspace.connection.execute(
        "SELECT superseded_in_version FROM ingest_batches WHERE series_id = %s AND batch_key = %s",
        (first.append.series_id, first.append.batch_key),
    ).fetchone()
    assert superseded == (second.append.version,)
    item = second.manifest.extensions
    assert item.kind == "derived" and item.source_id == "water_derived"
    assert item.source_item_id.endswith(":2011-03")
    assert len(item.properties["inputs"]) == 2
    assert {"dataset_id", "dataset_version", "mapping_version", "variable"} <= set(item.properties["inputs"][0])
    inputs = json.loads(workspace.archive.store.open(second.manifest, "inputs").read_text())
    assert len(inputs["batch_keys"]) == 2


def test_duplicate_points_of_two_sources_count_once(workspace, grid):
    add_features(workspace, [feature("osm-well", Point(36.92, -1.44))])
    add_features(workspace, [feature("wpdx-well", Point(36.9201, -1.4401))], item_id="wpdx-1", source_id="wpdx")
    cell = cell_of(grid, 36.92, -1.44)

    rows = derived(workspace)

    assert rows[(cell, "water_point_density")][0] == pytest.approx(1 / 78.54, rel=0.001)


def test_nothing_to_derive_without_inputs(workspace):
    assert water.derive_water_month(workspace, BBOX, MARCH) is None


def test_derive_for_a_date_range_runs_each_month(workspace):
    add_features(workspace, [feature("bh", Point(36.92, -1.44))])

    outcomes = water.derive_water(workspace, BBOX, date(2011, 1, 15), date(2011, 3, 2))

    assert [o.manifest.extensions.source_item_id[-7:] for o in outcomes] == ["2011-01", "2011-02", "2011-03"]


def test_the_pipeline_derives_and_publishes_after_a_water_fetch(workspace, mock_http):
    rows = json.loads((Path(__file__).parent / "fixtures" / "water" / "wpdx_athi.json").read_text())
    mock_http(lambda request: httpx.Response(200, json=rows if request.url.params["$offset"] == "0" else []))
    request = build_request("wpdx", (37.18, -1.90, 37.30, -1.80), date(2021, 2, 1), date(2021, 2, 28), None, None)

    result = run(request, use_agent=False, workspace=workspace)

    derived_series = series_id_for("water_derived", "water-derived", workspace.grid)
    assert derived_series in result.published
    assert [o.status for o in result.outcomes if o.source_id == "water_derived"] == ["appended"]
    published = workspace.connection.execute(
        "SELECT count(*) FROM recipe_water_observations WHERE variable = 'water_point_density'"
    ).fetchone()
    assert published[0] > 0

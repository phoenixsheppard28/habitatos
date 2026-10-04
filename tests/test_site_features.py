import json
from datetime import UTC, datetime

import shapely
from shapely.geometry import LineString, Point, Polygon

from conftest import make_manifest
from habitat.catalog.publish import publish_series_version
from habitat.catalog.store import PostgresCatalog
from habitat.contracts import SITE_FEATURES_SCHEMA, TimePrecision
from habitat.normalize.rows import SITE_FEATURES, series_id
from habitat.normalize.site_features import FOREVER, SiteFeature, to_site_features
from habitat.storage.series import SeriesStore

MAPPED = datetime(2013, 5, 1, tzinfo=UTC)
REMAPPED = datetime(2016, 2, 1, tzinfo=UTC)


def feature(record_id, geometry, time_start=MAPPED, **changes):
    values = dict(
        source_record_id=record_id, feature_id="osm:node/1", feature_class="water_point", feature_type="borehole",
        origin="artificial", permanence="permanent", status="functional", geometry=geometry,
        time_start=time_start, available_at=time_start,
    )
    return SiteFeature(**(values | changes))


def manifest(item_id="athi@2026-10-03"):
    return make_manifest(
        "osm_overpass", {}, MAPPED, MAPPED, item_id=item_id, product="osm-water", precision=TimePrecision.STATIC,
        storage_format="json",
    )


def test_a_point_gets_a_cell_and_a_line_does_not(grid):
    batch = to_site_features(
        [
            feature("node/1@v1", Point(36.9, -1.45)),
            feature("way/2@v3", LineString([(36.9, -1.45), (36.95, -1.47)]), feature_id="osm:way/2",
                    feature_class="river", feature_type="stream", origin="natural", permanence="seasonal",
                    status="unknown"),
        ],
        manifest(), grid, "osm-water-v1",
    )

    assert batch.family == SITE_FEATURES
    assert batch.table.schema == SITE_FEATURES_SCHEMA
    point, line = batch.table.to_pylist()
    assert point["cell_id"].startswith("E1K-") and (point["longitude"], point["latitude"]) == (36.9, -1.45)
    assert (line["cell_id"], line["longitude"], line["latitude"]) == (None, None, None)
    assert shapely.from_wkb(line["geometry"]).geom_type == "LineString"
    assert point["time_end"] == FOREVER
    assert point["dataset_id"] == series_id(manifest(), grid)


def test_quality_flag_takes_the_first_reason_in_the_documented_order(grid):
    bowtie = Polygon([(36.9, -1.4), (36.91, -1.41), (36.91, -1.4), (36.9, -1.41), (36.9, -1.4)])
    batch = to_site_features(
        [
            feature("a", Point(36.9, -1.45), permanence="unknown", status="unknown"),
            feature("b", Point(36.9, -1.45), coordinate_uncertainty_m=800.0, permanence="unknown"),
            feature("c", bowtie, feature_class="lake", feature_type="lake", permanence="unknown", status="unknown"),
            feature("d", Point(36.9, -1.45), reasons=["feature_id_missing"], coordinate_uncertainty_m=800.0),
            feature("e", LineString([(36.9, -1.45), (36.95, -1.47)]), feature_class="river", status="unknown"),
        ],
        manifest(), grid, "m1",
    )

    flags = batch.table.column("quality_flag").to_pylist()
    assert flags == ["permanence_unknown", "location_imprecise", "geometry_repaired", "feature_id_missing", "ok"]
    assert shapely.from_wkb(batch.table.column("geometry")[2].as_py()).is_valid


def test_site_features_are_stored_summarized_and_published(database, grid):
    store = SeriesStore(database, grid)
    first = manifest("athi@2013")
    series = series_id(first, grid)
    store.append_batch(series, first, to_site_features(
        [feature("node/1@v1", Point(36.9, -1.45), attributes={"pump": "powered"})], first, grid, "m1"
    ))

    summary = store.summary(store.latest_version(series))
    descriptor = publish_series_version(store, PostgresCatalog(database), grid, series, "osm_overpass", "OSM", "public")

    row = database.execute(
        "SELECT ST_AsText(geometry), attributes, cell_id FROM site_features"
    ).fetchone()
    assert row[0] == "POINT(36.9 -1.45)" and row[1] == {"pump": "powered"}
    assert summary.row_count == 1 and summary.variables == ["water_point"]
    assert summary.start == MAPPED and summary.cell_ids == [row[2]]
    assert descriptor.family == SITE_FEATURES and descriptor.row_grain


def test_recipe_view_ends_a_feature_version_at_the_next_version(database, grid):
    store = SeriesStore(database, grid)
    first, second = manifest("athi@2013"), manifest("athi@2016")
    series = series_id(first, grid)
    store.append_batch(series, first, to_site_features(
        [feature("node/1@v1", Point(36.9, -1.45))], first, grid, "m1"
    ))
    store.append_batch(series, second, to_site_features(
        [feature("node/1@v1", Point(36.9, -1.45)),
         feature("node/1@v2", Point(36.9001, -1.45), time_start=REMAPPED, status="non_functional")],
        second, grid, "m1",
    ))
    publish_series_version(store, PostgresCatalog(database), grid, series, "osm_overpass", "OSM", "public")

    rows = database.execute(
        "SELECT source_record_id, status, valid_until FROM recipe_site_features ORDER BY time_start"
    ).fetchall()

    assert rows == [("node/1@v1", "functional", REMAPPED), ("node/1@v2", "non_functional", FOREVER)]


def test_attributes_are_json_text(grid):
    batch = to_site_features(
        [feature("a", Point(36.9, -1.45), attributes={"install_year": 2005})], manifest(), grid, "m1"
    )

    assert json.loads(batch.table.column("attributes")[0].as_py()) == {"install_year": 2005}

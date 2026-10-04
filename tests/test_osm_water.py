import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import shapely

from conftest import make_manifest
from habitat.archive.store import LocalArtifactStore
from habitat.contracts import TimePrecision
from habitat.normalize.rows import SITE_FEATURES, QuarantineError
from habitat.normalize.sources.osm_water import normalize_osm_water
from habitat.normalize.site_features import FOREVER

FIXTURE = Path(__file__).parent / "fixtures" / "water" / "overpass_athi.json"
OSM_BASE = datetime(2026, 10, 4, 2, 35, 56, tzinfo=UTC)


def overpass_manifest(tmp_path, document=None):
    path = tmp_path / "overpass.json"
    path.write_text(json.dumps(document) if document is not None else FIXTURE.read_text())
    return make_manifest(
        "osm_overpass", {"features": str(path)}, OSM_BASE, item_id="36.80000,-1.55000,37.00000,-1.40000@2026-10-04",
        product="osm-water", precision=TimePrecision.STATIC, processing_version="2026-10-04T02:35:56Z",
        available_at=OSM_BASE, storage_format="json",
    )


def rows_by_feature(tmp_path, grid, document=None):
    manifest = overpass_manifest(tmp_path, document)
    batch = normalize_osm_water(manifest, LocalArtifactStore(), grid, None)
    return batch, {row["feature_id"]: row for row in batch.table.to_pylist()}


def with_elements(*elements):
    document = json.loads(FIXTURE.read_text())
    document["elements"] = list(elements)
    return document


def node(tags, **changes):
    return {"type": "node", "id": 1, "lat": -1.43, "lon": 36.9, "timestamp": "2014-06-02T08:00:00Z", "version": 1,
            "tags": tags} | changes


def test_each_tag_row_maps_to_class_type_origin_and_permanence(tmp_path, grid):
    batch, rows = rows_by_feature(tmp_path, grid)

    def mapping(feature_id):
        row = rows[feature_id]
        return row["feature_class"], row["feature_type"], row["origin"], row["permanence"]

    assert batch.family == SITE_FEATURES
    assert mapping("osm:way/675032143") == ("river", "river", "natural", "permanent")
    assert mapping("osm:way/354198036") == ("river", "stream", "natural", "seasonal")
    assert mapping("osm:way/100149053") == ("reservoir", "reservoir", "artificial", "unknown")
    assert mapping("osm:way/355490213") == ("pan", "pond", "natural", "unknown")
    assert mapping("osm:way/161956406") == ("lake", "water", "unknown", "unknown")
    assert mapping("osm:way/100147501") == ("river", "river", "natural", "unknown")
    assert mapping("osm:way/871204319") == ("dam", "weir", "artificial", "unknown")
    assert mapping("osm:node/3807359328") == ("water_point", "borehole", "artificial", "unknown")
    assert mapping("osm:node/3926513161") == ("water_point", "well", "artificial", "unknown")
    assert mapping("osm:node/3807359327") == ("water_point", "tap", "artificial", "permanent")
    assert mapping("osm:relation/11874038") == ("river", "river", "natural", "unknown")
    assert mapping("osm:node/900000001") == ("water_point", "spring", "natural", "unknown")
    assert mapping("osm:way/900000003") == ("wetland", "swamp", "natural", "unknown")
    assert mapping("osm:way/900000004") == ("river", "canal", "artificial", "unknown")
    assert mapping("osm:way/900000005") == ("dam", "dam", "artificial", "unknown")
    assert mapping("osm:way/900000006") == ("lake", "lake", "natural", "seasonal")
    assert mapping("osm:relation/900000007") == ("wetland", "wetland", "natural", "unknown")


def test_status_comes_from_operational_and_lifecycle_tags(tmp_path, grid):
    _, rows = rows_by_feature(tmp_path, grid)

    assert rows["osm:node/3807359328"]["status"] == "functional"
    assert rows["osm:node/3807359329"]["status"] == "non_functional"
    assert rows["osm:node/900000002"]["status"] == "non_functional"
    assert rows["osm:node/3926513161"]["status"] == "unknown"
    assert rows["osm:node/3926513161"]["quality_flag"] == "permanence_unknown"
    assert rows["osm:node/3807359327"]["quality_flag"] == "ok"


def test_geometry_kinds_and_cells(tmp_path, grid):
    _, rows = rows_by_feature(tmp_path, grid)

    def kind(feature_id):
        return shapely.from_wkb(rows[feature_id]["geometry"]).geom_type

    assert kind("osm:node/3807359328") == "Point" and rows["osm:node/3807359328"]["cell_id"].startswith("E1K-")
    assert kind("osm:way/675032143") == "LineString" and rows["osm:way/675032143"]["cell_id"] is None
    assert kind("osm:way/100149053") == "Polygon"
    assert kind("osm:relation/11874038") == "MultiLineString"
    wetland = shapely.from_wkb(rows["osm:relation/900000007"]["geometry"])
    assert wetland.geom_type in ("Polygon", "MultiPolygon") and len(wetland.interiors if wetland.geom_type == "Polygon"
                                                                    else wetland.geoms[0].interiors) == 1


def test_each_row_has_its_element_version_time(tmp_path, grid):
    _, rows = rows_by_feature(tmp_path, grid)
    well = rows["osm:node/3807359328"]

    assert well["source_record_id"] == "node/3807359328@v2"
    assert well["time_start"] == datetime(2015, 11, 10, 17, 1, 37, tzinfo=UTC)
    assert well["available_at"] == well["time_start"]
    assert well["time_end"] == FOREVER
    assert well["name"] is None and rows["osm:way/675032143"]["name"] == "Mbagathi River"
    attributes = json.loads(well["attributes"])
    assert attributes["tags"]["construction_date"] == "2005" and attributes["changeset"] == 35220286
    assert "user" not in attributes


def test_landuse_reservoir_alone_is_a_reservoir(tmp_path, grid):
    closed = [{"lat": -1.41, "lon": 36.95}, {"lat": -1.41, "lon": 36.96}, {"lat": -1.42, "lon": 36.96},
              {"lat": -1.41, "lon": 36.95}]
    way = {"type": "way", "id": 7, "timestamp": "2014-06-02T08:00:00Z", "version": 1, "geometry": closed,
           "tags": {"landuse": "reservoir"}}

    _, rows = rows_by_feature(tmp_path, grid, with_elements(way))

    assert rows["osm:way/7"]["feature_class"] == "reservoir"
    assert shapely.from_wkb(rows["osm:way/7"]["geometry"]).geom_type == "Polygon"


def test_an_element_without_a_mapped_tag_is_quarantined(tmp_path, grid):
    with pytest.raises(QuarantineError, match="node/1"):
        rows_by_feature(tmp_path, grid, with_elements(node({"natural": "water", "water": "wastewater"})))


def test_an_element_without_a_timestamp_is_quarantined(tmp_path, grid):
    element = node({"natural": "spring"})
    del element["timestamp"]

    with pytest.raises(QuarantineError, match="timestamp"):
        rows_by_feature(tmp_path, grid, with_elements(element))


def test_a_response_without_the_database_time_is_quarantined(tmp_path, grid):
    document = with_elements(node({"natural": "spring"}))
    del document["osm3s"]

    with pytest.raises(QuarantineError, match="timestamp_osm_base"):
        rows_by_feature(tmp_path, grid, document)

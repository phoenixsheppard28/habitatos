import shutil
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import numpy as np
import pytest
from pyproj import Geod
from rasterio.transform import from_origin

from conftest import make_manifest, write_raster
from habitat.archive import Archive
from habitat.archive.store import LocalArtifactStore
from habitat.contracts import RawManifest, TimePrecision
from habitat.fetch.connectors import ConnectorRequest
from habitat.fetch.connectors import jrc_gsw_monthly
from habitat.grid import parse_cell_id, transformer
from habitat.normalize.router import normalize
from habitat.normalize.rows import QuarantineError
from habitat.normalize.sources.jrc_gsw_monthly import normalize_jrc_gsw_monthly
from habitat.sources import SOURCES

PIXEL = 0.00025
WEST, NORTH = 36.9, -1.44
SIZE = 160
BBOX = (WEST, NORTH - SIZE * PIXEL, WEST + SIZE * PIXEL, NORTH)
MARCH_2011 = datetime(2011, 3, 1, tzinfo=UTC)
LAST_MODIFIED = "Sat, 05 Jan 2019 10:21:36 GMT"


def water_tile(tmp_path: Path, codes: np.ndarray, crs="EPSG:4326", name="2011_03-0000320000-0000840000.tif") -> str:
    return write_raster(tmp_path / name, codes.astype(np.uint8), crs, from_origin(WEST, NORTH, PIXEL, PIXEL))


def west_column_of_water() -> np.ndarray:
    """Water in the first 8 pixel columns, land elsewhere, and no data in the last 20 rows."""
    codes = np.ones((SIZE, SIZE), np.uint8)
    codes[:, :8] = 2
    codes[-20:, :] = 0
    return codes


def jrc_manifest(path: str):
    return make_manifest(
        "jrc_gsw_monthly", {"water": path}, MARCH_2011, datetime(2011, 4, 1, tzinfo=UTC),
        item_id="2011_03-0000320000-0000840000", product="gsw-monthly-history-v1.4",
        precision=TimePrecision.COMPOSITE, processing_version="1.4@2019-01-05T10:21:36Z",
        available_at=datetime(2019, 1, 5, 10, 21, 36, tzinfo=UTC),
    )


def test_tile_path_from_a_bbox():
    assert jrc_gsw_monthly.tile_offsets((36.8, -1.6, 37.1, -1.3)) == [(320000, 840000)]
    assert jrc_gsw_monthly.tile_url(date(2011, 3, 1), (320000, 840000)) == (
        "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GSWE/MonthlyHistory/LATEST/tiles/"
        "2011/2011_03/2011_03-0000320000-0000840000.tif"
    )
    assert jrc_gsw_monthly.tile_offsets((29.5, -0.5, 30.5, 0.5)) == [
        (280000, 800000), (280000, 840000), (320000, 800000), (320000, 840000),
    ]


def test_fraction_valid_fraction_and_distance(tmp_path, grid):
    manifest = jrc_manifest(water_tile(tmp_path, west_column_of_water()))

    batch = normalize_jrc_gsw_monthly(manifest, LocalArtifactStore(), grid, BBOX)

    rows = batch.table.to_pandas()
    assert set(rows["variable"]) == {"surface_water_fraction", "distance_to_surface_water_m"}
    fractions = rows[rows["variable"] == "surface_water_fraction"].set_index("cell_id")
    distances = rows[rows["variable"] == "distance_to_surface_water_m"].set_index("cell_id")
    assert fractions["value"].dropna().between(0, 1).all()
    assert (fractions["valid_fraction"] <= 1).all() and (fractions["pixel_count"] > 0).all()
    assert set(rows["stat"]) == {"mean", "centroid"} and set(rows["source_resolution_m"]) == {30.0}

    east_cell = fractions["value"].dropna().idxmin()
    assert fractions.loc[east_cell, "value"] == 0
    assert distances.loc[east_cell, "value"] == pytest.approx(geodesic_to_water(grid, east_cell), rel=0.01)


def geodesic_to_water(grid, cell_id: str) -> float:
    row, col = parse_cell_id(cell_id)
    x, y = grid.cell_centres_xy(np.array([row]), np.array([col]))
    lon, lat = transformer(grid.crs, "EPSG:4326").transform(x[0], y[0])
    water_lon = WEST + 7.5 * PIXEL
    water_rows = np.arange(SIZE - 20)
    water_lats = NORTH - (water_rows + 0.5) * PIXEL
    _, _, distances = Geod(ellps="WGS84").inv(
        np.full(water_lats.shape, lon), np.full(water_lats.shape, lat), np.full(water_lats.shape, water_lon), water_lats
    )
    return float(distances.min())


def test_a_month_without_water_gives_null_distances_with_a_flag(tmp_path, grid):
    manifest = jrc_manifest(water_tile(tmp_path, np.ones((SIZE, SIZE), np.uint8)))

    rows = normalize_jrc_gsw_monthly(manifest, LocalArtifactStore(), grid, BBOX).table.to_pandas()

    distances = rows[rows["variable"] == "distance_to_surface_water_m"]
    assert distances["value"].isna().all()
    assert set(distances["quality_flag"]) == {"no_water_observed"}


def test_the_value_3_is_quarantined(tmp_path, grid):
    codes = west_column_of_water()
    codes[0, 0] = 3

    with pytest.raises(QuarantineError, match="3"):
        normalize_jrc_gsw_monthly(jrc_manifest(water_tile(tmp_path, codes)), LocalArtifactStore(), grid, BBOX)


def test_a_projected_tile_is_quarantined(tmp_path, grid):
    path = write_raster(tmp_path / "projected.tif", west_column_of_water(), "EPSG:32737", from_origin(0, 0, 30, 30))

    with pytest.raises(QuarantineError, match="EPSG:4326"):
        normalize_jrc_gsw_monthly(jrc_manifest(path), LocalArtifactStore(), grid, BBOX)


def jrc_server(existing: set[str], heads: list | None = None):
    def handle(request: httpx.Request) -> httpx.Response:
        if heads is not None:
            heads.append(str(request.url))
        month = str(request.url).rsplit("/", 1)[1][:7]
        if month not in existing:
            return httpx.Response(404)
        return httpx.Response(200, headers={"last-modified": LAST_MODIFIED})

    return handle


@pytest.fixture
def clipped_tiles(tmp_path, monkeypatch):
    tile = water_tile(tmp_path, west_column_of_water(), name="source.tif")
    clipped = []

    def clip(href, bbox, target):
        clipped.append(href)
        shutil.copyfile(tile, target)
        return target.stat().st_size

    monkeypatch.setattr(jrc_gsw_monthly, "clip_to_bbox", clip)
    return clipped


def request(start=date(2011, 3, 1), end=date(2011, 3, 31), **changes):
    return ConnectorRequest(bbox=BBOX, start=start, end=end, **changes)


def test_connector_archives_one_clip_per_month_and_tile(mock_http, clipped_tiles, grid):
    mock_http(jrc_server({"2011_03"}))
    archive = Archive()

    [manifest] = jrc_gsw_monthly.fetch_jrc_gsw_monthly(request(), archive).manifests

    assert RawManifest.model_validate(manifest.model_dump(mode="json")) == manifest
    item = manifest.extensions
    assert item.source_item_id == "2011_03-0000320000-0000840000"
    assert item.processing_version == "1.4@2019-01-05T10:21:36Z"
    assert item.available_at == datetime(2019, 1, 5, 10, 21, 36, tzinfo=UTC)
    assert (item.time_start, item.time_end) == (MARCH_2011, datetime(2011, 4, 1, tzinfo=UTC))
    assert item.time_precision is TimePrecision.COMPOSITE
    assert item.source_key == "jrc_gsw_monthly:2011_03-0000320000-0000840000:" + ",".join(f"{v:.5f}" for v in BBOX)
    assert manifest.storage.format == SOURCES["jrc_gsw_monthly"].storage_format
    assert clipped_tiles[0].endswith("2011_03-0000320000-0000840000.tif")
    assert normalize(manifest, archive.store, grid, BBOX).table.num_rows > 0


def test_months_after_the_last_release_give_a_warning_and_no_request(mock_http, clipped_tiles):
    heads = []
    mock_http(jrc_server({"2021_12"}, heads))

    result = jrc_gsw_monthly.fetch_jrc_gsw_monthly(request(date(2021, 12, 1), date(2022, 2, 28)), Archive())

    assert len(result.manifests) == 1 and len(heads) == 1
    assert any("2021-12" in warning for warning in result.warnings)


def test_a_missing_month_gives_a_warning(mock_http, clipped_tiles):
    mock_http(jrc_server(set()))

    result = jrc_gsw_monthly.fetch_jrc_gsw_monthly(request(), Archive())

    assert result.manifests == [] and clipped_tiles == []
    assert "2011_03" in result.warnings[0]


def test_item_limit_cache_and_ingested_items(mock_http, clipped_tiles):
    mock_http(jrc_server({"2011_01", "2011_02", "2011_03"}))
    archive = Archive()

    first = jrc_gsw_monthly.fetch_jrc_gsw_monthly(request(date(2011, 1, 1), date(2011, 3, 31), max_items=2), archive)
    again = jrc_gsw_monthly.fetch_jrc_gsw_monthly(request(date(2011, 1, 1), date(2011, 2, 28)), archive)
    skipped = jrc_gsw_monthly.fetch_jrc_gsw_monthly(request(), Archive(), lambda item, version, status: True)

    assert len(first.manifests) == 2 and any("limit" in warning for warning in first.warnings)
    assert again.manifests == first.manifests
    assert skipped.manifests == []
    assert len(clipped_tiles) == 2

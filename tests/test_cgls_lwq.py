from datetime import UTC, date, datetime

import numpy as np
import pystac
import pytest
from rasterio.transform import from_origin

from conftest import make_manifest, write_raster
from habitat.archive import Archive
from habitat.archive.store import LocalArtifactStore
from habitat.contracts import TimePrecision
from habitat.fetch.connectors import ConnectorRequest
from habitat.fetch.connectors import cgls_lwq as connector
from habitat.fetch.connectors import stac
from habitat.normalize.router import normalize
from habitat.sources import SOURCES

# The asset files use the netCDF float fill value as nodata, although the STAC item says "nan".
FILL = np.float32(9.969209968386869e36)
PIXEL_DEGREES = 0.002232142857138797
# Lake Naivasha, Kenya.
ORIGIN = (36.25, -0.70)
BBOX = (36.25, -0.90, 36.45, -0.70)
TURBIDITY_NTU = 12.5
TSI = 61.0


def lake_rasters(tmp_path):
    size = 90
    water = np.zeros((size, size), bool)
    water[20:70, 20:70] = True
    transform = from_origin(*ORIGIN, PIXEL_DEGREES, PIXEL_DEGREES)

    def layer(name, value):
        data = np.where(water, value, FILL).astype(np.float32)
        return write_raster(tmp_path / f"{name}.tif", data, "EPSG:4326", transform, float(FILL))

    return {"turbidity": layer("turbidity_mean", TURBIDITY_NTU), "trophic_state_index": layer("tsi", TSI)}


def stac_item(tmp_path, properties=None):
    item = pystac.Item(
        "ca9f6ecf-2c68-5027-9ae6-a5c9672e146d", None, [28.85, -5.28, 38.80, 2.26], datetime(2011, 1, 1, tzinfo=UTC),
        properties if properties is not None else {
            "created": "2020-07-14T12:48:57.130374Z", "start_datetime": "2011-01-01T00:00:00Z",
            "end_datetime": "2011-01-10T23:59:59Z", "odc:dataset_version": "v1.3.0", "platform": "envisat",
        },
        collection="cgls_lwq300_2002_2012",
    )
    for name, path in lake_rasters(tmp_path).items():
        item.add_asset(connector.ASSETS[name], pystac.Asset(path))
    return item


def test_cgls_lwq_is_registered_for_water_quality_observations():
    source = SOURCES["cgls_lwq"]

    assert "water_quality_observations" in source.data_kinds
    assert source.needs_area_and_dates


def test_s3_hrefs_become_public_https_urls_of_an_allowed_host():
    href = connector.https_href("s3://deafrica-input-datasets/cgls_lwq300_2002_2012/x021/y007/a_turbidity_mean.tif")

    assert href == (
        "https://deafrica-input-datasets.s3.af-south-1.amazonaws.com/cgls_lwq300_2002_2012/x021/y007/"
        "a_turbidity_mean.tif"
    )
    stac.check_asset_href(href)
    with pytest.raises(ValueError):
        connector.https_href("s3://another-bucket/file.tif")


def test_the_item_gives_a_composite_with_its_creation_date(tmp_path):
    description = connector.describe_cgls_lwq(stac_item(tmp_path))

    assert description["precision"] is TimePrecision.COMPOSITE
    assert description["time_start"] == datetime(2011, 1, 1, tzinfo=UTC)
    assert description["time_end"] == datetime(2011, 1, 10, 23, 59, 59, tzinfo=UTC)
    assert description["available_at"] == datetime(2020, 7, 14, 12, 48, 57, 130374, tzinfo=UTC)
    assert description["processing_version"] == "v1.3.0"
    assert description["rights"].license == "CC-BY-4.0"


def test_an_item_without_a_creation_date_has_no_publication_date(tmp_path):
    item = stac_item(tmp_path, {"start_datetime": "2011-01-01T00:00:00Z", "end_datetime": "2011-01-10T23:59:59Z",
                                "odc:dataset_version": "v1.3.0"})

    assert connector.describe_cgls_lwq(item)["available_at"] is None


def test_the_scene_is_clipped_archived_and_normalized(tmp_path, monkeypatch, grid):
    item = stac_item(tmp_path)
    monkeypatch.setattr(connector, "search_cgls_lwq", lambda *args: [item])
    monkeypatch.setattr(stac, "check_asset_href", lambda href: None)
    archive = Archive()

    request = ConnectorRequest(bbox=BBOX, start=date(2011, 1, 1), end=date(2011, 1, 10))
    result = connector.fetch_cgls_lwq(request, archive)

    assert not result.errors, result.errors
    [manifest] = result.manifests
    assert manifest.extensions.source_id == "cgls_lwq"
    assert set(manifest.extensions.assets) == {"turbidity", "trophic_state_index"}

    rows = normalize(manifest, archive.store, grid, BBOX).table.to_pandas()
    turbidity = rows[rows["variable"] == "water_turbidity"]
    assert np.allclose(turbidity["value"], TURBIDITY_NTU)
    assert set(turbidity["unit"]) == {"NTU"}
    assert np.allclose(rows[rows["variable"] == "trophic_state_index"]["value"], TSI)
    assert set(rows["time_precision"]) == {"composite"}
    assert set(rows["source_resolution_m"]) == {300.0}


def test_fill_values_are_never_averaged(tmp_path, grid):
    assets = lake_rasters(tmp_path)
    manifest = make_manifest(
        "cgls_lwq", assets, datetime(2011, 1, 1, tzinfo=UTC), datetime(2011, 1, 10, 23, 59, 59, tzinfo=UTC),
        item_id="cgls-test", product=connector.PRODUCT, precision=TimePrecision.COMPOSITE,
    )

    rows = normalize(manifest, LocalArtifactStore(), grid).table.to_pandas()

    assert rows["value"].max() < 100
    assert (rows["valid_fraction"] < 1).any()

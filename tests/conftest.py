import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from habitat import config
from habitat.archive.store import LocalArtifactStore
from habitat.fetch import http
from habitat.contracts import (
    Coverage,
    ProductStatus,
    SourceItem,
    RawManifest,
    Rights,
    SourceRef,
    StorageRef,
    TimePrecision,
)
from habitat.db import DATABASE_URL_VARIABLE, connect, database_url, migration_files
from habitat.grid import default_grid

# A point in northern Namibia (UTM 33S), close to Etosha.
UTM_33S = "EPSG:32733"
UTM_ORIGIN = (600_000.0, 7_900_000.0)


def write_raster(path: Path, data: np.ndarray, crs: str, transform, nodata=None) -> str:
    with rasterio.open(
        path, "w", driver="GTiff", width=data.shape[1], height=data.shape[0], count=1,
        dtype=data.dtype, crs=crs, transform=transform, nodata=nodata,
    ) as target:
        target.write(data, 1)
    return str(path)


def make_manifest(
    source_id: str,
    assets: dict[str, str],
    time_start: datetime,
    time_end: datetime | None = None,
    item_id: str = "item-1",
    product: str = "test-product",
    precision: TimePrecision = TimePrecision.INSTANT,
    processing_version: str = "1",
    status: ProductStatus = ProductStatus.FINAL,
    available_at: datetime | None = None,
    properties: dict | None = None,
    storage_format: str = "geotiff",
) -> RawManifest:
    """A manifest for an archived item. Asset values that are existing files are put into the local archive."""
    time_end = time_end or time_start
    artifact_id = item_id.replace("/", "_")
    files = {Path(path).name: Path(path) for path in assets.values() if Path(path).is_file()}
    stored = LocalArtifactStore().put(artifact_id, "1", files, storage_format) if files else None
    return RawManifest(
        artifact_id=artifact_id,
        version="1",
        created_at=datetime(2026, 10, 3, tzinfo=UTC),
        access_scope="public",
        source=SourceRef(name=source_id),
        storage=stored.storage if stored else StorageRef(uri=f"artifact://{artifact_id}/1", format=storage_format),
        checksum=stored.checksum if stored else "sha256:test",
        retrieved_at=datetime(2026, 10, 3, tzinfo=UTC),
        coverage=Coverage(start=time_start, end=time_end),
        rights=Rights(),
        extensions=SourceItem(
            source_id=source_id,
            product=product,
            source_item_id=item_id,
            source_key=f"{source_id}:{item_id}",
            time_start=time_start,
            time_end=time_end,
            time_precision=precision,
            available_at=available_at or time_end,
            processing_version=processing_version,
            product_status=status,
            assets={name: Path(path).name for name, path in assets.items()},
            properties=properties or {},
        ),
    )


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path):
    """Every test writes below its own tmp_path, never to the project data folder."""
    config.reset()
    yield config.configure(data_dir=tmp_path / "data")
    config.reset()


def refuse_network(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"unit test tried a network call: {request.method} {request.url}")


@pytest.fixture(autouse=True)
def no_network(request, monkeypatch):
    """Unit tests make no network calls. Tests with the `live` marker use the real network."""
    if request.node.get_closest_marker("live") is None:
        monkeypatch.setattr(http, "TRANSPORT", httpx.MockTransport(refuse_network))


@pytest.fixture
def mock_http(monkeypatch):
    """Route every request of the fetch layer to `handler(request) -> httpx.Response`."""

    def install(handler):
        requests = []

        def record(request):
            requests.append(request)
            return handler(request)

        monkeypatch.setattr(http, "TRANSPORT", httpx.MockTransport(record))
        return requests

    return install


def pytest_collection_modifyitems(config, items):
    if os.environ.get("HABITAT_LIVE_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="live test; set HABITAT_LIVE_TESTS=1")
    for item in items:
        if item.get_closest_marker("live"):
            item.add_marker(skip)


@pytest.fixture
def grid():
    return default_grid()


@pytest.fixture
def sentinel2_scene(tmp_path):
    """3 km x 3 km scene. The west half is clear, the east half is cloud (SCL 9)."""
    size_10m, size_20m = 300, 150
    transform_10m = from_origin(*UTM_ORIGIN, 10, 10)
    transform_20m = from_origin(*UTM_ORIGIN, 20, 20)

    scl = np.full((size_20m, size_20m), 4, dtype=np.uint8)
    scl[:, size_20m // 2 :] = 9

    # Baseline >= 04.00 adds 1000 to every digital number.
    assets = {
        "green": write_raster(tmp_path / "B03.tif", np.full((size_10m, size_10m), 1500, np.uint16), UTM_33S, transform_10m, 0),
        "red": write_raster(tmp_path / "B04.tif", np.full((size_10m, size_10m), 2000, np.uint16), UTM_33S, transform_10m, 0),
        "nir": write_raster(tmp_path / "B08.tif", np.full((size_10m, size_10m), 6000, np.uint16), UTM_33S, transform_10m, 0),
        "swir16": write_raster(tmp_path / "B11.tif", np.full((size_20m, size_20m), 4000, np.uint16), UTM_33S, transform_20m, 0),
        "scl": write_raster(tmp_path / "SCL.tif", scl, UTM_33S, transform_20m, 0),
    }
    return make_manifest(
        "sentinel2",
        assets,
        datetime(2024, 3, 9, 8, 47, tzinfo=UTC),
        item_id="S2B_TEST",
        product="sentinel-2-l2a",
        processing_version="05.10",
        properties={"boa_add_offset": -1000.0},
    )


@pytest.fixture
def database():
    """A throwaway schema with the migrations applied. Skips when no database is configured."""
    try:
        url = database_url()
    except RuntimeError:
        pytest.skip(f"{DATABASE_URL_VARIABLE} is not set")

    schema = f"habitat_test_{uuid4().hex[:10]}"
    connection = connect(url)
    connection.execute(f"CREATE SCHEMA {schema}")
    connection.execute(f"SET search_path = {schema}, extensions")
    try:
        for migration in migration_files():
            if migration.name != "001_roles.sql":
                connection.execute(migration.read_text())
        yield connection
    finally:
        connection.execute(f"DROP SCHEMA {schema} CASCADE")
        connection.close()

import gzip
from datetime import date
from pathlib import Path

import httpx
import numpy as np
from rasterio.transform import from_origin

from conftest import write_raster
from habitat.archive import Archive
from habitat.contracts import ProductStatus, RawManifest
from habitat.fetch.connectors import ConnectorRequest
from habitat.fetch.connectors import chirps
from habitat.normalize.router import normalize
from habitat.sources import SOURCES

BBOX = (36.8, -1.6, 37.1, -1.3)
DAY = date(2024, 2, 17)


def gzipped_tif(tmp_path: Path) -> bytes:
    rainfall = np.full((20, 20), 4.5, dtype=np.float32)
    tif = write_raster(tmp_path / "rain.tif", rainfall, "EPSG:4326", from_origin(36.5, -1.0, 0.05, 0.05), -9999)
    return gzip.compress(Path(tif).read_bytes())


def server(final: set[date], preliminary: set[date], body: bytes, gets: list | None = None):
    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        day = date(*(int(part) for part in url.rsplit("chirps-v2.0.", 1)[1].split(".")[:3]))
        exists = day in (preliminary if "/prelim/" in url else final)
        if not exists:
            return httpx.Response(404)
        if request.method == "HEAD":
            return httpx.Response(200, headers={"last-modified": "Tue, 05 Mar 2024 10:00:00 GMT"})
        if gets is not None:
            gets.append(url)
        return httpx.Response(200, content=body)

    return handle


def request(start=DAY, end=DAY, **changes):
    return ConnectorRequest(bbox=BBOX, start=start, end=end, **changes)


def test_final_file_wins_over_preliminary(mock_http, tmp_path):
    mock_http(server({DAY}, {DAY}, gzipped_tif(tmp_path)))

    [manifest] = chirps.fetch_chirps(request(), Archive()).manifests

    assert manifest.extensions.product_status is ProductStatus.FINAL
    assert "/prelim/" not in manifest.source.url


def test_preliminary_is_used_when_final_is_missing(mock_http, tmp_path):
    mock_http(server(set(), {DAY}, gzipped_tif(tmp_path)))

    [manifest] = chirps.fetch_chirps(request(), Archive()).manifests

    assert manifest.extensions.product_status is ProductStatus.PRELIMINARY
    assert manifest.extensions.source_key.endswith(":preliminary")


def test_neither_file_gives_no_manifest(mock_http):
    mock_http(server(set(), set(), b""))

    result = chirps.fetch_chirps(request(), Archive())

    assert result.manifests == [] and result.errors == []


def test_manifest_keeps_the_gzip_file_and_normalizes(mock_http, tmp_path, grid):
    mock_http(server({DAY}, set(), gzipped_tif(tmp_path)))
    archive = Archive()

    [manifest] = chirps.fetch_chirps(request(), archive).manifests

    assert RawManifest.model_validate(manifest.model_dump(mode="json")) == manifest
    assert manifest.storage.uri.startswith("artifact://chirps-final-20240217/")
    assert manifest.storage.format == SOURCES["chirps"].storage_format
    assert manifest.extensions.assets == {"precipitation": "chirps-v2.0.2024.02.17.tif.gz"}
    assert archive.store.open(manifest, "precipitation").read_bytes()[:2] == b"\x1f\x8b"
    rows = normalize(manifest, archive.store, grid, BBOX).table.to_pandas()
    assert np.allclose(rows["value"], 4.5)


def test_day_limit_and_cache(mock_http, tmp_path):
    gets = []
    days = {date(2024, 2, d) for d in range(15, 20)}
    mock_http(server(days, set(), gzipped_tif(tmp_path), gets))
    archive = Archive()

    first = chirps.fetch_chirps(request(date(2024, 2, 15), date(2024, 2, 19), max_days=3), archive)
    again = chirps.fetch_chirps(request(date(2024, 2, 15), date(2024, 2, 17)), archive)

    assert len(first.manifests) == 3
    assert any("day limit" in warning for warning in first.warnings)
    assert again.manifests == first.manifests
    assert len(gets) == 3


def test_outside_coverage_makes_no_request(mock_http):
    calls = mock_http(lambda request: httpx.Response(500))

    result = chirps.fetch_chirps(ConnectorRequest(bbox=(-85, 51, -84, 52), start=DAY, end=DAY), Archive())

    assert result.manifests == [] and calls == []
    assert "50°" in result.warnings[0]


def test_html_payload_is_rejected(mock_http):
    mock_http(server({DAY}, set(), b"<html>login</html>"))

    result = chirps.fetch_chirps(request(), Archive())

    assert result.manifests == []
    assert "gzip" in result.warnings[0]


def test_already_ingested_day_is_skipped(mock_http, tmp_path):
    gets = []
    mock_http(server({DAY}, set(), gzipped_tif(tmp_path), gets))

    result = chirps.fetch_chirps(request(), Archive(), lambda item, version, status: status == "final")

    assert result.manifests == [] and gets == []

import json
import shutil
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pandas as pd
import pytest

from conftest import make_manifest
from habitat.archive import Archive
from habitat.contracts import ProductStatus, TimePrecision
from habitat.fetch.connectors import ConnectorRequest
from habitat.fetch.connectors import wqp as connector
from habitat.normalize.router import normalize
from habitat.normalize.rows import MONITORING_SITES, SITE_OBSERVATIONS, QuarantineError
from habitat.normalize.sources import wqp
from habitat.sources import SOURCES

FIXTURES = Path(__file__).parent / "fixtures" / "water_quality"
RESULTS = FIXTURES / "wqp_result.csv"
STATIONS = FIXTURES / "wqp_station.csv"
BBOX = (-77.2, 38.8, -76.9, 39.0)


def wqp_manifest(tmp_path, results=RESULTS, stations=STATIONS, status=ProductStatus.PRELIMINARY):
    assets = {"results": shutil.copy(results, tmp_path / "results.csv")}
    if stations is not None:
        assets["stations"] = shutil.copy(stations, tmp_path / "stations.csv")
    return make_manifest(
        "wqp", assets, datetime(2023, 3, 13, tzinfo=UTC), datetime(2023, 6, 29, tzinfo=UTC),
        item_id="wqp:test", product=connector.PRODUCT, precision=TimePrecision.COMPOSITE,
        processing_version="2026-07-21T00:00:00+00:00", status=status,
        available_at=datetime(2026, 7, 21, tzinfo=UTC), storage_format="csv",
    )


def rows_by(table, column="source_record_id"):
    return {row[column]: row for row in table.to_pylist()}


@pytest.fixture
def batch(tmp_path, grid):
    return normalize(wqp_manifest(tmp_path), Archive().store, grid)


def record_id(characteristic, site):
    frame = pd.read_csv(RESULTS, dtype=str)
    match = frame[(frame["Result_Characteristic"] == characteristic) & (frame["Location_Identifier"] == site)]
    return match["Result_MeasureIdentifier"].iloc[0]


def row(batch, characteristic, site):
    return rows_by(batch.table)[record_id(characteristic, site)]


def test_wqp_is_registered_for_water_quality_samples():
    source = SOURCES["wqp"]

    assert "water_quality_samples" in source.data_kinds
    assert source.needs_area_and_dates
    assert source.normalizer is not None


def test_the_fixture_normalizes_to_site_observations(batch):
    assert batch.family == SITE_OBSERVATIONS
    assert batch.table.num_rows == 19
    assert batch.references[MONITORING_SITES].num_rows == 17
    assert set(batch.table.column("source_id").to_pylist()) == {"wqp"}


def test_an_unmapped_characteristic_is_dropped(batch):
    assert "USGS-01646500" not in {site.split(":", 1)[1] for site in batch.table.column("site_id").to_pylist()}


def test_a_not_detected_row_keeps_its_censoring_limit(batch):
    lead = row(batch, "Lead", "USGS-01651800")

    assert (lead["parameter"], lead["fraction"], lead["censored"]) == ("lead", "dissolved", "left")
    assert lead["value"] == pytest.approx(0.03)
    assert lead["detection_limit"] == pytest.approx(0.03)
    assert lead["quality_flag"] == "ok"


def test_a_censored_text_value_gives_its_limit(batch):
    ecoli = row(batch, "Escherichia coli", "DOEE-RCR01")

    assert (ecoli["parameter"], ecoli["censored"], ecoli["value"]) == ("ecoli_mpn", "left", 1.0)
    assert ecoli["unit"] == "MPN/100mL"


def test_a_censored_value_is_never_zero(batch):
    censored = [row for row in batch.table.to_pylist() if row["censored"] != "none"]

    assert censored
    assert all(row["value"] is None or row["value"] > 0 for row in censored)


def test_the_unit_selects_the_parameter(batch):
    assert row(batch, "Turbidity", "DOEE-TCO06")["parameter"] == "turbidity_fnu"
    assert row(batch, "Turbidity", "CBP_WQX-ANA0082")["parameter"] == "turbidity"
    assert row(batch, "Dissolved oxygen (DO)", "CHESAPEAKEMONITORINGCOOP-CMC.ARK.BWP.1")["parameter"] == (
        "dissolved_oxygen_saturation"
    )
    assert row(batch, "Escherichia coli", "USGS-01651812")["parameter"] == "ecoli"


def test_speciation_converts_nitrate_to_nitrogen(batch):
    nitrate = row(batch, "Nitrate", "USGS-01649500")

    assert nitrate["value"] == pytest.approx(1.55 * 14.007 / 62.004)
    assert nitrate["unit"] == "mg/L as N"


def test_ph_without_a_unit_is_accepted(batch):
    ph = row(batch, "pH", "CBP_WQX-NWA0016")

    assert (ph["value"], ph["unit"], ph["fraction"]) == (7.2, "pH", "not_applicable")


def test_a_time_with_a_zone_is_an_instant_in_utc(batch):
    temperature = row(batch, "Temperature, water", "CHESAPEAKEMONITORINGCOOP-CMC.PRK.PR-7")

    assert temperature["time_precision"] == "instant"
    assert temperature["time_start"] == datetime(2023, 6, 28, 11, 40, tzinfo=UTC)
    assert temperature["time_end"] == temperature["time_start"]
    assert temperature["sample_depth_m"] == pytest.approx(0.3)


def test_a_date_without_a_time_is_a_day(batch):
    ammonia = row(batch, "Ammonia", "NARS_WQX-NRS_MD-10078")

    assert ammonia["time_precision"] == "day"
    assert ammonia["time_start"] == datetime(2023, 6, 13, tzinfo=UTC)
    assert ammonia["time_end"] == datetime(2023, 6, 14, tzinfo=UTC)
    assert ammonia["detection_limit"] == pytest.approx(0.0015)


def test_status_and_publication_date_come_from_each_row(batch):
    provisional = row(batch, "Temperature, water", "USGS-01651812")
    final = row(batch, "pH", "CBP_WQX-NWA0016")

    assert (provisional["product_status"], provisional["quality_flag"]) == ("preliminary", "preliminary")
    assert final["product_status"] == "final"
    assert final["available_at"] == datetime(2025, 9, 26, 9, 9, 22, tzinfo=UTC)
    assert provisional["available_at"] == datetime(2026, 7, 20, tzinfo=UTC)


def test_sites_are_namespaced_and_typed(batch):
    sites = rows_by(batch.references[MONITORING_SITES], "site_id")

    lake = sites["wqp:DOEE-TCO06"]
    assert lake["water_body_type"] == "lake"
    assert sites["wqp:USGS-01651800"]["water_body_type"] == "river"
    assert sites["wqp:21VASWCB-PMS10"]["water_body_type"] == "other"
    assert sites["wqp:21VASWCB-PMS10"]["coordinate_uncertainty_m"] == pytest.approx(30.48)
    assert sites["wqp:NARS_WQX-NRS_MD-10078"]["elevation_m"] == pytest.approx(53.31)
    assert json.loads(lake["attributes"])["location_type"] == "Lake"
    assert lake["cell_id"].startswith("E1K-")


def test_unmapped_fields_are_kept_in_attributes(batch):
    attributes = json.loads(row(batch, "Lead", "USGS-01648010")["attributes"])

    assert attributes["Result_SampleFraction"] == "Filtered field and/or lab"
    assert attributes["local_start_time"] == "09:20:00"


def test_the_station_file_is_optional(tmp_path, grid):
    batch = wqp.normalize_wqp(wqp_manifest(tmp_path, stations=None), Archive().store, grid)

    assert batch.references[MONITORING_SITES].num_rows == 17


def test_an_unknown_unit_quarantines_the_item(tmp_path, grid):
    frame = pd.read_csv(RESULTS, dtype=str)
    frame.loc[frame["Result_Characteristic"] == "Specific conductance", "Result_MeasureUnit"] = "ppm"
    broken = tmp_path / "broken.csv"
    frame.to_csv(broken, index=False)

    with pytest.raises(QuarantineError, match="ppm"):
        wqp.normalize_wqp(wqp_manifest(tmp_path, results=broken), Archive().store, grid)


def test_a_station_without_coordinates_quarantines_the_item(tmp_path, grid):
    frame = pd.read_csv(RESULTS, dtype=str)
    frame.loc[0, ["Location_LatitudeStandardized", "Location_LongitudeStandardized"]] = None
    broken = tmp_path / "broken.csv"
    frame.to_csv(broken, index=False)

    with pytest.raises(QuarantineError, match="coordinates"):
        wqp.normalize_wqp(wqp_manifest(tmp_path, results=broken, stations=None), Archive().store, grid)


def test_an_unknown_datum_quarantines_the_item(tmp_path, grid):
    frame = pd.read_csv(RESULTS, dtype=str)
    frame.loc[0, "Location_HorzCoordStandardizedDatum"] = "NAD27"
    broken = tmp_path / "broken.csv"
    frame.to_csv(broken, index=False)

    with pytest.raises(QuarantineError, match="NAD27"):
        wqp.normalize_wqp(wqp_manifest(tmp_path, results=broken, stations=None), Archive().store, grid)


def test_a_file_without_mapped_parameters_quarantines_the_item(tmp_path, grid):
    frame = pd.read_csv(RESULTS, dtype=str)
    unmapped = tmp_path / "unmapped.csv"
    frame[frame["Result_Characteristic"].str.startswith("Bottle")].to_csv(unmapped, index=False)

    with pytest.raises(QuarantineError, match="no mapped parameter"):
        wqp.normalize_wqp(wqp_manifest(tmp_path, results=unmapped, stations=None), Archive().store, grid)


def test_an_aoi_keeps_only_its_stations(tmp_path, grid):
    batch = wqp.normalize_wqp(wqp_manifest(tmp_path), Archive().store, grid, (-77.05, 38.85, -76.9, 39.0))

    longitudes = batch.table.column("longitude").to_pylist()
    assert longitudes and all(-77.05 <= value <= -76.9 for value in longitudes)


def wqp_server(results: bytes, stations: bytes):
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "www.waterqualitydata.us"
        body = results if request.url.path.endswith("/Result/search") else stations
        return httpx.Response(200, content=body, headers={"content-type": "text/csv"})

    return handle


def request(**changes):
    values = {"bbox": BBOX, "start": date(2023, 6, 1), "end": date(2023, 6, 30)}
    return ConnectorRequest(**(values | changes))


def test_the_connector_archives_the_result_and_station_files(mock_http):
    requests = mock_http(wqp_server(RESULTS.read_bytes(), STATIONS.read_bytes()))
    archive = Archive()

    result = connector.fetch_wqp(request(parameters=("ph", "lead")), archive)

    assert not result.errors, result.errors
    [manifest] = result.manifests
    item = manifest.extensions
    assert set(item.assets) == {"results", "stations"}
    assert item.kind == "tabular"
    assert item.available_at == datetime(2026, 7, 21, tzinfo=UTC)
    assert item.processing_version == "2026-07-21T00:00:00+00:00"
    assert item.product_status is ProductStatus.PRELIMINARY
    assert item.source_item_id.startswith("wqp:-77.20000,38.80000,-76.90000,39.00000:2023-06-01:2023-06-30:")
    assert manifest.rights.license == "U.S. public domain"
    query = parse_qs(requests[0].url.query.decode())
    assert query["bBox"] == ["-77.2,38.8,-76.9,39.0"]
    assert query["startDateLo"] == ["06-01-2023"] and query["startDateHi"] == ["06-30-2023"]
    assert set(query["characteristicName"]) == {"pH", "Lead"}
    assert query["dataProfile"] == ["fullPhysChem"]


def test_the_same_query_is_served_from_the_archive(mock_http):
    requests = mock_http(wqp_server(RESULTS.read_bytes(), STATIONS.read_bytes()))
    archive = Archive()

    first = connector.fetch_wqp(request(), archive)
    again = connector.fetch_wqp(request(), archive)

    assert len(requests) == 2
    assert again.manifests[0].extensions.source_key == first.manifests[0].extensions.source_key


def test_an_empty_result_is_a_coverage_gap_not_an_error(mock_http):
    header = RESULTS.read_text().splitlines()[0] + "\n"
    mock_http(wqp_server(header.encode(), b""))

    result = connector.fetch_wqp(request(bbox=(36.8, -1.6, 37.1, -1.3)), Archive())

    assert not result.manifests and not result.errors
    assert "coverage gap" in result.warnings[0]


def test_an_unknown_parameter_is_refused_before_any_request(mock_http):
    requests = mock_http(wqp_server(b"", b""))

    result = connector.fetch_wqp(request(parameters=("glyphosate",)), Archive())

    assert result.errors[0].code == "invalid_request"
    assert not requests


def test_a_full_lastchangedate_text_is_parsed():
    assert connector.parse_change_date("Fri Jan 31 08:59:46 UTC 2025") == datetime(2025, 1, 31, 8, 59, 46, tzinfo=UTC)
    assert connector.parse_change_date("2026-07-20") == datetime(2026, 7, 20, tzinfo=UTC)
    assert connector.parse_change_date(None) is None

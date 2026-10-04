from datetime import UTC, datetime, timedelta

import pandas as pd

from conftest import make_manifest
from habitat.catalog.publish import ROW_GRAIN, publish_series_version
from habitat.catalog.store import PostgresCatalog
from habitat.contracts import SITE_OBSERVATIONS_SCHEMA, ProductStatus, TimePrecision
from habitat.normalize.rows import MONITORING_SITES, SITE_OBSERVATIONS, series_id
from habitat.normalize.water_quality import site_observations_batch
from habitat.storage.series import FAMILY_SUMMARIES, REFERENCE_UPSERTS, SeriesStore

SAMPLED = datetime(2023, 6, 14, 14, 30, tzinfo=UTC)


def station(name="Little Falls"):
    return pd.DataFrame(
        [{"site_id": "wqp:USGS-01646500", "source_id": "wqp", "local_site_id": "USGS-01646500", "site_name": name,
          "water_body_type": "river", "longitude": -77.1276, "latitude": 38.9498, "attributes": '{"huc8": "02070008"}'}]
    )


def sample(record_id, value, status, available_at, parameter="lead", fraction="dissolved"):
    return {
        "source_record_id": record_id, "dataset_id": "wqp--wqp-results--ease2-global-1km",
        "site_id": "wqp:USGS-01646500", "time_start": SAMPLED, "time_end": SAMPLED, "time_precision": "instant",
        "available_at": available_at, "source_id": "wqp", "source_item_id": "wqp:test", "processing_version": "1",
        "product_status": status, "mapping_version": "wqp-wqx3-v1", "parameter": parameter, "fraction": fraction,
        "value": value, "censored": "none", "detection_limit": None, "detection_limit_type": None,
        "sample_depth_m": None, "method": None, "quality_flag": "ok", "attributes": "{}",
    }


def manifest(item_id, status, available_at):
    return make_manifest(
        "wqp", {}, SAMPLED, SAMPLED + timedelta(days=1), item_id=item_id, product="wqp-results",
        precision=TimePrecision.INSTANT, status=status, available_at=available_at, storage_format="csv",
    )


def batch(grid, *samples, name="Little Falls"):
    return site_observations_batch(pd.DataFrame(list(samples)), station(name), grid, "wqp-wqx3-v1")


def test_site_observations_is_a_registered_family():
    assert MONITORING_SITES in REFERENCE_UPSERTS
    assert SITE_OBSERVATIONS in FAMILY_SUMMARIES
    assert SITE_OBSERVATIONS in ROW_GRAIN


def test_the_batch_takes_coordinates_and_cell_from_the_site(grid):
    result = batch(grid, sample("r1", 0.03, "final", SAMPLED))

    row = result.table.to_pylist()[0]
    site = result.references[MONITORING_SITES].to_pylist()[0]
    assert (row["longitude"], row["latitude"], row["cell_id"]) == (site["longitude"], site["latitude"], site["cell_id"])
    assert row["unit"] == "ug/L"
    assert result.family == SITE_OBSERVATIONS


def test_a_final_row_replaces_a_preliminary_row_of_the_same_sample(database, grid):
    store = SeriesStore(database, grid)
    preliminary = manifest("wqp:early", ProductStatus.PRELIMINARY, datetime(2023, 7, 1, tzinfo=UTC))
    final = manifest("wqp:late", ProductStatus.FINAL, datetime(2023, 9, 1, tzinfo=UTC))
    series = series_id(final, grid)

    store.append_batch(series, final, batch(grid, sample("r-final", 0.03, "final", datetime(2023, 9, 1, tzinfo=UTC))))
    store.append_batch(
        series, preliminary,
        batch(grid, sample("r-prelim", 0.05, "preliminary", datetime(2023, 10, 1, tzinfo=UTC)), name="Little Falls PS"),
    )
    publish_series_version(store, PostgresCatalog(database), grid, series, "wqp", "WQP samples", "public")

    rows = database.execute(
        "SELECT value, product_status, water_body_type, dataset_version FROM recipe_site_observations"
    ).fetchall()
    assert rows == [(0.03, "final", "river", "2")]
    assert database.execute("SELECT site_name, cell_id IS NOT NULL FROM monitoring_sites").fetchall() == [
        ("Little Falls PS", True)
    ]


def test_the_summary_lists_parameters_cells_and_dates(database, grid):
    store = SeriesStore(database, grid)
    item = manifest("wqp:one", ProductStatus.FINAL, SAMPLED)
    series = series_id(item, grid)
    store.append_batch(
        series, item,
        batch(grid, sample("r1", 0.03, "final", SAMPLED), sample("r2", 7.2, "final", SAMPLED, "ph", "not_applicable")),
    )

    summary = store.summary(store.latest_version(series))

    assert summary.variables == ["lead", "ph"]
    assert summary.row_count == 2
    assert summary.start == SAMPLED
    assert len(summary.cell_ids) == 1


def test_a_censored_row_keeps_its_limit_in_the_database(database, grid):
    store = SeriesStore(database, grid)
    item = manifest("wqp:censored", ProductStatus.FINAL, SAMPLED)
    censored = sample("r1", 0.03, "final", SAMPLED) | {"censored": "left", "detection_limit": 0.03}
    store.append_batch(series_id(item, grid), item, batch(grid, censored))

    assert database.execute("SELECT value, censored, detection_limit FROM site_observations").fetchall() == [
        (0.03, "left", 0.03)
    ]


def test_schema_columns_match_the_table(database):
    columns = {
        name for (name,) in database.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'site_observations' "
            "AND table_schema = current_schema()"
        )
    }

    assert set(SITE_OBSERVATIONS_SCHEMA.names) | {"series_id", "batch_key"} == columns

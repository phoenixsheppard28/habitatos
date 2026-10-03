from datetime import UTC, datetime

import pystac

from habitat.fetch.stac import published_at

RETRIEVED = datetime(2026, 10, 3, tzinfo=UTC)


def item(properties):
    return pystac.Item("x", None, None, datetime(2024, 2, 2, 7, 41, tzinfo=UTC), properties)


def test_sentinel2_is_public_from_its_generation_time():
    generated = item({"s2:generation_time": "2024-02-02T13:12:12.579537Z"})

    assert published_at(generated, RETRIEVED) == datetime(2024, 2, 2, 13, 12, 12, 579537, tzinfo=UTC)


def test_retrieval_time_is_the_fallback():
    assert published_at(item({}), RETRIEVED) == RETRIEVED

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from conftest import make_manifest
from habitat.contracts import SourceItem


@pytest.mark.parametrize("kind", ["raster", "tabular", "vector", "derived"])
def test_a_source_item_is_raster_tabular_vector_or_derived(kind):
    item = make_manifest("test", {}, datetime(2024, 1, 1, tzinfo=UTC)).extensions

    assert SourceItem.model_validate({**item.model_dump(), "kind": kind}).kind == kind


def test_an_unknown_source_item_kind_is_rejected():
    item = make_manifest("test", {}, datetime(2024, 1, 1, tzinfo=UTC)).extensions

    with pytest.raises(ValidationError):
        SourceItem.model_validate({**item.model_dump(), "kind": "video"})

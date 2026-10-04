import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from conftest import make_manifest
from habitat.catalog.ai import CatalogAssistant, RefusedError
from habitat.catalog.publish import publish_series_version
from habitat.catalog.store import MemoryCatalog, PostgresCatalog
from habitat.contracts import SearchFilters, Tag, TagOrigin, TimePrecision
from habitat.normalize.rows import series_id, to_cell_observations
from habitat.storage.series import SeriesStore


class FakeMessages:
    def __init__(self, answers, refusal=None):
        self.answers = list(answers)
        self.refusal = refusal
        self.requests = []

    def create(self, **request):
        self.requests.append(request)
        text = json.dumps(self.answers.pop(0)) if self.answers else ""
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content=text, refusal=self.refusal),
        )])


def fake_assistant(answers, refusal=None):
    messages = FakeMessages(answers, refusal)
    client = SimpleNamespace(chat=SimpleNamespace(completions=messages))
    return CatalogAssistant(client=client), messages


LABELS = {
    "summary": "Daily rainfall over Etosha.",
    "tags": [
        {"key": "habitat", "value": "salt_pan", "confidence": 0.8, "evidence": "footprint over Etosha Pan"},
        {"key": "habitat", "value": "volcano", "confidence": 0.9, "evidence": "invented"},
    ],
}


def test_parse_question_keeps_species_names_for_later_resolution():
    assistant, messages = fake_assistant([{
        "families": ["animal_locations"],
        "species_names": ["antelope"],
        "start": "2019-01-01T00:00:00Z",
        "end": "2019-12-31T23:59:59Z",
        "variables": [],
        "tags_any": [{"key": "topic", "value": "drought", "confidence": 0.9, "evidence": "question says drought"}],
    }])

    filters = assistant.parse_question("antelope tracking in northern Namibia during the 2019 drought")

    assert filters.species_names == ["antelope"]
    assert filters.tags_any == [Tag(key="topic", value="drought", origin=TagOrigin.AI)]
    request = messages.requests[0]
    assert request["response_format"]["type"] == "json_schema"
    assert request["response_format"]["json_schema"]["strict"] is True


def test_refusal_raises_instead_of_returning_empty_filters():
    assistant, _ = fake_assistant([], refusal="declined")

    with pytest.raises(RefusedError):
        assistant.parse_question("anything")


def append_rainfall(store, grid, day, cells):
    manifest = make_manifest(
        "chirps", {}, day, day.replace(day=day.day + 1), item_id=f"chirps-{day:%Y%m%d}", product="chirps",
        precision=TimePrecision.DAY,
    )
    stats = pd.DataFrame({"cell_id": cells, "variable": "rainfall_mm", "value": 1.0, "std": None, "valid_fraction": 1.0, "pixel_count": 1})
    batch = to_cell_observations(stats, manifest, grid, "chirps-v1", "sum", {"rainfall_mm": "mm"}, 5566)
    return store.append_batch(series_id(manifest, grid), manifest, batch)


def test_publish_labels_once_then_reuses_labels_for_small_appends(database, grid):
    store = SeriesStore(database, grid)
    catalog = PostgresCatalog(database)
    assistant, messages = fake_assistant([LABELS])
    cells = [f"E1K-r9000-c{col}" for col in range(18600, 18620)]

    first = append_rainfall(store, grid, datetime(2024, 3, 5, tzinfo=UTC), cells)
    v1 = publish_series_version(store, catalog, grid, first.series_id, "chirps", "rain", "public", assistant)
    append_rainfall(store, grid, datetime(2024, 3, 6, tzinfo=UTC), cells)
    v2 = publish_series_version(store, catalog, grid, first.series_id, "chirps", "rain", "public", assistant)

    assert len(messages.requests) == 1
    assert ("habitat", "salt_pan") in {(t.key, t.value) for t in v2.tags}
    assert ("habitat", "volcano") not in {(t.key, t.value) for t in v1.tags}
    assert v2.version == 2 and v2.row_count == 40
    assert v2.coverage.end == datetime(2024, 3, 7, tzinfo=UTC)
    assert v2.summary == LABELS["summary"]

    matches = catalog.search_datasets(SearchFilters(access_scope=["public"], tags_any=[Tag(key="habitat", value="salt_pan")]))
    assert [m.dataset.version for m in matches] == [2]


def test_publish_relabels_when_coverage_grows_materially(database, grid):
    store = SeriesStore(database, grid)
    catalog = MemoryCatalog()
    assistant, messages = fake_assistant([LABELS, LABELS])

    first = append_rainfall(store, grid, datetime(2024, 3, 5, tzinfo=UTC), [f"E1K-r9000-c{c}" for c in range(18600, 18610)])
    publish_series_version(store, catalog, grid, first.series_id, "chirps", "rain", "public", assistant)
    append_rainfall(store, grid, datetime(2024, 3, 6, tzinfo=UTC), [f"E1K-r9001-c{c}" for c in range(18600, 18610)])
    publish_series_version(store, catalog, grid, first.series_id, "chirps", "rain", "public", assistant)

    assert len(messages.requests) == 2


def test_publish_skips_a_version_that_is_already_registered(database, grid):
    store = SeriesStore(database, grid)
    catalog = PostgresCatalog(database)
    first = append_rainfall(store, grid, datetime(2024, 3, 5, tzinfo=UTC), ["E1K-r9000-c18600"])

    assert publish_series_version(store, catalog, grid, first.series_id, "chirps", "rain", "public") is not None
    assert publish_series_version(store, catalog, grid, first.series_id, "chirps", "rain", "public") is None

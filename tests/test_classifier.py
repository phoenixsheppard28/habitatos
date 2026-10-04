import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from habitat import config
from habitat.catalog.classifier import ClassificationUnavailable, GLiClassLabeler, dataset_labeler
from habitat.catalog.publish import publish_series_version
from habitat.catalog.store import MemoryCatalog
from habitat.contracts import Coverage, DatasetVersion, StorageRef, Tag, TagOrigin


@pytest.fixture
def rainfall():
    return DatasetVersion(
        dataset_id="rainfall", version=1, created_at=datetime(2024, 1, 3, tzinfo=UTC),
        access_scope="public", family="cell_observations", source_id="chirps", status="ready",
        description="Daily rainfall observations during a drought.",
        storage=StorageRef(uri="postgres://cell_observations", format="postgres"),
        row_grain="cell and day", row_count=2, raw_artifact_refs=[], mapping_version="1",
        coverage=Coverage(), variables=["rainfall_mm"],
        tags=[Tag(key="study_design", value="gridded_climate")],
    )


def test_local_classifier_preserves_scores_and_rejects_unrequested_or_weak_tags(rainfall):
    requests = []

    def classify(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "model": "knowledgator/gliclass-small-v1.0", "revision": "pinned-revision",
            "predictions": [
                {"label": "topic: drought", "score": 0.95},
                {"label": "topic: drought", "score": 0.95},
                {"label": "habitat: wetland", "score": 0.2},
                {"label": "study_design: camera trap", "score": 0.99},
                {"label": "habitat: invented", "score": 0.99},
            ],
        })

    labeler = GLiClassLabeler("http://classifier:8001", transport=httpx.MockTransport(classify))
    labels = labeler.label_dataset(rainfall, [])

    assert labels.summary == rainfall.description
    assert len(labels.tags) == 1
    tag = labels.tags[0]
    assert (tag.key, tag.value, tag.origin, tag.confidence) == ("topic", "drought", TagOrigin.AI, 0.95)
    assert tag.model == "knowledgator/gliclass-small-v1.0@pinned-revision"
    assert "not a measured observation" in tag.evidence
    assert requests[0]["threshold"] == 0.7
    assert not any(label.startswith("study_design:") for label in requests[0]["labels"])


@pytest.mark.parametrize("score", [-0.1, 1.2, "NaN"])
def test_invalid_classifier_scores_fail_instead_of_becoming_evidence(rainfall, score):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
        "model": "gliclass", "revision": "1", "predictions": [{"label": "topic: drought", "score": score}],
    }))

    with pytest.raises(ClassificationUnavailable):
        GLiClassLabeler("http://classifier:8001", transport=transport).label_dataset(rainfall, [])


def test_local_configuration_labels_without_an_anthropic_key():
    config.configure(classifier_url="http://classifier:8001", classifier_threshold=0.8, anthropic_api_key=None)

    labeler = dataset_labeler()

    assert isinstance(labeler, GLiClassLabeler)
    assert labeler.threshold == 0.8


def test_classifier_outage_does_not_block_publication(monkeypatch, caplog):
    transport = httpx.MockTransport(lambda request: httpx.Response(503))
    labeler = GLiClassLabeler("http://classifier:8001", transport=transport)
    version = SimpleNamespace(version=1, family="cell_observations", batches=[])
    summary = SimpleNamespace(cell_ids=[], taxa=[], row_count=2, start=None, end=None, variables=["rainfall_mm"])
    store = SimpleNamespace(latest_version=lambda _: version, summary=lambda _: summary, sample_rows=lambda *_: [])
    catalog = MemoryCatalog()
    monkeypatch.setattr("habitat.catalog.publish.footprint_from_cells", lambda *_: None)

    published = publish_series_version(store, catalog, None, "rainfall", "chirps", "Daily rainfall", "public", labeler)

    assert published.status == "ready"
    assert published.summary is None
    assert all(tag.origin is TagOrigin.DETERMINISTIC for tag in published.tags)
    assert catalog.latest("rainfall") is not None
    assert "Local classification failed" in caplog.text

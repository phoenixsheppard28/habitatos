import json
from typing import Any

import httpx
from pydantic import BaseModel, Field

from habitat.catalog.ai import CatalogAssistant, DatasetLabels
from habitat.catalog.tags import validate_ai_tags
from habitat.config import settings
from habitat.contracts import DatasetVersion, Tag, TagOrigin, load_contract


class ClassificationUnavailable(RuntimeError):
    pass


class Prediction(BaseModel):
    label: str
    score: float = Field(ge=0, le=1)


class ClassificationResponse(BaseModel):
    model: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    predictions: list[Prediction]


class GLiClassLabeler:
    def __init__(self, url: str, threshold: float = 0.7, transport: httpx.BaseTransport | None = None):
        self.url = url.rstrip("/")
        self.threshold = threshold
        self.transport = transport
        self.vocabulary = load_contract("tag_vocabulary.json")["ai_keys"]

    def label_dataset(self, dataset: DatasetVersion, sample_rows: list[dict[str, Any]]) -> DatasetLabels:
        computed_keys = {tag.key for tag in dataset.tags if tag.origin is TagOrigin.DETERMINISTIC}
        metadata = dataset.model_dump(mode="json", include={
            "description", "family", "source_id", "variables", "species", "coverage",
        })
        text = json.dumps(metadata, ensure_ascii=False)[:16000]
        tags = []

        try:
            with httpx.Client(timeout=60, transport=self.transport) as client:
                for key, values in self.vocabulary.items():
                    if key in computed_keys:
                        continue

                    candidates = {f"{key}: {value.replace('_', ' ')}": value for value in values}
                    response = client.post(f"{self.url}/classify", json={
                        "text": text, "labels": [*candidates, f"no {key} specified"], "threshold": self.threshold,
                    })
                    response.raise_for_status()
                    result = ClassificationResponse.model_validate(response.json())
                    accepted = [prediction for prediction in result.predictions
                                if prediction.label in candidates and prediction.score >= self.threshold]
                    if not accepted:
                        continue

                    prediction = max(accepted, key=lambda item: item.score)
                    tags.append(Tag(
                        key=key, value=candidates[prediction.label], origin=TagOrigin.AI, confidence=prediction.score,
                        model=f"{result.model}@{result.revision}",
                        evidence="Classifier score from dataset description and metadata; not a measured observation.",
                    ))
        except (httpx.HTTPError, ValueError) as error:
            raise ClassificationUnavailable("The local classification service could not label this dataset.") from error

        return DatasetLabels(summary=dataset.summary or dataset.description, tags=validate_ai_tags(tags))


def dataset_labeler(use_ai: bool = False):
    config = settings()
    if config.classifier_url:
        return GLiClassLabeler(config.classifier_url, config.classifier_threshold)

    return CatalogAssistant() if use_ai else None

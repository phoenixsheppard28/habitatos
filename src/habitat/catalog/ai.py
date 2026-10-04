import json
from datetime import datetime
from typing import Any

import anthropic
from pydantic import BaseModel

from habitat.catalog.tags import validate_ai_tags
from habitat import llm
from habitat.contracts import DatasetVersion, Tag, load_contract

FALLBACK_BETA = "server-side-fallback-2026-07-01"

TAGGING_SYSTEM = """You label ecological datasets for a search catalog.
Choose tags only from the vocabulary in the request. Choose a tag only when the metadata or sample rows support it.
Give a short evidence sentence for each tag, naming the field or value that supports it.
Write a two-sentence summary that names the data type, place, period and measured variables."""

QUESTION_SYSTEM = """You turn a user's question about animals and habitat into catalog search filters.
Copy species names as the user wrote them; a later step resolves them against a taxonomy. Never invent a species.
Use null for anything the question does not state. Do not widen dates or species beyond what the question says.
Use only tag keys and values from the vocabulary in the request."""


class DatasetLabels(BaseModel):
    summary: str
    tags: list[Tag]


class QuestionFilters(BaseModel):
    families: list[str]
    species_names: list[str]
    start: datetime | None
    end: datetime | None
    variables: list[str]
    tags_any: list[Tag]


class RefusedError(RuntimeError):
    pass


def tag_schema(vocabulary: dict[str, list[str]]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "key": {"type": "string", "enum": sorted(vocabulary)},
            "value": {"type": "string", "enum": sorted({v for values in vocabulary.values() for v in values})},
            "confidence": {"type": "number"},
            "evidence": {"type": "string"},
        },
        "required": ["key", "value", "confidence", "evidence"],
        "additionalProperties": False,
    }


class CatalogAssistant:
    def __init__(self, client: anthropic.Anthropic | None = None, model: str = llm.CATALOG_MODEL):
        self.client = client or llm.client()
        self.model = model
        self.vocabulary: dict[str, list[str]] = load_contract("tag_vocabulary.json")["ai_keys"]

    def label_dataset(self, dataset: DatasetVersion, sample_rows: list[dict[str, Any]]) -> DatasetLabels:
        schema = {
            "type": "object",
            "properties": {"summary": {"type": "string"}, "tags": {"type": "array", "items": tag_schema(self.vocabulary)}},
            "required": ["summary", "tags"],
            "additionalProperties": False,
        }
        metadata = dataset.model_dump(
            mode="json", include={"family", "source_id", "description", "coverage", "variables", "species", "tags"}
        )
        prompt = (
            f"<vocabulary>{json.dumps(self.vocabulary)}</vocabulary>\n"
            f"<metadata>{json.dumps(metadata)}</metadata>\n"
            f"<sample_rows>{json.dumps(sample_rows, default=str)}</sample_rows>"
        )

        answer = self.structured_call(TAGGING_SYSTEM, prompt, schema)

        tags = [
            Tag(key=t["key"], value=t["value"], confidence=t["confidence"], evidence=t["evidence"], model=self.model)
            for t in answer["tags"]
        ]
        return DatasetLabels(summary=answer["summary"], tags=validate_ai_tags(tags))

    def parse_question(self, question: str) -> QuestionFilters:
        nullable_date = {"anyOf": [{"type": "string", "format": "date-time"}, {"type": "null"}]}
        schema = {
            "type": "object",
            "properties": {
                "families": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["animal_locations", "occurrences", "cell_observations",
                                                         "site_features"]},
                },
                "species_names": {"type": "array", "items": {"type": "string"}},
                "start": nullable_date,
                "end": nullable_date,
                "variables": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["ndvi", "evi", "mndwi", "ndmi", "rainfall_mm",
                                                         "surface_water_fraction", "distance_to_surface_water_m",
                                                         "distance_to_water_m", "distance_to_permanent_water_m",
                                                         "distance_to_natural_water_m",
                                                         "distance_to_artificial_water_m", "water_point_density"]},
                },
                "tags_any": {"type": "array", "items": tag_schema(self.vocabulary)},
            },
            "required": ["families", "species_names", "start", "end", "variables", "tags_any"],
            "additionalProperties": False,
        }
        prompt = f"<vocabulary>{json.dumps(self.vocabulary)}</vocabulary>\n<question>{question}</question>"

        answer = self.structured_call(QUESTION_SYSTEM, prompt, schema)

        answer["tags_any"] = validate_ai_tags(Tag(key=t["key"], value=t["value"]) for t in answer["tags_any"])
        return QuestionFilters.model_validate(answer)

    def structured_call(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            system=system,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": prompt}],
        )
        if response.stop_reason == "refusal":
            raise RefusedError(f"model declined: {response.stop_details}")

        text = next(block.text for block in response.content if block.type == "text")
        return json.loads(text)

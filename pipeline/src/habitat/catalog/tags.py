from collections.abc import Iterable
from datetime import datetime

from shapely.geometry.base import BaseGeometry

from habitat.catalog.footprint import area_share
from habitat.contracts import Tag, TagOrigin, load_contract

SOURCE_TAGS: dict[str, list[Tag]] = {
    "sentinel2": [
        Tag(key="sensor_type", value="multispectral_optical"),
        Tag(key="temporal_resolution", value="5_day_revisit"),
        Tag(key="study_design", value="remote_sensing"),
    ],
    "modis_mod13q1": [
        Tag(key="sensor_type", value="multispectral_optical"),
        Tag(key="temporal_resolution", value="16_day_composite"),
        Tag(key="study_design", value="remote_sensing"),
    ],
    "chirps": [
        Tag(key="sensor_type", value="satellite_gauge_blend"),
        Tag(key="temporal_resolution", value="daily"),
        Tag(key="study_design", value="gridded_climate"),
    ],
    "movebank": [
        Tag(key="sensor_type", value="gps"),
        Tag(key="temporal_resolution", value="sub_daily"),
        Tag(key="study_design", value="gps_collar"),
    ],
}

MIN_REGION_SHARE = 0.05


def deterministic_tags(
    source_id: str,
    variables: Iterable[str],
    start: datetime | None,
    end: datetime | None,
    footprint: BaseGeometry | None,
    region_layers: dict[str, Iterable[tuple[str, BaseGeometry]]] | None = None,
) -> list[Tag]:
    """Tags computed from the data. `region_layers` maps a tag key such as 'ecoregion' to (name, polygon) pairs."""
    tags = list(SOURCE_TAGS.get(source_id, []))
    tags += [Tag(key="variable", value=variable) for variable in sorted(set(variables))]

    if start is not None and end is not None:
        tags += [Tag(key="year", value=str(year)) for year in range(start.year, end.year + 1)]

    if footprint is not None:
        for key, regions in (region_layers or {}).items():
            tags += region_tags(key, footprint, regions)

    return tags


def region_tags(key: str, footprint: BaseGeometry, regions: Iterable[tuple[str, BaseGeometry]]) -> list[Tag]:
    tags = []
    for name, polygon in regions:
        if not polygon.intersects(footprint):
            continue

        share = area_share(polygon, footprint)
        if share >= MIN_REGION_SHARE:
            tags.append(Tag(key=key, value=name, evidence=f"{share:.0%} of footprint"))
    return tags


def merge_tags(deterministic: list[Tag], ai: list[Tag]) -> list[Tag]:
    """An AI tag never overrides a deterministic tag with the same key."""
    deterministic_keys = {tag.key for tag in deterministic}
    return deterministic + [tag for tag in ai if tag.key not in deterministic_keys]


def validate_ai_tags(tags: Iterable[Tag]) -> list[Tag]:
    vocabulary = load_contract("tag_vocabulary.json")["ai_keys"]
    valid = []
    for tag in tags:
        if tag.value in vocabulary.get(tag.key, ()):
            valid.append(tag.model_copy(update={"origin": TagOrigin.AI}))
    return valid

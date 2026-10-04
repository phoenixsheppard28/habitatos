import hashlib
import json

import numpy as np
import pandas as pd
import pyarrow as pa

from habitat.archive.store import ArtifactStore
from habitat.contracts import ANIMAL_ENTITIES_SCHEMA, ANIMAL_LOCATIONS_SCHEMA, AnimalEntity, BBox, RawManifest
from habitat.grid import Grid, transformer
from habitat.normalize.rows import ANIMAL_ENTITIES, ANIMAL_LOCATIONS, NormalizedBatch, QuarantineError, series_id

MAPPING_VERSION = "movebank-csv-v1"
DIRECT_READ_MAPPING_VERSION = "movebank-direct-read-csv-v1"
ENTITY_SOURCE_ID = "movebank"
REQUIRED_COLUMNS = ["event-id", "timestamp", "location-long", "location-lat", "individual-local-identifier"]
MAPPED_COLUMNS = {
    *REQUIRED_COLUMNS,
    "visible",
    "sensor-type",
    "tag-local-identifier",
    "individual-taxon-canonical-name",
    "study-name",
}
REFERENCE_COLUMNS = {
    "animal-id": "local_identifier",
    "animal-taxon": "taxon_name",
    "animal-sex": "sex",
    "animal-life-stage": "life_stage",
    "study-site": "study_site",
}


def entity_id(study_id: str, local_identifier: str) -> str:
    return f"movebank:{study_id}:{local_identifier}"


def normalize_movebank(
    manifest: RawManifest, store: ArtifactStore, grid: Grid, aoi: BBox | None = None
) -> NormalizedBatch:
    """Movebank CSV export to animal_locations. Movebank timestamps are UTC by definition.

    https://www.movebank.org/cms/movebank-content/mb-data-model
    """
    fixes = pd.read_csv(store.open(manifest, "locations"), low_memory=False)
    reference = store.open(manifest, "reference") if "reference" in manifest.extensions.assets else None
    return normalize_fixes(manifest, fixes, reference, grid, aoi, MAPPING_VERSION)


def normalize_movebank_study(
    manifest: RawManifest, store: ArtifactStore, grid: Grid, aoi: BBox | None = None
) -> NormalizedBatch:
    """The direct-read API names attributes with underscores and the public preview has no event ids."""
    fixes = pd.read_csv(store.open(manifest, "locations"), low_memory=False)
    fixes.columns = [column.replace("_", "-") for column in fixes.columns]
    if "event-id" not in fixes.columns or fixes["event-id"].isna().any():
        fixes["event-id"] = [
            synthetic_event_id(manifest.extensions.properties.get("study_id"), name, time)
            for name, time in zip(fixes["individual-local-identifier"], fixes["timestamp"])
        ]
    return normalize_fixes(manifest, fixes, None, grid, aoi, DIRECT_READ_MAPPING_VERSION)


def synthetic_event_id(study_id, individual, timestamp) -> str:
    return "synthetic:" + hashlib.sha256(f"{study_id}|{individual}|{timestamp}".encode()).hexdigest()[:24]


def normalize_fixes(
    manifest: RawManifest, fixes: pd.DataFrame, reference_path, grid: Grid, aoi: BBox | None, mapping_version: str
) -> NormalizedBatch:
    item = manifest.extensions
    study_id = item.properties.get("study_id")
    if study_id is None:
        raise QuarantineError("the manifest has no Movebank study id; entity ids cannot be namespaced")

    missing = [column for column in REQUIRED_COLUMNS if column not in fixes.columns]
    if missing:
        raise QuarantineError(f"Movebank file has no column(s) {missing}")

    fixes = fixes.dropna(subset=["location-long", "location-lat", "timestamp"])
    if aoi is not None:
        west, south, east, north = aoi
        fixes = fixes[fixes["location-long"].between(west, east) & fixes["location-lat"].between(south, north)]

    rows = pd.DataFrame(
        {
            "source_record_id": fixes["event-id"].astype(str),
            "dataset_id": series_id(manifest, grid),
            "entity_id": [entity_id(study_id, str(name)) for name in fixes["individual-local-identifier"]],
            "tag_id": fixes.get("tag-local-identifier", pd.Series(index=fixes.index, dtype=object)).astype("string"),
            "observed_at": pd.to_datetime(fixes["timestamp"], utc=True),
            "available_at": pd.Timestamp(item.available_at),
            "longitude": fixes["location-long"].astype(float),
            "latitude": fixes["location-lat"].astype(float),
            "cell_id": cell_ids_for_points(grid, fixes["location-long"].to_numpy(), fixes["location-lat"].to_numpy()),
            "sensor_type": fixes.get("sensor-type", pd.Series("gps", index=fixes.index)).fillna("unknown"),
            "quality_flag": quality_flags(fixes),
            "mapping_version": mapping_version,
            "attributes": unmapped_attributes(fixes),
        }
    )
    table = pa.Table.from_pandas(rows, schema=ANIMAL_LOCATIONS_SCHEMA, preserve_index=False)

    entities = entities_table(entities_from(fixes, reference_path, study_id))
    return NormalizedBatch(table, mapping_version, family=ANIMAL_LOCATIONS, references={ANIMAL_ENTITIES: entities})


def cell_ids_for_points(grid: Grid, longitudes: np.ndarray, latitudes: np.ndarray) -> np.ndarray:
    x, y = transformer("EPSG:4326", grid.crs).transform(longitudes, latitudes)
    rows, cols = grid.rows_cols_from_xy(np.asarray(x), np.asarray(y))
    return grid.cell_ids(rows, cols)


def quality_flags(fixes: pd.DataFrame) -> pd.Series:
    """Movebank sets `visible` to false for fixes that the data owner or a filter marked as outliers."""
    flags = pd.Series("ok", index=fixes.index)
    if "visible" in fixes.columns:
        visible = fixes["visible"].astype(str).str.lower().isin(["true", "1"])
        flags[~visible] = "marked_outlier"
    return flags


def unmapped_attributes(fixes: pd.DataFrame) -> list[str]:
    rest = fixes[[column for column in fixes.columns if column not in MAPPED_COLUMNS]]
    if rest.empty:
        return ["{}"] * len(fixes)

    return rest.to_json(orient="records", lines=True, date_format="iso").splitlines()


def entities_from(fixes: pd.DataFrame, reference_path: str | None, study_id: str) -> list[AnimalEntity]:
    taxa = (
        fixes.groupby("individual-local-identifier")["individual-taxon-canonical-name"].first()
        if "individual-taxon-canonical-name" in fixes.columns
        else pd.Series(dtype=object)
    )
    entities = {
        str(name): AnimalEntity(
            entity_id=entity_id(study_id, str(name)),
            source_id=ENTITY_SOURCE_ID,
            study_id=study_id,
            local_identifier=str(name),
            taxon_name=taxa.get(name),
        )
        for name in fixes["individual-local-identifier"].unique()
    }
    if reference_path is None:
        return list(entities.values())

    reference = pd.read_csv(reference_path)
    for deployment in reference.to_dict("records"):
        name = str(deployment.get("animal-id"))
        if name not in entities:
            continue

        entity = entities[name]
        for column, field in REFERENCE_COLUMNS.items():
            value = deployment.get(column)
            if isinstance(value, str) and field != "local_identifier":
                setattr(entity, field, getattr(entity, field) or value)

        deploy_on = parse_time(deployment.get("deploy-on-date"))
        deploy_off = parse_time(deployment.get("deploy-off-date"))
        entity.deploy_on = min(filter(None, [entity.deploy_on, deploy_on]), default=None)
        entity.deploy_off = max(filter(None, [entity.deploy_off, deploy_off]), default=None)
        extra = {k: v for k, v in deployment.items() if k not in REFERENCE_COLUMNS and not pd.isna(v)}
        entity.attributes.setdefault("deployments", []).append(json.loads(json.dumps(extra, default=str)))

    return list(entities.values())


def entities_table(entities: list[AnimalEntity]) -> pa.Table:
    rows = [{**entity.model_dump(exclude={"attributes"}), "attributes": json.dumps(entity.attributes)} for entity in entities]
    return pa.Table.from_pylist(rows, schema=ANIMAL_ENTITIES_SCHEMA)


def parse_time(value) -> pd.Timestamp | None:
    if not isinstance(value, str):
        return None

    return pd.Timestamp(value, tz="UTC").to_pydatetime()

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
import psycopg
import pyarrow as pa
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel
from shapely import wkt

from habitat.area_cells import area_cell_overlaps
from habitat.contracts import RawManifest
from habitat.grid import Grid, default_grid, parse_cell_id, transformer
from habitat.normalize.rows import ANIMAL_ENTITIES, ANIMAL_LOCATIONS, CELL_OBSERVATIONS, NormalizedBatch
from habitat.normalize.rows import COUNT_AREAS, POPULATION_COUNTS


class BatchRecord(BaseModel):
    batch_key: str
    source_item_id: str
    processing_version: str
    product_status: str
    mapping_version: str
    row_count: int
    created_at: datetime


class SeriesVersion(BaseModel):
    series_id: str
    family: str
    version: int
    batches: list[BatchRecord]

    def has_batch(self, batch_key: str) -> bool:
        return any(batch.batch_key == batch_key for batch in self.batches)

    def has_item(self, source_item_id: str, processing_version: str, product_status: str) -> bool:
        return any(
            (b.source_item_id, b.processing_version, b.product_status)
            == (source_item_id, processing_version, product_status)
            for b in self.batches
        )


@dataclass
class AppendResult:
    series_id: str
    version: int
    batch_key: str
    appended: bool


@dataclass
class SeriesSummary:
    start: datetime | None
    end: datetime | None
    row_count: int
    variables: list[str]
    cell_ids: list[str]
    taxa: list[tuple[int, str]] = field(default_factory=list)
    footprint_wkt: str | None = None


def batch_key(manifest: RawManifest, mapping_version: str) -> str:
    item = manifest.extensions
    return "|".join(
        [item.source_id, item.source_item_id, item.processing_version, item.product_status.value, mapping_version]
    )


VERSION_BATCHES = """
    SELECT batch_key FROM ingest_batches
    WHERE series_id = %(series_id)s
      AND added_in_version <= %(version)s
      AND (superseded_in_version IS NULL OR superseded_in_version > %(version)s)
"""


class SeriesStore:
    """Append-only series in PostgreSQL. A version is the set of batches that were live when it was made."""

    def __init__(self, connection: psycopg.Connection, grid: Grid):
        self.connection = connection
        self.grid = grid

    def append_batch(
        self, series_id: str, manifest: RawManifest, batch: NormalizedBatch, supersedes: tuple[str, ...] = ()
    ) -> AppendResult:
        key = batch_key(manifest, batch.mapping_version)
        item = manifest.extensions
        unregistered = sorted(set(batch.references) - set(REFERENCE_UPSERTS))
        if unregistered:
            raise ValueError(f"no upsert is registered for the reference table(s) {unregistered}")

        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO series (series_id, family, source_id, product) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (series_id) DO NOTHING",
                (series_id, batch.family, item.source_id, item.product),
            )
            # The row lock serializes writers of one series, so two writers never create the same version.
            (latest,) = cursor.execute(
                "SELECT latest_version FROM series WHERE series_id = %s FOR UPDATE", (series_id,)
            ).fetchone()

            already_present = cursor.execute(
                "SELECT 1 FROM ingest_batches WHERE series_id = %s AND batch_key = %s", (series_id, key)
            ).fetchone()
            if already_present:
                return AppendResult(series_id, latest, key, appended=False)

            version = latest + 1
            cursor.execute(
                "INSERT INTO series_versions (series_id, version, parent_version) VALUES (%s, %s, %s)",
                (series_id, version, latest or None),
            )
            cursor.execute(
                """
                INSERT INTO ingest_batches (series_id, batch_key, source_item_id, processing_version, product_status,
                                            mapping_version, row_count, raw_manifest, added_in_version)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    series_id, key, item.source_item_id, item.processing_version, item.product_status.value,
                    batch.mapping_version, batch.table.num_rows, Jsonb(manifest.model_dump(mode="json")), version,
                ),
            )
            if supersedes:
                cursor.execute(
                    "UPDATE ingest_batches SET superseded_in_version = %s "
                    "WHERE series_id = %s AND batch_key = ANY(%s) AND superseded_in_version IS NULL",
                    (version, series_id, list(supersedes)),
                )

            insert_grid_cells(cursor, self.grid, cell_ids_in([*batch.references.values(), batch.table]))
            for name, references in batch.references.items():
                REFERENCE_UPSERTS[name](cursor, references)
            copy_rows(cursor, batch.family, series_id, key, batch.table)

            cursor.execute("UPDATE series SET latest_version = %s WHERE series_id = %s", (version, series_id))

        return AppendResult(series_id, version, key, appended=True)

    def latest_version(self, series_id: str) -> SeriesVersion | None:
        row = self.connection.execute(
            "SELECT family, latest_version FROM series WHERE series_id = %s", (series_id,)
        ).fetchone()
        if row is None or row[1] == 0:
            return None

        return self.version(series_id, row[1], family=row[0])

    def version(self, series_id: str, version: int, family: str | None = None) -> SeriesVersion:
        if family is None:
            (family,) = self.connection.execute(
                "SELECT family FROM series WHERE series_id = %s", (series_id,)
            ).fetchone()

        with self.connection.cursor(row_factory=dict_row) as cursor:
            batches = cursor.execute(
                f"""
                SELECT batch_key, source_item_id, processing_version, product_status, mapping_version,
                       row_count, created_at
                FROM ingest_batches
                WHERE series_id = %(series_id)s AND batch_key IN ({VERSION_BATCHES})
                ORDER BY added_in_version
                """,
                {"series_id": series_id, "version": version},
            ).fetchall()

        return SeriesVersion(
            series_id=series_id, family=family, version=version, batches=[BatchRecord(**b) for b in batches]
        )

    def summary(self, version: SeriesVersion) -> SeriesSummary:
        summarize = FAMILY_SUMMARIES.get(version.family)
        if summarize is None:
            raise ValueError(f"no summary function is registered for the family {version.family!r}")

        return summarize(self.connection, version)

    def sample_rows(self, version: SeriesVersion, limit: int) -> list[dict[str, Any]]:
        with self.connection.cursor(row_factory=dict_row) as cursor:
            return cursor.execute(
                f"SELECT * FROM {version.family} "
                f"WHERE series_id = %(series_id)s AND batch_key IN ({VERSION_BATCHES}) LIMIT %(limit)s",
                {"series_id": version.series_id, "version": version.version, "limit": limit},
            ).fetchall()

    def current_cell_rows(
        self, series_id: str, version: int | None = None, as_of: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Rows after de-duplication. With `as_of`, only values that were public at that time take part."""
        with self.connection.cursor(row_factory=dict_row) as cursor:
            return cursor.execute(
                "SELECT * FROM current_cell_observations_at(%s, %s, %s) ORDER BY cell_id, variable, time_start",
                (series_id, version, as_of),
            ).fetchall()


def insert_grid_cells(cursor: psycopg.Cursor, grid: Grid, cell_ids: Iterable[str]) -> None:
    wanted = [cell_id for cell_id in cell_ids if cell_id is not None]
    known = {
        cell_id for (cell_id,) in cursor.execute("SELECT cell_id FROM grid_cells WHERE cell_id = ANY(%s)", (wanted,))
    }
    missing = [cell_id for cell_id in wanted if cell_id not in known]
    if not missing:
        return

    rows, cols = np.array([parse_cell_id(cell_id) for cell_id in missing]).T
    centre_x, centre_y = grid.cell_centres_xy(rows, cols)
    longitudes, latitudes = transformer(grid.crs, "EPSG:4326").transform(centre_x, centre_y)
    cursor.executemany(
        """
        INSERT INTO grid_cells (cell_id, grid_id, row_index, col_index, geometry, centroid)
        VALUES (%s, %s, %s, %s, ST_GeomFromText(%s, 4326), ST_SetSRID(ST_MakePoint(%s, %s), 4326))
        ON CONFLICT (cell_id) DO NOTHING
        """,
        [
            (cell_id, grid.grid_id, int(row), int(col), grid.cell_polygon_wgs84(cell_id).wkt, float(lon), float(lat))
            for cell_id, row, col, lon, lat in zip(missing, rows, cols, longitudes, latitudes)
        ],
    )


def cell_ids_in(tables: Iterable[pa.Table]) -> set[str]:
    return {
        cell_id
        for table in tables
        if "cell_id" in table.column_names
        for cell_id in table.column("cell_id").unique().to_pylist()
        if cell_id is not None
    }


ReferenceUpsert = Callable[[psycopg.Cursor, pa.Table], None]


def upsert_on_key(table_name: str, key_columns: tuple[str, ...]) -> ReferenceUpsert:
    """An upsert that inserts new reference rows and replaces every non-key column of existing rows."""

    def upsert(cursor: psycopg.Cursor, table: pa.Table) -> None:
        columns = table.column_names
        updates = [f"{column} = EXCLUDED.{column}" for column in columns if column not in key_columns]
        conflict_action = f"DO UPDATE SET {', '.join(updates)}" if updates else "DO NOTHING"
        cursor.executemany(
            f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))}) "
            f"ON CONFLICT ({', '.join(key_columns)}) {conflict_action}",
            list(zip(*(column.to_pylist() for column in table.columns))),
        )

    return upsert


def upsert_animal_entities(cursor: psycopg.Cursor, entities: pa.Table) -> None:
    """A later package of the same study adds to what is known about an animal. It never erases a value."""
    cursor.executemany(
        """
        INSERT INTO animal_entities (entity_id, source_id, study_id, local_identifier, taxon_name, gbif_taxon_key,
                                     sex, life_stage, deploy_on, deploy_off, study_site, attributes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (entity_id) DO UPDATE SET
            taxon_name = coalesce(EXCLUDED.taxon_name, animal_entities.taxon_name),
            gbif_taxon_key = coalesce(EXCLUDED.gbif_taxon_key, animal_entities.gbif_taxon_key),
            sex = coalesce(EXCLUDED.sex, animal_entities.sex),
            life_stage = coalesce(EXCLUDED.life_stage, animal_entities.life_stage),
            deploy_on = least(EXCLUDED.deploy_on, animal_entities.deploy_on),
            deploy_off = greatest(EXCLUDED.deploy_off, animal_entities.deploy_off),
            study_site = coalesce(EXCLUDED.study_site, animal_entities.study_site),
            attributes = animal_entities.attributes || EXCLUDED.attributes
        """,
        [
            (
                e["entity_id"], e["source_id"], e["study_id"], e["local_identifier"], e["taxon_name"],
                e["gbif_taxon_key"], e["sex"], e["life_stage"], e["deploy_on"], e["deploy_off"], e["study_site"],
                Jsonb(json.loads(e["attributes"])),
            )
            for e in entities.to_pylist()
        ],
    )


def upsert_count_areas(cursor: psycopg.Cursor, areas: pa.Table) -> None:
    """Replace the areas, then compute the grid cells again for each area that is new or has a new geometry."""
    rows = areas.to_pylist()
    area_ids = [area["area_id"] for area in rows]
    unchanged = {
        area_id
        for (area_id,) in cursor.execute(
            """
            SELECT a.area_id
            FROM count_areas a JOIN unnest(%s::text[], %s::text[]) AS n(area_id, geometry_wkt) USING (area_id)
            WHERE a.geometry IS NOT DISTINCT FROM ST_GeomFromText(n.geometry_wkt, 4326)
            """,
            (area_ids, [area["geometry_wkt"] for area in rows]),
        )
    }

    cursor.executemany(
        """
        INSERT INTO count_areas (area_id, source_id, area_name, area_type, area_km2, geometry, geometry_source,
                                 valid_from, valid_to, attributes)
        VALUES (%s, %s, %s, %s, %s, ST_GeomFromText(%s, 4326), %s, %s, %s, %s)
        ON CONFLICT (area_id) DO UPDATE SET
            source_id = EXCLUDED.source_id, area_name = EXCLUDED.area_name, area_type = EXCLUDED.area_type,
            area_km2 = EXCLUDED.area_km2, geometry = EXCLUDED.geometry, geometry_source = EXCLUDED.geometry_source,
            valid_from = EXCLUDED.valid_from, valid_to = EXCLUDED.valid_to, attributes = EXCLUDED.attributes
        """,
        [
            (
                a["area_id"], a["source_id"], a["area_name"], a["area_type"], a["area_km2"], a["geometry_wkt"],
                a["geometry_source"], a["valid_from"], a["valid_to"], Jsonb(json.loads(a["attributes"])),
            )
            for a in rows
        ],
    )

    changed = [area for area in rows if area["area_id"] not in unchanged]
    cursor.execute("DELETE FROM count_area_cells WHERE area_id = ANY(%s)", ([area["area_id"] for area in changed],))
    grid = default_grid()
    with cursor.copy("COPY count_area_cells (area_id, cell_id, overlap_fraction) FROM STDIN") as copy:
        for area in changed:
            if area["geometry_wkt"] is None:
                continue
            for cell_id, share in area_cell_overlaps(grid, wkt.loads(area["geometry_wkt"])):
                copy.write_row((area["area_id"], cell_id, share))


REFERENCE_UPSERTS: dict[str, ReferenceUpsert] = {ANIMAL_ENTITIES: upsert_animal_entities}
REFERENCE_UPSERTS[COUNT_AREAS] = upsert_count_areas


def version_parameters(version: SeriesVersion) -> dict[str, Any]:
    return {"series_id": version.series_id, "version": version.version}


def distinct_cell_ids(connection: psycopg.Connection, table: str, version: SeriesVersion) -> list[str]:
    return [
        cell_id
        for (cell_id,) in connection.execute(
            f"SELECT DISTINCT cell_id FROM {table} "
            f"WHERE series_id = %(series_id)s AND batch_key IN ({VERSION_BATCHES}) AND cell_id IS NOT NULL",
            version_parameters(version),
        )
    ]


def summarize_cell_observations(connection: psycopg.Connection, version: SeriesVersion) -> SeriesSummary:
    start, end, row_count, variables = connection.execute(
        f"""
        SELECT min(time_start), max(time_end), count(*), coalesce(array_agg(DISTINCT variable), '{{}}')
        FROM cell_observations
        WHERE series_id = %(series_id)s AND batch_key IN ({VERSION_BATCHES})
        """,
        version_parameters(version),
    ).fetchone()

    return SeriesSummary(
        start, end, row_count, sorted(variables), distinct_cell_ids(connection, CELL_OBSERVATIONS, version)
    )


def summarize_animal_locations(connection: psycopg.Connection, version: SeriesVersion) -> SeriesSummary:
    parameters = version_parameters(version)
    start, end, row_count = connection.execute(
        f"""
        SELECT min(observed_at), max(observed_at), count(*)
        FROM animal_locations
        WHERE series_id = %(series_id)s AND batch_key IN ({VERSION_BATCHES})
        """,
        parameters,
    ).fetchone()
    taxa = connection.execute(
        f"""
        SELECT DISTINCT e.gbif_taxon_key, e.taxon_name
        FROM animal_locations l JOIN animal_entities e USING (entity_id)
        WHERE l.series_id = %(series_id)s AND l.batch_key IN ({VERSION_BATCHES})
          AND e.gbif_taxon_key IS NOT NULL
        """,
        parameters,
    ).fetchall()

    return SeriesSummary(
        start, end, row_count, [], distinct_cell_ids(connection, ANIMAL_LOCATIONS, version), [tuple(t) for t in taxa]
    )


def summarize_population_counts(connection: psycopg.Connection, version: SeriesVersion) -> SeriesSummary:
    """The footprint is the union of the count areas. A point area counts as a small box around the point."""
    parameters = version_parameters(version)
    start, end, row_count, metrics = connection.execute(
        f"""
        SELECT min(time_start), max(time_end), count(*), coalesce(array_agg(DISTINCT metric), '{{}}')
        FROM population_counts
        WHERE series_id = %(series_id)s AND batch_key IN ({VERSION_BATCHES})
        """,
        parameters,
    ).fetchone()
    taxa = connection.execute(
        f"""
        SELECT DISTINCT gbif_taxon_key, taxon_name FROM population_counts
        WHERE series_id = %(series_id)s AND batch_key IN ({VERSION_BATCHES}) AND gbif_taxon_key IS NOT NULL
        """,
        parameters,
    ).fetchall()
    (footprint_wkt,) = connection.execute(
        f"""
        SELECT ST_AsText(ST_SimplifyPreserveTopology(ST_Union(
            CASE WHEN ST_Dimension(a.geometry) = 2 THEN a.geometry ELSE ST_Expand(a.geometry, 0.005) END
        ), 0.005))
        FROM count_areas a
        WHERE a.geometry IS NOT NULL AND a.area_id IN (
            SELECT area_id FROM population_counts
            WHERE series_id = %(series_id)s AND batch_key IN ({VERSION_BATCHES})
        )
        """,
        parameters,
    ).fetchone()

    return SeriesSummary(
        start, end, row_count, sorted(metrics), [], [tuple(t) for t in taxa], footprint_wkt=footprint_wkt
    )


FamilySummary = Callable[[psycopg.Connection, SeriesVersion], SeriesSummary]

FAMILY_SUMMARIES: dict[str, FamilySummary] = {
    CELL_OBSERVATIONS: summarize_cell_observations,
    ANIMAL_LOCATIONS: summarize_animal_locations,
}
FAMILY_SUMMARIES[POPULATION_COUNTS] = summarize_population_counts


def copy_rows(cursor: psycopg.Cursor, family: str, series_id: str, key: str, table: pa.Table) -> None:
    columns = ["series_id", "batch_key", *table.column_names]
    with cursor.copy(f"COPY {family} ({', '.join(columns)}) FROM STDIN") as copy:
        for row in zip(*(column.to_pylist() for column in table.columns)):
            copy.write_row((series_id, key, *row))

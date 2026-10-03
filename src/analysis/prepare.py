"""Turn a feature table into the columns a method is allowed to use.

Rows outside the query, and rows for a different species, are removed here.
A missing displacement stays missing. It is never filled with zero.
"""

from datetime import datetime, timezone

import pandas as pd

from contracts.models import ColumnSpec, FeatureArtifact, QuerySpec


class PrepareError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def as_utc(value) -> datetime:
    if isinstance(value, str):
        text = value.replace("Z", "+00:00")
        value = datetime.fromisoformat(text)
    elif isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if not isinstance(value, datetime):
        raise PrepareError("invalid_timestamps", "could not read a timestamp")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def scope_allows(request_scope: str, artifact_scope: str) -> bool:
    """A public table is readable by anyone. Any other table requires the same scope."""
    if artifact_scope == "public":
        return True
    return request_scope == artifact_scope


def bind_roles(columns: list[ColumnSpec]) -> dict[str, ColumnSpec]:
    bound: dict[str, ColumnSpec] = {}
    for column in columns:
        if not column.role:
            continue
        if column.role in bound:
            raise PrepareError("ambiguous_role", f"more than one column uses role {column.role}")
        bound[column.role] = column
    return bound


def missing_roles(bound: dict[str, ColumnSpec], required: list[str]) -> list[str]:
    return [role for role in required if role not in bound]


def coverage_problem(artifact: FeatureArtifact, query: QuerySpec) -> str | None:
    coverage = artifact.coverage
    if not coverage:
        return None
    species = coverage.get("species")
    if species is not None and set(species).isdisjoint(query.species):
        return "species"
    start = coverage.get("start")
    end = coverage.get("end")
    if start and end:
        covered_start = as_utc(start)
        covered_end = as_utc(end)
        query_start = as_utc(query.time_range.start)
        query_end = as_utc(query.time_range.end)
        if covered_end < query_start or query_end < covered_start:
            return "time"
    return None


def prepare_frame(frame: pd.DataFrame, artifact: FeatureArtifact, query: QuerySpec, roles: dict[str, ColumnSpec]):
    warnings: list[str] = []
    if artifact.row_count is not None and artifact.row_count != len(frame):
        raise PrepareError(
            "artifact_row_count_mismatch",
            f"feature table has {len(frame)} rows, descriptor says {artifact.row_count}",
        )
    entity = roles["entity_id"].name if "entity_id" in roles else None
    event_time = roles["event_time"].name
    for column in roles.values():
        if column.name not in frame.columns:
            raise PrepareError("missing_column", f"feature table is missing column {column.name}")

    parsed_time = pd.to_datetime(frame[event_time], utc=True, errors="coerce", format="mixed")
    if parsed_time.isna().any():
        raise PrepareError("invalid_timestamps", "event_time contains values that are not timestamps")
    frame = frame.copy()
    frame[event_time] = parsed_time

    for role in ("longitude", "latitude", "daily_displacement", "rainfall", "vegetation_index"):
        column = roles.get(role)
        if column is None or column.name not in frame.columns:
            continue
        numeric = pd.to_numeric(frame[column.name], errors="coerce")
        original = frame[column.name]
        bad = original.notna() & numeric.isna()
        if bad.any():
            raise PrepareError("invalid_values", f"{column.name} contains non-numeric values")
        frame[column.name] = numeric
        if role == "longitude" and numeric.notna().any() and ((numeric < -180) | (numeric > 180)).any():
            raise PrepareError("invalid_values", f"{column.name} has a longitude outside -180 to 180")
        if role == "latitude" and numeric.notna().any() and ((numeric < -90) | (numeric > 90)).any():
            raise PrepareError("invalid_values", f"{column.name} has a latitude outside -90 to 90")
        if role == "daily_displacement":
            negative = int((numeric < 0).sum())
            if negative:
                warnings.append(
                    f"{negative} displacement values are negative. They are kept and are not treated as zero."
                )

    start = as_utc(query.time_range.start)
    end = as_utc(query.time_range.end)
    inside = (frame[event_time] >= start) & (frame[event_time] <= end)
    excluded = int((~inside).sum())
    if excluded:
        warnings.append(f"Excluded {excluded} feature rows outside the query time range.")
    frame = frame.loc[inside].copy()
    if frame.empty:
        raise PrepareError("no_rows_in_range", "no feature rows fall inside the query time range")

    if "species" in roles:
        species_column = roles["species"].name
        keep = frame[species_column].isin(query.species)
        excluded_species = int((~keep).sum())
        if excluded_species:
            warnings.append(f"Excluded {excluded_species} feature rows for other species.")
        frame = frame.loc[keep].copy()
        if frame.empty:
            raise PrepareError("no_rows_for_species", "no feature rows match the requested species")

    # Grain is one row per animal per UTC day. A second fix on that day is an error,
    # not an average, because averaging here would hide a join mistake.
    if entity is not None:
        frame["_day"] = frame[event_time].dt.floor("D")
        if frame.duplicated([entity, "_day"]).any():
            raise PrepareError(
                "invalid_grain",
                "feature table has more than one row for the same animal on the same day",
            )
        frame = frame.drop(columns=["_day"])
    if artifact.coverage is None:
        warnings.append("Feature coverage was not provided, so species and time coverage were not verified.")
    return frame, warnings

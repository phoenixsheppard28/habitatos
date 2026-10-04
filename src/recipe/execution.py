"""Fixture executor and transactional Supabase PostgreSQL execution adapter."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Protocol

from shapely.geometry import Point, shape

from .compiler import SQLCompiler, ident
from .errors import RecipeError
from .models import Aggregate, AsOfJoin, Filter, Join, Select, SpatialJoin, TimeBucket, Window
from .progress import stage
from .validation import validate_recipe


def timestamp(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise RecipeError("INVALID_DATA", "expected a timezone-aware timestamp")
    return value.astimezone(timezone.utc)


class Executor(Protocol):
    version: str
    def execute(self, recipe, query, datasets) -> tuple[list[dict], dict]: ...


class FixtureExecutor:
    """Reference execution for safe fixtures; does not replace Stage 2 storage."""
    version = "recipe-fixture-1"

    def __init__(self, reader, *, max_rows=100_000):
        self.reader, self.max_rows = reader, max_rows

    def execute(self, recipe, query, datasets):
        validate_recipe(recipe, query, datasets)
        tables, report = {}, {"steps": {}, "warnings": []}
        region = shape(query.region)
        lookback = max([0] + [s.window_seconds if isinstance(s, Window) else s.tolerance_seconds
                              for s in recipe.steps if isinstance(s, (Window, AsOfJoin))])
        history_start = query.time_range.start-timedelta(seconds=lookback)
        for alias, ref in recipe.inputs.items():
            ds = datasets[ref.key]
            rows = []
            roles = {c.role: c.name for c in ds.columns if c.role}
            for original in self.reader(ds, query):
                row = {c.name: original.get(c.name) for c in ds.columns}
                for c in ds.columns:
                    if c.type == "timestamp" and row[c.name] is not None:
                        row[c.name] = timestamp(row[c.name])
                if query.species and "species" in roles and row[roles["species"]] not in query.species:
                    continue
                if "event_time" in roles:
                    time = row[roles["event_time"]]
                    in_range = time is not None and history_start <= time <= query.time_range.end
                elif {"interval_start", "interval_end"} <= roles.keys():
                    start, end = row[roles["interval_start"]], row[roles["interval_end"]]
                    in_range = start is not None and end is not None and history_start <= start and end <= query.time_range.end
                else:
                    raise RecipeError("INVALID_DATA", "input lacks complete time semantics")
                if not in_range:
                    continue
                if "geometry" in roles:
                    geo = row[roles["geometry"]]
                    if geo is None:
                        continue
                    intersects = shape(geo).intersects(region)
                elif {"longitude", "latitude"} <= roles.keys():
                    lon, lat = row[roles["longitude"]], row[roles["latitude"]]
                    if lon is None or lat is None:
                        continue
                    intersects = Point(lon, lat).intersects(region)
                else:
                    raise RecipeError("INVALID_DATA", "input location semantics unavailable")
                if intersects:
                    rows.append(row)
                self._limit(rows)
            tables[alias] = rows
        for step in recipe.steps:
            detail = {}
            if isinstance(step, Select):
                rows = [{out: row[src] for out, src in step.columns.items()} for row in tables[step.input]]
            elif isinstance(step, Filter):
                rows = [dict(row) for row in tables[step.input] if all(_predicate(row, p) for p in step.predicates)]
            elif isinstance(step, TimeBucket):
                rows = []
                for row in tables[step.input]:
                    time = row[step.column]
                    if time is not None:
                        time = timestamp(time).replace(hour=0, minute=0, second=0, microsecond=0)
                        if step.period in {"month", "year"}:
                            time = time.replace(day=1)
                        if step.period == "year":
                            time = time.replace(month=1)
                    rows.append({**row, step.output: time})
            elif isinstance(step, Aggregate):
                groups = defaultdict(list)
                for row in tables[step.input]:
                    groups[tuple(row[k] for k in step.group_by)].append(row)
                rows = []
                for key, group in groups.items():
                    out = dict(zip(step.group_by, key))
                    for agg in step.aggregations:
                        values = [row[agg.column] for row in group if row[agg.column] is not None]
                        if agg.method == "count":
                            value = len(values)
                        elif not values:
                            value = None
                        else:
                            value = {"sum": lambda: sum(values), "mean": lambda: sum(values)/len(values),
                                     "min": lambda: min(values), "max": lambda: max(values)}[agg.method]()
                        out[agg.output] = value
                    rows.append(out)
            else:
                left, right = tables[step.left], tables[step.right]
                rows, unmatched, ambiguous = [], 0, 0
                if isinstance(step, Join):
                    _unique(right, list(step.keys.values()), "right join keys")
                    if step.cardinality == "one_to_one":
                        _unique(left, list(step.keys), "left join keys")
                elif isinstance(step, SpatialJoin):
                    _unique(right, [step.right_tie_break], "spatial tie-break", nonnull=True)
                    if any(row[step.left_geometry] is not None and shape(row[step.left_geometry]).geom_type != "Point" for row in left):
                        raise RecipeError("JOIN_VALIDATION_FAILED", "spatial left geometry must be a point")
                    if any(row[step.right_geometry] is not None and shape(row[step.right_geometry]).geom_type not in {"Polygon", "MultiPolygon"} for row in right):
                        raise RecipeError("JOIN_VALIDATION_FAILED", "spatial right geometry must be a polygon/cell")
                elif isinstance(step, AsOfJoin):
                    _unique(right, [*step.keys.values(), step.right_time, step.right_tie_break], "temporal tie-break")
                    if any(r[step.right_tie_break] is None for r in right):
                        raise RecipeError("JOIN_VALIDATION_FAILED", "null temporal tie-break")
                elif isinstance(step, Window):
                    _intervals(right, step)
                for lrow in left:
                    matches = []
                    for rrow in right:
                        if isinstance(step, SpatialJoin):
                            match = (lrow[step.left_geometry] is not None and rrow[step.right_geometry] is not None
                                and shape(rrow[step.right_geometry]).covers(shape(lrow[step.left_geometry])))
                        else:
                            match = all(lrow[l] is not None and rrow[r] is not None and lrow[l] == rrow[r]
                                        for l, r in step.keys.items())
                        if match and isinstance(step, AsOfJoin):
                            lt, rt = lrow[step.left_time], rrow[step.right_time]
                            match = lt is not None and rt is not None and timedelta(0) <= lt-rt <= timedelta(seconds=step.tolerance_seconds)
                        if match and isinstance(step, Window):
                            lt = lrow[step.left_time]
                            match = lt is not None and rrow[step.right_start] >= lt-timedelta(seconds=step.window_seconds) and rrow[step.right_end] <= lt
                        if match and isinstance(step, (AsOfJoin, Window)) and step.right_available_at:
                            available = rrow[step.right_available_at]
                            match = available is not None and available <= lrow[step.left_time]
                        if match:
                            matches.append(rrow)
                    if isinstance(step, Window):
                        present = [r for r in matches if r[step.value_column] is not None]
                        coverage = sum((r[step.right_end]-r[step.right_start]).total_seconds() for r in present)/step.window_seconds
                        value = sum(r[step.value_column] for r in present) if present and coverage >= step.minimum_coverage else None
                        rows.append({**lrow, step.output: value, step.output+"_coverage": coverage})
                        if not matches:
                            unmatched += 1
                        continue
                    if not matches:
                        unmatched += 1
                        if step.type == "inner":
                            continue
                        rows.append({**lrow, **{out: None for out in step.right_columns}})
                    else:
                        if len(matches) > 1:
                            ambiguous += 1
                        if isinstance(step, SpatialJoin):
                            matches.sort(key=lambda r: r[step.right_tie_break])
                        elif isinstance(step, AsOfJoin):
                            matches.sort(key=lambda r: r[step.right_tie_break])
                            matches.sort(key=lambda r: r[step.right_time], reverse=True)
                        chosen = matches[0]
                        rows.append({**lrow, **{out: chosen[src] for out, src in step.right_columns.items()}})
                    self._limit(rows)
                detail = {"input_rows": len(left), "unmatched_rows": unmatched,
                          "multiple_eligible_matches": ambiguous}
            self._limit(rows)
            tables[step.id] = rows
            report["steps"][step.id] = {**detail, "row_count": len(rows)}
        out = recipe.output
        rows = [{c.name: row[c.name] for c in out.columns} for row in tables[out.step]
                if row[out.time_column] is not None and query.time_range.start <= row[out.time_column] <= query.time_range.end]
        rows.sort(key=lambda row: tuple((row[k] is None, row[k]) for k in out.keys))
        validate_output(rows, recipe, self.max_rows)
        return rows, report

    def _limit(self, rows):
        if len(rows) > self.max_rows:
            raise RecipeError("RESOURCE_LIMIT", "intermediate/output row limit exceeded")


def _predicate(row, predicate):
    value = row[predicate.column]
    if predicate.operator == "is_null":
        return value is None
    if predicate.operator == "not_null":
        return value is not None
    if value is None:
        return False
    target = predicate.value
    if isinstance(value, datetime):
        target = [timestamp(v) for v in target] if predicate.operator == "in" else timestamp(target)
    return {"eq": lambda: value == target, "ne": lambda: value != target,
            "lt": lambda: value < target, "le": lambda: value <= target,
            "gt": lambda: value > target, "ge": lambda: value >= target,
            "in": lambda: value in target}[predicate.operator]()


def _unique(rows, keys, label, *, nonnull=False):
    seen = set()
    for row in rows:
        key = tuple(row[k] for k in keys)
        if key in seen or (nonnull and any(v is None for v in key)):
            raise RecipeError("JOIN_VALIDATION_FAILED", f"{label} not unique/non-null")
        seen.add(key)


def _intervals(rows, step):
    groups = defaultdict(list)
    for row in rows:
        start, end = row[step.right_start], row[step.right_end]
        if start is None or end is None or end <= start:
            raise RecipeError("JOIN_VALIDATION_FAILED", "invalid measurement interval")
        groups[tuple(row[k] for k in step.keys.values())].append((start, end))
    for intervals in groups.values():
        intervals.sort()
        previous_end = None
        for start, end in intervals:
            if previous_end and start < previous_end:
                raise RecipeError("JOIN_VALIDATION_FAILED", "overlapping measurement intervals")
            previous_end = end


def validate_output(rows, recipe, max_rows):
    import math
    if not rows:
        raise RecipeError("INSUFFICIENT_DATA", "recipe produced no rows within the requested interval")
    if len(rows) > max_rows:
        raise RecipeError("RESOURCE_LIMIT", "output row limit exceeded")
    _unique(rows, recipe.output.keys, "output grain keys", nonnull=True)
    for row in rows:
        for col in recipe.output.columns:
            value = row.get(col.name)
            if value is None:
                if not col.nullable:
                    raise RecipeError("OUTPUT_VALIDATION_FAILED", f"required column is null: {col.name}")
                continue
            if col.type == "timestamp":
                row[col.name] = timestamp(value)
            elif col.type == "geometry":
                if isinstance(value, str):
                    import json
                    row[col.name] = value = json.loads(value)
                geo = shape(value)
                if not geo.is_valid or geo.is_empty:
                    raise RecipeError("OUTPUT_VALIDATION_FAILED", f"invalid geometry: {col.name}")
            elif col.type in {"number", "integer"}:
                if isinstance(value, Decimal):
                    row[col.name] = value = int(value) if col.type == "integer" else float(value)
                if type(value) not in {int, float} or not math.isfinite(value) or (col.type == "integer" and type(value) is not int):
                    raise RecipeError("OUTPUT_VALIDATION_FAILED", f"invalid numeric value: {col.name}")
            elif col.type == "string" and not isinstance(value, str):
                raise RecipeError("OUTPUT_VALIDATION_FAILED", f"invalid string: {col.name}")
            elif col.type == "boolean" and type(value) is not bool:
                raise RecipeError("OUTPUT_VALIDATION_FAILED", f"invalid boolean: {col.name}")


class SupabaseExecutor:
    """Direct/session-pooler PostgreSQL backend, not an arbitrary-SQL RPC.

    connection_factory supplies an authorized backend connection. Catalog
    authorization and trusted bindings are required separately by the service.
    """
    version = "recipe-supabase-2"

    def __init__(self, connection_factory, bindings, *, postgis_schema="extensions",
                 max_rows=100_000, statement_timeout_ms=30_000):
        self.connection_factory = connection_factory
        self.compiler = SQLCompiler(bindings, postgis_schema=postgis_schema, max_rows=max_rows)
        self.max_rows, self.timeout = max_rows, statement_timeout_ms

    def execute(self, recipe, query, datasets):
        from psycopg.rows import dict_row
        plan = self.compiler.compile(recipe, query, datasets, materialize=True)
        report = {"steps": {}, "checks": [], "warnings": [], "compiler_version": plan.version,
                  "cell_scopes": plan.cell_scopes}
        try:
            with self.connection_factory() as connection:
                with connection.transaction():
                    with connection.cursor(row_factory=dict_row) as cursor:
                        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                        cursor.execute("SELECT set_config('statement_timeout', %s, true)", (str(self.timeout),))
                        cursor.execute("SELECT set_config('TimeZone', 'UTC', true)")
                        for preparation in plan.stages:
                            for check in preparation.checks_before:
                                with stage("sql.validation", check.name):
                                    cursor.execute(check.sql, plan.params, prepare=False)
                                    if cursor.fetchone()["invalid"]:
                                        raise RecipeError(check.code, check.name)
                                    report["checks"].append(check.name)

                            with stage("sql.materialize", f"Prepare {preparation.name}"):
                                table = f'"pg_temp".{ident(preparation.table)}'
                                cursor.execute(f"CREATE TEMP TABLE {ident(preparation.table)} ON COMMIT DROP AS "
                                               f"SELECT * FROM ({preparation.sql}) prepared LIMIT {self.max_rows + 1}",
                                               plan.params, prepare=False)
                                count = cursor.rowcount
                                if count > self.max_rows:
                                    raise RecipeError("RESOURCE_LIMIT", preparation.limit_check.name)
                                report["checks"].append(preparation.limit_check.name)
                                report["steps"][f"{preparation.name}: row count"] = count
                                for columns in preparation.indexes:
                                    if columns:
                                        cursor.execute(f"CREATE INDEX ON {table} ({', '.join(ident(c) for c in columns)})")
                                cursor.execute(f"ANALYZE {table}")

                            for label, sql in preparation.statistics.items():
                                if label == f"{preparation.name}: row count":
                                    report["steps"][label] = count
                                else:
                                    with stage("sql.statistics", label):
                                        cursor.execute(sql, plan.params, prepare=False)
                                        report["steps"][label] = cursor.fetchone()["value"]

                        with stage("sql.output", "Read and validate the prepared table"):
                            cursor.execute(plan.sql, plan.params, prepare=False)
                            rows = cursor.fetchmany(self.max_rows + 1)
                            validate_output(rows, recipe, self.max_rows)
                            rows.sort(key=lambda row: tuple(row[k] for k in recipe.output.keys))

                        return rows, report
        except RecipeError:
            raise
        except Exception as exc:
            # Database errors may contain secrets, paths or record values.
            raise RecipeError("DATABASE_EXECUTION_FAILED", "database execution failed; inspect backend logs",
                              retryable=getattr(exc, "sqlstate", None) in {"40001", "40P01", "57014", "08006"}) from exc

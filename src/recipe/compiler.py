"""A closed PostgreSQL/PostGIS compiler; model text never becomes SQL."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import timedelta
from functools import lru_cache
from uuid import uuid4

from .errors import RecipeError
from .models import Aggregate, AsOfJoin, Filter, Join, Select, SpatialJoin, TimeBucket, Window
from .validation import validate_recipe

COMPILER_VERSION = "recipe-sql-3"


def ident(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise RecipeError("INVALID_BINDING", "invalid database identifier")
    return '"' + value.replace('"', '""') + '"'


@dataclass(frozen=True)
class TableBinding:
    """Trusted Stage 2 mapping, never accepted from the recipe/LLM.

    Shared tables must have dataset/version/scope columns. An immutable,
    physically isolated version can instead use pinned_relation=True.
    """
    schema: str
    table: str
    columns: dict[str, str]
    dataset_id_column: str = "dataset_id"
    version_column: str = "dataset_version"
    scope_column: str = "access_scope"
    pinned_relation: bool = False
    native_geometry_columns: frozenset[str] = frozenset()


@dataclass
class SQLCheck:
    name: str
    sql: str
    code: str = "JOIN_VALIDATION_FAILED"


@dataclass
class SQLStage:
    name: str
    table: str
    sql: str
    checks_before: list[SQLCheck]
    limit_check: SQLCheck
    indexes: list[tuple[str, ...]]
    statistics: dict[str, str]
    input_context: dict = field(default_factory=dict)


@dataclass
class CompiledRecipe:
    sql: str
    params: dict
    checks: list[SQLCheck]
    statistics: dict[str, str]
    version: str = COMPILER_VERSION
    stages: list[SQLStage] = field(default_factory=list)
    cell_scopes: dict[str, list[str]] = field(default_factory=dict)
    index_scopes: dict[str, list[str]] = field(default_factory=dict)


def environmental_cell_scopes(recipe, datasets):
    inputs = {alias: datasets[ref.key] for alias, ref in recipe.inputs.items()}
    steps = {step.id: step for step in recipe.steps}
    users = {name: [] for name in [*recipe.inputs, *(step.id for step in recipe.steps)]}
    for step in recipe.steps:
        for name in ([step.input] if isinstance(step, (Select, Filter, TimeBucket, Aggregate))
                     else [step.left, step.right]):
            users[name].append(step)

    @lru_cache(maxsize=None)
    def origin(name, column):
        if name in inputs:
            return name, column
        step = steps[name]
        if isinstance(step, Select):
            return origin(step.input, step.columns[column])
        if isinstance(step, Filter) or isinstance(step, TimeBucket) and column != step.output:
            return origin(step.input, column)
        return None

    @lru_cache(maxsize=None)
    def consumers(name, column):
        if name == recipe.output.step or not users[name]:
            return None
        movement = set()
        for step in users[name]:
            if isinstance(step, (Filter, TimeBucket)):
                matches = consumers(step.id, column)
            elif isinstance(step, Select):
                renamed = [out for out, source in step.columns.items() if source == column]
                matches = consumers(step.id, renamed[0]) if len(renamed) == 1 else None
            elif isinstance(step, (Join, AsOfJoin, Window)) and step.right == name:
                left_columns = [left for left, right in step.keys.items() if right == column]
                matches = set()
                for left in left_columns:
                    source = origin(step.left, left)
                    if source and inputs[source[0]].family in {"animal_locations", "animal_daily_movement"}:
                        cell = next((c.name for c in inputs[source[0]].columns if c.role == "cell_id"), None)
                        if source[1] == cell:
                            matches.add(source[0])
                matches = matches or None
            else:
                matches = None
            if matches is None:
                return None
            movement.update(matches)
        return movement

    scopes = {}
    for alias, dataset in inputs.items():
        cell = next((c.name for c in dataset.columns if c.role == "cell_id"), None)
        if dataset.family in {"rainfall_observations", "vegetation_observations"} and cell:
            matches = consumers(alias, cell)
            if matches:
                scopes[alias] = sorted(matches)
    return scopes


def vegetation_index_scopes(recipe, datasets):
    users = {name: [] for name in [*recipe.inputs, *(step.id for step in recipe.steps)]}
    for step in recipe.steps:
        for name in ([step.input] if isinstance(step, (Select, Filter, TimeBucket, Aggregate))
                     else [step.left, step.right]):
            users[name].append(step)

    def paths(name, column, allowed=None):
        if name == recipe.output.step or not users[name]:
            return [allowed]
        restrictions = []
        for step in users[name]:
            selected = allowed
            if isinstance(step, Filter):
                for predicate in step.predicates:
                    if predicate.column != column:
                        continue
                    if predicate.operator == "eq" and isinstance(predicate.value, str):
                        values = {predicate.value}
                    elif predicate.operator == "in" and isinstance(predicate.value, list):
                        values = set(predicate.value)
                    else:
                        continue
                    selected = values if selected is None else selected & values
                restrictions.extend(paths(step.id, column, selected))
            elif isinstance(step, Select):
                renamed = [out for out, source in step.columns.items() if source == column]
                restrictions.extend(paths(step.id, renamed[0], selected) if len(renamed) == 1 else [selected])
            elif isinstance(step, TimeBucket) and step.output != column:
                restrictions.extend(paths(step.id, column, selected))
            else:
                restrictions.append(selected)
        return restrictions

    scopes = {}
    for alias, ref in recipe.inputs.items():
        dataset = datasets[ref.key]
        if dataset.family != "vegetation_observations" or "index_name" not in {c.name for c in dataset.columns}:
            continue
        restrictions = paths(alias, "index_name")
        if all(values is not None for values in restrictions):
            scopes[alias] = sorted(set().union(*restrictions))

    return scopes


class SQLCompiler:
    def __init__(self, bindings: dict, *, postgis_schema="extensions", max_rows=100_000):
        self.bindings = bindings
        self.postgis_schema = postgis_schema
        self.max_rows = max_rows

    def compile(self, recipe, query, datasets, *, materialize=False):
        schemas = validate_recipe(recipe, query, datasets)
        params, ctes, checks, stats = {}, [], [], {}
        stages = []
        namespace = "recipe_" + uuid4().hex
        tables = {name: f"{namespace}_{index}" for index, name in
                  enumerate([*recipe.inputs, *(step.id for step in recipe.steps)])}
        cell_scopes = environmental_cell_scopes(recipe, datasets) if materialize else {}
        index_scopes = vegetation_index_scopes(recipe, datasets) if materialize else {}
        indexes = {name: set() for name in tables}
        for step in recipe.steps:
            if isinstance(step, (Join, AsOfJoin, Window)):
                right = tuple(step.keys.values())
                if isinstance(step, AsOfJoin):
                    right += (step.right_time, step.right_tie_break)
                elif isinstance(step, Window):
                    right += (step.right_start, step.right_end)
                indexes[step.right].add(tuple(dict.fromkeys(right)))
                indexes[step.left].add(tuple(step.keys))
        for alias, movement in cell_scopes.items():
            for source in movement:
                indexes[source].add(tuple(c.name for c in datasets[recipe.inputs[source].key].columns
                                          if c.role == "cell_id"))
        lookback = max([0] + [s.window_seconds if isinstance(s, Window) else s.tolerance_seconds
                              for s in recipe.steps if isinstance(s, (Window, AsOfJoin))])

        def param(value):
            name = f"p{len(params)}"
            params[name] = value
            return f"%({name})s"

        def pg(function):
            return f"{ident(self.postgis_schema)}.{function}"

        def geometry(expr):
            return f"{pg('ST_SetSRID')}({pg('ST_GeomFromGeoJSON')}(({expr})::text),4326)"

        def prefix():
            return "" if materialize else "WITH " + ",\n".join(ctes) + "\n"

        def relation(name):
            return f'"pg_temp".{ident(tables[name])}' if materialize else ident(name)

        def check(name, body, code="JOIN_VALIDATION_FAILED"):
            checks.append(SQLCheck(name, prefix() + body, code))

        def unique(name, cols, label):
            group = ", ".join(ident(c) for c in cols)
            check(label, f"SELECT EXISTS(SELECT 1 FROM {relation(name)} GROUP BY {group} HAVING count(*)>1) AS invalid")

        input_order = sorted(recipe.inputs, key=lambda alias: alias in cell_scopes)
        for alias in input_order:
            ref = recipe.inputs[alias]
            ds = datasets[ref.key]
            binding = self.bindings.get(ref.key)
            if binding is None:
                raise RecipeError("ARTIFACT_UNREADABLE", f"no trusted SQL binding for {ref.key}")
            projected = []
            for col in ds.columns:
                if col.name not in binding.columns:
                    raise RecipeError("INVALID_BINDING", f"missing physical column mapping: {col.name}")
                expr = ident(binding.columns[col.name])
                if col.name in binding.native_geometry_columns:
                    expr = f"{pg('ST_AsGeoJSON')}({expr})::jsonb"
                projected.append(f"{expr} AS {ident(col.name)}")
            restrictions = []
            if not binding.pinned_relation:
                restrictions += [f"{ident(binding.dataset_id_column)}={param(ds.dataset_id)}",
                    f"{ident(binding.version_column)}={param(ds.version)}",
                    f"{ident(binding.scope_column)}={param(ds.access_scope)}"]
            roles = {c.role: c.name for c in ds.columns if c.role}
            region = geometry(param(json.dumps(query.region)))
            native_geometry = roles.get("geometry") in binding.native_geometry_columns
            if native_geometry:
                restrictions.append(f"{pg('ST_Intersects')}({ident(binding.columns[roles['geometry']])},{region})")
            if alias in cell_scopes:
                cell = ident(binding.columns[roles["cell_id"]])
                matches = []
                for source in cell_scopes[alias]:
                    source_cell = next(c.name for c in datasets[recipe.inputs[source].key].columns if c.role == "cell_id")
                    matches.append(f"EXISTS(SELECT 1 FROM {relation(source)} cells WHERE cells.{ident(source_cell)}=source.{cell})")
                restrictions.append("(" + " OR ".join(matches) + ")")
            if alias in index_scopes:
                indices = index_scopes[alias]
                column = ident(binding.columns["index_name"])
                restrictions.append(f"{column} IN ({', '.join(param(index) for index in indices)})" if indices else "FALSE")

            raw = f"SELECT {', '.join(projected)} FROM {ident(binding.schema)}.{ident(binding.table)} AS source"
            if restrictions:
                raw += " WHERE " + " AND ".join(restrictions)
            if "geometry" in roles:
                geo = geometry(ident(roles["geometry"]))
            elif {"longitude", "latitude"} <= roles.keys():
                geo = f"{pg('ST_SetSRID')}({pg('ST_MakePoint')}({ident(roles['longitude'])},{ident(roles['latitude'])}),4326)"
            else:
                raise RecipeError("INVALID_BINDING", "input lacks location field semantics")
            if "event_time" in roles:
                temporal = f"{ident(roles['event_time'])}>={param(query.time_range.start-timedelta(seconds=lookback))} AND {ident(roles['event_time'])}<={param(query.time_range.end)}"
            elif {"interval_start", "interval_end"} <= roles.keys():
                temporal = f"{ident(roles['interval_start'])}>={param(query.time_range.start-timedelta(seconds=lookback))} AND {ident(roles['interval_end'])}<={param(query.time_range.end)}"
            else:
                raise RecipeError("INVALID_BINDING", "input lacks complete time semantics")
            species = ""
            if query.species and "species" in roles:
                species = f" AND {ident(roles['species'])} IN ({', '.join(param(s) for s in query.species)})"
            spatial = "" if native_geometry else f"{pg('ST_Intersects')}({geo},{region}) AND "
            body = f"SELECT * FROM ({raw}) AS scoped WHERE {spatial}{temporal}{species}"
            ctes.append(f"{ident(alias)} AS ({body})")
            check(f"{alias}: input row limit", f"SELECT count(*)>{param(self.max_rows)} AS invalid FROM {relation(alias)}", "RESOURCE_LIMIT")
            if materialize:
                input_context = {"dataset_id": ds.dataset_id, "dataset_version": ds.version,
                                 "family": ds.family, "region": query.region,
                                 "time_range": {"start": (query.time_range.start-timedelta(seconds=lookback)).isoformat(),
                                                "end": query.time_range.end.isoformat()},
                                 "selected_indices": index_scopes.get(alias),
                                 "track_cell_inputs": cell_scopes.get(alias, [])}
                stages.append(SQLStage(alias, tables[alias], body, [], checks[-1], sorted(indexes[alias]), {}, input_context))

        for step in recipe.steps:
            first_check, first_stat = len(checks), set(stats)
            if isinstance(step, Select):
                body = "SELECT " + ", ".join(f"{ident(src)} AS {ident(out)}" for out, src in step.columns.items()) + f" FROM {relation(step.input)}"
            elif isinstance(step, Filter):
                predicates = []
                operators = {"eq": "=", "ne": "<>", "lt": "<", "le": "<=", "gt": ">", "ge": ">="}
                for pred in step.predicates:
                    col = ident(pred.column)
                    cast = "::timestamptz" if schemas[step.input][pred.column].type == "timestamp" else ""
                    if pred.operator in {"is_null", "not_null"}:
                        predicates.append(f"{col} IS {'NOT ' if pred.operator == 'not_null' else ''}NULL")
                    elif pred.operator == "in":
                        predicates.append(f"{col} IN ({', '.join(param(v)+cast for v in pred.value)})")
                    else:
                        predicates.append(f"{col}{operators[pred.operator]}{param(pred.value)}{cast}")
                body = f"SELECT * FROM {relation(step.input)} WHERE " + " AND ".join(predicates)
            elif isinstance(step, TimeBucket):
                body = f"SELECT *, date_trunc({param(step.period)}, {ident(step.column)} AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' AS {ident(step.output)} FROM {relation(step.input)}"
            elif isinstance(step, Aggregate):
                groups = ", ".join(ident(k) for k in step.group_by)
                funcs = {"mean": "avg", "sum": "sum", "count": "count", "min": "min", "max": "max"}
                aggs = ", ".join(f"{funcs[a.method]}({ident(a.column)}) AS {ident(a.output)}" for a in step.aggregations)
                body = f"SELECT {groups}, {aggs} FROM {relation(step.input)} GROUP BY {groups}"
            else:
                left, right = relation(step.left), relation(step.right)
                cols = ", ".join(f"r.{ident(src)} AS {ident(out)}" for out, src in step.right_columns.items())
                projection = "l.*" + (", " + cols if cols else "")
                join = "LEFT JOIN" if step.type == "left" else "JOIN"
                if isinstance(step, Join):
                    unique(step.right, list(step.keys.values()), f"{step.id}: right key uniqueness")
                    if step.cardinality == "one_to_one":
                        unique(step.left, list(step.keys), f"{step.id}: left key uniqueness")
                    predicate = " AND ".join(f"l.{ident(l)}=r.{ident(r)}" for l, r in step.keys.items())
                    body = f"SELECT {projection} FROM {left} l {join} {right} r ON {predicate}"
                elif isinstance(step, SpatialJoin):
                    unique(step.right, [step.right_tie_break], f"{step.id}: stable spatial tie-break")
                    check(f"{step.id}: non-null tie-break", f"SELECT EXISTS(SELECT 1 FROM {right} WHERE {ident(step.right_tie_break)} IS NULL) AS invalid")
                    check(f"{step.id}: spatial geometry types", f"SELECT EXISTS(SELECT 1 FROM {left} WHERE {ident(step.left_geometry)} IS NOT NULL AND {pg('ST_GeometryType')}({geometry(ident(step.left_geometry))})<>'ST_Point') OR EXISTS(SELECT 1 FROM {right} WHERE {ident(step.right_geometry)} IS NOT NULL AND {pg('ST_GeometryType')}({geometry(ident(step.right_geometry))}) NOT IN ('ST_Polygon','ST_MultiPolygon')) AS invalid")
                    predicate = f"{pg('ST_Covers')}({geometry('r.'+ident(step.right_geometry))},{geometry('l.'+ident(step.left_geometry))})"
                    tie = f"r.{ident(step.right_tie_break)}" + (' COLLATE "C"' if schemas[step.right][step.right_tie_break].type == "string" else '')
                    body = f"SELECT {projection} FROM {left} l {join} LATERAL (SELECT * FROM {right} r WHERE {predicate} ORDER BY {tie} LIMIT 1) r ON TRUE"
                    stats[f"{step.id}: ambiguous matches"] = prefix() + f"SELECT count(*) AS value FROM {left} l WHERE (SELECT count(*) FROM {right} r WHERE {predicate})>1"
                elif isinstance(step, AsOfJoin):
                    unique(step.right, [*step.keys.values(), step.right_time, step.right_tie_break], f"{step.id}: stable temporal tie-break")
                    check(f"{step.id}: non-null tie-break", f"SELECT EXISTS(SELECT 1 FROM {right} WHERE {ident(step.right_tie_break)} IS NULL) AS invalid")
                    predicate = " AND ".join(f"l.{ident(l)}=r.{ident(r)}" for l, r in step.keys.items())
                    predicate += f" AND r.{ident(step.right_time)}<=l.{ident(step.left_time)} AND r.{ident(step.right_time)}>=l.{ident(step.left_time)}-({param(step.tolerance_seconds)}*INTERVAL '1 second')"
                    if step.right_available_at:
                        predicate += f" AND r.{ident(step.right_available_at)}<=l.{ident(step.left_time)}"
                    tie = f"r.{ident(step.right_tie_break)}" + (' COLLATE "C"' if schemas[step.right][step.right_tie_break].type == "string" else '')
                    body = f"SELECT {projection} FROM {left} l {join} LATERAL (SELECT * FROM {right} r WHERE {predicate} ORDER BY r.{ident(step.right_time)} DESC,{tie} ASC LIMIT 1) r ON TRUE"
                    stats[f"{step.id}: multiple eligible matches"] = prefix() + f"SELECT count(*) AS value FROM {left} l WHERE (SELECT count(*) FROM {right} r WHERE {predicate})>1"
                elif isinstance(step, Window):
                    keys = ", ".join(ident(k) for k in step.keys.values())
                    start, end = ident(step.right_start), ident(step.right_end)
                    check(f"{step.id}: valid intervals", f"SELECT EXISTS(SELECT 1 FROM {right} WHERE {start} IS NULL OR {end} IS NULL OR {end}<={start}) AS invalid")
                    check(f"{step.id}: non-overlapping intervals", f"SELECT EXISTS(SELECT 1 FROM (SELECT {start}, max({end}) OVER (PARTITION BY {keys} ORDER BY {start},{end} ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS previous_end FROM {right}) q WHERE {start}<previous_end) AS invalid")
                    predicate = " AND ".join(f"l.{ident(l)}=r.{ident(r)}" for l, r in step.keys.items())
                    window = param(step.window_seconds)
                    predicate += f" AND r.{start}>=l.{ident(step.left_time)}-({window}*INTERVAL '1 second') AND r.{end}<=l.{ident(step.left_time)}"
                    if step.right_available_at:
                        predicate += f" AND r.{ident(step.right_available_at)}<=l.{ident(step.left_time)}"
                    coverage = f"COALESCE(sum(EXTRACT(EPOCH FROM (r.{end}-r.{start}))) FILTER (WHERE r.{ident(step.value_column)} IS NOT NULL),0)/{window}::double precision"
                    body = f"SELECT l.*, r.{ident(step.output)}, r.{ident(step.output+'_coverage')} FROM {left} l LEFT JOIN LATERAL (SELECT CASE WHEN {coverage}>={param(step.minimum_coverage)} THEN sum(r.{ident(step.value_column)}) ELSE NULL END AS {ident(step.output)}, {coverage} AS {ident(step.output+'_coverage')} FROM {right} r WHERE {predicate}) r ON TRUE"
                    stats[f"{step.id}: unmatched rows"] = prefix() + f"SELECT count(*) AS value FROM {left} l WHERE NOT EXISTS(SELECT 1 FROM {right} r WHERE {predicate})"
                else:
                    raise RecipeError("UNSUPPORTED_OPERATION", "unsupported operation")
                if isinstance(step, (Join, SpatialJoin, AsOfJoin)):
                    stats[f"{step.id}: unmatched rows"] = prefix() + f"SELECT count(*) AS value FROM {left} l WHERE NOT EXISTS(SELECT 1 FROM {right} r WHERE {predicate})"
            ctes.append(f"{ident(step.id)} AS ({body})")
            check(f"{step.id}: intermediate row limit", f"SELECT count(*)>{param(self.max_rows)} AS invalid FROM {relation(step.id)}", "RESOURCE_LIMIT")
            stats[f"{step.id}: row count"] = prefix() + f"SELECT count(*) AS value FROM {relation(step.id)}"
            if materialize:
                stages.append(SQLStage(step.id, tables[step.id], body, checks[first_check:-1], checks[-1],
                                       sorted(indexes[step.id]), {key: value for key, value in stats.items() if key not in first_stat}))

        output = recipe.output
        select = ", ".join(ident(c.name) for c in output.columns)
        time = ident(output.time_column)
        body = f"SELECT {select} FROM {relation(output.step)} WHERE {time}>={param(query.time_range.start)} AND {time}<={param(query.time_range.end)}"
        order = ", ".join(ident(k) for k in output.keys)
        final_sql = prefix() + body + f" ORDER BY {order} LIMIT {param(self.max_rows+1)}"
        return CompiledRecipe(final_sql, params, checks, stats, stages=stages,
                              cell_scopes=cell_scopes, index_scopes=index_scopes)

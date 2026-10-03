from __future__ import annotations

from copy import deepcopy

from .errors import RecipeError
from .models import (
    Aggregate, AsOfJoin, Column, DatasetVersion, Filter, Join, QuerySpec,
    RecipeSpec, Select, SpatialJoin, TimeBucket, Window,
)

NUMERIC = {"number", "integer"}


def fail(message, code="INVALID_RECIPE"):
    raise RecipeError(code, message)


def validate_recipe(recipe: RecipeSpec, query: QuerySpec,
                    datasets: dict[tuple[str, str], DatasetVersion]):
    """Validate the full operation graph and return inferred step schemas."""
    if recipe.query_ref != query.query_id or recipe.access_scope != query.access_scope:
        fail("recipe query/access context differs from the validated query")
    if not recipe.inputs:
        fail("recipe requires pinned dataset inputs")
    # Do not imply historical point-in-time guarantees before the shared
    # availability contract exists. Historical preparation remains supported.
    if query.task_type == "forecast":
        fail("strict forecast preparation requires Stage 2 availability metadata "
             "and a per-row feature-time contract; not yet enabled", "INSUFFICIENT_DATA")
    schemas = {}
    for alias, ref in recipe.inputs.items():
        ds = datasets.get(ref.key)
        if ds is None:
            fail(f"input {ref.key} was not selected and validated")
        if ds.status != "ready":
            fail(f"input {ref.key} is not ready")
        schemas[alias] = {c.name: c.model_copy(deep=True, update={"derived_from": [f"{alias}.{c.name}"]}) for c in ds.columns}
        if query.species and ds.coverage.species:
            if not set(ds.coverage.species) <= set(query.species) and not any(c.role == "species" and c.type == "string" for c in ds.columns):
                fail("multi-species input requires a species column to enforce the query", "INSUFFICIENT_DATA")

    def source(name):
        if name not in schemas:
            fail(f"unknown or forward input reference: {name}")
        return schemas[name]

    def column(schema, name, types=None):
        if name not in schema:
            fail(f"missing column: {name}")
        c = schema[name]
        if types and c.type not in types:
            fail(f"column {name} needs type {sorted(types)}, got {c.type}")
        return c

    def new(schema, name, c):
        if name in schema:
            fail(f"output column collision: {name}")
        schema[name] = c.model_copy(update={"name": name})

    def keys(left, right, mapping):
        if not mapping:
            fail("equality matching requires at least one key")
        if len(set(mapping.values())) != len(mapping):
            fail("join keys must map to distinct right columns")
        for l, r in mapping.items():
            lc, rc = column(left, l), column(right, r)
            if lc.type != rc.type or lc.unit != rc.unit:
                fail(f"incompatible join key types/units: {l}, {r}")
            if lc.type in {"json", "geometry"}:
                fail("use spatial matching for geometry; JSON keys are unsupported")

    for step in recipe.steps:
        if step.id in schemas:
            fail(f"duplicate input/step ID: {step.id}")
        if isinstance(step, Select):
            if not step.columns:
                fail("select needs output columns")
            out = {}
            for name, src in step.columns.items():
                new(out, name, column(source(step.input), src))
        elif isinstance(step, Filter):
            out = deepcopy(source(step.input))
            for predicate in step.predicates:
                c = column(out, predicate.column)
                if predicate.operator not in {"is_null", "not_null"}:
                    values = predicate.value if predicate.operator == "in" else [predicate.value]
                    if not isinstance(values, list) or not values or len(values) > 1000:
                        fail("predicate values must be a nonempty bounded list")
                    for value in values:
                        if value is None or not _literal_matches(c.type, value):
                            fail(f"invalid literal for {c.name} ({c.type})")
        elif isinstance(step, TimeBucket):
            out = deepcopy(source(step.input))
            column(out, step.column, {"timestamp"})
            new(out, step.output, Column(name=step.output, type="timestamp", nullable=True,
                role="event_time", description=f"UTC {step.period} bucket of {step.column}",
                derived_from=[f"{step.input}.{step.column}"]))
        elif isinstance(step, Aggregate):
            src = source(step.input)
            if len(set(step.group_by)) != len(step.group_by):
                fail("duplicate aggregation keys")
            out = {name: deepcopy(column(src, name)) for name in step.group_by}
            for agg in step.aggregations:
                c = column(src, agg.column)
                if agg.method in {"sum", "mean"} and c.type not in NUMERIC:
                    fail(f"{agg.method} requires a numeric measurement")
                if c.type in {"json", "geometry"}:
                    fail("geometry/JSON aggregation is unsupported")
                dtype = "integer" if agg.method == "count" else "number" if agg.method == "mean" else c.type
                new(out, agg.output, Column(name=agg.output, type=dtype,
                    unit=None if agg.method == "count" else c.unit,
                    nullable=agg.method != "count", role="measurement",
                    description=f"{agg.method} of {agg.column}; nulls excluded",
                    derived_from=[f"{step.input}.{agg.column}"]))
        else:
            left, right = source(step.left), source(step.right)
            out = deepcopy(left)
            for name, src in step.right_columns.items():
                c = column(right, src).model_copy(update={"nullable": True})
                new(out, name, c)
            if isinstance(step, Join):
                keys(left, right, step.keys)
            elif isinstance(step, SpatialJoin):
                column(left, step.left_geometry, {"geometry"})
                column(right, step.right_geometry, {"geometry"})
                column(right, step.right_tie_break, {"string", "integer"})
            elif isinstance(step, AsOfJoin):
                keys(left, right, step.keys)
                column(left, step.left_time, {"timestamp"})
                column(right, step.right_time, {"timestamp"})
                column(right, step.right_tie_break, {"string", "integer"})
                if step.right_available_at:
                    column(right, step.right_available_at, {"timestamp"})
            elif isinstance(step, Window):
                keys(left, right, step.keys)
                column(left, step.left_time, {"timestamp"})
                column(right, step.right_start, {"timestamp"})
                column(right, step.right_end, {"timestamp"})
                if step.right_available_at:
                    column(right, step.right_available_at, {"timestamp"})
                c = column(right, step.value_column, NUMERIC)
                if step.right_columns:
                    fail("window aggregation does not expose arbitrary source rows")
                new(out, step.output, Column(name=step.output, type="number", unit=c.unit,
                    description=f"sum of {step.value_column} in complete preceding intervals",
                    derived_from=[f"{step.right}.{step.value_column}"]))
                new(out, step.output + "_coverage", Column(name=step.output + "_coverage",
                    type="number", nullable=False, description="fraction of window with non-null measurements"))
            else:
                fail("unsupported operation", "UNSUPPORTED_OPERATION")
        schemas[step.id] = out
    out = source(recipe.output.step)
    if len(set(recipe.output.keys)) != len(recipe.output.keys):
        fail("duplicate output keys")
    for name in recipe.output.keys:
        column(out, name)
    column(out, recipe.output.time_column, {"timestamp"})
    declared = {c.name: c for c in recipe.output.columns}
    if len(declared) != len(recipe.output.columns) or set(declared) != set(out):
        fail("declared output columns must exactly match inferred columns")
    for name, c in declared.items():
        inferred = out[name]
        if c.type != inferred.type or c.unit != inferred.unit:
            fail(f"declared output type/unit differs from calculation: {name}")
        if inferred.nullable and not c.nullable:
            fail(f"output {name} can be null and must declare nullable=true")
    return schemas


def _literal_matches(dtype, value):
    from datetime import datetime
    if dtype == "timestamp":
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).utcoffset() is not None
        except ValueError:
            return False
    return ((dtype == "string" and isinstance(value, str))
            or (dtype == "integer" and type(value) is int)
            or (dtype == "number" and type(value) in {int, float})
            or (dtype == "boolean" and type(value) is bool))

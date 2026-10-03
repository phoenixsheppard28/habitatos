"""Validate a query and pin relative dates once."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError

from contracts.models import QuerySpec


class QueryValidationError(ValueError):
    pass


def normalize_request(request: dict) -> dict:
    if not isinstance(request, dict):
        raise QueryValidationError("request must be an object")
    if request.get("contract_version") != "1.0":
        raise QueryValidationError("unsupported contract_version")
    for field in ("request_id", "query_id", "access_scope", "input"):
        if not request.get(field):
            raise QueryValidationError(f"missing {field}")
    if not isinstance(request["input"], dict) or "query" not in request["input"]:
        raise QueryValidationError("missing query")

    normalized = deepcopy(request)
    raw_query = normalized["input"]["query"]
    if not isinstance(raw_query, dict):
        raise QueryValidationError("query must be an object")
    raw_query["time_range"] = resolve_time_range(raw_query.get("time_range"))
    raw_query["query_id"] = _matching_id(raw_query.get("query_id"), request["query_id"], "query_id")
    raw_query["access_scope"] = _matching_id(
        raw_query.get("access_scope"), request["access_scope"], "access_scope"
    )
    try:
        query = QuerySpec.model_validate(raw_query)
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"])
        raise QueryValidationError(f"{location}: {first['msg']}") from exc
    if query.task_type == "forecast" and query.forecast is None:
        raise QueryValidationError("forecast requests need a cutoff, horizon, and target")
    normalized["input"]["query"] = query.model_dump(mode="json")
    return normalized


def resolve_time_range(raw) -> dict:
    if not isinstance(raw, dict):
        raise QueryValidationError("time_range must be an object")
    if raw.get("start") and raw.get("end"):
        start = _as_utc(raw["start"])
        end = _as_utc(raw["end"])
    elif raw.get("relative_days") and raw.get("as_of"):
        days = raw["relative_days"]
        if isinstance(days, bool) or not isinstance(days, int) or days <= 0:
            raise QueryValidationError("relative_days must be a positive integer")
        end = _as_utc(raw["as_of"])
        start = end - timedelta(days=days)
    else:
        raise QueryValidationError("time range needs absolute dates or relative_days plus as_of")
    if start >= end:
        raise QueryValidationError("time_range.start must be before time_range.end")
    return {"start": _iso(start), "end": _iso(end)}


def _matching_id(value, expected: str, label: str) -> str:
    if value is None:
        return expected
    if value != expected:
        raise QueryValidationError(f"{label} must match the request")
    return value


def _as_utc(value) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

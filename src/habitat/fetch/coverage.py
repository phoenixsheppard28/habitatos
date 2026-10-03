"""Conservative checks of declared bounds; not proof of dense observations."""

from datetime import UTC, date, datetime, time

from habitat.contracts import FetchRequest, RawManifest


def requested_instant(value: str, end: bool) -> datetime:
    if len(value) == 10:
        return datetime.combine(date.fromisoformat(value), time.max if end else time.min, tzinfo=UTC)

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def coverage_gaps(manifest: RawManifest, request: FetchRequest) -> list[str]:
    query, requirements = request.input.query, request.input.requirements
    coverage = manifest.coverage
    gaps = []

    bbox = requirements.bbox or (query.region or {}).get("bbox")
    if bbox:
        actual = coverage.bbox
        if actual is None:
            gaps.append("geographic coverage unknown")
        elif not (actual[0] <= bbox[0] and actual[1] <= bbox[1] and actual[2] >= bbox[2] and actual[3] >= bbox[3]):
            gaps.append("file bounds do not cover the entire requested region")

    for field in ("start", "end"):
        requested = getattr(requirements, field) or getattr(query.time_range, field)
        if not requested:
            continue
        actual = getattr(coverage, field)
        if actual is None:
            gaps.append(f"{field} date unknown")
            continue
        try:
            wanted = requested_instant(requested, end=field == "end")
        except ValueError:
            gaps.append(f"{field} date could not be verified")
            continue
        if (field == "start" and actual > wanted) or (field == "end" and actual < wanted):
            gaps.append(f"file {field} does not cover the requested date")

    return [f"{manifest.artifact_id}: {gap}" for gap in gaps]

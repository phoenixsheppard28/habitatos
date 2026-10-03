"""Conservative checks of declared bounds; not proof of dense observations."""
from datetime import datetime, timezone
import math
from fetch.models import RawManifest, FetchRequest


def coverage_gaps(manifest: RawManifest, request: FetchRequest) -> list[str]:
    q, r = request.input.query, request.input.requirements
    cov = manifest.coverage
    gaps = []
    bbox = r.bbox or (q.region or {}).get('bbox')
    if bbox:
        actual = cov.bbox
        def valid_bounds(value):
            return (isinstance(value, (list, tuple)) and len(value) == 4
                    and all(isinstance(x, (int, float)) and math.isfinite(x) for x in value)
                    and -180 <= value[0] <= value[2] <= 180
                    and -90 <= value[1] <= value[3] <= 90)
        if not valid_bounds(bbox):
            gaps.append('requested geographic bounds could not be verified')
        elif not valid_bounds(actual):
            gaps.append('geographic coverage unknown')
        elif not (actual[0] <= bbox[0] and actual[1] <= bbox[1] and actual[2] >= bbox[2] and actual[3] >= bbox[3]):
            gaps.append('file bounds do not cover the entire requested region')
    for field in ('start', 'end'):
        requested = getattr(r, field) or getattr(q.time_range, field)
        if not requested:
            continue
        actual = getattr(cov, field)
        if not actual:
            gaps.append(f'{field} date unknown')
            continue
        try:
            def instant(value, end=False):
                if len(value) == 10:
                    value += 'T23:59:59+00:00' if end else 'T00:00:00+00:00'
                result = datetime.fromisoformat(value.replace('Z', '+00:00'))
                return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result
            actual_time = instant(actual, field == 'end')
            requested_time = instant(requested, field == 'end')
            if (field == 'start' and actual_time > requested_time) or (field == 'end' and actual_time < requested_time):
                gaps.append(f'file {field} does not cover requested date')
        except ValueError:
            gaps.append(f'{field} date could not be verified')
    return [f'{manifest.artifact_id}: {gap}' for gap in gaps]

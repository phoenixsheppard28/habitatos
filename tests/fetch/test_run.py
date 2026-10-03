from __future__ import annotations

from fetch.models import FetchRequest, FetchRequestInput, FetchRequirements, QuerySpec, TimeRange
from fetch.run import run


def test_run_deterministic_returns_manifests(isolated_data_dir) -> None:
    req = FetchRequest(
        request_id="req-1",
        query_id="q-1",
        input=FetchRequestInput(
            query=QuerySpec(
                query_id="q-1",
                question="Demo movement data",
                task_type="discovery",
                species=["example-antelope"],
                time_range=TimeRange(start="2025-01-01T00:00:00Z", end="2025-01-07T23:59:59Z"),
            ),
            requirements=FetchRequirements(
                species=["example-antelope"],
                data_kinds=["animal_locations"],
            ),
        ),
    )
    resp = run(req, use_agent=False)
    assert resp.status == "ok"
    assert len(resp.output.raw_artifacts) == 1
    assert resp.output.raw_artifacts[0].source.study_id == "study-demo-1"

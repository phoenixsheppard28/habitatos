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


def test_agent_handoff_uses_only_this_requests_receipts(isolated_data_dir):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import patch
    from fetch.service import download_dataset
    from fetch.run import run_with_agent
    download_dataset('fixture-rainfall-001')  # unrelated cached data
    req = FetchRequest(request_id='agent-1', query_id='q-1', input=FetchRequestInput(
        query=QuerySpec(query_id='q-1', question='download movement', task_type='discovery')))
    async def fake_runner(*args, **kwargs):
        download_dataset('fixture-movement-001')
        return SimpleNamespace(final_output='done')
    with patch('fetch.run.build_fetch_agent'), patch('fetch.run.Runner.run', side_effect=fake_runner):
        result = asyncio.run(run_with_agent(req))
    assert len(result.output.raw_artifacts) == 1
    assert result.output.raw_artifacts[0].source.study_id == 'study-demo-1'


def test_agent_claim_without_download_is_insufficient(isolated_data_dir):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch
    from fetch.run import run_with_agent
    req = FetchRequest(request_id='agent-2', query_id='q-1', input=FetchRequestInput(
        query=QuerySpec(query_id='q-1', question='demo', task_type='discovery')))
    with patch('fetch.run.build_fetch_agent'), patch('fetch.run.Runner.run', new=AsyncMock(return_value=SimpleNamespace(final_output='I downloaded everything'))):
        result = asyncio.run(run_with_agent(req))
    assert result.status == 'insufficient_data'
    assert not result.output.raw_artifacts


def test_agent_keeps_downloads_after_failure(isolated_data_dir):
    import asyncio
    from unittest.mock import patch
    from fetch.service import download_dataset
    from fetch.run import run_with_agent
    req = FetchRequest(request_id='agent-failure', query_id='q-1', input=FetchRequestInput(
        query=QuerySpec(query_id='q-1', question='demo', task_type='discovery')))
    async def fake_runner(*args, **kwargs):
        download_dataset('fixture-movement-001')
        raise TimeoutError()
    with patch('fetch.run.build_fetch_agent'), patch('fetch.run.Runner.run', side_effect=fake_runner):
        result = asyncio.run(run_with_agent(req))
    assert result.status == 'partial'
    assert len(result.output.raw_artifacts) == 1
    assert result.error.code == 'agent_failed'


def test_coverage_mismatch_reported(isolated_data_dir):
    req = FetchRequest(request_id='coverage', query_id='q-1', input=FetchRequestInput(
        query=QuerySpec(query_id='q-1', question='Demo', task_type='discovery', species=['example-antelope']),
        requirements=FetchRequirements(start='2026-01-01', end='2026-01-31', bbox=[0, 0, 1, 1])))
    result = run(req)
    assert result.status == 'partial'
    assert any('requested' in warning for warning in result.warnings)
    assert result.output.raw_artifacts[0].coverage.start.startswith('2025')

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def command(tmp_path, *args):
    return subprocess.run([sys.executable, '-m', 'fetch', *args],
        env={**os.environ, 'HABITAT_DATA_DIR': str(tmp_path / 'data')},
        capture_output=True, text=True, timeout=30)


def test_deterministic_cli_saves_consistent_receipts(tmp_path):
    result = command(tmp_path, 'run')
    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert response['status'] == 'ok'
    assert len(response['output']['raw_artifacts']) == 2
    directory = Path(response['extensions']['run_directory'])
    assert json.loads((directory / 'response.json').read_text()) == response
    assert len((directory / 'manifest.jsonl').read_text().splitlines()) == 2


def test_environment_discovery_cli_is_bounded_and_persists_request(tmp_path):
    result = command(tmp_path, 'environment', '--bbox', '10', '-2', '11', '-1',
        '--start', '2024-02-28', '--end', '2024-03-01', '--sources', 'chirps', '--discover-only')
    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert response['status'] == 'discovered'
    assert response['raw_artifacts'] == []
    directory = Path(response['run_directory'])
    assert len((directory / 'events.jsonl').read_text().splitlines()) == 3
    assert (directory / 'manifest.jsonl').read_text() == ''


@pytest.mark.parametrize('args', [('download', 'missing'), ('environment', '--bbox', '10', '-2', '11', '-1', '--start', '2025-02-01', '--end', '2025-01-01', '--sources', 'chirps')])
def test_cli_failure_returns_nonzero_status(tmp_path, args):
    assert command(tmp_path, *args).returncode == 2

"""Smoke-test backend wheels without relying on checkout-only resource files."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


def main():
    wheels = sorted(Path(sys.argv[1]).glob('*.whl'))
    if len(wheels) != 2:
        raise SystemExit('Expected one wheel for each backend package')
    with tempfile.TemporaryDirectory(prefix='habitatos-wheel-test-') as directory:
        for wheel in wheels:
            with zipfile.ZipFile(wheel) as archive:
                archive.extractall(directory)
        code = '''
from pathlib import Path
import habitat, fetch
from habitat.grid import default_grid
from habitat.db import migration_files
from habitat.contracts import load_contract
from fetch.models import FetchRequest
from fetch.run import run
assert Path(habitat.__file__).is_relative_to(Path.cwd())
assert Path(fetch.__file__).is_relative_to(Path.cwd())
assert default_grid().grid_id == 'ease2-global-1km'
assert len(migration_files()) == 6
assert load_contract('tag_vocabulary.json')['ai_keys']
request = FetchRequest.model_validate({
    'request_id': 'wheel', 'query_id': 'q',
    'input': {'query': {'query_id': 'q', 'question': 'demo', 'task_type': 'discovery'}}})
response = run(request)
assert response.status == 'ok'
assert len(response.output.raw_artifacts) == 2
print('Installed wheel smoke test passed')
'''
        subprocess.run([sys.executable, '-c', code], cwd=directory, check=True,
                       env={**os.environ, 'PYTHONPATH': directory,
                            'HABITAT_DATA_DIR': str(Path(directory) / 'data')})


if __name__ == '__main__':
    main()

import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))


@pytest.fixture
def scenarios():
    return deepcopy(json.loads((Path(__file__).parent / "fixtures" / "scenarios.json").read_text()))

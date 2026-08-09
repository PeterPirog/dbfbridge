from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(scope="session")
def sample_input_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generate the DBF fixtures so tests work in a clean checkout."""
    output = tmp_path_factory.mktemp("dbf-fixtures") / "input"
    script = Path(__file__).parent / "fixtures" / "generate_sample_dbf.py"
    namespace: dict[str, Any] = runpy.run_path(str(script), run_name="dbfbridge_fixture_factory")
    namespace["generate"](output)
    return output

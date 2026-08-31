from __future__ import annotations

import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

import dbfbridge

ROOT = Path(__file__).parents[1]


def _pyproject() -> dict[str, object]:
    with (ROOT / "pyproject.toml").open("rb") as infile:
        return tomllib.load(infile)


def test_pypi_metadata_is_complete_and_version_is_released() -> None:
    config = _pyproject()
    project = config["project"]
    build_system = config["build-system"]

    assert project["name"] == "dbfbridge"
    assert project["version"] == dbfbridge.__version__
    assert project["readme"] == "README.md"
    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    assert project["requires-python"] == ">=3.10"
    assert {"Homepage", "Source", "Documentation", "Issues", "Changelog"} <= set(
        project["urls"]
    )
    assert "setuptools>=77.0.3" in build_system["requires"]

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    version = re.escape(project["version"])
    assert re.search(rf"^## \[{version}\] - \d{{4}}-\d{{2}}-\d{{2}}$", changelog, re.MULTILINE)


def test_release_files_and_workflows_stay_synchronized() -> None:
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    publishing = (ROOT / "PUBLISHING.md").read_text(encoding="utf-8")
    publish_workflow = (ROOT / ".github" / "workflows" / "publish.yml").read_text(
        encoding="utf-8"
    )
    ci_workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "include PUBLISHING.md" in manifest
    assert "recursive-include .github/workflows *.yml" in manifest
    assert "Workflow | `publish.yml`" in publishing
    assert f"dbfbridge=={dbfbridge.__version__}" in publishing
    assert "name: pypi" in publish_workflow
    assert "pypa/gh-action-pypi-publish@release/v1" in publish_workflow
    for version in ("3.10", "3.11", "3.12", "3.13", "3.14"):
        assert f'python: "{version}"' in ci_workflow

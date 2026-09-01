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
    assert {"Homepage", "Source", "Documentation", "Issues", "Changelog"} <= set(project["urls"])
    assert "setuptools>=77.0.3" in build_system["requires"]

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    version = re.escape(project["version"])
    assert re.search(rf"^## \[{version}\] - \d{{4}}-\d{{2}}-\d{{2}}$", changelog, re.MULTILINE)


def test_release_files_and_workflows_stay_synchronized() -> None:
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    publishing = (ROOT / "PUBLISHING.md").read_text(encoding="utf-8")
    publish_workflow = (ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
    ci_workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "include PUBLISHING.md" in manifest
    assert "recursive-include .github/workflows *.yml" in manifest
    assert "Workflow | `publish.yml`" in publishing
    assert f"dbfbridge=={dbfbridge.__version__}" in publishing
    assert "name: pypi" in publish_workflow
    assert "pypa/gh-action-pypi-publish@release/v1" in publish_workflow
    for version in ("3.10", "3.11", "3.12", "3.13", "3.14"):
        assert f'python: "{version}"' in ci_workflow


def test_changelog_ordering_and_version_consistency() -> None:
    """CHANGELOG must have exactly one [Unreleased] and one release heading
    for the current project version, with Unreleased appearing first."""

    from dbf_bridge import __version__ as _version

    pyproject = Path(__file__).parents[1] / "pyproject.toml"
    project_version = pyproject.read_text(encoding="utf-8").split('version = "')[1].split('"')[0]
    changelog = (Path(__file__).parents[1] / "CHANGELOG.md").read_text(encoding="utf-8")

    # Exactly one Unreleased section
    assert changelog.count("## [Unreleased]") == 1, (
        "CHANGELOG must contain exactly one '## [Unreleased]' heading"
    )

    # Exactly one release heading for the current project version
    version_heading = f"## [{_version}] - "
    version_heading_count = len(re.findall(re.escape(version_heading), changelog))
    assert version_heading_count == 1, (
        f"CHANGELOG must contain exactly one dated heading for version "
        f"{_version!r}, found {version_heading_count}"
    )

    # Unreleased appears before the current version heading
    assert changelog.index("## [Unreleased]") < changelog.index(version_heading), (
        "CHANGELOG: '## [Unreleased]' must appear before the current version heading"
    )

    # Verify the version in pyproject.toml matches the package
    assert project_version == _version, (
        f"pyproject.toml version {project_version!r} != package __version__ {_version!r}"
    )

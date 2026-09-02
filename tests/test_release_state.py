"""Unit tests for the release-final-state validator (scripts/check_release_state.py).

The validator is exercised against temporary fixture trees — the real
repository docs are never mutated.  The current release-preparation state of
the repository is intentionally NOT expected to pass the final-state gate
(that rejection is proven here with fixtures and, against the real tree, in
the documented manual gate run before publication).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
_SCRIPT = ROOT / "scripts" / "check_release_state.py"

_spec = importlib.util.spec_from_file_location("check_release_state", _SCRIPT)
assert _spec is not None and _spec.loader is not None
check_release_state = importlib.util.module_from_spec(_spec)
sys.modules["check_release_state"] = check_release_state
_spec.loader.exec_module(check_release_state)

PYPROJECT = '[project]\nname = "dbfbridge"\nversion = "{version}"\n'
INIT = '__version__ = "{version}"\n'
CHANGELOG_FINAL = (
    "# Changelog\n\n"
    "## [Unreleased]\n\n"
    "## [0.3.0] - 2026-09-15\n\n"
    "### Added\n- Optional dependency split\n\n"
    "## [0.2.0] - 2026-09-01\n\n"
    "### Added\n- Direct Read Core\n"
)
CHANGELOG_UNRELEASED = CHANGELOG_FINAL.replace("## [0.3.0] - 2026-09-15", "## [0.3.0] - Unreleased")
README_FINAL = (
    "# dbfbridge\n\n"
    "> Status: **0.3.0 (alpha)**.\n\n"
    "This guide documents the dbfbridge 0.3.0 PyPI distribution.\n"
    "Check PyPI for currently available releases.\n"
)
README_PREP = README_FINAL.replace(
    "> Status: **0.3.0 (alpha)**.",
    "> Status: **0.3.0 (alpha)** — the 0.3.0 release is being prepared and has "
    "not yet been verified as available from PyPI.",
)
GUIDE_FINAL = (
    "# Using dbfbridge from PyPI\n\n"
    "This guide documents the dbfbridge 0.3.0 PyPI distribution.\n"
    "Check PyPI for currently available releases.\n"
)
GUIDE_PENDING = GUIDE_FINAL.replace(
    "This guide documents the dbfbridge 0.3.0 PyPI distribution.",
    "The 0.3.0 publication is pending; the release is being prepared.",
)


def _make_tree(
    tmp_path,
    *,
    version: str = "0.3.0",
    changelog: str = CHANGELOG_FINAL,
    readme: str = README_FINAL,
    guide: str = GUIDE_FINAL,
):
    root = tmp_path / "repo"
    (root / "src" / "dbf_bridge").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "pyproject.toml").write_text(PYPROJECT.format(version=version), encoding="utf-8")
    (root / "src" / "dbf_bridge" / "__init__.py").write_text(
        INIT.format(version=version), encoding="utf-8"
    )
    (root / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    (root / "README.md").write_text(readme, encoding="utf-8")
    (root / "docs" / "pypi-usage.md").write_text(guide, encoding="utf-8")
    return root


def test_valid_final_release_state_passes(tmp_path) -> None:
    root = _make_tree(tmp_path)
    assert check_release_state.check_release_state("v0.3.0", root) == []


def test_tag_version_mismatch_fails(tmp_path) -> None:
    root = _make_tree(tmp_path)
    violations = check_release_state.check_release_state("v0.2.1", root)
    assert any("pyproject.toml" in v for v in violations)
    assert any("__version__" in v for v in violations)


def test_invalid_tag_format_fails(tmp_path) -> None:
    root = _make_tree(tmp_path)
    violations = check_release_state.check_release_state("release-0.3.0", root)
    assert violations == [
        "tag 'release-0.3.0' does not encode a release version (expected v<pep440>, e.g. v0.3.0)"
    ]


def test_unreleased_changelog_section_fails(tmp_path) -> None:
    root = _make_tree(tmp_path, changelog=CHANGELOG_UNRELEASED)
    violations = check_release_state.check_release_state("v0.3.0", root)
    assert any("Unreleased" in v for v in violations)
    assert any("no dated heading" in v for v in violations)


def test_readme_release_candidate_wording_fails(tmp_path) -> None:
    root = _make_tree(tmp_path, readme=README_PREP)
    violations = check_release_state.check_release_state("v0.3.0", root)
    assert any("README.md" in v and "release is being prepared" in v for v in violations)
    assert any("README.md" in v and "not yet been verified" in v for v in violations)


def test_pypi_guide_publication_pending_wording_fails(tmp_path) -> None:
    root = _make_tree(tmp_path, guide=GUIDE_PENDING)
    violations = check_release_state.check_release_state("v0.3.0", root)
    assert any("docs/pypi-usage.md" in v for v in violations)


def test_dated_changelog_with_neutral_final_docs_passes(tmp_path) -> None:
    root = _make_tree(
        tmp_path,
        changelog=CHANGELOG_FINAL,
        readme=README_FINAL,
        guide=GUIDE_FINAL,
    )
    assert check_release_state.check_release_state("v0.3.0", root) == []


def test_validator_does_not_require_a_publication_claim(tmp_path) -> None:
    """The timeless final wording (no 'already published' claim) must pass."""
    root = _make_tree(tmp_path)  # GUIDE_FINAL / README_FINAL make no availability claim
    assert check_release_state.check_release_state("v0.3.0", root) == []


def test_pyproject_and_init_are_parsed_statically(tmp_path) -> None:
    root = _make_tree(tmp_path, version="0.4.0")
    violations = check_release_state.check_release_state("v0.3.0", root)
    assert any("0.4.0" in v and "pyproject.toml" in v for v in violations)

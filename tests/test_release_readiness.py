"""Release-readiness guards for the stable 1.x line.

These tests protect the release contract that pure unit tests cannot see:
version synchronization across `pyproject.toml`, the package `__version__`,
and the built wheel METADATA; reusable (version-agnostic) release
workflows; and truthfulness of the user-facing release documentation.
"""

from __future__ import annotations

import email.parser
import re
import zipfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

import dbf_bridge

ROOT = Path(__file__).parents[1]
DIST = ROOT / "dist"


def _pyproject_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def _changelog() -> str:
    return (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# version synchronization
# ---------------------------------------------------------------------------


def test_version_is_synchronized_across_pyproject_and_both_namespaces() -> None:
    version = _pyproject_version()
    assert version == dbf_bridge.__version__
    import dbfbridge

    assert version == dbfbridge.__version__


def _built_wheel() -> Path | None:
    wheels = sorted(DIST.glob("dbfbridge-*.whl"))
    return wheels[0] if wheels else None


def test_built_wheel_metadata_matches_project_contract() -> None:
    """When a built wheel exists (local `python -m build` or release CI),
    its METADATA must agree with pyproject.toml and carry the typing and
    optional-dependency contract. The publish workflow performs the same
    check on its exact artifact."""
    wheel = _built_wheel()
    if wheel is None:
        import pytest

        pytest.skip("no built wheel in dist/ (run `python -m build` to exercise this test)")
        raise AssertionError("unreachable")  # pragma: no cover - skip() above

    version = _pyproject_version()
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        metadata_path = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = email.parser.BytesParser().parsebytes(archive.read(metadata_path))

    assert metadata["Name"] == "dbfbridge"
    assert metadata["Version"] == version, "wheel METADATA version drifted from pyproject.toml"
    assert "Typing :: Typed" in metadata.get_all("Classifier", [])
    for marker in ("dbf_bridge/py.typed", "dbfbridge/py.typed"):
        assert marker in names, f"{marker} missing from the built wheel"

    requires_dist = metadata.get_all("Requires-Dist") or []
    base_requirements = [line for line in requires_dist if "extra ==" not in line]
    assert base_requirements == ["dbfread>=2.0.7"], base_requirements

    extras = set(metadata.get_all("Provides-Extra", []))
    for documented_extra in ("write", "xlsx", "fast", "all", "import"):
        assert documented_extra in extras, f"missing extra: {documented_extra}"


# ---------------------------------------------------------------------------
# reusable release workflows (no hardcoded historical expected version)
# ---------------------------------------------------------------------------


def test_release_workflows_do_not_hardcode_expected_version() -> None:
    """The wheel smokes read the expected version from pyproject.toml; the
    workflows must stay reusable for future versions."""
    pattern = re.compile(r'--expected-version[ ="]*\d')
    for workflow in ("publish.yml", "ci.yml"):
        text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
        match = pattern.search(text)
        assert match is None, f"{workflow} hardcodes an expected version: {match!r}"


def test_publish_workflow_smokes_and_publishes_the_same_artifact() -> None:
    """Build-once contract: the release-state gate runs before the build,
    both smokes run in the build job on dist/, the artifact is uploaded
    once, and the publish job only downloads and uploads those exact
    files."""
    publish = (ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
    build_section = publish.split("publish:", 1)[0]

    # Release-state gate: runs on the exact tag BEFORE anything is built.
    gate_position = build_section.index("scripts/check_release_state.py")
    build_position = build_section.index("python -m build")
    assert gate_position < build_position, "release-state gate must run before the build"
    assert 'check_release_state.py --tag "$GITHUB_REF_NAME"' in build_section

    assert "python -m build" in build_section
    assert "python -m twine check dist/*" in build_section
    assert "scripts/release_wheel_smoke.py --wheel" in build_section
    assert "scripts/pypi_install_smoke.py --wheel" in build_section
    assert "actions/upload-artifact" in build_section
    # The wheel/sdist structural gates live in the SHARED verifier
    # (scripts/verify_release_artifacts.py — proven by
    # test_release_artifacts.py and test_shared_artifact_verifier_runs_in_ci_and_publish);
    # the sdist PKG-INFO check is part of that verifier, not an inline copy.

    publish_section = "publish:" + publish.split("publish:", 1)[1]
    assert "download-artifact" in publish_section
    assert "python -m build" not in publish_section, "publish job must not rebuild"
    assert "pypa/gh-action-pypi-publish@release/v1" in publish_section
    # Trusted Publishing only: no token/password-based upload.
    assert "PYPI_TOKEN" not in publish
    assert "password:" not in publish


def test_shared_artifact_verifier_runs_in_ci_and_publish() -> None:
    """ONE shared artifact gate: both the CI package job and the publish
    build job must run ``scripts/verify_release_artifacts.py`` after
    ``python -m build`` + ``twine check`` and before the wheel smokes, so
    every PR proves the same artifact structure that later gets published."""
    for workflow in ("ci.yml", "publish.yml"):
        text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
        assert "python scripts/verify_release_artifacts.py --dist dist" in text, (
            f"{workflow} does not run the shared release-artifact verifier"
        )
        build_position = text.index("python -m build")
        twine_position = text.index("python -m twine check dist/*")
        verifier_position = text.index("python scripts/verify_release_artifacts.py")
        smoke_position = text.index("scripts/release_wheel_smoke.py --wheel")
        assert build_position < twine_position < verifier_position < smoke_position, (
            f"{workflow}: build → twine → verify_release_artifacts → smokes ordering violated"
        )
    # The duplicated inline verifier must stay gone: one implementation.
    publish = (ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
    assert "email.parser" not in publish, "publish.yml must not carry an inline artifact verifier"
    assert "python - <<'PY'" not in publish, "publish.yml must delegate to the shared verifier"


# ---------------------------------------------------------------------------
# release documentation truthfulness
# ---------------------------------------------------------------------------


def _current_version_section_is_unreleased() -> bool:
    version = re.escape(_pyproject_version())
    return bool(re.search(rf"^## \[{version}\] - Unreleased$", _changelog(), re.MULTILINE))


def _readme_status_mentions(readme: str, version: str) -> bool:
    """Whether the README status area carries *version* (release-stage
    neutral: the maturity marker may change with the release stage)."""
    return bool(re.search(rf"\*\*{re.escape(version)}\b", readme))


def test_readme_release_status_is_truthful() -> None:
    version = _pyproject_version()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert _readme_status_mentions(readme, version)
    assert "docs/pypi-usage.md" in readme
    assert "docs/migration-1.0.md" in readme
    # The unsupported availability claim must never come back: repository
    # evidence does not prove any "previous published PyPI version".
    assert "resolves to the previous published" not in readme

    if _current_version_section_is_unreleased():
        # While the release is only prepared, the README must carry an
        # explicit release-preparation marker and must not claim the
        # version is already published.
        assert "available on PyPI" not in readme
        assert "release is being prepared" in readme or "release candidate" in readme


def test_readiness_status_logic_does_not_require_the_current_stage() -> None:
    """The release-readiness logic is release-stage neutral: a hypothetical
    future stable version would pass the same README status check without
    rewriting the tests.  (The current project version and Development
    Status classifier are intentionally NOT changed here — that belongs to
    final release preparation.)"""
    hypothetical_stable = "# dbfbridge\n\n> Status: **1.0.0 (stable)**.\n"
    assert _readme_status_mentions(hypothetical_stable, "1.0.0")
    hypothetical_future = "> Status: **9.9.9**.\n"
    assert _readme_status_mentions(hypothetical_future, "9.9.9")


def test_pypi_usage_guide_is_installed_distribution_only() -> None:
    """The canonical PyPI guide must work for a user with only Python, pip,
    and DBF/FPT files — no repository checkout, no editable install, no
    PYTHONPATH, and no private-module imports."""
    guide = (ROOT / "docs" / "pypi-usage.md").read_text(encoding="utf-8")

    forbidden = [
        "git clone",
        "export PYTHONPATH",
        "$env:PYTHONPATH",
        "set PYTHONPATH",
        "pip install -e",
        "python -m pip install -e",
        "from dbf_bridge.core",
        "from dbf_bridge.importer",
        "from dbf_bridge.exporter",
    ]
    present = [marker for marker in forbidden if marker in guide]
    assert not present, f"repository-only content in the PyPI guide: {present}"
    # The guide must explicitly state the no-checkout contract.
    assert "no repository checkout" in guide

    for extra in ("[write]", "[xlsx]", "[write,xlsx]", "[fast]", "[all]", "[import]"):
        assert f"dbfbridge{extra}" in guide or f'dbfbridge{extra}"' in guide, (
            f"install profile {extra} missing from the PyPI guide"
        )


def test_migration_guide_exists_and_covers_the_1_x_contract() -> None:
    guide_path = ROOT / "docs" / "migration-1.0.md"
    assert guide_path.is_file()
    guide = guide_path.read_text(encoding="utf-8")

    for extra in ("[write]", "[xlsx]", "[write,xlsx]", "[fast]", "[all]", "[import]"):
        assert extra in guide, f"migration guide does not cover {extra}"
    assert "OptionalDependencyMissingError" in guide
    assert "cancel_check" in guide
    assert 'encoding="mazovia"' in guide
    assert "dbf_bridge" in guide
    # CDX expectations must be explicit, not overpromised.
    assert "reports structural CDX presence" in guide
    assert "not parse or reconstruct" in guide


def test_changelog_current_version_section_documents_the_release() -> None:
    """The current version has a changelog section (dated for a published
    release, or an explicit `Unreleased` section while the release is only
    being prepared) and the changelog carries the version comparison link."""
    version = _pyproject_version()
    changelog = _changelog()
    match = re.search(
        rf"^## \[{re.escape(version)}\] - .*$(.*?)(?=^## \[|\Z)",
        changelog,
        re.MULTILINE | re.DOTALL,
    )
    assert match, f"no changelog section for {version}"
    assert f"[{version}]: https://github.com/PeterPirog/dbfbridge/compare/" in changelog


# ---------------------------------------------------------------------------
# install-profile smoke contract ([import] alias included)
# ---------------------------------------------------------------------------


def _pypi_install_smoke_module():
    import importlib.util

    script = ROOT / "scripts" / "pypi_install_smoke.py"
    spec = importlib.util.spec_from_file_location("pypi_install_smoke_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_install_profile_smoke_covers_every_documented_profile() -> None:
    """The canonical INSTALL_PROFILES constant is the single source of truth
    for the install profiles the wheel smoke verifies — including the
    `[import]` compatibility alias."""
    smoke = _pypi_install_smoke_module()
    assert smoke.INSTALL_PROFILES == (
        "base",
        "write",
        "xlsx",
        "write,xlsx",
        "fast",
        "all",
        "import",
    )


def test_install_profile_smoke_has_an_import_alias_venv() -> None:
    """Guard against silently dropping the [import] fresh-venv smoke."""
    source = (ROOT / "scripts" / "pypi_install_smoke.py").read_text(encoding="utf-8")
    assert 'build_fresh_venv(work_root, "venv-import")' in source
    assert 'install_extra(venv_import, wheel, "import", dist_dir)' in source
    assert "import_extra_smoke" in source
    # The orchestrator self-checks every canonical profile reported PASS.
    assert "p not in profiles_passed" in source

"""Deterministic release-final-state validator (stdlib only, no runtime deps).

A Git tag must point at a commit that already contains the FINAL release
state.  This gate rejects a tagged build whose sources still carry the
release-preparation state, so the ordering

    tag first, then modify CHANGELOG/docs

is impossible: the publish workflow runs this validator on the exact tag
BEFORE building.

Checked for the version encoded in the tag (``vX.Y.Z``):

- ``pyproject.toml`` ``project.version`` equals the tag version;
- the ``__version__`` declaration in ``src/dbf_bridge/__init__.py`` equals
  the tag version (static parse — the package is never imported);
- ``CHANGELOG.md`` contains a dated section ``## [X.Y.Z] - YYYY-MM-DD``
  and no longer the ``## [X.Y.Z] - Unreleased`` release-prep heading;
- ``README.md`` and ``docs/pypi-usage.md`` no longer contain release-
  preparation markers (release candidate / preparation / publication-pending
  wording) for the current release;
- no stale "not yet published" statement remains for the current version.

The validator never requires the docs to claim that the version is already
available on PyPI — the final wording stays timeless (for example: "This
guide documents the dbfbridge 0.3.0 PyPI distribution.  Check PyPI for
currently available releases.").

Usage:
    python scripts/check_release_state.py --tag v0.3.0

Exit code 0 = final release state, 1 = violations (each printed).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Wording that documents a release *preparation*.  None of these may remain
#: in the packaged README/docs of a tagged release.
RELEASE_PREP_MARKERS = (
    "release candidate",
    "release preparation",
    "release-preparation",
    "release is being prepared",
    "not yet published",
    "not yet been verified",
    "publication has not happened",
    "publication must be verified",
    "publication pending",
    "resolves to the previous published",
)

_TAG_PATTERN = re.compile(r"^v(\d+\.\d+\.\d+(?:[+-][0-9A-Za-z.-]+)?)$")
_VERSION_DECLARATION = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)


def parse_tag_version(tag: str) -> str | None:
    """Return the PEP 440 version encoded in *tag* (``v0.3.0`` -> ``0.3.0``)."""
    match = re.match(_TAG_PATTERN, tag)
    return match.group(1) if match else None


def _pyproject_version(root: Path) -> str | None:
    """Extract ``project.version`` without a TOML dependency (regex parse).

    ``[project]`` is the only table declaring a bare ``version = "..."``
    at line start in this project's ``pyproject.toml``.
    """
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    project_section = text.split("[project]", 1)[-1]
    match = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', project_section, re.MULTILINE)
    return match.group(1) if match else None


def declared_init_version(root: Path) -> str | None:
    """Statically read ``__version__`` from ``src/dbf_bridge/__init__.py``."""
    init = root / "src" / "dbf_bridge" / "__init__.py"
    match = _VERSION_DECLARATION.search(init.read_text(encoding="utf-8"))
    return match.group(1) if match else None


def check_release_state(tag: str, root: Path | None = None) -> list[str]:
    """Return the list of release-state violations; an empty list means PASS."""
    root = (root or _PROJECT_ROOT).resolve()
    violations: list[str] = []

    version = parse_tag_version(tag)
    if version is None:
        return [f"tag {tag!r} does not encode a release version (expected v<pep440>, e.g. v0.3.0)"]

    pyproject = _pyproject_version(root)
    if pyproject is None:
        violations.append("pyproject.toml: cannot parse project.version")
    elif pyproject != version:
        violations.append(
            f"pyproject.toml project.version {pyproject!r} != tag version {version!r}"
        )

    declared = declared_init_version(root)
    if declared is None:
        violations.append("src/dbf_bridge/__init__.py: cannot parse __version__ declaration")
    elif declared != version:
        violations.append(
            f"src/dbf_bridge/__init__.py __version__ {declared!r} != tag version {version!r}"
        )

    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    dated_heading = re.search(
        rf"^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}$",
        changelog,
        re.MULTILINE,
    )
    unreleased_heading = re.search(
        rf"^## \[{re.escape(version)}\] - Unreleased$", changelog, re.MULTILINE
    )
    if dated_heading is None:
        violations.append(
            f"CHANGELOG.md has no dated heading '## [{version}] - YYYY-MM-DD'; "
            "the final release commit must set the real release date before tagging"
        )
    if unreleased_heading is not None:
        violations.append(
            f"CHANGELOG.md still marks {version} as 'Unreleased'; set the real "
            "release date in the final release commit before tagging"
        )

    for relative in ("README.md", "docs/pypi-usage.md"):
        content = (root / relative).read_text(encoding="utf-8")
        for marker in RELEASE_PREP_MARKERS:
            if marker in content:
                violations.append(
                    f"{relative} still contains release-preparation wording "
                    f"{marker!r}; the final release commit must replace it with "
                    "timeless released-distribution wording"
                )

    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tag",
        required=True,
        help="release tag being published, e.g. v0.3.0",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=_PROJECT_ROOT,
        help="project root to validate (default: repository of this script)",
    )
    args = parser.parse_args(argv)

    violations = check_release_state(args.tag, args.root)
    if violations:
        print(f"RELEASE STATE: FAIL (tag {args.tag})", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation}", file=sys.stderr)
        return 1

    print(f"RELEASE STATE: PASS (tag {args.tag} points at a final release commit)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

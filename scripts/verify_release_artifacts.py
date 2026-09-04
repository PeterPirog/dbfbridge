"""Verify the structural contract of built release artifacts.

One shared, version-neutral gate used by BOTH the CI package job and the
publish workflow (build job), so every pull request proves the same artifact
structure that later gets published:

- exactly one ``dbfbridge-*.whl``  — METADATA Name/Version (against
  ``pyproject.toml``), ``Typing :: Typed``, both ``py.typed`` markers, the
  base dependency contract (``dbfread`` only) and the documented user extras;
- exactly one ``dbfbridge-*.tar.gz`` — ``PKG-INFO`` Name/Version and the
  required public files (README, LICENSE, pyproject, PUBLISHING, public docs,
  both ``py.typed`` markers), root-prefix aware.

stdlib only; no package import; no network; deterministic.  The expected
version is read from ``pyproject.toml`` next to the project root (pass
``--expected-version`` to verify artifacts built elsewhere).

Usage:
    python scripts/verify_release_artifacts.py --dist dist
"""

from __future__ import annotations

import argparse
import email.parser
import sys
import tarfile
import zipfile
from pathlib import Path

import tomllib

PROJECT_ROOT = Path(__file__).resolve().parent.parent

WHEEL_GLOB = "dbfbridge-*.whl"
SDIST_GLOB = "dbfbridge-*.tar.gz"

REQUIRED_SDIST_FILES = (
    "README.md",
    "LICENSE",
    "pyproject.toml",
    "PUBLISHING.md",
    "docs/pypi-usage.md",
    "docs/api-1.0.md",
    "docs/migration-1.0.md",
    "docs/compatibility-vfp.md",
    "src/dbf_bridge/py.typed",
    "src/dbfbridge/py.typed",
)

DOCUMENTED_EXTRAS = ("write", "xlsx", "fast", "all", "import")


def _pyproject_version(project_root: Path) -> str:
    data = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def verify_release_artifacts(dist: Path, expected_version: str) -> list[str]:
    """Return the list of artifact violations; an empty list means PASS."""
    violations: list[str] = []

    wheels = sorted(dist.glob(WHEEL_GLOB))
    if len(wheels) != 1:
        return [
            f"expected exactly one {WHEEL_GLOB}, found {len(wheels)}: "
            f"{[path.name for path in wheels]}"
        ]
    sdists = sorted(dist.glob(SDIST_GLOB))
    if len(sdists) != 1:
        violations.append(
            f"expected exactly one {SDIST_GLOB}, found {len(sdists)}: "
            f"{[path.name for path in sdists]}"
        )

    violations.extend(_verify_wheel(wheels[0], expected_version))
    if sdists:
        violations.extend(_verify_sdist(sdists[0], expected_version))
    return violations


def _verify_wheel(wheel: Path, expected_version: str) -> list[str]:
    violations: list[str] = []
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        metadata_path = next(
            name for name in names if name.endswith(".dist-info/METADATA")
        )
        metadata = email.parser.BytesParser().parsebytes(archive.read(metadata_path))
    if metadata["Name"] != "dbfbridge":
        violations.append(f"wheel METADATA Name {metadata['Name']!r} != 'dbfbridge'")
    if metadata["Version"] != expected_version:
        violations.append(
            f"wheel METADATA Version {metadata['Version']!r} != {expected_version!r}"
        )
    if "Typing :: Typed" not in (metadata.get_all("Classifier") or []):
        violations.append("wheel METADATA lacks the 'Typing :: Typed' classifier")
    for marker in ("dbf_bridge/py.typed", "dbfbridge/py.typed"):
        if marker not in names:
            violations.append(f"wheel lacks {marker}")
    requires_dist = metadata.get_all("Requires-Dist") or []
    base_requirements = [line for line in requires_dist if "extra ==" not in line]
    if base_requirements != ["dbfread>=2.0.7"]:
        violations.append(
            f"wheel base Requires-Dist {base_requirements!r} != ['dbfread>=2.0.7']"
        )
    extras = set(metadata.get_all("Provides-Extra") or [])
    for documented in DOCUMENTED_EXTRAS:
        if documented not in extras:
            violations.append(f"wheel lacks the documented extra {documented!r}")
    return violations


def _verify_sdist(sdist: Path, expected_version: str) -> list[str]:
    violations: list[str] = []
    with tarfile.open(sdist) as tar:
        members = {member.name for member in tar.getmembers() if member.isfile()}
        root = sdist.name[: -len(".tar.gz")]
        pkg_info_path = f"{root}/PKG-INFO"
        if pkg_info_path not in members:
            violations.append(f"sdist lacks {pkg_info_path}")
            return violations
        pkg_info = email.parser.BytesParser().parsebytes(
            tar.extractfile(pkg_info_path).read()  # type: ignore[union-attr]
        )
        if pkg_info["Name"] != "dbfbridge":
            violations.append(f"sdist PKG-INFO Name {pkg_info['Name']!r} != 'dbfbridge'")
        if pkg_info["Version"] != expected_version:
            violations.append(
                f"sdist PKG-INFO Version {pkg_info['Version']!r} != {expected_version!r}"
            )
        for required in REQUIRED_SDIST_FILES:
            if f"{root}/{required}" not in members:
                violations.append(f"sdist lacks {required}")
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dist",
        type=Path,
        default=Path("dist"),
        help="directory holding the built distributions (default: dist)",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=PROJECT_ROOT,
        help="project root providing pyproject.toml (default: this repository)",
    )
    parser.add_argument(
        "--expected-version",
        default=None,
        help="expected distribution version (default: read from pyproject.toml)",
    )
    args = parser.parse_args(argv)

    dist = args.dist if args.dist.is_absolute() else Path.cwd() / args.dist
    if not dist.is_dir():
        print(f"ARTIFACTS: FAIL — distribution directory not found: {dist}", file=sys.stderr)
        return 1
    try:
        expected_version = args.expected_version or _pyproject_version(args.project_root)
    except (OSError, KeyError, ValueError) as exc:
        print(f"ARTIFACTS: FAIL — cannot read pyproject version: {exc}", file=sys.stderr)
        return 1

    violations = verify_release_artifacts(dist, expected_version)
    if violations:
        print(f"ARTIFACTS: FAIL (expected version {expected_version})", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation}", file=sys.stderr)
        return 1
    print(f"ARTIFACTS: PASS (one wheel, one sdist, expected version {expected_version})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

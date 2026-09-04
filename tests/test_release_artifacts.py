"""Release-artifact verifier regression (scripts/verify_release_artifacts.py).

The shared verifier is the ONE implementation of the structural artifact
gate used by both the CI package job and the publish build job.  These
tests prove its success path and its negative boundaries with synthetic
wheel/sdist fixtures (stdlib zip/tar) — no PyPI, no runtime imports.
"""

from __future__ import annotations

import email.parser
import importlib.util
import io
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).parents[1]
_SCRIPT = ROOT / "scripts" / "verify_release_artifacts.py"

_spec = importlib.util.spec_from_file_location("verify_release_artifacts", _SCRIPT)
assert _spec is not None and _spec.loader is not None
verify_module = importlib.util.module_from_spec(_spec)
sys.modules["verify_release_artifacts"] = verify_module
_spec.loader.exec_module(verify_module)

VERSION = "9.9.9"

METADATA_TEMPLATE = "\n".join(
    [
        "Metadata-Version: 2.1",
        "Name: {name}",
        "Version: {version}",
        "Summary: dbfbridge wheel",
        "{classifiers}",
        "{requires_dist}",
        "{provides_extra}",
        "",
    ]
)
CLASSIFIER_TYPED = "Classifier: Typing :: Typed"
REQUIRES_BASE = "Requires-Dist: dbfread>=2.0.7"
EXTRAS = ("write", "xlsx", "fast", "all", "import")

SDIST_REQUIRED_FILES = verify_module.REQUIRED_SDIST_FILES


def _metadata(
    *,
    name: str = "dbfbridge",
    version: str = VERSION,
    typed: bool = True,
    base: bool = True,
    extras: bool = True,
) -> bytes:
    lines = [
        f"Name: {name}",
        f"Version: {version}",
    ]
    lines.append("Classifier: Typing :: Typed" if typed else "Classifier: Development Status :: 3 - Alpha")
    if base:
        lines.append("Requires-Dist: dbfread>=2.0.7")
    if extras:
        for extra in EXTRAS:
            lines.append(f"Provides-Extra: {extra}")
    return "\n".join(lines).encode("utf-8")


def _make_wheel(directory: Path, version: str = VERSION, **kwargs: bool) -> Path:
    wheel = directory / f"dbfbridge-{version}-py3-none-any.whl"
    payload = _metadata(version=version, **kwargs)
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("dbfbridge/__init__.py", "")
        archive.writestr("dbf_bridge/py.typed", "")
        archive.writestr("dbfbridge/py.typed", "")
        archive.writestr(
            "dbfbridge-9.9.9.dist-info/METADATA" if version == VERSION else f"dbfbridge-{version}.dist-info/METADATA",
            payload,
        )
    return wheel


def _make_sdist(directory: Path, version: str = VERSION, *, missing: tuple[str, ...] = ()) -> Path:
    sdist = directory / f"dbfbridge-{version}.tar.gz"
    root = f"dbfbridge-{version}"

    def dropped(name: str) -> bool:
        return name in missing or f"{root}/{name}" in missing

    pkg_info = _metadata(version=version, typed=False, base=False, extras=False)
    members: dict[str, str] = {}
    if not dropped("PKG-INFO"):
        members[f"{root}/PKG-INFO"] = pkg_info.decode("utf-8")
    all_files = {
        "README.md": "# dbfbridge\n",
        "LICENSE": "MIT\n",
        "pyproject.toml": '[project]\nname = "dbfbridge"\nversion = "9.9.9"\n',
        "PUBLISHING.md": "# Publishing\n",
        "docs/pypi-usage.md": "# PyPI usage\n",
        "docs/api-1.0.md": "# API contract\n",
        "docs/migration-1.0.md": "# Migration\n",
        "docs/compatibility-vfp.md": "# Compatibility\n",
        "src/dbf_bridge/py.typed": "",
        "src/dbfbridge/py.typed": "",
    }
    for name, content in all_files.items():
        if name not in missing:
            members[f"{root}/{name}"] = content
    with tarfile.open(sdist, "w:gz") as tar:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            data = content.encode("utf-8")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return sdist


def _verify(dist: Path) -> list[str]:
    return verify_module.verify_release_artifacts(dist, VERSION)


# ---------------------------------------------------------------------------
# success path
# ---------------------------------------------------------------------------


def test_valid_synthetic_pair_passes(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    _make_wheel(dist)
    _make_sdist(dist)
    assert _verify(dist) == []


def test_cli_passes_on_valid_synthetic_pair(tmp_path: Path, capsys) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    _make_wheel(dist)
    _make_sdist(dist)
    exit_code = verify_module.main(["--dist", str(dist), "--expected-version", VERSION])
    assert exit_code == 0


def test_verifier_is_stdlib_only() -> None:
    source = _SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("import dbf", "import dbfread", "import requests", "import urllib", "from dbf_bridge"):
        assert forbidden not in source


# ---------------------------------------------------------------------------
# negative boundaries
# ---------------------------------------------------------------------------


def test_zero_wheels_fails(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    _make_sdist(dist)
    assert any("exactly one dbfbridge-*.whl" in violation for violation in _verify(dist))


def test_two_wheels_fail(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    _make_wheel(dist, "9.9.8")
    _make_wheel(dist)
    violations = _verify(dist)
    assert any("expected exactly one dbfbridge-*.whl" in violation for violation in violations)


def test_zero_sdists_fail(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    _make_wheel(dist)
    violations = _verify(dist)
    assert any("expected exactly one dbfbridge-*.tar.gz" in violation for violation in violations)


def test_two_sdists_fail(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    _make_wheel(dist)
    _make_sdist(dist)
    moved = dist / "dbfbridge-9.9.9.alt.tar.gz"
    moved.write_bytes((dist / "dbfbridge-9.9.9.tar.gz").read_bytes())
    violations = _verify(dist)
    assert any("expected exactly one dbfbridge-*.tar.gz" in violation for violation in violations)


def test_wrong_wheel_version_fails(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel = _make_wheel(dist)
    # rebuild the wheel with a mismatched METADATA version
    with __import__("zipfile").ZipFile(wheel) as archive:
        payload = _metadata(version="1.2.3")
    wheel.unlink()
    with __import__("zipfile").ZipFile(wheel, "w") as archive:
        archive.writestr("dbfbridge-9.9.9.dist-info/METADATA", payload)
        archive.writestr("dbf_bridge/py.typed", "")
        archive.writestr("dbfbridge/py.typed", "")
    violations = _verify(dist)
    assert any("wheel METADATA Version '1.2.3' != '9.9.9'" in violation for violation in violations)


def test_wrong_wheel_name_fails(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel = _make_wheel(dist)
    with __import__("zipfile").ZipFile(wheel) as archive:
        payload = archive.read("dbfbridge-9.9.9.dist-info/METADATA")
    payload = payload.replace(b"Name: dbfbridge", b"Name: notdbfbridge")
    wheel.unlink()
    with __import__("zipfile").ZipFile(wheel, "w") as archive:
        archive.writestr("dbfbridge-9.9.9.dist-info/METADATA", payload)
        archive.writestr("dbf_bridge/py.typed", "")
        archive.writestr("dbfbridge/py.typed", "")
    violations = _verify(dist)
    assert any("wheel METADATA Name 'notdbfbridge'" in violation for violation in violations)


def test_missing_wheel_py_typed_fails(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel = _make_wheel(dist)
    with __import__("zipfile").ZipFile(wheel) as archive:
        payload = archive.read("dbfbridge-9.9.9.dist-info/METADATA")
    wheel.unlink()
    with __import__("zipfile").ZipFile(wheel, "w") as archive:
        archive.writestr("dbfbridge-9.9.9.dist-info/METADATA", payload)
    violations = _verify(dist)
    assert any("wheel lacks dbf_bridge/py.typed" in violation for violation in violations)


def test_missing_typing_typed_classifier_fails(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    _make_wheel(dist, typed=False)
    violations = _verify(dist)
    assert any("Typing :: Typed" in violation for violation in violations)


def test_missing_base_dependency_contract_fails(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    _make_wheel(dist, base=False)
    violations = _verify(dist)
    assert any("base Requires-Dist" in violation for violation in violations)


def test_missing_documented_extra_fails(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    _make_wheel(dist, extras=False)
    violations = _verify(dist)
    assert any("documented extra 'write'" in violation for violation in violations)


def test_wrong_sdist_version_fails(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    _make_wheel(dist)
    _make_sdist(dist, version="1.2.3")
    violations = _verify(dist)
    assert any("sdist PKG-INFO Version" in violation for violation in violations)


def test_wrong_sdist_name_fails(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    _make_wheel(dist)
    sdist = _make_sdist(dist)
    with __import__("tarfile").open(sdist) as tar:
        pkg = tar.extractfile(f"dbfbridge-{VERSION}/PKG-INFO")
        payload = pkg.read().replace(b"Name: dbfbridge", b"Name: notdbfbridge")  # type: ignore[union-attr]
    sdist.unlink()
    root = f"dbfbridge-{VERSION}"
    with __import__("tarfile").open(sdist, "w:gz") as tar:
        info = __import__("tarfile").TarInfo(f"{root}/PKG-INFO")
        info.size = len(payload)
        tar.addfile(info, __import__("io").BytesIO(payload))
        for name, content in {
            "README.md": "# x\n",
            "LICENSE": "MIT\n",
            "pyproject.toml": "",
            "PUBLISHING.md": "",
            "docs/pypi-usage.md": "",
            "docs/api-1.0.md": "",
            "docs/migration-1.0.md": "",
            "docs/compatibility-vfp.md": "",
            "src/dbf_bridge/py.typed": "",
            "src/dbfbridge/py.typed": "",
        }.items():
            data = content.encode("utf-8")
            member = __import__("tarfile").TarInfo(f"{root}/{name}")
            member.size = len(data)
            tar.addfile(member, __import__("io").BytesIO(data))
    violations = _verify(dist)
    assert any("sdist PKG-INFO Name 'notdbfbridge'" in violation for violation in violations)


def test_missing_required_public_doc_fails(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    _make_wheel(dist)
    _make_sdist(dist, missing=("docs/migration-1.0.md",))
    violations = _verify(dist)
    assert any("sdist lacks docs/migration-1.0.md" in violation for violation in violations)


def test_missing_pkg_info_fails(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    _make_wheel(dist)
    _make_sdist(dist, missing=(f"dbfbridge-{VERSION}/PKG-INFO",))
    # The root PKG-INFO absence must be caught even though the required
    # file list does not mention it explicitly.
    violations = _verify(dist)
    assert any("sdist lacks" in violation for violation in violations)


def test_missing_metadata_parser_reads_exact_fields() -> None:
    """The verifier parses metadata strictly (email.parser), proving the
    Name/Version assertions are real, not lenient."""
    parsed = email.parser.BytesParser().parsebytes(
        _metadata(version=VERSION, typed=True, base=True, extras=True)
    )
    assert parsed["Name"] == "dbfbridge"
    assert parsed["Version"] == VERSION
    assert "Typing :: Typed" in (parsed.get_all("Classifier") or [])

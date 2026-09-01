"""Reusable fresh-wheel smoke: verify a built wheel is self-contained.

Creates an isolated venv, installs the wheel into it, verifies module origins,
public API imports, and CLI entry points — all from OUTSIDE the repository
checkout.  stdlib-only, no source-tree imports, no silent fallbacks.

Usage:
    python scripts/release_wheel_smoke.py \
        --wheel dist/dbfbridge-0.2.0-py3-none-any.whl \
        --expected-version 0.2.0
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _venv_python(venv_dir: Path) -> Path:
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _venv_scripts_dir(venv_dir: Path) -> Path:
    if sys.platform == "win32":
        return venv_dir / "Scripts"
    return venv_dir / "bin"


def _clean_environ() -> dict[str, str]:
    env = dict(os.environ)
    for key in ("PYTHONPATH", "PYTHONHOME"):
        env.pop(key, None)
    return env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", required=True, help="Path to the .whl to verify")
    parser.add_argument(
        "--expected-version",
        default=None,
        help="Expected package version; read from pyproject.toml if omitted",
    )
    args = parser.parse_args(argv)

    # --- Validate wheel path ------------------------------------------------
    wheel = Path(args.wheel).resolve()
    if not wheel.exists():
        raise SystemExit(f"FAIL: wheel does not exist: {wheel}")
    if not wheel.is_file():
        raise SystemExit(f"Not a file: {wheel}")
    if wheel.suffix != ".whl":
        raise SystemExit(f"Not a wheel: {wheel}")
    wheel_sha = hashlib.sha256(wheel.read_bytes()).hexdigest()
    print(f"wheel: {wheel}")
    print(f"wheel sha256: {wheel_sha}")

    # --- Resolve expected version -------------------------------------------
    if args.expected_version:
        expected_version = args.expected_version
    else:
        pyproject = _PROJECT_ROOT / "pyproject.toml"
        if pyproject.is_file():
            import tomllib

            with pyproject.open("rb") as f:
                data = tomllib.load(f)
            expected_version = str(data["project"]["version"])
        else:
            raise SystemExit(
                "Cannot determine expected version: no --expected-version and no pyproject.toml"
            )
    print(f"expected version: {expected_version}")

    # --- Create fresh venv ---------------------------------------------------

    tmp = tempfile.TemporaryDirectory(prefix="dbfbridge-smoke-")
    venv_dir = Path(tmp.name) / "fresh-venv"
    venv.EnvBuilder(with_pip=True).create(str(venv_dir))

    if sys.platform == "win32":
        fresh_python = venv_dir / "Scripts" / "python.exe"
        fresh_scripts = venv_dir / "Scripts"
    else:
        fresh_python = venv_dir / "bin" / "python"
        fresh_scripts = venv_dir / "bin"

    if not fresh_python.is_file():
        raise SystemExit(f"FAIL: Fresh venv python not found: {fresh_python}")
    print(f"fresh venv: {venv_dir}")

    # Clean environment for all child processes.
    env = dict(os.environ)
    for key in ("PYTHONPATH", "PYTHONHOME"):
        env.pop(key, None)

    def _run(
        cmd: list[str], *, cwd: Path | None = None, label: str = ""
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, env=env)
        if result.returncode != 0:
            print(result.stdout, file=sys.stdout)
            print(result.stderr, file=sys.stderr)
            raise SystemExit(f"FAIL [{label}]: exit {result.returncode}")
        return result

    # --- Install wheel into fresh venv (no source-tree imports) --------------
    _run(
        [
            str(fresh_python),
            "-m",
            "pip",
            "install",
            "--force-reinstall",
            "--disable-pip-version-check",
            "--no-input",
            str(wheel),
        ],
        label="wheel_install",
    )
    print(f"wheel installed into fresh venv: {wheel.name}")

    # --- Work directory outside the repo checkout ---
    work_dir = Path(tmp.name) / "work"
    work_dir.mkdir()

    # --- Import probe: version + module origins + public API ---
    probe = f"""
import sys, json
import dbfbridge
import dbf_bridge

expected = {expected_version!r}
assert dbfbridge.__version__ == expected, (
    f"dbfbridge version mismatch: {{dbfbridge.__version__!r}} != {{expected!r}}"
)
assert dbf_bridge.__version__ == expected, "dbf_bridge version mismatch"

venv_prefix = {str(venv_dir)!r}
for mod_name in ("dbfbridge", "dbf_bridge"):
    mod = sys.modules[mod_name]
    origin = mod.__file__
    assert origin is not None and venv_prefix in origin, (
        f"{{mod_name}} origin {{origin!r}} is not inside the fresh venv {{venv_prefix!r}}"
    )
    print(f"{{mod_name}} origin: {{origin}}")

from dbfbridge import (
    inspect_table, read_schema,
    iter_records, read_records, iter_raw_records,
    FieldInfo, TableInfo, TableSchema,
    DirectRecord, RecordPage, LazyMemoValue,
)
from dbf_bridge import (
    inspect_table, read_schema,
    iter_records, read_records, iter_raw_records,
)

from dbfbridge import (
    export_dbf, reconstruct_dbf, verify_conversion, check_conversion_quality,
)

print("Direct Read API: PASS")
print("legacy API: PASS")
print("module origins: PASS (fresh venv)")
"""
    result = _run([str(fresh_python), "-I", "-c", probe], cwd=work_dir, label="import_probe")
    for line in result.stdout.splitlines():
        print(f"  {line}")
    print("Direct Read API: PASS")
    print("legacy API: PASS")

    # --- Create a synthetic DBF for a real Direct Read round trip ---
    _run(
        [
            str(fresh_python),
            "-c",
            "import dbf; t = dbf.Table('smoke.dbf', field_specs='KOD N(4,0); NAME C(20)', "
            "dbf_type='vfp', codepage=0xC8); t.open(mode=dbf.READ_WRITE); "
            "t.append({'KOD': 1, 'NAME': 'smoke'}); t.close()",
        ],
        cwd=work_dir,
        label="synthetic_dbf",
    )

    _run(
        [
            str(fresh_python),
            "-I",
            "-c",
            "from dbfbridge import read_records; "
            "page = read_records('smoke.dbf', limit=1); "
            "assert len(page.records) == 1; "
            "assert page.records[0].values['KOD'] == 1",
        ],
        cwd=work_dir,
        label="direct_read_smoke",
    )
    print("Direct Read API: PASS")

    # --- CLI smoke using the fresh venv's scripts directory ---
    for cli_name in ("dbf-bridge", "dbf-bridge-import", "dbf-bridge-verify", "dbf-bridge-quality"):
        if sys.platform == "win32":
            cli_exe = fresh_scripts / f"{cli_name}.exe"
        else:
            cli_exe = fresh_scripts / cli_name
        _run([str(cli_exe), "--help"], cwd=work_dir, label=f"cli_{cli_name}")
    print("CLI entrypoints: PASS")

    # --- Evidence summary ---
    print(f"wheel path: {wheel}")
    print(f"wheel sha256: {wheel_sha}")
    print(f"expected version: {expected_version}")
    print(f"fresh venv location: {venv_dir}")
    print("Direct Read API: PASS")
    print("legacy API: PASS")
    print("CLI entrypoints: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


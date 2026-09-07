"""Shared physical writer boundary (Phase B) regression tests.

Deterministic evidence that:

1. exactly ONE physical DBF/FPT writer implementation exists
   (``dbf_bridge.write.backend``);
2. ``dbf_bridge.importer.writer`` is a pure delegation layer — it re-exports
   the backend objects and defines no second engine;
3. the writer boundary depends only on core/neutral primitives — no
   ``write -> importer/exporter`` import and no import cycle;
4. ``core/`` stays isolated (no write/importer/exporter/CLI/heavy deps);
5. importing the writer boundary (and the root ``dbfbridge`` facade) in a
   FRESH interpreter never loads the optional ``dbf`` dependency;
6. Phase D has not leaked: no public ``write_table`` is promoted.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import dbf_bridge
from dbf_bridge.importer import writer as importer_writer
from dbf_bridge.write import backend as writer_backend

REPO_ROOT = Path(__file__).parents[1]
PACKAGE_ROOT = REPO_ROOT / "src" / "dbf_bridge"


# ---------------------------------------------------------------------------
# 1 + 2: single physical writer / delegation, no second engine
# ---------------------------------------------------------------------------


def _package_sources() -> list[Path]:
    return sorted(path for path in PACKAGE_ROOT.rglob("*.py") if "__pycache__" not in path.parts)


def test_exactly_one_physical_write_dbf_definition_exists() -> None:
    """``def write_dbf`` appears exactly once in the package source: the
    authoritative physical writer in ``dbf_bridge/write/backend.py``."""
    definitions = [
        path.relative_to(PACKAGE_ROOT).as_posix()
        for path in _package_sources()
        if "\ndef write_dbf(" in path.read_text(encoding="utf-8")
    ]
    assert definitions == ["write/backend.py"]


def test_importer_writer_is_a_delegation_shim_of_the_backend() -> None:
    """Every reconstruction-facing writer name IS the backend object — the
    compatibility layer re-exports, it does not reimplement."""
    assert importer_writer.write_dbf is writer_backend.write_dbf
    assert importer_writer.ReconstructionError is writer_backend.ReconstructionError
    assert importer_writer.restore_raw_layout is writer_backend.restore_raw_layout
    assert importer_writer.memo_output_path is writer_backend.memo_output_path
    assert importer_writer.output_hashes is writer_backend.output_hashes
    assert importer_writer.DBF_HEADER_SIZE == writer_backend.DBF_HEADER_SIZE
    assert importer_writer.SUPPORTED_FIELD_TYPES is writer_backend.SUPPORTED_FIELD_TYPES
    assert importer_writer.TYPE_ALIASES is writer_backend.TYPE_ALIASES
    # Internal helpers are re-exported too (historical module-attribute
    # access), including the Varchar/_NullFlags repair primitive.
    assert importer_writer._repair_varchar_logical_layout is (
        writer_backend._repair_varchar_logical_layout
    )
    assert importer_writer._coerce_value is writer_backend._coerce_value
    assert importer_writer._patch_dbf_metadata is writer_backend._patch_dbf_metadata


def test_delegation_shim_retains_no_physical_algorithm_source() -> None:
    """The shim source contains no physical-writer algorithm definitions —
    it is import-and-re-export only."""
    source = (PACKAGE_ROOT / "importer" / "writer.py").read_text(encoding="utf-8")
    for forbidden_marker in (
        "def write_dbf(",
        "def restore_raw_layout(",
        "def _coerce_value(",
        "def _repair_varchar_logical_layout(",
        "def _patch_dbf_metadata(",
        "dbf.Table(",
        "os.replace(",
    ):
        assert forbidden_marker not in source, forbidden_marker
    assert "from dbf_bridge.write.backend import" in source


# ---------------------------------------------------------------------------
# 3: boundary dependency direction — no write -> importer/exporter, no cycle
# ---------------------------------------------------------------------------


def _import_statements(source: str) -> list[str]:
    statements = []
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            statements.append(stripped)
    return statements


def test_write_boundary_does_not_import_importer_or_exporter() -> None:
    """``write/`` may depend on core and neutral shared primitives only."""
    for path in (PACKAGE_ROOT / "write").glob("*.py"):
        for statement in _import_statements(path.read_text(encoding="utf-8")):
            assert "dbf_bridge.importer" not in statement, (path, statement)
            assert "dbf_bridge.exporter" not in statement, (path, statement)
            assert "dbf_bridge.cli" not in statement, (path, statement)


def test_importer_writer_shim_has_no_import_cycle_with_the_backend() -> None:
    """``dbf_bridge.write.backend`` imports FIRST and standalone: a
    ``write -> importer`` dependency would create a cycle through the eager
    ``importer/__init__`` (reconstruct -> writer shim) and fail here."""
    code = (
        "import dbf_bridge.write.backend as backend;"
        "import dbf_bridge.importer.writer as shim;"
        "assert shim.write_dbf is backend.write_dbf;"
        "print('OK')"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "OK" in completed.stdout


# ---------------------------------------------------------------------------
# 4: core/ isolation (DBFB-LAYER-004)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "forbidden",
    [
        "dbf_bridge.write",
        "dbf_bridge.importer",
        "dbf_bridge.exporter",
        "dbf_bridge.cli",
    ],
)
def test_core_never_imports_write_importer_exporter_or_cli(forbidden: str) -> None:
    offenders = [
        (path, statement)
        for path in (PACKAGE_ROOT / "core").rglob("*.py")
        if "__pycache__" not in path.parts
        for statement in _import_statements(path.read_text(encoding="utf-8"))
        if forbidden in statement
    ]
    assert offenders == []


@pytest.mark.parametrize("dependency", ["dbf", "openpyxl", "polars", "xlsxwriter", "orjson"])
def test_core_never_imports_heavy_optional_dependencies(dependency: str) -> None:
    offenders = [
        (path, statement)
        for path in (PACKAGE_ROOT / "core").rglob("*.py")
        if "__pycache__" not in path.parts
        for statement in _import_statements(path.read_text(encoding="utf-8"))
        if statement.split()[0] in {"import", "from"}
        and statement.split()[1].split(".")[0] == dependency
    ]
    assert offenders == []


# ---------------------------------------------------------------------------
# 5 + 6: lazy optional dependency, lazy root import, no Phase D leakage
# ---------------------------------------------------------------------------


def test_fresh_interpreter_import_of_writer_boundary_never_loads_dbf() -> None:
    code = (
        "import sys;"
        "import dbfbridge;"
        "import dbf_bridge.write.backend;"
        "import dbf_bridge.importer.writer;"
        "assert 'dbf' not in sys.modules, 'optional dbf dependency was loaded';"
        "print('PASS')"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "PASS" in completed.stdout


def test_fresh_interpreter_root_import_does_not_load_dbf() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import dbfbridge; assert 'dbf' not in sys.modules; print('PASS')",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "PASS" in completed.stdout


def test_no_direct_write_public_api_is_promoted() -> None:
    """Phase B promotes nothing: no ``write_table``/``WriteResult`` on the
    root facade, and the internal write package exposes no public API yet."""
    assert "write_table" not in dbf_bridge.__all__
    assert not hasattr(dbf_bridge, "write_table")
    assert not hasattr(writer_backend, "write_table")
    assert not hasattr(importer_writer, "write_table")
    import importlib

    write_ns = importlib.import_module("dbf_bridge.write")
    assert "write_table" not in getattr(write_ns, "__all__", [])

"""Optional-dependency split: error contract, guards, fallbacks, metadata.

Covers:

- the public ``OptionalDependencyMissingError`` contract (machine code,
  dependency/extra/operation/install_command payload, ``to_dict``);
- reconstruction/XLSX-export/quality operations fail typed **before any
  output side effect** when their optional dependency is missing;
- the ``[fast]`` accelerators (orjson/polars) never raise: the stdlib and
  Python fallbacks keep JSON/CSV conversions working with identical logical
  results;
- the packaging contract: the base distribution depends only on ``dbfread``
  and every optional dependency is exposed through a versioned extra.

Missing modules are simulated with a scoped meta-path blocker plus
``sys.modules`` removal; nothing is ever uninstalled or downloaded.
"""

from __future__ import annotations

import json
import sys
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from dbf_bridge import (
    OptionalDependencyMissingError,
    check_conversion_quality,
    export_dbf,
    reconstruct_dbf,
)

# ---------------------------------------------------------------------------
# scoped import blocking
# ---------------------------------------------------------------------------


class _BlockedImporter:
    """Meta-path finder that raises ImportError for the given top-level names."""

    def __init__(self, names: set[str]) -> None:
        self.names = names

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
        if fullname.split(".")[0] in self.names:
            raise ImportError(f"import of {fullname!r} blocked for this test")
        return None


BlockFn = Callable[..., None]


@pytest.fixture()
def block_imports(monkeypatch: pytest.MonkeyPatch) -> Iterator[BlockFn]:
    """Block the given top-level module names for the duration of a test."""
    blocked: set[str] = set()

    def _block(*names: str) -> None:
        blocked.update(names)
        for name in names:
            monkeypatch.delitem(sys.modules, name, raising=False)

    monkeypatch.setattr(sys, "meta_path", [_BlockedImporter(blocked), *sys.meta_path])
    yield _block


# ---------------------------------------------------------------------------
# error contract
# ---------------------------------------------------------------------------


def test_optional_dependency_error_contract() -> None:
    error = OptionalDependencyMissingError(
        dependency="dbf",
        extra="write",
        operation="reconstruct_dbf",
    )
    assert error.code == "OPTIONAL_DEPENDENCY_MISSING"
    assert error.dependency == "dbf"
    assert error.extra == "write"
    assert error.operation == "reconstruct_dbf"
    assert error.install_command == 'python -m pip install "dbfbridge[write]"'
    assert error.to_dict() == {
        "code": "OPTIONAL_DEPENDENCY_MISSING",
        "dependency": "dbf",
        "extra": "write",
        "operation": "reconstruct_dbf",
        "install_command": 'python -m pip install "dbfbridge[write]"',
    }
    payload = json.loads(json.dumps(error.to_dict()))
    assert payload["code"] == "OPTIONAL_DEPENDENCY_MISSING"
    assert isinstance(error, RuntimeError)
    assert "dbfbridge[write]" in str(error)


def test_optional_dependency_error_install_command_per_extra() -> None:
    for extra, dependency in (
        ("xlsx", "xlsxwriter"),
        ("xlsx", "openpyxl"),
    ):
        error = OptionalDependencyMissingError(
            dependency=dependency, extra=extra, operation="export_dbf"
        )
        assert error.install_command == f'python -m pip install "dbfbridge[{extra}]"'


def test_public_namespaces_expose_the_error() -> None:
    import dbf_bridge
    import dbfbridge

    assert dbf_bridge.OptionalDependencyMissingError is OptionalDependencyMissingError
    assert dbfbridge.OptionalDependencyMissingError is OptionalDependencyMissingError
    assert "OptionalDependencyMissingError" in dbf_bridge.__all__
    assert "OptionalDependencyMissingError" in dbfbridge.__all__


# ---------------------------------------------------------------------------
# fail-before-side-effect guards
# ---------------------------------------------------------------------------


def test_reconstruction_without_write_extra_fails_typed_before_output(
    tmp_path: Path, sample_input_dir: Path, block_imports: BlockFn
) -> None:
    block_imports("dbf")
    export_dir = tmp_path / "export"
    result = export_dbf(str(sample_input_dir / "klienci.dbf"), str(export_dir), formats=("jsonl",))
    result.raise_for_errors()
    output_dir = tmp_path / "rebuilt"
    with pytest.raises(OptionalDependencyMissingError) as error:
        reconstruct_dbf(str(export_dir), str(output_dir), input_format="jsonl")
    payload = error.value.to_dict()
    expected = {
        "code": "OPTIONAL_DEPENDENCY_MISSING",
        "dependency": "dbf",
        "extra": "write",
        "operation": "reconstruct_dbf",
        "install_command": 'python -m pip install "dbfbridge[write]"',
    }
    for key, value in expected.items():
        assert payload[key] == value
    assert payload.get("purpose") == "DBF/FPT reconstruction"
    # Zero side effects: the guard runs before the output directory exists.
    assert not output_dir.exists()
    assert list(tmp_path.glob("*.partial")) == []


def test_reconstruction_report_of_missing_dep_is_not_created(
    tmp_path: Path, sample_input_dir: Path, block_imports: BlockFn
) -> None:
    block_imports("dbf")
    export_dir = tmp_path / "export"
    export_dbf(
        str(sample_input_dir / "klienci.dbf"), str(export_dir), formats=("jsonl",)
    ).raise_for_errors()
    with pytest.raises(OptionalDependencyMissingError):
        reconstruct_dbf(str(export_dir), str(tmp_path / "rebuilt"), input_format="jsonl")
    assert not (tmp_path / "rebuilt" / "reconstruction_report.jsonl").exists()


def test_xlsx_export_without_xlsx_extra_fails_typed_before_output(
    tmp_path: Path, sample_input_dir: Path, block_imports: BlockFn
) -> None:
    block_imports("xlsxwriter")
    output_dir = tmp_path / "out"
    with pytest.raises(OptionalDependencyMissingError) as error:
        export_dbf(
            str(sample_input_dir / "klienci.dbf"),
            str(output_dir),
            formats=("xlsx",),
        )
    payload = error.value.to_dict()
    assert payload["dependency"] == "xlsxwriter"
    assert payload["extra"] == "xlsx"
    assert payload["operation"] == "export_dbf"
    assert payload["install_command"] == 'python -m pip install "dbfbridge[xlsx]"'
    assert not output_dir.exists()
    assert list(tmp_path.iterdir()) == []


def test_quality_roundtrip_without_write_extra_fails_typed_before_output(
    tmp_path: Path, sample_input_dir: Path, block_imports: BlockFn
) -> None:
    block_imports("dbf")
    output_dir = tmp_path / "quality"
    with pytest.raises(OptionalDependencyMissingError) as error:
        check_conversion_quality(str(sample_input_dir / "klienci.dbf"), str(output_dir))
    payload = error.value.to_dict()
    assert payload["dependency"] == "dbf"
    assert payload["extra"] == "write"
    assert payload["operation"] == "check_conversion_quality"
    assert not output_dir.exists()


def test_jsonl_migration_is_not_blocked_by_missing_write_extra(
    tmp_path: Path, sample_input_dir: Path, block_imports: BlockFn
) -> None:
    block_imports("dbf")
    output_dir = tmp_path / "out"
    result = export_dbf(str(sample_input_dir / "klienci.dbf"), str(output_dir), formats=("jsonl",))
    result.raise_for_errors()
    assert (output_dir / "klienci.jsonl").is_file()


def test_base_export_json_csv_do_not_need_the_optional_heavy_dependencies(
    tmp_path: Path, sample_input_dir: Path, block_imports: BlockFn
) -> None:
    block_imports("dbf", "openpyxl", "xlsxwriter", "orjson", "polars")
    output_dir = tmp_path / "out"
    result = export_dbf(
        str(sample_input_dir / "klienci.dbf"), str(output_dir), formats=("jsonl", "json", "csv")
    )
    result.raise_for_errors()
    assert (output_dir / "klienci.jsonl").is_file()
    assert (output_dir / "klienci.json").is_file()
    assert (output_dir / "klienci.csv").is_file()


# ---------------------------------------------------------------------------
# [fast] fallback equivalence (never raises)
# ---------------------------------------------------------------------------


def _json_values(path: Path) -> list[dict[str, Any]]:
    """Parse a converted .json document (a JSON array, not JSONL)."""
    return json.loads(path.read_text(encoding="utf-8"))


def test_csv_without_polars_uses_the_python_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dbf_bridge import converters

    source = tmp_path / "rows.jsonl"
    rows = [
        {"id": 1, "name": "Żółw", "amount": 1.5},
        {"id": 2, "name": "Książka", "amount": 2.5},
    ]
    source.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    common: dict[str, Any] = {
        "columns": ["id", "name", "amount"],
        "schema_types": {"id": "integer", "name": "string", "amount": "number"},
        "expected_record_count": len(rows),
        "source_is_validated": True,
    }

    fast_csv = tmp_path / "fast.csv"
    converters.jsonl_to_csv(source, fast_csv, **common)

    # Simulate a missing polars install: `import polars` raises ImportError
    # and jsonl_to_csv must fall back to the Python engine, never raise.
    monkeypatch.setitem(sys.modules, "polars", None)
    fallback_csv = tmp_path / "fallback.csv"
    converters.jsonl_to_csv(source, fallback_csv, **common)

    def _rows(path: Path) -> list[list[str]]:
        return [line.split(",") for line in path.read_text(encoding="utf-8-sig").splitlines()]

    assert _rows(fallback_csv) == _rows(fast_csv)
    assert len(_rows(fallback_csv)) == 3  # header + 2 data rows


def test_json_conversion_without_orjson_matches_the_stdlib_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dbf_bridge import converters

    source = tmp_path / "rows.jsonl"
    rows = [{"id": 1, "name": "Żółw ąęłóńśćźż"}, {"id": 2, "name": "plain"}]
    source.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )

    fast_json = tmp_path / "fast.json"
    converters.jsonl_to_json(source, fast_json)

    monkeypatch.setattr(converters, "orjson", None)
    fallback_json = tmp_path / "fallback.json"
    converters.jsonl_to_json(source, fallback_json)

    assert _json_values(fast_json) == _json_values(fallback_json) == rows


# ---------------------------------------------------------------------------
# packaging contract (wheel METADATA)
# ---------------------------------------------------------------------------


def _load_pyproject() -> dict[str, Any]:
    from pathlib import Path as _Path

    import tomllib

    with _Path(__file__).parents[1].joinpath("pyproject.toml").open("rb") as infile:
        return tomllib.load(infile)


def test_base_install_requires_only_dbfread() -> None:
    project = _load_pyproject()["project"]
    assert project["dependencies"] == ["dbfread>=2.0.7"]
    forbidden = {"dbf", "openpyxl", "xlsxwriter", "orjson", "polars"}
    mandatory = {
        requirement.split(">")[0].split("=")[0].split("[")[0].strip()
        for requirement in project["dependencies"]
    }
    assert not (mandatory & forbidden), mandatory


def test_extras_map_every_optional_dependency() -> None:
    extras = _load_pyproject()["project"]["optional-dependencies"]
    assert extras["write"] == ["dbf>=0.99.11"]
    assert extras["xlsx"] == ["xlsxwriter>=3.2", "openpyxl>=3.1.5"]
    assert extras["fast"] == ["orjson>=3.10", "polars>=1.0"]
    assert sorted(extras["all"]) == sorted(
        ["dbf>=0.99.11", "openpyxl>=3.1.5", "orjson>=3.10", "polars>=1.0", "xlsxwriter>=3.2"]
    )
    # Historical compatibility alias of [write].
    assert extras["import"] == ["dbf>=0.99.11"]
    # No test/dev tooling leaks into [all].
    for tool in ("pytest", "ruff", "build", "twine", "psutil"):
        assert all(tool not in requirement for requirement in extras["all"])


def _normalized_names(requirements: list[str]) -> set[str]:
    """Canonical distribution names of PEP 508 requirements (marker-safe)."""
    names: set[str] = set()
    for requirement in requirements:
        head = requirement.split(";")[0].strip()
        name = head.split(";")[0]
        for separator in (";", ">", "<", "=", "[", " ", "("):
            name = name.split(separator)[0]
        names.add(name.strip().lower())
    return names


def test_dev_extras_contains_the_full_runtime_capability_set() -> None:
    """[dev] must remain the complete test environment after the split.

    The first 0.3 push removed the heavy dependencies from the base install
    but left [dev] without xlsxwriter/orjson/polars, so the legacy
    full-feature tests (polars streaming engine, XLSX export/import, CLI
    integration) failed in CI run 33504140559.  This regression test pins
    the contract: every runtime capability dependency of [all] must also be
    available in [dev], while [all] itself stays free of dev-only tooling.
    """
    extras = _load_pyproject()["project"]["optional-dependencies"]
    runtime_all = _normalized_names(extras["all"])
    dev = _normalized_names(extras["dev"])
    assert runtime_all <= dev, sorted(runtime_all - dev)
    # …and [dev] is explicitly enumerated, never a self-referential extra.
    assert not any(name.startswith("dbfbridge") for name in dev)


def test_dev_is_never_a_user_profile() -> None:
    """[all] stays the full USER feature set; dev tooling belongs to [dev]."""
    extras = _load_pyproject()["project"]["optional-dependencies"]
    dev_tools = {"pytest", "pytest-cov", "ruff", "build", "twine", "psutil"}
    all_names = _normalized_names(extras["all"])
    assert not (all_names & dev_tools), sorted(all_names & dev_tools)
    for tool in dev_tools:
        assert any(tool in requirement for requirement in extras["dev"]), tool


def test_built_wheel_metadata_matches_the_contract() -> None:
    """When a built wheel is present, its METADATA must match the contract."""
    dist = Path(__file__).parents[1] / "dist"
    wheels = sorted(dist.glob("dbfbridge-*.whl"))
    if not wheels:
        pytest.skip("no built wheel in dist/ (run `python -m build` first)")
    with zipfile.ZipFile(wheels[-1]) as wheel:
        metadata_name = next(name for name in wheel.namelist() if name.endswith("METADATA"))
        metadata = wheel.read(metadata_name).decode("utf-8")
    requires = [line for line in metadata.splitlines() if line.startswith("Requires-Dist:")]
    base = [line for line in requires if "extra ==" not in line]
    assert base == ["Requires-Dist: dbfread>=2.0.7"]
    joined = "\n".join(requires)
    for extra, dependency in (
        ("write", "dbf"),
        ("xlsx", "xlsxwriter"),
        ("xlsx", "openpyxl"),
        ("fast", "orjson"),
        ("fast", "polars"),
        ("all", "dbf"),
        ("import", "dbf"),
    ):
        marker = f"extra == '{extra}'" in joined or f'extra == "{extra}"' in joined
        assert marker, (extra, dependency)
        assert dependency in joined


# ---------------------------------------------------------------------------
# documentation contract (user-facing, PyPI-first)
# ---------------------------------------------------------------------------


def test_documentation_covers_the_pypi_contract() -> None:
    repo_root = Path(__file__).parents[1]
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    assert "pip install dbfbridge" in readme
    for extra in ("write", "xlsx", "fast", "all"):
        assert f"dbfbridge[{extra}]" in readme, extra
    for function in (
        "inspect_table",
        "read_schema",
        "iter_records",
        "read_records",
        "iter_raw_records",
    ):
        assert function in readme, function

    usage = repo_root / "docs" / "pypi-usage.md"
    assert usage.is_file(), "docs/pypi-usage.md is the canonical PyPI user guide"

    documentation_url = _load_pyproject()["project"]["urls"]["Documentation"]
    assert documentation_url.endswith("docs/pypi-usage.md")
    assert documentation_url.startswith("https://github.com/PeterPirog/dbfbridge/")

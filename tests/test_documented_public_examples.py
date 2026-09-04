"""Executable regression for the installed-package API examples.

The representative Python examples in ``docs/python-api-examples.md`` are not
only present — for ALL NINE stable public operations the documented block is
extracted and EXECUTED against deterministic synthetic fixtures. The examples
describe post-installation usage: only public ``dbfbridge`` imports, no
private modules, no ``PYTHONPATH``/``sys.path`` tricks, no network.
"""

from __future__ import annotations

import contextlib
import io
import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[1]
DOC = ROOT / "docs" / "python-api-examples.md"


def _blocks() -> list[str]:
    text = DOC.read_text(encoding="utf-8")
    return re.findall(r"```python\n(.*?)```", text, re.DOTALL)


def _one_block(pattern: str) -> str:
    matches = [block for block in _blocks() if re.search(pattern, block)]
    assert len(matches) == 1, f"expected exactly one example matching {pattern!r}"
    return matches[0]


def _fixture_dbf(path: Path) -> None:
    import dbf as dbf_lib

    table = dbf_lib.Table(
        str(path),
        "KOD N(3,0); NAZWA C(10); NOTATKA M",
        dbf_type="vfp",
        codepage=0xC8,
        memo_size=64,
    )
    table.open(dbf_lib.READ_WRITE)
    table.append({"KOD": 1, "NAZWA": "abc", "NOTATKA": "hello"})
    table.append({"KOD": 2, "NAZWA": "def", "NOTATKA": "world"})
    table.close()


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A working directory containing `KLIENCI.DBF` (with a memo companion)
    and a `data/` source tree — exactly the paths the documented examples use."""
    _fixture_dbf(tmp_path / "KLIENCI.DBF")
    source = tmp_path / "data"
    source.mkdir()
    _fixture_dbf(source / "KLIENCI.DBF")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _run(block: str, namespace: dict[str, Any]) -> str:
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        exec(compile(block, str(DOC), "exec"), namespace)  # noqa: S102 - docs code
    return stdout.getvalue()


def test_installed_boundary_pure() -> None:
    """The documented examples must use ONLY the public installed package."""
    for block in _blocks():
        assert "from dbf_bridge." not in block
        assert "import dbf_bridge" not in block
        assert "sys.path" not in block
        assert "PYTHONPATH" not in block
        assert "git clone" not in block


def test_example_inspect_table(workspace: Path) -> None:
    namespace: dict[str, Any] = {"__name__": "example"}
    _run(_one_block(r"info = inspect_table\("), namespace)
    payload = namespace["payload"]
    assert payload["record_count"] == 2
    assert any(field["name"] == "KOD" for field in payload["fields"])


def test_example_read_schema(workspace: Path) -> None:
    namespace: dict[str, Any] = {"__name__": "example"}
    _run(_one_block(r"schema = read_schema\("), namespace)
    assert namespace["schema"].path.name == "KLIENCI.DBF"
    assert any(field.name == "NOTATKA" for field in namespace["schema"].fields)


def test_example_iter_records(workspace: Path) -> None:
    namespace: dict[str, Any] = {"__name__": "example"}
    _run(_one_block(r'iter_records\("KLIENCI\.DBF", fields=\["KOD", "NAZWA"\], memo="skip"\)'), namespace)


def test_example_read_records(workspace: Path) -> None:
    namespace: dict[str, Any] = {"__name__": "example"}
    _run(_one_block(r"def read_all_pages\("), namespace)


def test_example_iter_raw_records(workspace: Path) -> None:
    namespace: dict[str, Any] = {"__name__": "example"}
    _run(_one_block(r"for record in iter_raw_records\("), namespace)


def test_example_export_dbf(workspace: Path) -> None:
    namespace: dict[str, Any] = {"__name__": "example"}
    _run(_one_block(r"result = export_dbf\("), namespace)
    payload = namespace["payload"]
    assert any(table["status"] in {"OK", "WARNING"} for table in payload["results"])
    exported = list((workspace / "exported").rglob("*.jsonl"))
    assert exported, "the documented export produced no JSONL output"


def test_example_reconstruct_dbf(workspace: Path) -> None:
    # The reconstruct example consumes the documented export example's output.
    _run(_one_block(r"result = export_dbf\("), {"__name__": "example_export"})
    namespace: dict[str, Any] = {"__name__": "example"}
    _run(_one_block(r"result = reconstruct_dbf\("), namespace)
    rebuilt = [
        path
        for path in (workspace / "rebuilt").rglob("*")
        if path.is_file() and path.suffix.lower() == ".dbf"
    ]
    assert rebuilt, "the documented reconstruction produced no DBF output"


def test_example_verify_conversion(workspace: Path) -> None:
    from dbfbridge import export_dbf

    export_dbf("data", "exported", formats=("jsonl",), overwrite=True)
    namespace: dict[str, Any] = {"__name__": "example"}
    _run(_one_block(r"result = verify_conversion\("), namespace)
    payload = namespace["payload"]
    assert "summary" in payload


def test_example_check_conversion_quality(workspace: Path) -> None:
    from dbfbridge import export_dbf

    export_dbf("data", "exported", formats=("jsonl",), overwrite=True)
    namespace: dict[str, Any] = {"__name__": "example"}
    _run(_one_block(r"result = check_conversion_quality\("), namespace)
    payload = namespace["payload"]
    assert "summary" in payload


def test_example_optional_dependency_error(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The typed optional-dependency example runs exactly as documented and
    creates no output."""
    import sys

    class _BlockedImporter:
        def __init__(self, names: set[str]) -> None:
            self.names = names

        def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
            if fullname.split(".")[0] in self.names:
                raise ImportError(f"import of {fullname!r} blocked")
            return None

    monkeypatch.delitem(sys.modules, "dbf", raising=False)
    monkeypatch.setattr(sys, "meta_path", [_BlockedImporter({"dbf"}), *sys.meta_path])
    (workspace / "exported").mkdir(exist_ok=True)
    namespace: dict[str, Any] = {"__name__": "example"}
    printed = _run(_one_block(r"except OptionalDependencyMissingError"), namespace)
    for expected in (
        "OPTIONAL_DEPENDENCY_MISSING",
        "'extra': 'write'",
        "reconstruct_dbf",
        'python -m pip install "dbfbridge[write]"',
    ):
        assert expected in printed, printed
    assert not (workspace / "rebuilt").exists()

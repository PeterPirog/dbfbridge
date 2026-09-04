"""Executable regression for the 0.x -> 1.x migration guide.

The representative snippet from ``docs/migration-1.0.md`` is not only
checked for presence — it is compiled and executed:

- the public import surface used by the guide must work;
- the ``OptionalDependencyMissingError`` example must run correctly with the
  ``[write]`` dependency absent (simulated with a scoped meta-path blocker,
  the same technique as ``test_optional_dependencies.py``);
- no snippet may require private ``dbf_bridge.*`` module imports.
"""

from __future__ import annotations

import ast
import contextlib
import io
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[1]
GUIDE = ROOT / "docs" / "migration-1.0.md"


def _python_blocks() -> list[str]:
    text = GUIDE.read_text(encoding="utf-8")
    return re.findall(r"```python\n(.*?)```", text, re.DOTALL)


def _progress_example_block() -> str:
    blocks = [
        block
        for block in _python_blocks()
        if "iter_records(" in block and "progress=show" in block
    ]
    assert len(blocks) == 1, "expected exactly one executable progress example"
    return blocks[0]


def test_documented_event_attributes_exist_on_progress_event() -> None:
    """Every ``event.<attr>`` reference in the guide must exist on the public
    ``ProgressEvent`` contract — documentation may not reference members that
    the frozen API does not have."""
    import dataclasses

    from dbfbridge import ProgressEvent

    real_fields = {field.name for field in dataclasses.fields(ProgressEvent)}
    referenced: set[str] = set()
    for block in _python_blocks():
        referenced.update(re.findall(r"\bevent\.(\w+)", block))
    assert referenced, "progress example lost from the migration guide"
    unknown = referenced - real_fields
    assert not unknown, f"guide references nonexistent ProgressEvent members: {unknown}"


def test_progress_example_executes_exactly_as_documented(tmp_path, monkeypatch) -> None:
    """Execute the Direct Read progress example against a synthetic DBF.

    Proves the public import succeeds, the callback actually runs, and the
    delivered events satisfy the public ``ProgressEvent`` contract — with no
    ``AttributeError`` and no private-module use.
    """
    import dbf as dbf_lib

    from dbfbridge import ProgressEvent, iter_records

    dbf_path = tmp_path / "KLIENCI.DBF"
    table = dbf_lib.Table(str(dbf_path), "KOD N(3,0); NAZWA C(10)", dbf_type="vfp", codepage=0xC8)
    table.open(dbf_lib.READ_WRITE)
    table.append({"KOD": 1, "NAZWA": "abc"})
    table.append({"KOD": 2, "NAZWA": "def"})
    table.append({"KOD": 3, "NAZWA": "ghi"})
    table.close()

    monkeypatch.chdir(tmp_path)
    namespace: dict[str, object] = {"__name__": "migration_guide_progress_snippet"}
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        exec(  # noqa: S102 - documentation code
            compile(_progress_example_block(), str(GUIDE), "exec"),
            namespace,
        )
    printed = stdout.getvalue()
    assert "read" in printed, printed

    # The documented callback receives real public events: verify the
    # contract directly through the public surface.
    events: list[object] = []

    def collect(event) -> None:
        events.append(event)

    records = list(iter_records(dbf_path, progress=collect, cancel_check=lambda: False))
    assert len(records) == 3
    assert events, "callback never executed"
    total = None
    for event in events:
        assert type(event) is ProgressEvent
        assert event.operation == "read"
        assert 0 <= event.current <= event.total
        assert event.records is None or event.records >= 0
        total = event.total
    assert total == 3
    assert events[-1].current == total


def test_migration_guide_contains_the_typed_error_snippet() -> None:
    blocks = _python_blocks()
    assert blocks, "migration guide lost its executable examples"
    assert any("OptionalDependencyMissingError" in block for block in blocks)


def test_migration_guide_snippets_use_public_imports_only() -> None:
    for block in _python_blocks():
        assert "from dbf_bridge." not in block, block
        assert "import dbf_bridge" not in block, block


def test_migration_guide_snippets_are_syntactically_valid() -> None:
    for block in _python_blocks():
        ast.parse(block, filename="docs/migration-1.0.md")


class _BlockedImporter:
    """Meta-path finder raising ImportError for the given top-level names."""

    def __init__(self, names: set[str]) -> None:
        self.names = names

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
        if fullname.split(".")[0] in self.names:
            raise ImportError(f"import of {fullname!r} blocked for this test")
        return None


@pytest.fixture()
def block_dbf(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Make the ``dbf`` writer dependency unimportable, as in a base install."""
    for name in ("dbf",):
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setattr(sys, "meta_path", [_BlockedImporter({"dbf"}), *sys.meta_path])
    yield
    sys.modules.pop("dbf", None)


def test_typed_error_snippet_executes_exactly_as_documented(
    block_dbf, tmp_path, monkeypatch
) -> None:
    """Extract the OptionalDependencyMissingError example and execute it.

    The block is self-contained: it imports from the public ``dbfbridge``
    namespace, triggers the typed failure (no ``[write]`` installed), and
    prints the structured payload — proving the documentation example is
    execution-correct, not merely present.  The guide's ``"output"``
    argument is the export directory from the preceding section, so the
    test materializes it in a temporary working directory.
    """
    blocks = [
        block
        for block in _python_blocks()
        if "OptionalDependencyMissingError" in block and "reconstruct_dbf(" in block
    ]
    assert len(blocks) == 1, "expected exactly one typed-error example"
    code = blocks[0]

    monkeypatch.chdir(tmp_path)
    (tmp_path / "output").mkdir()  # the exported tree the guide builds earlier

    namespace: dict[str, object] = {"__name__": "migration_guide_snippet"}
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        exec(compile(code, str(GUIDE), "exec"), namespace)  # noqa: S102 - docs code
    printed = stdout.getvalue()

    # The documented printout must appear (the snippet prints the payload
    # inside its except block; `except ... as error` unbinds the name).
    for expected in (
        "OPTIONAL_DEPENDENCY_MISSING",
        "dbf",
        "write",
        "reconstruct_dbf",
        'python -m pip install "dbfbridge[write]"',
    ):
        assert expected in printed, printed
    # The typed failure happened before any output was created.
    assert not (tmp_path / "rebuilt").exists()

"""Executable regression for the tool-server / MCP integration guide.

The representative generic adapter examples from
``docs/tool-server-integration.md`` are extracted and EXECUTED against
deterministic synthetic fixtures — proving the documentation conforms to the
frozen runtime contract (no runtime change, no MCP package, no network, no
downstream-project references).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[1]
DOC = ROOT / "docs" / "tool-server-integration.md"

sys.path.insert(0, str(ROOT / "tests"))
import vfp_fixture_factory as factory  # noqa: E402


def _fixture_dbf(path: Path, *, with_unsupported: bool = False) -> Path:
    """Create a small authentic VFP table; `with_unsupported` adds a `Q`
    (Varbinary) column — unsupported by design, exported as UNSUPPORTED."""
    if with_unsupported:
        return factory.build_vfp32_table(
            path,
            columns=[
                {"name": "KOD", "type": "N", "width": 3},
                {"name": "NAZWA", "type": "C", "width": 10},
                {"name": "BIN", "type": "Q", "width": 10},
            ],
            rows=[{"KOD": 1, "NAZWA": "abc", "BIN": b"payload"}],
        )
    return factory.build_vfp32_table(
        path,
        columns=[
            {"name": "KOD", "type": "N", "width": 3},
            {"name": "NAZWA", "type": "C", "width": 10},
        ],
        rows=[{"KOD": 1, "NAZWA": "abc"}],
    )


def _blocks() -> list[str]:
    text = DOC.read_text(encoding="utf-8")
    return re.findall(r"```python\n(.*?)```", text, re.DOTALL)


def _adapter_module_block() -> str:
    matches = [block for block in _blocks() if "def backend_status() -> dict:" in block]
    assert len(matches) == 1, "expected exactly one complete adapter example"
    return matches[0]


def _progress_serializer_block() -> str:
    matches = [block for block in _blocks() if "def progress_payload(event):" in block]
    assert len(matches) == 1, "expected exactly one progress serializer example"
    return matches[0]


def _patch_type_to_q(dbf_path: Path, field_name: str = "BIN") -> None:
    """Patch the field-type byte of *field_name* to Q (0x06, Varbinary)."""
    raw = bytearray(dbf_path.read_bytes())
    index = 32
    while raw[index] != 0x0D:
        name = bytes(raw[index : index + 11]).split(b"\x00")[0].decode("ascii", "replace")
        if name == field_name:
            raw[index + 11] = 0x06
        index += 32
    dbf_path.write_bytes(bytes(raw))


# ---------------------------------------------------------------------------
# documentation contract regressions (narrow, semantic)
# ---------------------------------------------------------------------------


def test_documented_progress_events_use_host_side_serialization() -> None:
    """``ProgressEvent`` has no ``to_dict()``; the guide must use an explicit
    host-side serializer instead of the invalid ``event.to_dict()`` pattern."""
    from dbfbridge import ProgressEvent

    assert not hasattr(ProgressEvent(operation="read", current=1, total=1), "to_dict")
    for block in _blocks():
        assert "event.to_dict()" not in block, "invalid ProgressEvent serialization pattern"
        assert "ProgressEvent.to_dict()" not in block
    assert "def progress_payload(event):" in DOC.read_text(encoding="utf-8")


def test_documented_direct_read_raw_key_is_distinct_from_migration_key() -> None:
    """The Direct Read JSON boundary (``DirectRecord.to_dict()["raw_record"]``,
    Base64) and the migration forensic record key (``__dbfbridge_raw_record__``)
    are different contracts and must be documented separately."""
    examples_doc = (ROOT / "docs" / "python-api-examples.md").read_text(encoding="utf-8")
    assert "boundary key `raw_record`" in examples_doc
    assert "__dbfbridge_raw_record__" in examples_doc
    assert "Do not" in examples_doc and "conflate" in examples_doc


def test_documented_tableschema_is_not_the_migration_artifact() -> None:
    """``TableSchema.to_dict()`` must never be described as the exporter's
    ``<table>_schema.json`` reconstruction artifact."""
    examples_doc = (ROOT / "docs" / "python-api-examples.md").read_text(encoding="utf-8")
    assert "Warning — two different schema concepts" in examples_doc
    assert "Never manufacture reconstruction input from" in examples_doc
    # The old false equivalence must not return.
    assert "payload is what the exporter writes as `<table>_schema.json`" not in examples_doc


def test_adapter_does_not_use_ok_count_as_aggregate_success() -> None:
    """`result.ok` is a COUNT of OK tables — the documented adapter must use
    `failed == 0` as the aggregate success signal."""
    adapter = _adapter_module_block()
    assert "result.ok > 0" not in adapter
    assert "result.failed == 0" in adapter


def test_adapter_capability_probe_is_fail_closed() -> None:
    adapter = _adapter_module_block()
    assert '"available": True' not in adapter
    assert "direct_read_ok = all(" in adapter


# ---------------------------------------------------------------------------
# executable adapter examples
# ---------------------------------------------------------------------------


@pytest.fixture()
def adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Execute the complete adapter module from the guide and return its namespace."""
    _fixture_dbf(tmp_path / "klienci.dbf")
    monkeypatch.chdir(tmp_path)
    namespace: dict[str, Any] = {"__name__": "adapter_example"}
    exec(compile(_adapter_module_block(), str(DOC), "exec"), namespace)  # noqa: S102 - docs code
    return namespace


def test_backend_status_is_derived(adapter: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    status = adapter["backend_status"]()
    assert status["available"] is True  # real dbfbridge: the operations exist
    assert status["direct_read"] is True
    assert status["public_api"]["inspect_table"] is True
    json.dumps(status)

    # fail-closed: with a stub module lacking the operations, the probe
    # must report unavailable (derived, not hardcoded).
    stub = type(sys.modules["dbfbridge"])("dbfbridge_stub")
    stub.__version__ = "9.9.9"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "dbfbridge", stub)
    isolated: dict[str, Any] = {"__name__": "adapter_example"}
    exec(compile(_adapter_module_block(), str(DOC), "exec"), isolated)  # noqa: S102
    broken = isolated["backend_status"]()
    assert broken["available"] is False
    assert broken["public_api"]["inspect_table"] is False


def test_inspect_table_tool_returns_json_safe_payload(
    adapter: dict[str, Any], tmp_path: Path
) -> None:
    payload = adapter["inspect_table_tool"](str(tmp_path / "klienci.dbf"))
    assert payload["ok"] is True
    json.dumps(payload)  # JSON-safe


def test_read_table_page_tool_is_bounded_and_json_safe(
    adapter: dict[str, Any], tmp_path: Path
) -> None:
    payload = adapter["read_table_page_tool"](str(tmp_path / "klienci.dbf"), limit=1)
    assert payload["ok"] is True
    assert payload["data"]["limit"] == 1  # bounded by the caller's limit
    json.dumps(payload)  # a raw-bytes leak would break json.dumps

    oversized = adapter["read_table_page_tool"](str(tmp_path / "klienci.dbf"), limit=5000)
    assert oversized["data"]["limit"] == 1000  # host-policy cap


def test_direct_read_error_response_is_structured(
    adapter: dict[str, Any], tmp_path: Path
) -> None:
    payload = adapter["read_table_page_tool"](str(tmp_path / "missing.dbf"))
    assert payload["ok"] is False
    assert payload["error"]["code"] == "PATH_NOT_FOUND"
    json.dumps(payload)


def test_export_tool_aggregate_success_semantics(
    adapter: dict[str, Any], tmp_path: Path
) -> None:
    """A multi-table export with one unsupported table must NOT report
    aggregate success merely because the other table is OK."""
    source = tmp_path / "source"
    source.mkdir()
    _fixture_dbf(source / "good.dbf")
    broken = _fixture_dbf(source / "broken.dbf", with_unsupported=True)
    _patch_type_to_q(broken, "BIN")

    payload = adapter["export_tool"](str(source), str(tmp_path / "exported"))
    assert payload["exit_code"] in {1, 2}
    assert payload["ok"] is False, "one OK table must not make a partial run successful"
    statuses = {result["status"] for result in payload["data"]["results"]}
    assert "UNSUPPORTED" in statuses


def test_reconstruct_tool_round_trip(
    adapter: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dbfbridge

    source = tmp_path / "data"
    source.mkdir()
    _fixture_dbf(source / "klienci.dbf")
    monkeypatch.chdir(tmp_path)
    export = dbfbridge.export_dbf(
        source, tmp_path / "exported", formats=("jsonl",), overwrite=True
    )
    assert export.ok == 1
    payload = adapter["reconstruct_tool"](
        str(tmp_path / "exported"), str(tmp_path / "rebuilt")
    )
    assert payload["ok"] is True  # no FAILED tables
    # warnings are not failures: the synthetic fixture reconstructs canonically
    # (canonical_match True) but not byte-identically, so the run carries a
    # warning state (exit_code 2) — the adapter surfaces it truthfully.
    assert payload["exit_code"] == 2
    assert payload["data"]["results"][0]["canonical_match"] is True
    json.dumps(payload)


def test_progress_serializer_is_json_safe() -> None:
    namespace: dict[str, Any] = {"__name__": "adapter_example"}
    exec(compile(_progress_serializer_block(), str(DOC), "exec"), namespace)  # noqa: S102
    from dbfbridge import ProgressEvent

    event = ProgressEvent(
        operation="read", current=2, total=5, table="klienci.dbf", records=2
    )
    payload = namespace["progress_payload"](event)
    assert set(payload) == {
        "operation",
        "current",
        "total",
        "table",
        "format",
        "records",
        "message",
    }
    assert payload["operation"] == "read"
    assert payload["current"] == 2
    assert payload["total"] == 5
    json.dumps(payload)  # JSON-safe

    # and the runtime contract: ProgressEvent itself has NO to_dict()
    assert not hasattr(event, "to_dict")

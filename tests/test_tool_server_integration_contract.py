"""Phase E anti-drift regressions for the tool-server / MCP integration guide.

Narrow, semantic contract tests protecting the Phase E maintained surfaces
(``docs/tool-server-integration.md``, ``docs/schemas/write-result.schema.json``):

- examples import ONLY the public ``dbfbridge`` API (DBFB-MCP-001);
- write capability is host opt-in and fail-closed (DBFB-MCP-009);
- bounded remote reads use a finite host limit (DBFB-MCP-003);
- ``LazyMemoValue`` never crosses a transport (DBFB-MCP-004);
- the Direct Write workflow never prescribes an unbounded records array and
  requires no JSONL transport (DBFB-MCP-006);
- error mapping uses structured codes/``to_dict()``, never message parsing
  (DBFB-MCP-008);
- the maintained ``WriteResult`` schema matches the runtime ``to_dict()``
  contract exactly (DBFB-MCP-010 / DBFB-RESULT-001);
- dbfbridge stays transport-neutral: no MCP/HTTP/JSON-RPC runtime dependency
  (DBFB-MCP-011 / DBFB-NOGO-011);
- no stale "write_table is unreleased research" claim returns to a promoted
  surface (DBFB-DOC-004).
"""

from __future__ import annotations

import ast
import dataclasses
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
GUIDE = ROOT / "docs" / "tool-server-integration.md"
SCHEMA = ROOT / "docs" / "schemas" / "write-result.schema.json"

#: Maintained user-facing surfaces whose Direct Write invocation examples must
#: follow the DBFB-DOC-001 import convention (public `dbfbridge` import).
USER_FACING_EXAMPLE_DOCUMENTS = (
    GUIDE,
    ROOT / "README.md",
    ROOT / "docs" / "pypi-usage.md",
    ROOT / "docs" / "python-api-examples.md",
    ROOT / "examples" / "direct_copy.py",
)

sys.path.insert(0, str(ROOT / "tests"))
import vfp_fixture_factory as factory  # noqa: E402


def _blocks(text: str) -> list[str]:
    return re.findall(r"```python\n(.*?)```", text, re.DOTALL)


def _json_blocks(text: str) -> list[str]:
    return re.findall(r"```json\n(.*?)```", text, re.DOTALL)


def _guide_text() -> str:
    return GUIDE.read_text(encoding="utf-8")


def _adapter_block() -> str:
    return next(
        block
        for block in _blocks(_guide_text())
        if "def backend_status(\n    *," in block and "-> dict:" in block
    )


# ---------------------------------------------------------------------------
# DBFB-DOC-001: public import contract for Direct Write invocation examples
# ---------------------------------------------------------------------------


def _ast_source(document: Path) -> str:
    """Python source of a doc's code blocks, or the file itself for .py."""
    if document.suffix == ".py":
        return document.read_text(encoding="utf-8")
    return "\n\n".join(_blocks(document.read_text(encoding="utf-8")))


def _write_table_call_style(tree: ast.AST) -> str | None:
    """`imported_write_table` for bare ``write_table(...)`` calls,
    `module_attribute_write_table` for ``dbfbridge.write_table(...)``."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute)):
            if isinstance(node.func, ast.Name) and node.func.id == "write_table":
                return "imported_write_table"
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "write_table"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "dbfbridge"
            ):
                return "module_attribute_write_table"
    return None


def test_direct_write_examples_use_the_public_import_contract() -> None:
    """DBFB-DOC-001 (AST-based, semantic): every maintained user-facing block
    that INVOKES Direct Write must import ``write_table`` from the public
    package and call it by the imported name — never
    ``dbfbridge.write_table(...)``, never
    ``from dbf_bridge.write import write_table``.  Capability-probe blocks
    (which only test public symbol presence) are exempt."""
    checked_documents = 0
    for document in USER_FACING_EXAMPLE_DOCUMENTS:
        tree = ast.parse(_ast_source(document))
        style = _write_table_call_style(tree)
        if style is None:
            continue  # no Direct Write invocation on this surface
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "dbfbridge"
            for alias in node.names
        }
        assert "write_table" in imports, (
            f"{document.name}: invokes write_table without 'from dbfbridge import write_table'"
        )
        assert style == "imported_write_table", (
            f"{document.name}: DBFB-DOC-001 requires the imported public name,"
            " not dbfbridge.write_table(...)"
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module != "dbf_bridge.write", (
                    f"{document.name}: private write import is forbidden"
                )
        checked_documents += 1
    # All five maintained surfaces carrying Direct Write invocation examples
    # (guide, README, pypi-usage, python-api-examples, direct_copy) are under
    # the rule.
    assert checked_documents == 5, checked_documents


def test_capability_probe_blocks_may_use_module_level_import_only() -> None:
    """The capability probe legitimately inspects public symbol presence with
    ``import dbfbridge`` and NEVER invokes Direct Write (DBFB-MCP-009)."""
    for block in _blocks(_guide_text()):
        if "def backend_status" not in block:
            continue
        tree = ast.parse(block)
        assert _write_table_call_style(tree) is None, (
            "the capability probe must not invoke Direct Write"
        )
        # and it must still test symbol presence mechanically
        assert "hasattr(" in block


def test_integration_examples_import_the_public_api_only() -> None:
    for block in _blocks(_guide_text()):
        for forbidden in (
            "from dbf_bridge.core",
            "from dbf_bridge.write",
            "from dbf_bridge.importer",
            "from dbf_bridge.exporter",
            "import dbf_bridge.core",
            "import dbf_bridge.write",
            "import dbf_bridge.importer",
            "import dbf_bridge.exporter",
        ):
            assert forbidden not in block, forbidden
    assert "import dbfbridge" in _guide_text()


# ---------------------------------------------------------------------------
# capability discovery (DBFB-MCP-009)
# ---------------------------------------------------------------------------


def _adapter_namespace(monkeypatch=None, stub: bool = False) -> dict:
    """Execute the guide's adapter block (with a stubbed ``dbfbridge`` when
    *stub* is set) and return its namespace."""
    namespace: dict = {"__name__": "adapter_example"}
    if stub:
        assert monkeypatch is not None
        stub_module = type(sys.modules["dbfbridge"])("dbfbridge_stub")
        stub_module.__version__ = "9.9.9"  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "dbfbridge", stub_module)
    text = _guide_text()
    block = next(
        block
        for block in _blocks(text)
        if "def backend_status(\n    *," in block and "-> dict:" in block
    )
    exec(compile(block, str(GUIDE), "exec"), namespace)  # noqa: S102 - docs code
    return namespace


def test_capability_probe_distinguishes_the_four_layers() -> None:
    text = _guide_text()
    for anchor in (
        "write_api_available",
        "write_capability_configured",
        "write_enabled_by_policy",
        "direct_write_available",
        "fail-closed conjunction",
        "API surface available",
        "optional dependency actually usable at operation time",
        "Configured capability is therefore **not** a",
    ):
        assert anchor in text, anchor


def test_capability_probe_never_performs_destructive_discovery() -> None:
    text = _guide_text()
    for block in _blocks(text):
        assert "pip install" not in block, "no runtime install"
        assert "requests." not in block and "urllib" not in block
        if "def backend_status" in block:
            # the probe must never run a destructive test write
            assert ".write_table(" not in block
    assert "never perform a DBF read merely" in text


def test_direct_write_capability_is_the_fail_closed_conjunction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    r"""DBFB-MCP-009 truth table, executed against the DOCUMENTED adapter
    example (mechanical, not word-matching):

        api\cfg+policy -> direct_write_available
        missing        +True +True  -> False
        present        +False+True  -> False
        present        +True +False -> False
        present        +True +True   -> True
    """
    namespace = _adapter_namespace(monkeypatch)

    # write_table MISSING: even configured + enabled stays unavailable.
    stub_namespace = _adapter_namespace(monkeypatch, stub=True)
    assert (
        stub_namespace["backend_status"](
            write_enabled=True, write_capability_configured=True
        )["direct_write_available"]
        is False
    )

    # write_table present, capability NOT configured -> unavailable.
    assert (
        namespace["backend_status"](
            write_enabled=True, write_capability_configured=False
        )["direct_write_available"]
        is False
    )
    # write_table present, capability configured, policy NOT enabling ->
    # unavailable.
    assert (
        namespace["backend_status"](
            write_enabled=False, write_capability_configured=True
        )["direct_write_available"]
        is False
    )
    # present + configured + explicitly enabled -> the only True case.
    enabled = namespace["backend_status"](
        write_enabled=True, write_capability_configured=True
    )
    assert enabled["direct_write_available"] is True
    assert enabled["write_api_available"] is True
    assert enabled["write_capability_configured"] is True
    assert enabled["write_enabled_by_policy"] is True
    # defaults fail closed; the probe never hardcodes a writable result.
    assert namespace["backend_status"]()["direct_write_available"] is False


def test_probe_does_not_enable_write_from_an_import() -> None:
    """`write_enabled` is a host flag; a successful import alone must never
    enable write exposure."""
    import dbfbridge

    assert hasattr(dbfbridge, "write_table")
    adapter = _adapter_block()
    assert "write_enabled: bool = False" in adapter
    assert "write_capability_configured: bool = False" in adapter
    assert (
        "write_api_ok and write_capability_configured and write_enabled"
        in adapter
    )
    # the probe derives its API facts, never hardcodes availability
    assert '"available": True' not in adapter


# ---------------------------------------------------------------------------
# bounded tool workflows (DBFB-MCP-003 / DBFB-MCP-006)
# ---------------------------------------------------------------------------


def test_bounded_remote_read_recommendation_is_explicit() -> None:
    text = _guide_text()
    assert "min(limit, 1000)" in text
    assert "HOST POLICY" in text


def test_direct_write_workflow_never_prescribes_an_unbounded_records_array() -> None:
    text = _guide_text()
    assert "Never accept an unbounded record array as ONE tool argument" in text
    for block in _json_blocks(text):
        assert '"records"' not in block, (
            "a host request example must carry no record payload"
        )
    assert "write_from_host_stream" in text
    assert "run_write_job" in text


def test_direct_write_requires_no_jsonl_transport() -> None:
    text = _guide_text()
    assert "does not require JSONL" in text
    assert "no JSONL transport, no spool on the caller side" in text


# ---------------------------------------------------------------------------
# memo boundary (DBFB-MCP-004)
# ---------------------------------------------------------------------------


def test_lazymemo_value_is_documented_as_local_only() -> None:
    lowered = _guide_text().casefold()
    assert "tool/mcp/json transport" in lowered
    assert "local python handle" in lowered
    assert "not** remote memo content" in lowered


# ---------------------------------------------------------------------------
# structured error mapping (DBFB-MCP-008)
# ---------------------------------------------------------------------------


def test_error_mapping_uses_codes_and_to_dict_not_message_parsing() -> None:
    text = _guide_text()
    assert "write_error_payload" in text
    assert 'payload["code"] in WRITE_ERROR_CODES' in text
    for block in _blocks(text):
        assert "startswith(" not in block
        assert "re.match(" not in block
        assert "re.search(" not in block


# ---------------------------------------------------------------------------
# WriteResult schema asset (DBFB-MCP-010 / DBFB-RESULT-001)
# ---------------------------------------------------------------------------


def test_maintained_write_result_schema_matches_the_runtime_contract(
    tmp_path: Path,
) -> None:
    import dbfbridge

    source = tmp_path / "fixture.dbf"
    factory.build_vfp32_table(
        source,
        columns=[{"name": "CODE", "type": "C", "width": 5}],
        rows=[{"CODE": "A1"}],
    )
    schema = dbfbridge.read_schema(source)
    result = dbfbridge.write_table(tmp_path / "copy.dbf", schema=schema, records=[])
    payload = result.to_dict()

    contract = json.loads(SCHEMA.read_text(encoding="utf-8"))
    properties = set(contract["properties"])
    # The maintained schema matches the runtime payload exactly: same keys,
    # same required set, JSON-safe, POSIX paths, nullable companion fields.
    assert set(payload) == properties
    assert set(contract["required"]) == properties
    json.dumps(payload)
    assert "\\" not in payload["destination"]
    assert isinstance(payload["warnings"], list)
    assert payload["fpt_path"] is None and payload["fpt_sha256"] is None
    assert payload["fpt_published"] is False
    assert payload["index_rebuild_required"] is False


def test_schema_keys_are_a_subset_of_the_runtime_model_fields() -> None:
    from dbfbridge import WriteResult

    documented = set(json.loads(SCHEMA.read_text(encoding="utf-8"))["properties"])
    runtime_fields = {field.name for field in dataclasses.fields(WriteResult)}
    assert documented <= runtime_fields


# ---------------------------------------------------------------------------
# DirectWriteError JSON boundary (DBFB-MCP-008 / DBFB-ERR-004)
# ---------------------------------------------------------------------------


def test_directwriteerror_public_payload_shape(tmp_path: Path) -> None:
    """The public Direct Write error boundary is exactly
    ``{code, message, path, context}`` — JSON-safe, no record/memo values
    (proven through the PUBLIC package boundary, not internals)."""
    import dbfbridge

    source = tmp_path / "fixture.dbf"
    factory.build_vfp32_table(
        source,
        columns=[{"name": "CODE", "type": "C", "width": 5}],
        rows=[{"CODE": "A1"}],
    )
    schema = dbfbridge.read_schema(source)

    with pytest.raises(dbfbridge.DirectWriteError) as error:
        dbfbridge.write_table(tmp_path / "out.dbf", schema=schema, records=[{}])
    payload = error.value.to_dict()
    assert set(payload) == {"code", "message", "path", "context"}
    assert payload["code"] == "WRITE_VALUE_INVALID"
    json.dumps(payload)  # JSON-safe boundary

    # The reused families keep their OWN documented shapes.
    dbfbridge.write_table(tmp_path / "out.dbf", schema=schema, records=[])
    with pytest.raises(dbfbridge.OperationOutputExistsError) as exists:
        dbfbridge.write_table(tmp_path / "out.dbf", schema=schema, records=[])
    reused = exists.value.to_dict()
    json.dumps(reused)
    assert "operation" in reused and reused["code"] == "OUTPUT_EXISTS"
    assert set(reused) != {"code", "message", "path", "context"} or (
        "table" in reused
    )


# ---------------------------------------------------------------------------
# transport neutrality (DBFB-MCP-011 / DBFB-NOGO-011)
# ---------------------------------------------------------------------------

_FORBIDDEN_RUNTIME_MODULES = re.compile(
    r"^\s*(?:import\s+(?:mcp|fastapi|flask|jsonrpc[a-z_]*|websocket[a-z_]*|httpx|aiohttp|requests)"
    r"|from\s+(?:mcp|fastapi|flask|jsonrpc[a-z_]*|websocket[a-z_]*|aiohttp|requests)\b)",
    re.MULTILINE,
)


def test_runtime_is_transport_neutral() -> None:
    hits: list[str] = []
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in _FORBIDDEN_RUNTIME_MODULES.finditer(text):
            hits.append(f"{path.relative_to(ROOT)}: {match.group(0).strip()}")
    assert hits == [], "transport/protocol dependencies must not enter the library"


def test_no_protocol_state_in_public_models() -> None:
    from dbfbridge import FieldInfo, TableSchema, WriteResult

    for model in (FieldInfo, TableSchema, WriteResult):
        names = {field.name for field in dataclasses.fields(model)}
        leaked = [
            name
            for name in names
            for token in ("session", "token", "mcp", "connection")
            if token in name
        ]
        assert leaked == [], f"{model.__name__} carries protocol state: {leaked}"


# ---------------------------------------------------------------------------
# no stale research contract on promoted surfaces (DBFB-DOC-004)
# ---------------------------------------------------------------------------


def test_no_stale_unreleased_write_table_claim_in_phase_e_surfaces() -> None:
    from dbfbridge import write_table  # the approved public contract exists

    stale_pattern = re.compile(
        r"(unreleased|not released|research only)[^\n]*\n?[^\n]*", re.IGNORECASE
    )
    for document in (GUIDE, ROOT / "docs" / "api-1.1.md", ROOT / "docs" / "README.md"):
        text = document.read_text(encoding="utf-8")
        for match in stale_pattern.finditer(text):
            context = match.group(0).casefold()
            assert not ("write_table" in context or "direct write" in context), (
                f"{document.name}: stale Direct-Write research claim: {match.group(0)!r}"
            )
    assert callable(write_table)

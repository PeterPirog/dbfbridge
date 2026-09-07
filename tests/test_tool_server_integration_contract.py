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

import dataclasses
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
GUIDE = ROOT / "docs" / "tool-server-integration.md"
SCHEMA = ROOT / "docs" / "schemas" / "write-result.schema.json"

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
        if "def backend_status() -> dict:" in block
    )


# ---------------------------------------------------------------------------
# public API only (DBFB-MCP-001 / DBFB-DOC-001)
# ---------------------------------------------------------------------------


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


def test_capability_probe_distinguishes_the_three_layers() -> None:
    text = _guide_text()
    for anchor in (
        "direct_write_api",
        "write_enabled",
        "host deployment policy decides; probe never enables",
        "OptionalDependencyMissingError",
        "API surface available",
        "optional dependency actually usable at operation time",
    ):
        assert anchor in text, anchor


def test_capability_probe_never_performs_destructive_discovery() -> None:
    for block in _blocks(_guide_text()):
        assert "pip install" not in block, "no runtime install"
        assert "requests." not in block and "urllib" not in block
        assert ".write_table(" not in block or block.count("write_table(") == 0 or (
            "def backend_status" not in block
        )
    assert "never perform a DBF read merely" in _guide_text()


def test_probe_does_not_enable_write_from_an_import() -> None:
    """`write_enabled` is a host flag; a successful import alone must never
    enable write exposure."""
    import dbfbridge

    assert hasattr(dbfbridge, "write_table")
    adapter = _adapter_block()
    assert '"write_enabled": False' in adapter
    assert "direct_write_api" in adapter
    # the probe derives its facts, never hardcodes availability
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
    from dbf_bridge.write.api import WriteResult

    documented = set(json.loads(SCHEMA.read_text(encoding="utf-8"))["properties"])
    runtime_fields = {field.name for field in dataclasses.fields(WriteResult)}
    assert documented <= runtime_fields


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
    from dbf_bridge.core.models import FieldInfo, TableSchema
    from dbf_bridge.write.api import WriteResult

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

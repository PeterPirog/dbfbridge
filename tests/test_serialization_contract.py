"""Narrow regressions for the public serialization documentation contract.

Proves the exact runtime serialization boundary documented in
``docs/api-1.0.md`` §5 and that the maintained documentation no longer makes
the two documented false claims.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1]

API_DOC = ROOT / "docs" / "api-1.0.md"
EXAMPLES_DOC = ROOT / "docs" / "python-api-examples.md"
README = ROOT / "README.md"


def test_tableresult_serializer_contract() -> None:
    """`TableResult` exposes `to_report_dict()` — and NOT `to_dict()`."""
    from dbf_bridge.exporter.models import TableResult

    assert hasattr(TableResult, "to_report_dict")
    assert not hasattr(TableResult, "to_dict")


def test_progressevent_serializer_contract() -> None:
    """`ProgressEvent` is a typed public event object without `to_dict()`."""
    import dataclasses

    from dbfbridge import ProgressEvent

    event = ProgressEvent(operation="read", current=1, total=1)
    assert not hasattr(event, "to_dict")
    fields = {field.name for field in dataclasses.fields(event)}
    assert fields == {
        "operation",
        "current",
        "total",
        "table",
        "format",
        "records",
        "message",
    }


def test_normative_docs_describe_the_tableresult_contract() -> None:
    """The normative docs must name `to_report_dict()` for TableResult and
    must not claim a `to_dict()` for it."""
    api_text = API_DOC.read_text(encoding="utf-8")
    assert "`TableResult` exposes **`to_report_dict()`**" in api_text
    assert "has **no\n  `to_dict()`**" in api_text or "no `to_dict()`" in api_text
    # The old false claim must not return.
    assert "`TableResult`, `ReconstructionResult`" not in api_text


def test_documented_examples_distinguish_raw_keys() -> None:
    """The examples document the Direct Read boundary key `raw_record` and the
    migration forensic key `__dbfbridge_raw_record__` separately, and warn
    against manufacturing reconstruction input from `read_schema().to_dict()`."""
    examples = EXAMPLES_DOC.read_text(encoding="utf-8")
    assert "boundary key `raw_record`" in examples
    assert "Do not" in examples and "conflate" in examples
    assert "Warning — two different schema concepts" in examples
    assert "Never manufacture reconstruction input from" in examples
    # the read_schema heading must not call TableSchema the reconstruction authority
    assert "the full reconstruction authority" not in examples


def test_documented_examples_have_no_duplicate_xlsx_rows() -> None:
    """Exactly one XLSX reconstruction install-profile row exists."""
    examples = EXAMPLES_DOC.read_text(encoding="utf-8")
    assert examples.count('XLSX → DBF/FPT reconstruction') == 1
    assert examples.count('XLSX → DBF reconstruction') == 0


def test_readme_references_the_committed_overview_asset() -> None:
    asset = ROOT / "docs" / "assets" / "dbfbridge-overview.png"
    assert asset.is_file()
    readme = README.read_text(encoding="utf-8")
    assert "docs/assets/dbfbridge-overview.png" in readme
    assert (
        "https://raw.githubusercontent.com/PeterPirog/dbfbridge/main/"
        "docs/assets/dbfbridge-overview.png"
    ) in readme
    # meaningful English alt text
    assert "dbfbridge overview" in readme


def test_agents_dependency_model_agrees_with_pyproject() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    # every runtime extra documented in AGENTS exists in pyproject
    for extra in ("write", "xlsx", "fast", "all", "import", "benchmark", "dev"):
        assert f"{extra} =" in pyproject or f"{extra} =[" in pyproject, (
            f"extra {extra!r} missing from pyproject"
        )
        assert f"`[{extra}]`" in agents or f"`{extra}`" in agents, (
            f"extra {extra!r} missing from AGENTS"
        )
    # base dependency
    assert "dbfread" in agents
    # the obsolete "empty xlsx extra" claim must not return
    assert "empty `import` and `xlsx` extras" not in agents

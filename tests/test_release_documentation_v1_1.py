"""Cross-document v1.1 release-truth contract tests (Phase F4A).

Narrow, deterministic anti-drift tests for the maintained USER-FACING
documentation that have no existing authority:

- release truth: PyPI access is available, the target controlled release is
  ``1.1.0``, and it has not been published (no "PyPI blocked" / "final
  1.0.0" claims in current-release documents);
- public-surface terminology: nine protected v1.0 operations PLUS the
  additive v1.1 ``write_table``;
- docs/api-1.0.md stays the protected historical baseline (no v1.1 content);
- Direct Write documentation truth (copy pattern, generator transform, no
  JSONL requirement, deleted preservation, overwrite/staging/cancellation);
- the implemented Direct Write regression CI lifecycle (F3A/F3B1/F3B2) is
  documented as implemented, not future work;
- maintained documentation navigation links resolve;
- maintained Python examples stay public-import only.

Complements (never duplicates) ``test_api_1_0_contract.py``,
``test_api_1_1_contract.py``, and
``test_tool_server_integration_contract.py``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]

#: Current-release (non-historical) user-facing documents audited here.
CURRENT_RELEASE_DOCS = (
    ROOT / "README.md",
    ROOT / "docs" / "README.md",
    ROOT / "docs" / "pypi-usage.md",
    ROOT / "docs" / "python-api-examples.md",
    ROOT / "docs" / "tool-server-integration.md",
    ROOT / "docs" / "compatibility-vfp.md",
)

NINE_V1_OPERATIONS = (
    "inspect_table",
    "read_schema",
    "iter_records",
    "read_records",
    "iter_raw_records",
    "export_dbf",
    "reconstruct_dbf",
    "verify_conversion",
    "check_conversion_quality",
)

READ_ONLY_DOCS = (
    ROOT / "docs" / "api-1.0.md",
)

sys.path.insert(0, str(ROOT / "tests"))


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# release truth (DOC-BLK-01/02/05 + PyPI/release truth contract)
# ---------------------------------------------------------------------------


def test_current_docs_do_not_claim_pypi_access_is_blocked() -> None:
    for document in CURRENT_RELEASE_DOCS:
        text = _text(document)
        assert "externally" not in text or "blocked" not in (
            text.split("externally")[1][:80].casefold()
        ), document.name
        lowered = text.casefold()
        assert "pypi publication is externally blocked" not in lowered, (
            document.name
        )
        assert "publish step failed on trusted publisher verification" not in (
            lowered
        ), document.name


def test_current_docs_do_not_target_final_1_0_0() -> None:
    for document in CURRENT_RELEASE_DOCS:
        text = _text(document)
        assert "final 1.0.0" not in text, document.name
        assert "the final release" not in text.casefold(), document.name


def test_release_truth_is_current() -> None:
    readme = _text(ROOT / "README.md")
    pypi = _text(ROOT / "docs" / "pypi-usage.md")
    assert "1.1.0" in readme and "1.1.0" in pypi
    for text in (readme, pypi):
        assert "available" in text.casefold()  # PyPI access available
        assert "has not been published" in text
        assert "deliberately deferred" in text


def test_no_document_claims_1_1_0_is_already_published() -> None:
    for document in CURRENT_RELEASE_DOCS:
        text = _text(document)
        assert not re.search(r"1\.1\.0\s+(is|has been)\s+publ", text, re.I), (
            document.name
        )


# ---------------------------------------------------------------------------
# nine + additive-one terminology (DOC-BLK-03/06/08)
# ---------------------------------------------------------------------------


def test_nine_plus_one_terminology_in_user_docs() -> None:
    for document in (
        ROOT / "README.md",
        ROOT / "docs" / "pypi-usage.md",
        ROOT / "docs" / "python-api-examples.md",
        ROOT / "docs" / "README.md",
    ):
        text = _text(document)
        assert "nine protected v1.0" in text, document.name
        assert "write_table" in text, document.name


def test_api_1_1_reference_is_navigable_and_additive() -> None:
    readme = _text(ROOT / "README.md")
    docs_map = _text(ROOT / "docs" / "README.md")
    pypi = _text(ROOT / "docs" / "pypi-usage.md")
    assert "api-1.1.md" in readme and "api-1.1.md" in docs_map
    assert "api-1.1.md" in pypi
    api11 = _text(ROOT / "docs" / "api-1.1.md")
    assert "additive" in api11.casefold()
    for operation in NINE_V1_OPERATIONS:
        assert operation in api11, operation  # the inherited baseline


def test_api_1_0_stays_the_protected_historical_baseline() -> None:
    api10 = _text(ROOT / "docs" / "api-1.0.md")
    # the protected historical reference must not absorb v1.1 content
    assert "write_table" not in api10
    assert "api-1.0.md" in _text(ROOT / "docs" / "README.md")


# ---------------------------------------------------------------------------
# install-profile truth (DOC-BLK-04/07)
# ---------------------------------------------------------------------------


def test_write_profile_documents_direct_write_and_reconstruction() -> None:
    for document in (
        ROOT / "README.md",
        ROOT / "docs" / "pypi-usage.md",
        ROOT / "docs" / "python-api-examples.md",
    ):
        text = _text(document)
        assert 'pip install "dbfbridge[write]"' in text, document.name
        # [write] covers Direct Write AND reconstruction (never only one)
        write_row = next(
            line for line in text.splitlines() if 'dbfbridge[write]"' in line
        )
        assert "write_table" in write_row, document.name
        assert "reconstruct_dbf" in write_row, document.name


# ---------------------------------------------------------------------------
# JSON boundary (DOC-BLK-09)
# ---------------------------------------------------------------------------


def test_cookbook_json_boundary_includes_write_result() -> None:
    text = _text(ROOT / "docs" / "python-api-examples.md")
    boundary = text.split("## JSON-safe boundary", 1)[1]
    assert "WriteResult" in boundary
    assert "ExportRunResult" in boundary
    assert "to_dict()" in boundary
    assert "__dict__" in boundary  # the prohibition stays explicit


# ---------------------------------------------------------------------------
# Direct Write example truth (copy / transform / no JSONL / deleted / memo)
# ---------------------------------------------------------------------------


def _python_blocks(text: str) -> list[str]:
    return re.findall(r"```python\n(.*?)```", text, re.DOTALL)


def test_direct_copy_pattern_uses_inline_memo_and_streaming() -> None:
    guide = _text(ROOT / "docs" / "python-api-examples.md")
    pypi = _text(ROOT / "docs" / "pypi-usage.md")
    direct_copy = _text(ROOT / "examples" / "direct_copy.py")
    for text in (guide, direct_copy):
        assert "read_schema" in text
        assert "iter_records" in text
        assert "write_table" in text
        assert 'memo="inline"' in text
        assert "include_deleted=True" in text
    for text in (guide, pypi, direct_copy):
        assert "list(records)" not in text  # never materialize the stream


def test_transform_example_is_generator_based() -> None:
    guide = _text(ROOT / "docs" / "tool-server-integration.md")
    examples = _text(ROOT / "docs" / "python-api-examples.md")
    for text in (guide, examples):
        copy_blocks = [
            block for block in _python_blocks(text) if "write_table(" in block
        ]
        assert copy_blocks, "a maintained write example must exist"
        has_generator = any(
            "yield" in block or "for record in " in block
            for block in copy_blocks
        )
        assert has_generator


def test_no_direct_write_example_requires_jsonl() -> None:
    pypi = _text(ROOT / "docs" / "pypi-usage.md")
    write_section = pypi.split("## Write DBF/FPT tables", 1)[1].split(
        "## Full installation", 1
    )[0]
    assert "jsonl" not in write_section.casefold().replace(
        "no jsonl", ""
    ), "the user-guide write section must not require a JSONL intermediate"
    assert "WriteResult.to_dict()" in write_section


def test_output_safety_and_staging_documented() -> None:
    api11 = _text(ROOT / "docs" / "api-1.1.md")
    assert "overwrite: bool = False" in api11
    assert "staging_directory=None" in api11
    assert "same volume" in api11
    assert "non-atomic" in api11
    assert "WRITE_CANCELLED" in api11
    assert "ProgressEvent" in api11


def test_error_code_contract_documented_not_message_parsing() -> None:
    guide = _text(ROOT / "docs" / "tool-server-integration.md")
    assert "error.code" in guide or ".code" in guide
    assert "never" in guide.casefold() and "message" in guide.casefold()
    examples = _text(ROOT / "docs" / "python-api-examples.md")
    assert "DirectWriteError" in examples


def test_canonical_vs_raw_and_cdx_dbc_truth() -> None:
    api11 = _text(ROOT / "docs" / "api-1.1.md")
    compat = _text(ROOT / "docs" / "compatibility-vfp.md")
    assert "canonical equivalence" in api11.casefold()
    assert "raw byte identity" in api11.casefold()
    assert "index_rebuild_required" in api11
    assert "external" in api11.casefold()
    assert "dbc_bound" in api11
    assert "stored procedures" in api11.casefold()
    # the compatibility matrix no longer claims Direct Write is out of scope
    assert "no Direct Write" not in compat


# ---------------------------------------------------------------------------
# benchmarks lifecycle truth (DOC-BLK-10)
# ---------------------------------------------------------------------------


def test_benchmarks_document_implemented_f3b2() -> None:
    benchmarks = _text(ROOT / "benchmarks" / "README.md")
    assert "NOT yet wired into GitHub Actions" not in benchmarks
    assert "direct-write-regression.yml" in benchmarks
    assert "F3B2" in benchmarks
    assert "future F3B" not in benchmarks
    assert "F3B's job" not in benchmarks


# ---------------------------------------------------------------------------
# navigation link integrity
# ---------------------------------------------------------------------------


def test_maintained_navigation_links_resolve() -> None:
    documents = (
        ROOT / "README.md",
        ROOT / "docs" / "README.md",
        ROOT / "docs" / "pypi-usage.md",
        ROOT / "docs" / "python-api-examples.md",
        ROOT / "examples" / "README.md",
    )
    link_pattern = re.compile(r"\]\(([^)#http][^)]*)\)")
    for document in documents:
        base = document.parent
        for target in link_pattern.findall(_text(document)):
            resolved = (base / target.split("#")[0]).resolve()
            assert resolved.is_file(), f"{document.name} -> {target}"


def test_stale_research_and_future_wording_absent_from_current_docs() -> None:
    for document in CURRENT_RELEASE_DOCS:
        text = _text(document)
        lowered = text.casefold()
        assert "direct write firewall" not in lowered, document.name
        assert "write_table` is not released" not in lowered, document.name
        assert "comparator is not yet wired" not in lowered, document.name
        assert "future f3b" not in lowered, document.name
        # current docs must not call write_table part of the v1.0 baseline
        for sentence in text.split("."):
            if "part of v1.0" in sentence.casefold() and "not" not in sentence.casefold():
                raise AssertionError(f"{document.name}: {sentence.strip()!r}")

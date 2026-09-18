"""v1.1.1 NULL / empty-string fidelity — documentation contract test.

Narrow, semantic-marker regression tests that protect the maintained
documentation against silent drift away from the normative NULL/empty
contract locked by ``tests/test_null_fidelity.py``.  These tests check
structural and semantic markers, not brittle prose equality.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]

API11 = ROOT / "docs" / "api-1.1.md"
COMPAT = ROOT / "docs" / "compatibility-vfp.md"
EXAMPLES = ROOT / "docs" / "python-api-examples.md"
CHANGES = ROOT / "CHANGELOG.md"
PYPI = ROOT / "docs" / "pypi-usage.md"
README = ROOT / "README.md"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_api_1_1_documents_null_and_empty_distinction() -> None:
    text = _text(API11).casefold()
    # The normative subsection must appear.
    assert "null and empty-value fidelity" in text
    # Core semantic markers.
    assert "none" in text
    assert "empty" in text
    assert "_nullflags" in text
    assert "writer-managed" in text
    assert "writer-managed" in _text(API11)
    # Both text states are explicitly named.
    assert '""' in _text(API11)
    # Round-trip invariant.
    assert "read(write(read(d)))" in text
    # Canonical vs raw byte identity remains stated.
    assert "canonical equivalence" in text
    assert "raw byte identity" in text


def test_release_truth_is_durable_after_the_1_1_0_release_commit() -> None:
    api = _text(API11).casefold()
    readme = _text(README).casefold()
    # 1.1.0 publication is historical fact, not a future possibility.
    assert "not a claim that a `1.1.0` package version is already" not in api
    assert "the `1.1.0` bump happens" not in api
    # Current main may advance beyond a release commit without making these
    # maintained documents false.
    assert "this commit is the dbfbridge" not in readme
    assert "this commit is the **1.1.0** release state" not in readme
    assert "current `main`" in _text(README)
    assert "[Unreleased]" in _text(README)


def test_compatibility_vfp_documents_value_states_and_allocation() -> None:
    text = _text(COMPAT)
    lowered = text.casefold()
    # Value-state markers.
    assert "none" in lowered
    assert "empty" in lowered
    assert "_nullflags" in lowered
    assert "zero" in lowered
    # Canonical allocation documented.
    assert "varlength bit" in lowered
    assert "descriptor order" in lowered or "field order" in lowered
    # Bitmap may span multiple bytes.
    assert "multiple bytes" in lowered
    # References the normative test file.
    assert "tests/test_null_fidelity.py" in text
    # Exact test names for the key matrices.
    assert "test_nullable_c_tri_state" in text
    assert "test_nullable_v_quartet" in text
    assert "test_nullable_numeric" in text
    assert "test_mixed_bitmap" in text
    assert "test_cross_byte" in text
    # No production-fix claim.
    assert "no runtime code correction was required" in lowered
    # The historical NULL-fix provenance is preserved.
    assert "test_vfp_nullable_ordinary_fields_null_bit_reads_as_none" in text


def test_python_examples_streaming_round_trip_contract() -> None:
    text = _text(EXAMPLES)
    # Required streaming markers all present.
    assert "read_schema" in text
    assert "iter_records" in text
    assert "write_table" in text
    assert 'memo="inline"' in text
    assert "include_deleted=True" in text
    # No materialized stream.
    assert "list(records)" not in text
    # Nullable states preserved in prose.
    assert '""' in text
    assert "none" in text.casefold()
    # Round-trip invariant.
    assert "read(write(read(d)))" in text.casefold()
    # Writer-managed bitmap.
    assert "writer-managed" in text.casefold()


def test_changelog_unreleased_is_contract_not_fix() -> None:
    text = _text(CHANGES)
    # The [Unreleased] section must be present and before the [1.1.0] section.
    unreleased = text.split("## [1.1.0]", 1)[0]
    assert "## [Unreleased]" in unreleased
    # Contract hardening language.
    lowered = unreleased.casefold()
    assert "contract" in lowered
    assert "null" in lowered
    assert "empty" in lowered
    # Explicit "no production fix" statement.
    assert "no runtime production-code correction" in lowered
    # The 1.1.0 historical section is untouched.
    assert "## [1.1.0] - 2026-09-10" in text


def test_no_document_claims_1_1_1_is_published() -> None:
    for doc in (README, PYPI, API11, COMPAT, EXAMPLES, CHANGES):
        text = _text(doc)
        # No claim that 1.1.1 is already available/published.
        assert not re.search(r"1\.1\.1\s+(is|has been|became)\s+publ", text, re.I), (
            doc.name
        )
        assert "1.1.1 is published" not in text.casefold(), doc.name
        assert "1.1.1 has been published" not in text.casefold(), doc.name

"""FPT corruption and boundary correctness (typed errors, lazy boundary,
atomic reconstruction failure safety).

Complements the existing FPT tests in ``test_direct_read_records.py`` (missing
FPT, lazy missing companion, block size 0, truncated companion, binary memo
bytes) with the corruption boundaries that were not covered yet.  Every case
proves the typed public error boundary, the lazy read boundary, and — for
Direct Read corruption — that the sources stay byte-identical with no files
created (no ``.partial``, lock, report, or temporary artifact).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import vfp_fixture_factory as factory

from dbf_bridge import reconstruct_dbf
from dbfbridge import (
    ErrorCode,
    FptInvalidError,
    LazyMemoValue,
    inspect_table,
    iter_raw_records,
    iter_records,
    read_schema,
)


def _memo_table(tmp_path: Path, stem: str = "memos") -> Path:
    """One VFP table with a text memo field and a genuine FPT companion."""
    return factory.create_vfp_table(
        tmp_path / f"{stem}.dbf",
        "K N(4,0); NOTATKA M",
        [{"K": 1, "NOTATKA": "zapisana notatka"}],
    )


def _assert_json_safe(payload: Any) -> None:
    json.dumps(payload, ensure_ascii=False, allow_nan=False)


# ---------------------------------------------------------------------------
# corruption boundaries: pointer beyond EOF
# ---------------------------------------------------------------------------


def test_fpt_pointer_beyond_eof_is_typed_invalid(tmp_path: Path) -> None:
    source = _memo_table(tmp_path)
    fpt_path = source.with_suffix(".fpt")
    factory.set_memo_pointer(source, 0, 1, 1_000_000)
    dbf_sha = factory.sha256_file(source)
    fpt_sha = factory.sha256_file(fpt_path)
    fingerprint_before = factory.directory_fingerprint(tmp_path)

    with pytest.raises(FptInvalidError) as error:
        next(iter_records(source, memo="inline"))
    assert error.value.code is ErrorCode.FPT_INVALID
    _assert_json_safe(error.value.to_dict())

    # The forensic stream never opens the FPT: raw bytes stay readable.
    raw = next(iter(iter_raw_records(source)))
    assert raw.raw_record is not None

    # Read-only guarantee: both sources byte-identical, nothing created.
    assert factory.sha256_file(source) == dbf_sha
    assert factory.sha256_file(fpt_path) == fpt_sha
    assert factory.directory_fingerprint(tmp_path) == fingerprint_before


def test_lazy_corrupt_memo_fails_only_on_load(tmp_path: Path) -> None:
    """With memo="lazy" a corrupt pointer must not break iteration or
    to_dict(): the corruption surfaces exactly at the explicit load()."""
    source = _memo_table(tmp_path)
    factory.set_memo_pointer(source, 0, 1, 1_000_000)
    fingerprint_before = factory.directory_fingerprint(tmp_path)

    records = list(iter_records(source, memo="lazy"))
    lazy = records[0].values["NOTATKA"]
    assert isinstance(lazy, LazyMemoValue)
    # Describing the pointer performs no memo I/O.
    description = lazy.to_dict()
    _assert_json_safe(description)
    assert description["block"] == 1_000_000

    with pytest.raises(FptInvalidError) as error:
        lazy.load()
    assert error.value.code is ErrorCode.FPT_INVALID
    _assert_json_safe(error.value.to_dict())
    assert factory.directory_fingerprint(tmp_path) == fingerprint_before


def test_fpt_payload_length_beyond_eof_is_typed_invalid(tmp_path: Path) -> None:
    """A memo block whose declared payload length extends beyond the FPT EOF
    is a typed invalid-companion error, never a partial read."""
    source = _memo_table(tmp_path)
    fpt_path = source.with_suffix(".fpt")
    _next_free, block_size = factory.fpt_layout(fpt_path)
    block = factory.read_memo_pointer(source, 0, 1)
    factory.patch_memo_block_header(fpt_path, block, block_size, payload_length=10**6)
    dbf_sha = factory.sha256_file(source)

    with pytest.raises(FptInvalidError) as error:
        next(iter_records(source, memo="inline"))
    assert error.value.code is ErrorCode.FPT_INVALID
    _assert_json_safe(error.value.to_dict())

    recs = list(iter_records(source, memo="lazy"))
    with pytest.raises(FptInvalidError) as lazy_error:
        recs[0].values["NOTATKA"].load()
    assert lazy_error.value.code is ErrorCode.FPT_INVALID
    assert factory.sha256_file(source) == dbf_sha


def test_fpt_truncated_block_header_is_typed_invalid_on_lazy_load(tmp_path: Path) -> None:
    """A memo block whose 8-byte header is cut by the end of the file stays
    lazy-clean during iteration and fails typed at the load boundary."""
    source = _memo_table(tmp_path)
    fpt_path = source.with_suffix(".fpt")
    _next_free, block_size = factory.fpt_layout(fpt_path)
    block = factory.read_memo_pointer(source, 0, 1)
    # Keep only the first half of the block header.
    fpt_path.write_bytes(fpt_path.read_bytes()[: block * block_size + 4])

    records = list(iter_records(source, memo="lazy"))
    lazy = records[0].values["NOTATKA"]
    assert isinstance(lazy, LazyMemoValue)
    assert lazy.to_dict()["block"] == block

    with pytest.raises(FptInvalidError) as error:
        lazy.load()
    assert error.value.code is ErrorCode.FPT_INVALID
    _assert_json_safe(error.value.to_dict())


# ---------------------------------------------------------------------------
# empty-memo semantics
# ---------------------------------------------------------------------------


def test_fpt_pointer_zero_reads_as_empty_across_policies(tmp_path: Path) -> None:
    """A zero memo pointer means 'no value': inline/lazy/null resolve to
    None and skip omits the field — no FPT payload read can crash it."""
    source = factory.create_vfp_table(
        tmp_path / "empty.dbf", "K N(4,0); NOTATKA M", [{"K": 1, "NOTATKA": None}]
    )
    assert factory.read_memo_pointer(source, 0, 1) == 0

    inline = next(iter(iter_records(source, memo="inline")))
    assert inline.values["NOTATKA"] is None
    lazy = next(iter(iter_records(source, memo="lazy")))
    assert lazy.values["NOTATKA"] is None  # pointer 0 never becomes LazyMemoValue
    null_policy = next(iter(iter_records(source, memo="null")))
    assert null_policy.values["NOTATKA"] is None
    skipped = next(iter(iter_records(source, memo="skip")))
    assert "NOTATKA" not in skipped.values


def test_fpt_empty_payload_block_reads_as_empty_string(tmp_path: Path) -> None:
    """A present block with a declared payload length of 0 is a valid,
    deterministic empty value — an empty string, never an exception and
    never confused with the pointer-0 'no value' state."""
    source = _memo_table(tmp_path)
    fpt_path = source.with_suffix(".fpt")
    _next_free, block_size = factory.fpt_layout(fpt_path)
    block = factory.read_memo_pointer(source, 0, 1)
    factory.patch_memo_block_header(fpt_path, block, block_size, payload_length=0)

    record = next(iter(iter_records(source, memo="inline")))
    assert record.values["NOTATKA"] == ""


# ---------------------------------------------------------------------------
# layout robustness
# ---------------------------------------------------------------------------


def test_fpt_non_default_block_size_reads_payload_correctly(tmp_path: Path) -> None:
    """A hand-built FPT with a 1024-byte block size (a legal VFP size above
    the 512-byte unit range) is read back with the exact payload, and the
    schema reports the declared block size."""
    source = factory.create_vfp_table(
        tmp_path / "bs.dbf", "K N(4,0); NOTATKA M", [{"K": 1, "NOTATKA": "blok 1024"}]
    )
    fpt_path = source.with_suffix(".fpt")
    payload = "blok 1024".encode("cp1250")
    factory.rewrite_fpt(fpt_path, block_size=1024, blocks=[(1, 0x1, payload)])
    factory.set_memo_pointer(source, 0, 1, 1)

    record = next(iter(iter_records(source, memo="inline")))
    assert record.values["NOTATKA"] == "blok 1024"

    schema = read_schema(source)
    assert schema.memo_block_size == 1024
    # Header-level validation accepts the non-default size (no power-of-two rule).
    assert not any("power" in warning.lower() for warning in schema.warnings)


def test_multiple_memo_fields_are_read_independently(tmp_path: Path) -> None:
    """Two memo fields of one record point at separate blocks; inline reads
    both payloads and lazy loading reads each block independently."""
    source = factory.create_vfp_table(
        tmp_path / "two.dbf",
        "K N(4,0); A M; B M",
        [{"K": 1, "A": "memo A", "B": "memo B"}],
    )
    inline = next(iter(iter_records(source, memo="inline")))
    assert inline.values["A"] == "memo A"
    assert inline.values["B"] == "memo B"

    lazy = next(iter(iter_records(source, memo="lazy")))
    first = lazy.values["A"]
    second = lazy.values["B"]
    assert isinstance(first, LazyMemoValue) and isinstance(second, LazyMemoValue)
    assert first.to_dict()["block"] != second.to_dict()["block"]
    assert first.load() == "memo A"
    assert second.load() == "memo B"

    schema = inspect_table(source)
    memo_fields = [field.name for field in schema.fields if field.is_memo]
    assert memo_fields == ["A", "B"]


def test_deleted_record_memo_is_readable_when_included(tmp_path: Path) -> None:
    """A deleted record keeps its physical memo pointer: with
    include_deleted=True the inline path still resolves its payload, while
    the forensic stream preserves the pointer bytes without opening the
    FPT."""
    source = factory.create_vfp_table(
        tmp_path / "del.dbf",
        "K N(4,0); NOTATKA M",
        [{"K": 1, "NOTATKA": "zapisany"}, {"K": 2, "NOTATKA": "usunięty"}],
    )
    factory.mark_deleted(source, 1)
    fpt_sha = factory.sha256_file(source.with_suffix(".fpt"))

    records = list(iter_records(source, memo="inline", include_deleted=True))
    assert [record.values["NOTATKA"] for record in records] == ["zapisany", "usunięty"]
    assert [record.deleted for record in records] == [False, True]

    raw = list(iter_raw_records(source))
    assert [(r.physical_index, r.deleted) for r in raw] == [(0, False), (1, True)]
    header_length, record_length, _count = factory.dbf_layout(source)
    assert (
        raw[1].raw_record
        == source.read_bytes()[header_length + record_length : header_length + 2 * record_length]
    )
    assert factory.sha256_file(source.with_suffix(".fpt")) == fpt_sha


# ---------------------------------------------------------------------------
# atomic reconstruction failure safety
# ---------------------------------------------------------------------------


def test_reconstruction_failure_leaves_no_partial_outputs(tmp_path: Path) -> None:
    """A reconstruction that fails while writing a record publishes nothing:
    no DBF/FPT in the destination, no ``.partial`` residue, and the sources
    stay byte-identical."""
    source = factory.create_vfp_table(
        tmp_path / "t.dbf", "K N(4,0); TX C(5)", [{"K": 1, "TX": "abcde"}]
    )
    source_sha = factory.sha256_file(source)
    export_dir = tmp_path / "export"

    from dbf_bridge import export_dbf

    export_result = export_dbf(source, export_dir, formats=("jsonl",), overwrite=True)
    export_result.raise_for_errors()

    # Corrupt the payload: the value no longer fits the schema's C(5) field.
    data_file = export_dir / "t.jsonl"
    lines = data_file.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    record["TX"] = "MUCH_TOO_LONG_VALUE"
    lines[0] = json.dumps(record, ensure_ascii=False)
    data_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    destination = tmp_path / "rebuilt"
    result = reconstruct_dbf(export_dir, destination, input_format="jsonl", overwrite=True)
    assert result.ok == 0
    assert result.results[0].status == "FAILED"
    assert destination.exists()
    residue = sorted(item.name for item in destination.rglob("*"))
    assert residue == ["reconstruction_report.jsonl"], residue
    assert not list(destination.rglob("*.partial"))
    assert factory.sha256_file(source) == source_sha

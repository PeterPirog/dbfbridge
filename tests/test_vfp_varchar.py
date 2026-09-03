"""Authentic VFP Varchar (0x32) and ``_NullFlags`` semantics evidence.

The fixtures here are NOT type-byte patches: ``build_vfp32_table`` writes the
whole file per the documented VFP physical contract — version byte 0x32, a
real ``_NullFlags`` system column (type ``0``) with per-record varlength/NULL
bits allocated in field order, and variable-length payloads whose last byte
carries the actual value length.  See ``tests/vfp_fixture_factory.py`` for
the construction; ``src/dbf_bridge/core/nullflags.py`` for the runtime's
independent implementation of the same contract.

Source of the physical rule: external VFP format review — V/Q columns use
``_NullFlags`` varlength bits (varlength bit set -> last payload byte is the
actual length; clear -> the full declared width is the value; NULL bit set ->
the logical value is NULL regardless of the stored bytes).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import vfp_fixture_factory as factory

from dbf_bridge import export_dbf, reconstruct_dbf
from dbfbridge import (
    DbfHeaderInvalidError,
    DbfRecordInvalidError,
    ErrorCode,
    inspect_table,
    iter_records,
)


def _varchar_table(tmp_path: Path, rows: list[dict]) -> Path:
    return factory.build_vfp32_table(
        tmp_path / "varchar.dbf",
        columns=[{"name": "TX", "type": "V", "width": 10, "nullable": True}],
        rows=rows,
    )


# ---------------------------------------------------------------------------
# varlength storage forms
# ---------------------------------------------------------------------------


def test_vfp_varchar_varlength_short_value_reads_exact_prefix(tmp_path: Path) -> None:
    """V(10) storing 'abc' with the varlength bit set: payload
    ``b'abc      \\x03'`` — the last byte is the actual length and the value
    is exactly the first three bytes."""
    source = _varchar_table(tmp_path, [{"TX": "abc"}])
    header_length, record_length, _count = factory.dbf_layout(source)
    raw = source.read_bytes()[header_length : header_length + record_length]
    # Physical proof: delete marker + 'abc' + padding + length byte + bitmap.
    assert raw == b" abc      \x03\x01"

    record = next(iter(iter_records(source)))
    assert record.values["TX"] == "abc"


def test_vfp_varchar_keeps_significant_trailing_spaces(tmp_path: Path) -> None:
    """The decisive case against a ``parseC``-style ``rstrip``: a varlength
    value with significant trailing spaces must read back exactly.

    Physical payload: ``b'abc      \\x05'`` — actual length byte says 5, so
    the logical value is ``'abc  '`` (two significant trailing spaces), not
    ``'abc'``."""
    source = _varchar_table(tmp_path, [{"TX": ("abc  ", True)}])
    header_length, record_length, _count = factory.dbf_layout(source)
    raw = source.read_bytes()[header_length : header_length + record_length]
    assert raw == b" abc      \x05\x01"

    record = next(iter(iter_records(source)))
    assert record.values["TX"] == "abc  "
    assert len(record.values["TX"]) == 5
    # JSON-safe representation keeps the exact value too.
    assert record.to_dict()["values"]["TX"] == "abc  "


def test_vfp_varchar_full_width_value_has_no_length_byte(tmp_path: Path) -> None:
    """With a CLEAR varlength bit the full declared width is the logical
    value — the last byte is never interpreted as a length."""
    source = _varchar_table(tmp_path, [{"TX": ("abcdefghij", False)}])
    header_length, record_length, _count = factory.dbf_layout(source)
    raw = source.read_bytes()[header_length : header_length + record_length]
    assert raw == b" abcdefghij\x00"

    record = next(iter(iter_records(source)))
    assert record.values["TX"] == "abcdefghij"
    assert len(record.values["TX"]) == 10


def test_vfp_varchar_full_width_trailing_spaces_are_significant(tmp_path: Path) -> None:
    """Full-width storage keeps every byte significant: 'abc' padded to
    width 10 reads back with its seven trailing spaces."""
    source = _varchar_table(tmp_path, [{"TX": ("abc       ", False)}])
    record = next(iter(iter_records(source)))
    assert record.values["TX"] == "abc       "
    assert len(record.values["TX"]) == 10


# ---------------------------------------------------------------------------
# NULL semantics
# ---------------------------------------------------------------------------


def test_vfp_varchar_nullable_value_and_null_record(tmp_path: Path) -> None:
    """A NULLable Varchar: a non-null variable-length record reads its exact
    value; a record with the NULL bit set reads None — regardless of the
    blank payload bytes."""
    source = _varchar_table(
        tmp_path,
        [{"TX": "abc"}, {"TX": None}],
    )
    records = list(iter_records(source))
    assert records[0].values["TX"] == "abc"
    assert records[1].values["TX"] is None

    # The NULL record's bitmap bit is provably set in the file.
    header_length, record_length, _count = factory.dbf_layout(source)
    data = source.read_bytes()
    null_record = data[header_length + record_length : header_length + 2 * record_length]
    assert null_record[-1] == 0x02  # varlength bit clear, NULL bit set


def test_vfp_nullable_ordinary_fields_null_bit_reads_as_none(tmp_path: Path) -> None:
    """Ordinary NULLable fields (C/I/Y written by the reference writer with
    its real NULL sentinel) resolve to None exactly when their NULL bit is
    set — blank payload bytes are never decoded into 0/'' for a NULL value.

    Reference-written fixture: ``dbf`` allocates one NULL bit per nullable
    field in field order (bit set = NULL), cross-checked against its own
    read-back."""
    import dbf

    source = factory.create_vfp_table(
        tmp_path / "nulls.dbf",
        "TX C(6) NULL; LICZ I NULL; KWOTA Y NULL",
        [
            {"TX": "abc", "LICZ": 5, "KWOTA": 1.5},
            {"TX": dbf.Null, "LICZ": dbf.Null, "KWOTA": dbf.Null},
            {"TX": dbf.Null, "LICZ": 7, "KWOTA": 2.5},
        ],
    )
    records = list(iter_records(source))
    assert records[0].values["TX"] == "abc"
    assert records[0].values["LICZ"] == 5
    assert records[1].values["TX"] is None  # was '' before the _NullFlags fix
    assert records[1].values["LICZ"] is None  # was 0 before the fix
    assert records[1].values["KWOTA"] is None  # was Decimal('0') before the fix
    assert records[2].values["TX"] is None
    assert records[2].values["LICZ"] == 7
    assert records[2].values["KWOTA"] == pytest.approx(2.5)


# ---------------------------------------------------------------------------
# bit allocation across mixed fields
# ---------------------------------------------------------------------------


def test_vfp_nullflags_bit_allocation_across_mixed_fields(tmp_path: Path) -> None:
    """Bits are allocated in field order: V1 varlength(0) + NULL(1),
    C1 NULL(2), V2 varlength(3) + NULL(4).  The bitmap proves each column
    owns its own bit — 'Varchar is always bit 0' is explicitly false."""
    source = factory.build_vfp32_table(
        tmp_path / "mixed.dbf",
        columns=[
            {"name": "V1", "type": "V", "width": 8, "nullable": True},
            {"name": "C1", "type": "C", "width": 4, "nullable": True},
            {"name": "V2", "type": "V", "width": 8, "nullable": True},
        ],
        rows=[
            # V1 non-null, C1 NULL, V2 non-null -> bitmap bits: V1.var=0,
            # V1.null=1 clear, C1.null=2 set, V2.var=3 set, V2.null=4 clear.
            {"V1": "row-one", "C1": None, "V2": "row-two"},
        ],
    )
    info = inspect_table(source)
    nullflags = next(field for field in info.fields if field.dbf_type == "0")
    assert nullflags.system is True
    assert nullflags.length == 1  # 5 allocated bits -> 1 byte

    # Physical record: V1(8) + C1(4) + V2(8) + _NullFlags(1).
    header_length, record_length, _count = factory.dbf_layout(source)
    raw = source.read_bytes()[header_length : header_length + record_length]
    # V1 varlength: 'row-one' (7) -> 'row-one' + \x07; C1 blank (NULL);
    # V2 varlength: 'row-two' + \x07.  Bitmap 0x0D = bits 0 (V1 varlength),
    # 2 (C1 NULL), 3 (V2 varlength) set; bits 1 and 4 (both NULL bits of the
    # non-null Varchars) clear — the exact byte proves field-order allocation.
    assert raw == b" row-one\x07    row-two\x07\x0d"

    records = list(iter_records(source))
    assert records[0].values["V1"] == "row-one"
    assert records[0].values["C1"] is None
    assert records[0].values["V2"] == "row-two"


# ---------------------------------------------------------------------------
# malformed bitmap / payload boundaries (typed errors, read-only safety)
# ---------------------------------------------------------------------------


def test_vfp_varchar_length_beyond_capacity_is_typed_invalid(tmp_path: Path) -> None:
    """A varlength payload whose length byte exceeds the field capacity (the
    width minus the length byte itself) is a typed record-level error."""
    source = _varchar_table(tmp_path, [{"TX": "abc"}])
    # Corrupt the length byte: 10 > capacity 9.
    header_length, record_length, _count = factory.dbf_layout(source)
    data = bytearray(source.read_bytes())
    data[header_length + 10] = 0x0A
    source.write_bytes(bytes(data))

    fingerprint_before = factory.directory_fingerprint(tmp_path)
    with pytest.raises(DbfRecordInvalidError) as error:
        next(iter_records(source))
    assert error.value.code is ErrorCode.DBF_RECORD_INVALID
    json.dumps(error.value.to_dict(), allow_nan=False)
    assert error.value.to_dict()["context"]["declared_length"] == 10
    assert factory.directory_fingerprint(tmp_path) == fingerprint_before


def test_vfp_missing_nullflags_column_is_typed_header_invalid(tmp_path: Path) -> None:
    """A table with a Varchar column but no ``_NullFlags`` bitmap column is
    structurally inconsistent: typed ``DBF_HEADER_INVALID`` at request time."""
    source = factory.build_vfp32_table(
        tmp_path / "nobitmap.dbf",
        columns=[{"name": "TX", "type": "V", "width": 10, "nullable": False}],
        rows=[{"TX": "abc"}],
    )
    # Strip the _NullFlags descriptor and its record bytes: header must be
    # rebuilt with the reduced layout (the builder always writes one when a
    # V column exists, so shrink it back manually).
    data = bytearray(source.read_bytes())
    header_length, record_length, record_count = factory.dbf_layout(source)
    new_header_length = header_length - 32
    new_record_length = record_length - 1
    header = bytearray(data[:32])
    header[8:10] = new_header_length.to_bytes(2, "little")
    header[10:12] = new_record_length.to_bytes(2, "little")
    descriptors = data[32 : header_length - 1 - 263]
    kept = descriptors[:-32]
    records_area = data[header_length : header_length + record_length * record_count]
    shrunk = bytearray()
    for index in range(record_count):
        chunk = records_area[index * record_length : (index + 1) * record_length]
        shrunk += chunk[:-1]  # drop the trailing bitmap byte
    source.write_bytes(
        bytes(header + kept + b"\x0d" + data[header_length - 263 : header_length] + shrunk)
    )

    with pytest.raises(DbfHeaderInvalidError) as error:
        next(iter_records(source))
    assert error.value.code is ErrorCode.DBF_HEADER_INVALID
    json.dumps(error.value.to_dict(), allow_nan=False)


def test_vfp_nullflags_too_short_is_typed_header_invalid(tmp_path: Path) -> None:
    """A ``_NullFlags`` column shorter than the allocated bits cannot be
    trusted: typed ``DBF_HEADER_INVALID``."""
    source = factory.build_vfp32_table(
        tmp_path / "short.dbf",
        columns=[
            {"name": "V1", "type": "V", "width": 8, "nullable": True},
            {"name": "V2", "type": "V", "width": 8, "nullable": True},
        ],
        rows=[{"V1": "a", "V2": "b"}],
    )
    # 4 allocated bits need 1 byte; shrink the declared bitmap column to 0
    # bytes and shrink the record accordingly.
    data = bytearray(source.read_bytes())
    header_length, record_length, record_count = factory.dbf_layout(source)
    data[32 + 2 * 32 + 16] = 0  # V2? no: descriptor 2 is _NullFlags -> length 0
    new_record_length = record_length - 1
    data[10:12] = new_record_length.to_bytes(2, "little")
    records_area = data[header_length : header_length + record_length * record_count]
    shrunk_records = b"".join(
        records_area[index * record_length : index * record_length + new_record_length]
        for index in range(record_count)
    )
    source.write_bytes(bytes(data[:header_length]) + shrunk_records)

    with pytest.raises(DbfHeaderInvalidError) as error:
        next(iter_records(source))
    assert error.value.code is ErrorCode.DBF_HEADER_INVALID
    payload = error.value.to_dict()
    json.dumps(payload, allow_nan=False)
    assert payload["context"]["required_bytes"] == 1


# ---------------------------------------------------------------------------
# migration + reconstruction evidence
# ---------------------------------------------------------------------------


def test_vfp_varchar_migration_jsonl_preserves_exact_values(tmp_path: Path) -> None:
    """Migration keeps the exact logical Varchar values: significant
    trailing spaces and the NULL distinction survive into JSONL."""
    source = _varchar_table(
        tmp_path,
        [{"TX": "abc"}, {"TX": ("abc  ", True)}, {"TX": None}],
    )
    export_dir = tmp_path / "export"
    result = export_dbf(source, export_dir, formats=("jsonl",), overwrite=True)
    result.raise_for_errors()
    lines = [
        json.loads(line)
        for line in (export_dir / "varchar.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [entry["TX"] for entry in lines] == ["abc", "abc  ", None]


def test_vfp_varchar_roundtrip_canonical_identity_with_raw_gap(tmp_path: Path) -> None:
    """The authentic Varchar table round-trips with CANONICAL identity
    (every variable-length value, trailing spaces included, compares equal).
    Raw DBF byte identity is a documented reconstruction gap: the writer
    cannot yet recreate the variable-length layout (its rebuilt bytes differ,
    e.g. the trailing 0x1A EOF marker), so the matrix records Varchar as
    READ_SUPPORTED / RECONSTRUCTION raw gap."""
    source = _varchar_table(
        tmp_path,
        [{"TX": "abc"}, {"TX": ("abc  ", True)}, {"TX": ("abcdefghij", False)}],
    )
    export_dir = tmp_path / "export"
    export_result = export_dbf(source, export_dir, formats=("jsonl",), overwrite=True)
    export_result.raise_for_errors()
    rebuilt = tmp_path / "rebuilt"
    result = reconstruct_dbf(export_dir, rebuilt, input_format="jsonl", overwrite=True)
    result.raise_for_errors()
    report = result.results[0]
    assert report.canonical_match is True
    # Honest limitation: raw bytes differ (writer gap), surfaced as a warning.
    assert report.raw_dbf_match is not True
    assert any("Raw DBF SHA-256 differs" in warning for warning in report.warnings)
    # The rebuilt table still decodes to the exact logical values.
    records = list(iter_records(rebuilt / "varchar.dbf"))
    assert [record.values["TX"] for record in records] == ["abc", "abc  ", "abcdefghij"]


def test_vfp_varchar_null_record_reconstruction_is_a_known_gap(tmp_path: Path) -> None:
    """A genuinely NULL-marked Varchar record does not yet reconstruct with
    canonical equality: the writer does not emit NULL bits and the
    verification re-read resolves the stored blanks.  The failure is typed
    and structured (per-table FAILED), never a crash — documented as a
    reconstruction gap, not hidden."""
    source = _varchar_table(tmp_path, [{"TX": "abc"}, {"TX": None}])
    export_dir = tmp_path / "export"
    export_result = export_dbf(source, export_dir, formats=("jsonl",), overwrite=True)
    export_result.raise_for_errors()
    rebuilt = tmp_path / "rebuilt"
    result = reconstruct_dbf(export_dir, rebuilt, input_format="jsonl", overwrite=True)
    assert result.ok == 0
    report = result.results[0]
    assert report.status == "FAILED"
    assert any("Canonical checksum mismatch" in message for message in report.errors)
    # No residue: only the report is published for the failed table.
    assert not list(rebuilt.rglob("*.partial"))

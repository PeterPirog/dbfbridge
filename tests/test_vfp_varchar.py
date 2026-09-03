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

import base64
import json
from pathlib import Path

import dbf
import pytest
import vfp_fixture_factory as factory

from dbf_bridge import export_dbf, reconstruct_dbf
from dbf_bridge.core import nullflags
from dbfbridge import (
    DbfHeaderInvalidError,
    DbfRecordInvalidError,
    ErrorCode,
    inspect_table,
    iter_raw_records,
    iter_records,
    read_schema,
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


def test_vfp_varchar_null_record_reconstructs_canonically(tmp_path: Path) -> None:
    """A genuinely NULL-marked Varchar record reconstructs with CANONICAL
    equality: the JSONL carries ``TX: null`` plus the raw ``_NullFlags``
    bitmap bytes, the rebuilt table restores those bytes, and the
    verification re-read resolves the NULL bit to ``None`` — matching the
    input side exactly.  (Before the NullFlags unification this failed with
    'Canonical checksum mismatch': expected None, actual ''.)"""
    source = _varchar_table(tmp_path, [{"TX": "abc"}, {"TX": None}])
    export_dir = tmp_path / "export"
    export_result = export_dbf(source, export_dir, formats=("jsonl",), overwrite=True)
    export_result.raise_for_errors()
    rebuilt = tmp_path / "rebuilt"
    result = reconstruct_dbf(export_dir, rebuilt, input_format="jsonl", overwrite=True)
    result.raise_for_errors()
    report = result.results[0]
    assert report.canonical_match is True
    # Logical values after re-read of the rebuilt table.
    assert [record.values["TX"] for record in iter_records(rebuilt / "varchar.dbf")] == [
        "abc",
        None,
    ]
    # Raw byte identity remains a known, honestly reported limitation.
    assert report.raw_dbf_match is not True
    assert any("Raw DBF SHA-256 differs" in warning for warning in report.warnings)
    assert not list(rebuilt.rglob("*.partial"))


def test_vfp_varchar_mixed_null_bitmap_reconstructs_canonically(tmp_path: Path) -> None:
    """Reconstruction of a table with THREE nullable columns whose bitmap
    allocation interleaves varlength and NULL bits: V1 varlength=0, V1
    NULL=1, C1 NULL=2, V2 varlength=3, V2 NULL=4.

    Record 1 has C1 NULL; record 2 has V1/V2 NULL — the exact per-field bit
    assignment must survive the round trip (the pre-unification importer
    read NULL bits from ``enumerate(nullable)`` positions instead)."""
    source = factory.build_vfp32_table(
        tmp_path / "mixed.dbf",
        columns=[
            {"name": "V1", "type": "V", "width": 8, "nullable": True},
            {"name": "C1", "type": "C", "width": 4, "nullable": True},
            {"name": "V2", "type": "V", "width": 8, "nullable": True},
        ],
        rows=[
            {"V1": "row-one", "C1": None, "V2": "row-two"},
            {"V1": None, "C1": "abc", "V2": None},
            {"V1": "r3", "C1": "d4", "V2": ("e5f6g7h8", False)},
        ],
    )
    export_dir = tmp_path / "export"
    export_result = export_dbf(source, export_dir, formats=("jsonl",), overwrite=True)
    export_result.raise_for_errors()
    rebuilt = tmp_path / "rebuilt"
    result = reconstruct_dbf(export_dir, rebuilt, input_format="jsonl", overwrite=True)
    result.raise_for_errors()
    report = result.results[0]
    assert report.canonical_match is True
    records = list(iter_records(rebuilt / "mixed.dbf"))
    assert [record.values["V1"] for record in records] == ["row-one", None, "r3"]
    assert [record.values["C1"] for record in records] == [None, "abc", "d4"]
    assert [record.values["V2"] for record in records] == ["row-two", None, "e5f6g7h8"]


def test_vfp_ordinary_nullable_fields_reconstruct_canonically(tmp_path: Path) -> None:
    """Ordinary NULLable C/I/Y fields (no Varchar at all) keep reconstructing
    canonically — the unified NullFlags verification must not regress the
    reference-written path."""
    source = factory.create_vfp_table(
        tmp_path / "nulls.dbf",
        "TX C(6) NULL; LICZ I NULL; KWOTA Y NULL",
        [
            {"TX": "abc", "LICZ": 5, "KWOTA": 1.5},
            {"TX": dbf.Null, "LICZ": dbf.Null, "KWOTA": dbf.Null},
        ],
    )
    export_dir = tmp_path / "export"
    export_result = export_dbf(source, export_dir, formats=("jsonl",), overwrite=True)
    export_result.raise_for_errors()
    rebuilt = tmp_path / "rebuilt"
    result = reconstruct_dbf(export_dir, rebuilt, input_format="jsonl", overwrite=True)
    result.raise_for_errors()
    report = result.results[0]
    assert report.canonical_match is True
    records = list(iter_records(rebuilt / "nulls.dbf"))
    assert [record.values["TX"] for record in records] == ["abc", None]
    assert [record.values["LICZ"] for record in records] == [5, None]
    assert [record.values["KWOTA"] for record in records] == [
        pytest.approx(1.5),
        None,
    ]


def test_vfp_interleaved_deleted_records_reconstruct_canonically(tmp_path: Path) -> None:
    """Deleted and active records interleave physically; reconstruction
    keeps the relative order within each group (active hash, deleted hash)
    and the deleted markers stay exact."""
    source = factory.create_vfp_table(
        tmp_path / "interleaved.dbf",
        "K N(4,0); TX C(6) NULL",
        [{"K": 1, "TX": "one"}, {"K": 2, "TX": "two"}, {"K": 3, "TX": "three"}],
    )
    factory.mark_deleted(source, 1)  # physical order: active, deleted, active
    export_dir = tmp_path / "export"
    export_result = export_dbf(
        source, export_dir, formats=("jsonl",), overwrite=True, deleted="include"
    )
    export_result.raise_for_errors()
    rebuilt = tmp_path / "rebuilt"
    result = reconstruct_dbf(export_dir, rebuilt, input_format="jsonl", overwrite=True)
    result.raise_for_errors()
    report = result.results[0]
    assert report.canonical_match is True
    records = list(iter_records(rebuilt / "interleaved.dbf", include_deleted=True))
    assert [(r.values["K"], r.deleted) for r in records] == [(1, False), (2, True), (3, False)]


# ---------------------------------------------------------------------------
# configured text policy: encodings through the parser instance
# ---------------------------------------------------------------------------


def test_vfp_varchar_cp1250_polish_text_reads_exact_unicode(tmp_path: Path) -> None:
    """Varchar payload with real Polish characters, header driver 0xC8
    (cp1250): the configured text policy decodes the exact Unicode."""
    polish = "Żółw ąęłóń"
    source = factory.build_vfp32_table(
        tmp_path / "pl.dbf",
        columns=[{"name": "TX", "type": "V", "width": 16, "nullable": True}],
        rows=[{"TX": polish}],
        codepage=0xC8,
    )
    assert read_schema(source).encoding == "cp1250"
    explicit = next(iter(iter_records(source, encoding="cp1250")))
    assert explicit.values["TX"] == polish
    auto = next(iter(iter_records(source)))  # driver byte resolves cp1250 too
    assert auto.values["TX"] == polish


def test_vfp_varchar_cp852_encoding_reads_exact_unicode(tmp_path: Path) -> None:
    """Varchar with cp852 (DOS Latin-2) payload, header driver 0x23: an
    explicit ``encoding="cp852"`` override decodes the exact Unicode."""
    text = "zażółć gęślą jaźń"
    source = factory.build_vfp32_table(
        tmp_path / "cp852.dbf",
        columns=[{"name": "TX", "type": "V", "width": 20, "nullable": True}],
        rows=[{"TX": text}],
        codepage=0x23,
    )
    assert read_schema(source).encoding == "cp852"
    record = next(iter(iter_records(source, encoding="cp852")))
    assert record.values["TX"] == text


def test_vfp_varchar_mazovia_encoding_reads_exact_unicode(tmp_path: Path) -> None:
    """Varchar with historical Mazovia payload, header driver 0x69 (the
    Mazovia language driver): both the honest auto path and the explicit
    override decode the exact Unicode."""
    text = "łódź żółw"
    source = factory.build_vfp32_table(
        tmp_path / "maz.dbf",
        columns=[{"name": "TX", "type": "V", "width": 12, "nullable": True}],
        rows=[{"TX": text}],
        codepage=0x69,
    )
    assert read_schema(source).encoding == "mazovia"
    explicit = next(iter(iter_records(source, encoding="mazovia")))
    assert explicit.values["TX"] == text
    auto = next(iter(iter_records(source)))
    assert auto.values["TX"] == text


def test_vfp_varchar_undecodable_bytes_raise_typed_text_error(tmp_path: Path) -> None:
    """Undecodable logical Varchar bytes raise the typed
    ``TEXT_DECODE_ERROR`` with a JSON-safe field/record/encoding context —
    never a raw ``UnicodeDecodeError`` on the public boundary."""
    source = _varchar_table(tmp_path, [{"TX": "abc"}])
    data = bytearray(source.read_bytes())
    header_length, _record_length, _count = factory.dbf_layout(source)
    data[header_length + 1] = 0x88  # undefined in cp1250
    source.write_bytes(bytes(data))

    from dbfbridge import TextDecodeError

    with pytest.raises(TextDecodeError) as error:
        next(iter_records(source, encoding="cp1250"))
    payload = error.value.to_dict()
    json.dumps(payload, allow_nan=False)
    assert payload["context"]["field"] == "TX"
    assert payload["context"]["record_index"] == 0
    assert payload["context"]["encoding"] == "cp1250"


# ---------------------------------------------------------------------------
# configured loss-aware migration policy (the decisive parser-policy case)
# ---------------------------------------------------------------------------


def test_vfp_varchar_migration_fallback_keeps_raw_bytes(tmp_path: Path) -> None:
    """THE parser-policy regression: cp852 bytes in a cp1250-declared table
    distinguish the configured loss-aware export parser from a plain
    ``bytes.decode(primary)``.

    - Direct Read with the default strict policy: typed ``TEXT_DECODE_ERROR``
      (core stays policy-neutral);
    - an explicit ``encoding="cp852"`` read decodes the exact value;
    - migration applies the configured export parser policy: the Polish
      fallback produces the exact logical Unicode AND retains the original
      raw bytes, which surface in JSONL under
      ``__dbfbridge_raw_text_fields__``.
    """
    from dbf_bridge.exporter.serialization import RAW_TEXT_FIELDS_KEY
    from dbfbridge import TextDecodeError

    polish = "łódź"
    cp852_bytes = polish.encode("cp852")
    with pytest.raises(UnicodeDecodeError):
        cp852_bytes.decode("cp1250")  # sanity: the payload NEEDS a fallback

    source = factory.build_vfp32_table(
        tmp_path / "fallback.dbf",
        columns=[{"name": "TX", "type": "V", "width": 10, "nullable": True}],
        rows=[{"TX": cp852_bytes}],  # raw cp852 bytes, header driver 0xC8
    )

    with pytest.raises(TextDecodeError):
        next(iter_records(source))  # default strict policy stays honest

    assert next(iter(iter_records(source, encoding="cp852"))).values["TX"] == polish

    export_dir = tmp_path / "export"
    result = export_dbf(source, export_dir, formats=("jsonl",), overwrite=True)
    result.raise_for_errors()
    entry = json.loads((export_dir / "fallback.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert entry["TX"] == polish  # exact logical Unicode via the fallback policy
    assert base64.b64decode(entry[RAW_TEXT_FIELDS_KEY]["TX"]) == cp852_bytes


# ---------------------------------------------------------------------------
# raw forensic + projection contracts on the authentic Varchar fixture
# ---------------------------------------------------------------------------


def test_vfp_varchar_raw_forensic_stream_is_parser_free(tmp_path: Path) -> None:
    """``iter_raw_records`` never decodes: on a Varchar fixture whose text
    bytes are undecodable, the forensic stream still yields the exact
    physical record with an empty values mapping and no file residue."""
    source = _varchar_table(tmp_path, [{"TX": "abc"}])
    data = bytearray(source.read_bytes())
    header_length, record_length, _count = factory.dbf_layout(source)
    data[header_length + 1] = 0x88  # undecodable in the declared cp1250
    source.write_bytes(bytes(data))

    fingerprint_before = factory.directory_fingerprint(tmp_path)
    raw = next(iter(iter_raw_records(source)))
    assert dict(raw.values) == {}
    assert raw.raw_record == data[header_length : header_length + record_length]
    assert factory.directory_fingerprint(tmp_path) == fingerprint_before


def test_vfp_varchar_projection_does_not_decode_other_columns(tmp_path: Path) -> None:
    """Selecting another column never decodes the Varchar: unselected fields
    stay unparsed, while the bitmap bytes of the already-read physical frame
    remain available for the selected NULL semantics."""
    source = factory.build_vfp32_table(
        tmp_path / "proj.dbf",
        columns=[
            {"name": "ID", "type": "N", "width": 4, "nullable": True},
            {"name": "TX", "type": "V", "width": 12, "nullable": True},
        ],
        rows=[{"ID": 7, "TX": "abc  "}],
    )
    projected = list(iter_records(source, fields=["ID"]))
    assert [record.values["ID"] for record in projected] == [7]
    assert "TX" not in projected[0].values
    assert "_NullFlags" not in projected[0].values  # projection stays selective
    # The hidden bitmap is a real system column: explicitly selectable, and
    # its raw bytes are always available inside the already-read frame for
    # the selected NULL/varlength semantics.
    explicit = list(iter_records(source, fields=["_NullFlags"]))
    assert isinstance(explicit[0].values["_NullFlags"], (bytes, bytearray))


# ---------------------------------------------------------------------------
# _NullFlags structural hardening
# ---------------------------------------------------------------------------


class _StubField:
    def __init__(self, name: str, dbf_type: str, flags: int, length: int = 1) -> None:
        self.name = name
        self.dbf_type = dbf_type
        self.flags = flags
        self.length = length


def test_nullflags_duplicate_bitmap_column_is_typed_invalid() -> None:
    """Two type-0 bitmap columns cannot be trusted as THE control structure:
    typed ``DBF_HEADER_INVALID``."""
    fields = [
        _StubField("TX", "V", 0x02),
        _StubField("_NullFlags", "0", 0x05),
        _StubField("_NullFlags2", "0", 0x05),
    ]
    with pytest.raises(DbfHeaderInvalidError):
        nullflags.build_nullflags_layout(fields)


def test_nullflags_non_system_bitmap_column_is_typed_invalid() -> None:
    """A type-0 column without the VFP system flag (0x01) is not a
    trustworthy ``_NullFlags`` structure: typed ``DBF_HEADER_INVALID``."""
    fields = [
        _StubField("TX", "V", 0x02),
        _StubField("FLAGS", "0", 0x04),  # binary but not SYSTEM
    ]
    with pytest.raises(DbfHeaderInvalidError):
        nullflags.build_nullflags_layout(fields)


def test_nullflags_accepts_one_shot_iterable() -> None:
    """The layout builder materializes its input exactly once: a one-shot
    generator produces the identical layout as the equivalent list."""
    as_list = [
        _StubField("V1", "V", 0x02),
        _StubField("C1", "C", 0x02),
        _StubField("_NullFlags", "0", 0x05),
    ]
    from_list = nullflags.build_nullflags_layout(as_list)
    from_generator = nullflags.build_nullflags_layout(
        _StubField(field.name, field.dbf_type, field.flags) for field in as_list
    )
    assert from_list is not None and from_generator is not None
    assert from_generator.field_name == from_list.field_name == "_NullFlags"
    assert from_generator.varlength_bits == from_list.varlength_bits == {"V1": 0}
    assert from_generator.null_bits == from_list.null_bits == {"V1": 1, "C1": 2}


def test_nullflags_accepts_attribute_and_mapping_descriptors() -> None:
    """One allocation engine for every descriptor shape: attribute-based
    (ParsedField/FieldMetadata) and Mapping-based (importer schema dicts)
    produce the identical layout — no parallel bit model may exist."""
    attributes = [
        _StubField("V1", "V", 0x02),
        _StubField("C1", "C", 0x02),
        _StubField("V2", "V", 0x02),
        _StubField("_NullFlags", "0", 0x05),
    ]
    mappings = [
        {"name": "V1", "dbf_type": "V", "flags": 0x02, "length": 8},
        {"name": "C1", "dbf_type": "C", "flags": 0x02, "length": 4},
        {"name": "V2", "dbf_type": "V", "flags": 0x02, "length": 8},
        {"name": "_NullFlags", "dbf_type": "0", "flags": 0x05, "length": 1},
    ]
    from_attributes = nullflags.build_nullflags_layout(attributes)
    from_mappings = nullflags.build_nullflags_layout(mappings)
    assert from_attributes is not None and from_mappings is not None
    assert from_mappings.varlength_bits == from_attributes.varlength_bits == {
        "V1": 0,
        "V2": 3,
    }
    assert from_mappings.null_bits == from_attributes.null_bits == {
        "V1": 1,
        "C1": 2,
        "V2": 4,
    }


def test_nullflags_single_system_bitmap_is_accepted() -> None:
    """Happy path: exactly one system-flagged bitmap column."""
    layout = nullflags.build_nullflags_layout(
        [
            _StubField("TX", "V", 0x02),
            _StubField("_NullFlags", "0", 0x05),
        ]
    )
    assert layout is not None
    assert layout.varlength_bits == {"TX": 0}
    assert layout.null_bits == {"TX": 1}

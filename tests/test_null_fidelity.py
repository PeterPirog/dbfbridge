"""dbfbridge v1.1.1 NULL / empty-string fidelity contract (DBFB-NULLTEST-001..016).

Normative matrix of ``DBFBRIDGE_TARGET_ARCHITECTURE_v1.1.1.md`` — contract
hardening only: it locks the already-proven behavior
(Phase A conclusion: ``CURRENT_MAIN_NULL_FIDELITY_ALREADY_CORRECT``) without
touching production code.  The key invariant is
``Read(Write(Read(D))) ≡ Read(D)`` with NULL / EMPTY / VALUE treated as
distinct logical states:

    NULL          -> None
    empty string  -> ""
    value         -> unchanged canonical value

Fixtures are implementation-independent physical oracles: ordinary nullable
``C``/``N``/``I`` fields come from the reference ``dbf`` writer (the same
independent path used by the existing suite), Varchar comes from the
authentic low-level 0x32 builder ``build_vfp32_table`` (real header,
descriptors, ``_NullFlags`` bitmap and length-byte payloads — never a
``write_table`` round trip), and the spool is exercised directly.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import dbf
import pytest
import vfp_fixture_factory as factory

from dbf_bridge.core import nullflags
from dbf_bridge.write.spool import RecordSpool
from dbfbridge import (
    DirectRecord,
    ErrorCode,
    TextDecodeError,
    inspect_table,
    iter_records,
    read_records,
    read_schema,
    write_table,
)

_C_TRI_STATE_ROWS: list[dict[str, Any]] = [
    {"TXT": dbf.Null},
    {"TXT": ""},
    {"TXT": "ABC"},
]


def _bitmap_key(values: Mapping[str, Any]) -> str:
    """The hidden system bitmap column key, whatever casing the writer used."""
    return next(key for key in values if "nullflags" in key.casefold())


def _raw_record(source: Path, index: int) -> bytes:
    """One physical record frame (delete marker + field bytes)."""
    header_length, record_length, _count = factory.dbf_layout(source)
    data = source.read_bytes()
    start = header_length + index * record_length
    return data[start : start + record_length]


def _field_bytes(record: bytes, offset: int, width: int) -> bytes:
    return record[offset : offset + width]


def _null_bit_state(bitmap: bytes | bytearray, bit: int) -> bool:
    return bool((bitmap[bit // 8] >> (bit % 8)) & 1)


def _write_fresh_stream(source: Path, destination: Path) -> None:
    """Public Read -> Write -> Read with a ONE-SHOT fresh record iterator."""
    schema = read_schema(source)
    write_table(
        destination,
        schema=schema,
        records=iter_records(source, memo="inline", include_deleted=True),
    )


def _logical_states(records: list[DirectRecord], fields: list[str]) -> list[dict[str, Any]]:
    return [{field: record.values.get(field) for field in fields} for record in records]


# ---------------------------------------------------------------------------
# DBFB-NULLTEST-001 / DBFB-NULL-C-001..004: nullable C tri-state
# ---------------------------------------------------------------------------


def test_nullable_c_tri_state_reads_none_empty_value(tmp_path: Path) -> None:
    """One nullable C fixture holds NULL, empty and value as three distinct
    logical states (reference-writer fixture; the NULL bit decides, never the
    blank payload)."""
    source = factory.create_vfp_table(tmp_path / "c_tri.dbf", "TXT C(10) NULL", _C_TRI_STATE_ROWS)

    # Physical control state: record 0 NULL bit SET, records 1/2 NULL bit CLEAR.
    bitmap = _field_bytes(_raw_record(source, 0), 11, 1)
    assert _null_bit_state(bitmap, 0) is True  # canonical allocation: bit 0
    assert _field_bytes(_raw_record(source, 0), 1, 10) == b" " * 10
    for index in (1, 2):
        bitmap = _field_bytes(_raw_record(source, index), 11, 1)
        assert _null_bit_state(bitmap, 0) is False
    assert _field_bytes(_raw_record(source, 2), 1, 10) == b"ABC" + b" " * 7

    rows = list(iter_records(source))
    assert rows[0].values["TXT"] is None
    assert rows[1].values["TXT"] == ""
    assert rows[2].values["TXT"] == "ABC"
    assert [type(row.values["TXT"]).__name__ for row in rows] == ["NoneType", "str", "str"]


def test_nullable_c_tri_state_projection_and_pagination(tmp_path: Path) -> None:
    """Selected-field projection and offset/limit pagination cross the
    NULL -> empty -> value boundary without state changes (NULLTEST-007/010)."""
    source = factory.create_vfp_table(tmp_path / "c_tri.dbf", "TXT C(10) NULL", _C_TRI_STATE_ROWS)

    projected = list(iter_records(source, fields=["TXT"]))
    assert [row.values["TXT"] for row in projected] == [None, "", "ABC"]
    assert list(projected[0].values.keys()) == ["TXT"]  # bitmap stays hidden

    pages = []
    for call in ({"offset": 0, "limit": 1}, {"offset": 1, "limit": 1}, {"offset": 2, "limit": 1},
                 {"offset": 0, "limit": 2}, {"offset": 1, "limit": 2}):
        page = read_records(source, fields=["TXT"], **call)
        pages.append(page)
    assert [row.values["TXT"] for page in pages for row in page.records] == [
        None, "", "ABC", None, "", "", "ABC",
    ]
    assert [(page.next_offset, page.exhausted) for page in pages] == [
        (1, False), (2, False), (None, True), (2, False), (None, True),
    ]

    full = read_records(source, fields=["TXT"])
    assert full.exhausted is True
    assert [row.values["TXT"] for row in full.records] == [None, "", "ABC"]


def test_nullable_c_tri_state_public_round_trip(tmp_path: Path) -> None:
    """Read(Write(Read(D))) ≡ Read(D) for the C tri-state: None, "" and "ABC"
    stay three distinct states through the public write_table stream."""
    source = factory.create_vfp_table(tmp_path / "c_tri.dbf", "TXT C(10) NULL", _C_TRI_STATE_ROWS)
    destination = tmp_path / "c_tri_copy.dbf"
    _write_fresh_stream(source, destination)

    after = list(iter_records(destination, memo="inline", include_deleted=True))
    assert [row.values["TXT"] for row in after] == [None, "", "ABC"]
    assert [row.deleted for row in after] == [False, False, False]
    assert [row.physical_index for row in after] == [0, 1, 2]
    # The rewritten NULL bit follows the logical values exactly.
    assert _null_bit_state(_field_bytes(_raw_record(destination, 0), 11, 1), 0) is True
    assert _null_bit_state(_field_bytes(_raw_record(destination, 1), 11, 1), 0) is False
    assert _null_bit_state(_field_bytes(_raw_record(destination, 2), 11, 1), 0) is False

    page = read_records(destination, fields=["TXT"])
    assert [row.values["TXT"] for row in page.records] == [None, "", "ABC"]
    assert page.exhausted is True


# ---------------------------------------------------------------------------
# DBFB-NULLTEST-002 / DBFB-NULL-V-001..005 / DBFB-FIXTURE-003: nullable V
# ---------------------------------------------------------------------------


def _v_quartet(tmp_path: Path) -> Path:
    return factory.build_vfp32_table(
        tmp_path / "v_quartet.dbf",
        columns=[{"name": "TXT", "type": "V", "width": 10, "nullable": True}],
        rows=[{"TXT": None}, {"TXT": ""}, {"TXT": "A"}, {"TXT": "ABCDEFGHIJ"}],
    )


def test_nullable_v_quartet_reads_exact_states(tmp_path: Path) -> None:
    """NULL, empty, short and max-width Varchar are four distinct logical
    states in Direct Read."""
    source = _v_quartet(tmp_path)
    rows = list(iter_records(source))
    assert [row.values["TXT"] for row in rows] == [None, "", "A", "ABCDEFGHIJ"]
    assert [type(row.values["TXT"]).__name__ for row in rows] == [
        "NoneType", "str", "str", "str",
    ]


def test_nullable_v_quartet_raw_bitmap_and_length_bytes(tmp_path: Path) -> None:
    """Physical proof per the documented VFP contract (bit 0 varlength,
    bit 1 NULL): the NULL row keeps the bit set, empty carries length byte 0,
    "A" length byte 1, and max-width uses the full-width no-length form."""
    source = _v_quartet(tmp_path)
    # bits: 0 = varlength, 1 = NULL
    expected = [
        b" " + b" " * 10 + b"\x02",  # NULL: blank payload, NULL bit set
        b" " + b" " * 9 + b"\x00" + b"\x01",  # empty: varlength, length byte 0
        b" " + b"A" + b" " * 8 + b"\x01" + b"\x01",  # "A": varlength, length byte 1
        b" " + b"ABCDEFGHIJ" + b"\x00",  # max-width: full-width form
    ]
    for index, want in enumerate(expected):
        assert _raw_record(source, index) == want

    rows = list(iter_records(source))
    assert [row.values["TXT"] for row in rows] == [None, "", "A", "ABCDEFGHIJ"]
    # The full-width value is never mistaken for NULL.
    assert _null_bit_state(_field_bytes(_raw_record(source, 3), 11, 1), 1) is False


def test_nullable_v_quartet_projection_pagination_round_trip(tmp_path: Path) -> None:
    """Projection, paging and the public round trip keep all four V states
    distinct (NULLTEST-002/007/009/010/011)."""
    source = _v_quartet(tmp_path)
    destination = tmp_path / "v_quartet_copy.dbf"

    projected = list(iter_records(source, fields=["TXT"]))
    assert [row.values["TXT"] for row in projected] == [None, "", "A", "ABCDEFGHIJ"]

    first = read_records(source, offset=0, limit=2, fields=["TXT"])
    second = read_records(source, offset=2, limit=2, fields=["TXT"])
    assert [row.values["TXT"] for row in first.records] == [None, ""]
    assert (first.next_offset, first.exhausted) == (2, False)
    assert [row.values["TXT"] for row in second.records] == ["A", "ABCDEFGHIJ"]
    assert (second.next_offset, second.exhausted) == (None, True)

    _write_fresh_stream(source, destination)
    after = list(iter_records(destination, memo="inline", include_deleted=True))
    assert [row.values["TXT"] for row in after] == [None, "", "A", "ABCDEFGHIJ"]
    assert [row.physical_index for row in after] == [0, 1, 2, 3]
    # The canonical output layout: NULL blank, empty length byte 0, "A"
    # length byte 1, max-width full-width.
    assert _raw_record(destination, 0)[1:11] == b" " * 10
    assert _field_bytes(_raw_record(destination, 1), 1, 10) == b" " * 9 + b"\x00"
    assert _field_bytes(_raw_record(destination, 2), 1, 10) == b"A" + b" " * 8 + b"\x01"
    assert _field_bytes(_raw_record(destination, 3), 1, 10) == b"ABCDEFGHIJ"

    page = read_records(destination, fields=["TXT"])
    assert [row.values["TXT"] for row in page.records] == [None, "", "A", "ABCDEFGHIJ"]
    assert page.exhausted is True


# ---------------------------------------------------------------------------
# DBFB-NULLTEST-003 / DBFB-NULL-NUM-001..002: numeric None/zero/non-zero
# ---------------------------------------------------------------------------


def _numeric_fixture(tmp_path: Path) -> Path:
    return factory.create_vfp_table(
        tmp_path / "numeric.dbf",
        "ID I NULL; AMT N(10,2) NULL",
        [
            {"ID": dbf.Null, "AMT": dbf.Null},
            {"ID": 0, "AMT": 0},
            {"ID": 7, "AMT": 12.5},
        ],
    )


def test_nullable_numeric_zero_stays_numeric_zero(tmp_path: Path) -> None:
    """Integer-like and decimal nullable fields keep None, zero and non-zero
    distinct; zero storage is never inferred as NULL."""
    source = _numeric_fixture(tmp_path)
    # Physical: NULL record has bits 0+1 set (ID/AMT), zero/value records clear.
    for index, bit0, bit1 in ((0, True, True), (1, False, False), (2, False, False)):
        bitmap = _field_bytes(_raw_record(source, index), 15, 1)
        assert _null_bit_state(bitmap, 0) is bit0
        assert _null_bit_state(bitmap, 1) is bit1

    rows = list(iter_records(source))
    assert [row.values["ID"] for row in rows] == [None, 0, 7]
    assert [type(row.values["ID"]).__name__ for row in rows] == ["NoneType", "int", "int"]
    assert [row.values["AMT"] for row in rows] == [None, 0.0, 12.5]
    assert [type(row.values["AMT"]).__name__ for row in rows] == ["NoneType", "float", "float"]


def test_nullable_numeric_public_round_trip(tmp_path: Path) -> None:
    """None vs zero vs non-zero survive Read -> Write -> Read with types."""
    source = _numeric_fixture(tmp_path)
    destination = tmp_path / "numeric_copy.dbf"
    _write_fresh_stream(source, destination)

    after = list(iter_records(destination, memo="inline", include_deleted=True))
    assert [row.values["ID"] for row in after] == [None, 0, 7]
    assert [row.values["AMT"] for row in after] == [None, 0.0, 12.5]
    assert [type(row.values["ID"]).__name__ for row in after] == ["NoneType", "int", "int"]
    page = read_records(destination, fields=["ID", "AMT"])
    assert [row.values["ID"] for row in page.records] == [None, 0, 7]
    assert [row.values["AMT"] for row in page.records] == [None, 0.0, 12.5]


# ---------------------------------------------------------------------------
# DBFB-NULLTEST-004/006 / DBFB-NULLLAYOUT-001..004: mixed shared bitmap
# ---------------------------------------------------------------------------


def _mixed_fixture(tmp_path: Path) -> Path:
    return factory.build_vfp32_table(
        tmp_path / "mixed.dbf",
        columns=[
            {"name": "V1", "type": "V", "width": 8, "nullable": True},
            {"name": "C1", "type": "C", "width": 4, "nullable": True},
            {"name": "N1", "type": "N", "width": 6, "nullable": True},
            {"name": "V2", "type": "V", "width": 8, "nullable": True},
        ],
        rows=[
            {"V1": None, "C1": "", "N1": 0, "V2": ""},
            {"V1": "", "C1": None, "N1": None, "V2": "A"},
            {"V1": "A", "C1": "ABC", "N1": 7, "V2": "12345678"},
        ],
    )


def test_mixed_bitmap_descriptor_order_allocation(tmp_path: Path) -> None:
    """One shared ``_NullFlags`` keeps every column's state independent:
    V1 varlength(0), V1 NULL(1), C1 NULL(2), N1 NULL(3), V2 varlength(4),
    V2 NULL(5) — proven by the exact raw bitmap bytes and decoded states."""
    source = _mixed_fixture(tmp_path)

    layout = nullflags.build_nullflags_layout(inspect_table(source).fields)
    assert layout is not None
    assert layout.byte_count == 1
    assert layout.varlength_bits == {"V1": 0, "V2": 4}
    assert layout.null_bits == {"V1": 1, "C1": 2, "N1": 3, "V2": 5}

    # Raw bitmap bytes: 0x12 / 0x1d / 0x01 (varlength + NULL bits only).
    bitmaps = [_field_bytes(_raw_record(source, index), 27, 1) for index in range(3)]
    assert bitmaps == [b"\x12", b"\x1d", b"\x01"]

    rows = list(iter_records(source))
    assert _logical_states(rows, ["V1", "C1", "N1", "V2"]) == [
        {"V1": None, "C1": "", "N1": 0, "V2": ""},
        {"V1": "", "C1": None, "N1": None, "V2": "A"},
        {"V1": "A", "C1": "ABC", "N1": 7, "V2": "12345678"},
    ]


def test_mixed_bitmap_public_round_trip(tmp_path: Path) -> None:
    """The mixed V+C+numeric+V table round-trips with every independent
    state preserved (DBFB-NULLRT-003/005)."""
    source = _mixed_fixture(tmp_path)
    destination = tmp_path / "mixed_copy.dbf"
    _write_fresh_stream(source, destination)

    after = list(iter_records(destination, memo="inline", include_deleted=True))
    assert _logical_states(after, ["V1", "C1", "N1", "V2"]) == [
        {"V1": None, "C1": "", "N1": 0, "V2": ""},
        {"V1": "", "C1": None, "N1": None, "V2": "A"},
        {"V1": "A", "C1": "ABC", "N1": 7, "V2": "12345678"},
    ]
    page = read_records(destination, fields=["V1", "C1", "N1", "V2"])
    assert _logical_states(list(page.records), ["V1", "C1", "N1", "V2"]) == [
        {"V1": None, "C1": "", "N1": 0, "V2": ""},
        {"V1": "", "C1": None, "N1": None, "V2": "A"},
        {"V1": "A", "C1": "ABC", "N1": 7, "V2": "12345678"},
    ]


# ---------------------------------------------------------------------------
# DBFB-NULLTEST-005 / DBFB-NULLLAYOUT-005: cross-byte _NullFlags bitmap
# ---------------------------------------------------------------------------


def _crossbyte_fixture(tmp_path: Path) -> Path:
    columns = [
        {"name": f"C{i}", "type": "C", "width": 4, "nullable": True} for i in range(1, 9)
    ] + [{"name": "V9", "type": "V", "width": 6, "nullable": True}]
    return factory.build_vfp32_table(
        tmp_path / "crossbyte.dbf",
        columns=columns,
        rows=[
            {"C1": "", "C2": "", "C3": "", "C4": None, "C5": "", "C6": "", "C7": "", "C8": "",
             "V9": None},
            {"C1": "ab", "C2": "", "C3": "", "C4": "x", "C5": "", "C6": "", "C7": "", "C8": "",
             "V9": "ab"},
            {"C1": "ab", "C2": "cd", "C3": "ef", "C4": "gh", "C5": "ij", "C6": "kl", "C7": "mn",
             "C8": "op", "V9": "long"},
        ],
    )


def test_cross_byte_nullflags_bitmap_direct_read(tmp_path: Path) -> None:
    """10 allocated varlength/NULL bits span TWO bitmap bytes: byte 0 offset 3
    (C4 NULL) and byte 1 offsets 0/1 (V9 varlength/NULL) are both exercised
    through the real Direct Read path."""
    source = _crossbyte_fixture(tmp_path)

    info = inspect_table(source)
    bitmap_field = next(field for field in info.fields if field.dbf_type == "0")
    assert bitmap_field.length == 2

    layout = nullflags.build_nullflags_layout(info.fields)
    assert layout is not None
    assert layout.byte_count == 2
    assert layout.varlength_bits == {"V9": 8}
    assert layout.null_bits["C4"] == 3
    assert layout.null_bits["V9"] == 9

    # Raw bitmap bytes: byte 0 holds the C4 NULL bit, byte 1 holds V9 bits.
    raws = [_raw_record(source, index) for index in range(3)]
    assert _field_bytes(raws[0], 39, 2) == b"\x08\x02"
    assert _field_bytes(raws[1], 39, 2) == b"\x00\x01"
    assert _field_bytes(raws[2], 39, 2) == b"\x00\x01"

    rows = list(iter_records(source))
    assert [row.values["C4"] for row in rows] == [None, "x", "gh"]
    assert [row.values["V9"] for row in rows] == [None, "ab", "long"]
    assert [row.values["C1"] for row in rows] == ["", "ab", "ab"]
    assert [row.values["C8"] for row in rows] == ["", "", "op"]


def test_cross_byte_bitmap_public_round_trip(tmp_path: Path) -> None:
    """The two-byte bitmap round-trips with every state intact."""
    source = _crossbyte_fixture(tmp_path)
    destination = tmp_path / "crossbyte_copy.dbf"
    _write_fresh_stream(source, destination)

    after = list(iter_records(destination, memo="inline", include_deleted=True))
    assert _logical_states(after, ["C1", "C4", "C8", "V9"]) == [
        {"C1": "", "C4": None, "C8": "", "V9": None},
        {"C1": "ab", "C4": "x", "C8": "", "V9": "ab"},
        {"C1": "ab", "C4": "gh", "C8": "op", "V9": "long"},
    ]


# ---------------------------------------------------------------------------
# DBFB-NULLTEST-007/008 / DBFB-NULLREAD-002..005: projection contract
# ---------------------------------------------------------------------------


def test_projection_preserves_null_and_empty_for_selected_fields(tmp_path: Path) -> None:
    """``fields=["TXT"]`` keeps None/""/value/max-width exactly on selected
    nullable C and V columns while unselected columns stay out of values."""
    c_source = factory.create_vfp_table(tmp_path / "c_tri.dbf", "TXT C(10) NULL", _C_TRI_STATE_ROWS)
    v_source = _v_quartet(tmp_path)

    c_rows = list(iter_records(c_source, fields=["TXT"]))
    assert [row.values["TXT"] for row in c_rows] == [None, "", "ABC"]
    assert list(c_rows[0].values.keys()) == ["TXT"]

    v_rows = list(iter_records(v_source, fields=["TXT"]))
    assert [row.values["TXT"] for row in v_rows] == [None, "", "A", "ABCDEFGHIJ"]
    assert list(v_rows[0].values.keys()) == ["TXT"]


def test_unselected_undecodable_field_is_not_decoded_for_projection(tmp_path: Path) -> None:
    """An intentionally undecodable UNSELECTED text field is never decoded
    merely to resolve another field's NULL state; selecting it fails typed."""
    source = factory.build_vfp32_table(
        tmp_path / "undecodable.dbf",
        columns=[
            {"name": "CBAD", "type": "C", "width": 4, "nullable": False},
            {"name": "TXT", "type": "C", "width": 6, "nullable": True},
        ],
        rows=[
            {"CBAD": "zzzz", "TXT": None},
            {"CBAD": "zzzz", "TXT": ""},
            {"CBAD": "zzzz", "TXT": "ABC"},
        ],
    )
    header_length, record_length, _count = factory.dbf_layout(source)
    data = bytearray(source.read_bytes())
    for index in range(3):
        start = header_length + index * record_length
        data[start + 1 : start + 5] = b"\x81\x83\x88\x90"  # undefined in cp1250
    source.write_bytes(bytes(data))

    projected = list(iter_records(source, fields=["TXT"]))
    assert [row.values["TXT"] for row in projected] == [None, "", "ABC"]

    with pytest.raises(TextDecodeError) as error:
        list(iter_records(source))
    assert error.value.code is ErrorCode.TEXT_DECODE_ERROR
    assert error.value.to_dict()["context"]["field"] == "CBAD"


# ---------------------------------------------------------------------------
# DBFB-NULLWRITE-001..008 / DBFB-NULLLAYOUT-008: writer-managed bitmap
# ---------------------------------------------------------------------------


def test_writer_managed_bitmap_wins_over_transported_bytes(tmp_path: Path) -> None:
    """Callers never compute ``_NullFlags``: Direct Read values carry the
    system column, the writer drops transported bytes, and deliberately
    corrupted transported bitmaps cannot override logical None/""/value."""
    source = factory.create_vfp_table(tmp_path / "c_tri.dbf", "TXT C(10) NULL", _C_TRI_STATE_ROWS)
    schema = read_schema(source)
    bitmap_name = next(field.name for field in schema.fields if field.dbf_type == "0")

    # Direct Read records carry the transported system bytes; the writer
    # re-derives the canonical bitmap from the logical values.
    transported = list(iter_records(source))
    assert _bitmap_key(transported[0].values) == bitmap_name
    transported_destination = tmp_path / "transported.dbf"
    write_table(transported_destination, schema=schema, records=iter(transported), overwrite=True)
    assert [row.values["TXT"] for row in iter_records(transported_destination)] == [
        None, "", "ABC",
    ]

    # Corrupted transported bitmaps: logical values win in both directions.
    corrupted = [
        DirectRecord(physical_index=0, deleted=False, values={"TXT": "ABC", bitmap_name: b"\x01"}),
        DirectRecord(physical_index=1, deleted=False, values={"TXT": None, bitmap_name: b"\x00"}),
    ]
    corrupted_destination = tmp_path / "corrupted.dbf"
    write_table(corrupted_destination, schema=schema, records=iter(corrupted), overwrite=True)
    assert _null_bit_state(_field_bytes(_raw_record(corrupted_destination, 0), 11, 1), 0) is False
    assert _null_bit_state(_field_bytes(_raw_record(corrupted_destination, 1), 11, 1), 0) is True
    assert [
        row.values["TXT"] for row in iter_records(corrupted_destination)
    ] == ["ABC", None]


def test_writer_managed_bitmap_for_empty_varchar_mapping(tmp_path: Path) -> None:
    """An empty Varchar mapping with a corrupted bitmap stays non-NULL: the
    output uses the canonical empty Varchar (NULL clear, length byte 0)."""
    source = _v_quartet(tmp_path)
    schema = read_schema(source)
    bitmap_name = next(field.name for field in schema.fields if field.dbf_type == "0")
    destination = tmp_path / "v_empty.dbf"
    write_table(
        destination,
        schema=schema,
        records=iter([{"TXT": "", bitmap_name: b"\x02"}]),
        overwrite=True,
    )
    payload = _field_bytes(_raw_record(destination, 0), 1, 10)
    assert payload == b" " * 9 + b"\x00"
    bitmap = _field_bytes(_raw_record(destination, 0), 11, 1)
    assert _null_bit_state(bitmap, 1) is False  # NULL bit stays clear
    assert _null_bit_state(bitmap, 0) is True  # varlength form of the empty value
    assert [row.values["TXT"] for row in iter_records(destination)] == [""]


# ---------------------------------------------------------------------------
# DBFB-NULLWRITE-009 / DBFB-NULLPERF-003: private spool None vs empty
# ---------------------------------------------------------------------------


_SPOOL_RECORDS: list[dict[str, Any]] = [
    {"TXT": None, "__deleted__": False},
    {"TXT": "", "__deleted__": False},
    {"TXT": "ABC", "__deleted__": False},
]


def test_record_spool_preserves_none_vs_empty_in_memory(tmp_path: Path) -> None:
    """The in-memory spool path replays None, "" and values exactly, in
    order, and discard removes the private spool residue."""
    spool_path = tmp_path / "spool_memory.bin"
    spool = RecordSpool(spool_path, memory_threshold=10)
    for record in _SPOOL_RECORDS:
        spool.append(dict(record))
    assert spool.spilled is False
    assert spool.count == 3

    replayed = list(spool.replay())
    assert [record["TXT"] for record in replayed] == [None, "", "ABC"]
    assert replayed[0]["TXT"] is None
    assert replayed[1]["TXT"] == ""

    spool.discard()
    assert not spool_path.exists()


def test_record_spool_preserves_none_vs_empty_after_disk_spill(tmp_path: Path) -> None:
    """The forced disk-spill path preserves the same exact states, and the
    spool file is deleted by discard."""
    spool_path = tmp_path / "spool_spilled.bin"
    spool = RecordSpool(spool_path, memory_threshold=1)
    for record in _SPOOL_RECORDS:
        spool.append(dict(record))
    assert spool.spilled is True
    assert spool.count == 3

    replayed = list(spool.replay())
    assert [record["TXT"] for record in replayed] == [None, "", "ABC"]
    assert replayed[0]["TXT"] is None
    assert replayed[1]["TXT"] == ""

    spool.discard()
    assert not spool_path.exists()

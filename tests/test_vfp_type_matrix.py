"""VFP physical type correctness matrix (evidence-driven).

Every test documents its fixture construction (legal ``dbf`` generation or
documented same-layout byte patching — see ``tests/vfp_fixture_factory.py``)
and asserts exactly the public contract the code provides.  Types whose
physical layout cannot be produced honestly are recorded as
``NOT_YET_VERIFIED`` in ``docs/compatibility-vfp.md`` instead of being faked
here.

Covered contracts per type:

- inspect/schema visibility and classification;
- Direct Read decoded values;
- migration/export JSONL semantics;
- reconstruction round trip (canonical and, where the contract provides raw
  metadata, byte-identical DBF/FPT);
- raw forensic readability for unsupported physical data.
"""

from __future__ import annotations

import base64
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import vfp_fixture_factory as factory

from dbf_bridge import export_dbf, reconstruct_dbf
from dbfbridge import (
    FieldTypeUnsupportedError,
    inspect_table,
    iter_raw_records,
    iter_records,
    read_schema,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _roundtrip(tmp_path: Path, source: Path) -> tuple[Any, Path]:
    """DBF/FPT → JSONL + schema → reconstruct; returns (result, rebuilt dir)."""
    export_dir = tmp_path / "export"
    export_result = export_dbf(source, export_dir, formats=("jsonl",), overwrite=True)
    export_result.raise_for_errors()
    rebuilt = tmp_path / "rebuilt"
    result = reconstruct_dbf(export_dir, rebuilt, input_format="jsonl", overwrite=True)
    result.raise_for_errors()
    return result, rebuilt


def _field(info: Any, name: str) -> Any:
    return next(field for field in info.fields if field.name == name)


# ---------------------------------------------------------------------------
# native numeric family: Y, B, I, F (+ T/D/L reference types)
# ---------------------------------------------------------------------------


def test_vfp_currency_direct_read_and_roundtrip_preserves_decimal_value(tmp_path: Path) -> None:
    """Y (Currency): 8-byte scaled integer -> Decimal with 4-digit precision,
    exported as an exact decimal string, reconstructed byte-identically."""
    source = factory.create_vfp_table(
        tmp_path / "currency.dbf",
        "KWOTA Y; K N(4,0)",
        [{"KWOTA": 1234.5678, "K": 1}, {"KWOTA": -0.0005, "K": 2}],
    )
    currency_field = _field(inspect_table(source), "KWOTA")
    assert currency_field.dbf_type == "Y"
    assert currency_field.supported is True
    assert currency_field.length == 8

    records = list(iter_records(source))
    assert records[0].values["KWOTA"] == Decimal("1234.5678")
    assert records[1].values["KWOTA"] == Decimal("-0.0005")

    result, rebuilt = _roundtrip(tmp_path, source)
    report = result.results[0]
    assert report.status == "OK"
    assert report.canonical_match is True
    assert report.raw_dbf_match is True

    rebuilt_records = list(iter_records(rebuilt / "currency.dbf"))
    assert [record.values["KWOTA"] for record in rebuilt_records] == [
        Decimal("1234.5678"),
        Decimal("-0.0005"),
    ]


def test_vfp_double_table_without_fpt_exports_and_roundtrips(tmp_path: Path) -> None:
    """A VFP B (Double) column is an inline 8-byte IEEE number, not a memo
    pointer: a table containing only a Double and no memo field at all has
    no FPT companion and must export and reconstruct without one."""
    source = factory.create_vfp_table(
        tmp_path / "double.dbf",
        "POMIAR B; K N(4,0)",
        [{"POMIAR": 2.5, "K": 1}, {"POMIAR": -0.125, "K": 2}],
    )
    assert not source.with_suffix(".fpt").exists()
    info = inspect_table(source)
    double_field = _field(info, "POMIAR")
    assert double_field.dbf_type == "B"
    assert double_field.supported is True
    assert double_field.is_memo is False  # a VFP double, not a memo pointer
    assert info.has_memo is False

    record_values = [dict(record.values) for record in iter_records(source)]
    assert record_values == [{"POMIAR": 2.5, "K": 1}, {"POMIAR": -0.125, "K": 2}]

    result, rebuilt = _roundtrip(tmp_path, source)
    report = result.results[0]
    assert report.status == "OK"
    assert report.canonical_match is True
    assert report.raw_dbf_match is True

    rebuilt_records = list(iter_records(rebuilt / "double.dbf"))
    assert [record.values["POMIAR"] for record in rebuilt_records] == [2.5, -0.125]


def test_vfp_integer_float_datetime_roundtrip(tmp_path: Path) -> None:
    """I (Integer), F (Float), T (DateTime), D (Date) survive the full round
    trip with values, schema metadata, and the physical layout intact."""
    source = factory.create_vfp_table(
        tmp_path / "natives.dbf",
        "LICZNIK I; WART F(12,3); KIEDY T; DZIEN D; LOG L; TEKST C(10)",
        [
            {
                "LICZNIK": -7,
                "WART": 3.25,
                "KIEDY": datetime(2026, 9, 3, 12, 30, 15),
                "DZIEN": date(2026, 9, 3),
                "LOG": None,
                "TEKST": "stały",
            }
        ],
    )
    result, rebuilt = _roundtrip(tmp_path, source)
    report = result.results[0]
    assert report.status == "OK"
    assert report.canonical_match is True
    assert report.raw_dbf_match is True

    original = next(iter(iter_records(source)))
    rebuilt_record = next(iter(iter_records(rebuilt / "natives.dbf")))
    assert dict(rebuilt_record.values) == dict(original.values)
    assert rebuilt_record.values["LICZNIK"] == -7
    assert rebuilt_record.values["WART"] == 3.25
    assert rebuilt_record.values["KIEDY"] == datetime(2026, 9, 3, 12, 30, 15)
    assert rebuilt_record.values["DZIEN"] == date(2026, 9, 3)


def test_vfp_double_alias_o_reads_like_a_double(tmp_path: Path) -> None:
    """O shares the exact 8-byte IEEE layout with the VFP B double, so a
    legal B fixture patched to O (documented same-layout patch) decodes as
    the same number."""
    source = factory.create_vfp_table(
        tmp_path / "odouble.dbf", "POMIAR B; K N(4,0)", [{"POMIAR": 2.5, "K": 1}]
    )
    factory.patch_field_type(source, 0, "O")  # same physical layout as B

    info = inspect_table(source)
    assert info.fields[0].dbf_type == "O"
    assert info.fields[0].supported is True

    record = next(iter(iter_records(source)))
    assert record.values["POMIAR"] == 2.5

    result, _rebuilt = _roundtrip(tmp_path, source)
    assert result.results[0].canonical_match is True
    assert result.results[0].raw_dbf_match is True


def test_vfp_varchar_direct_read_and_roundtrip(tmp_path: Path) -> None:
    """V (Varchar) is physically a character field; the table carries the
    VFP 0x32 'Varchar enabled' version byte.  Fixture: legal C table patched
    to V (same layout) with the documented version change."""
    source = factory.create_vfp_table(
        tmp_path / "varchar.dbf", "TX C(10); K N(4,0)", [{"TX": "hello", "K": 1}]
    )
    factory.patch_field_type(source, 0, "V")
    factory.patch_dbversion(source, 0x32)

    info = inspect_table(source)
    varchar_field = info.fields[0]
    assert varchar_field.dbf_type == "V"
    assert varchar_field.supported is True

    schema = read_schema(source)
    assert schema.dbversion_name == "Visual FoxPro (Varchar/Varbinary enabled)"

    record = next(iter(iter_records(source)))
    assert record.values["TX"] == "hello"

    result, rebuilt = _roundtrip(tmp_path, source)
    assert result.results[0].canonical_match is True
    assert result.results[0].raw_dbf_match is True
    rebuilt_record = next(iter(iter_records(rebuilt / "varchar.dbf")))
    assert rebuilt_record.values["TX"] == "hello"


def test_vfp_timestamp_alias_decodes_like_datetime(tmp_path: Path) -> None:
    """@ is the dBase Level 7 timestamp; it is physically the VFP datetime
    layout (8 bytes: julian day + milliseconds) and decodes identically.
    Fixture: legal T table patched to @ (same 8-byte layout)."""
    source = factory.create_vfp_table(
        tmp_path / "stamp.dbf",
        "TS T; K N(4,0)",
        [{"TS": datetime(2026, 9, 3, 12, 30, 15), "K": 1}],
    )
    factory.patch_field_type(source, 0, "@")

    info = inspect_table(source)
    assert info.fields[0].dbf_type == "@"
    assert info.fields[0].dbf_type_name == "Timestamp"
    assert info.fields[0].supported is True

    record = next(iter(iter_records(source)))
    assert record.values["TS"] == datetime(2026, 9, 3, 12, 30, 15)
    assert record.to_dict()["values"]["TS"] == "2026-09-03T12:30:15"


def test_vfp_plus_type_reads_as_integer_with_valid_layout(tmp_path: Path) -> None:
    """+ is a dBase Level 7 autoincrement marker with the physical layout of
    a 4-byte integer.  Inside a VFP table it is never autoincrement evidence
    (schema-level contract) and stays readable as an integer with a valid
    4-byte layout.  Fixture: legal I table patched to + (same width)."""
    source = factory.create_vfp_table(tmp_path / "plus.dbf", "ID I; K N(4,0)", [{"ID": 7, "K": 1}])
    factory.patch_field_type(source, 0, "+")

    info = inspect_table(source)
    assert info.fields[0].dbf_type == "+"
    assert info.fields[0].is_autoincrement is False
    assert info.fields[0].supported is True

    record = next(iter(iter_records(source)))
    assert record.values["ID"] == 7


def test_vfp_autoincrement_descriptor_survives_roundtrip(tmp_path: Path) -> None:
    """A VFP autoincrement Integer (flags mask 0x0C) keeps its semantics
    end to end: inspect metadata, Direct Read value, migration schema
    (descriptor_base64), JSONL, reconstruction, and raw descriptor bytes.
    The 0x04 bit inside the mask must never classify the field as
    NOCPTRANS/binary."""
    source = factory.create_vfp_table(tmp_path / "auto.dbf", "ID I; K N(4,0)", [{"ID": 1, "K": 2}])
    factory.patch_field_flags(source, 0, factory.FLAG_AUTOINCREMENT_MASK)
    factory.patch_autoincrement_bookkeeping(source, 0, next_value=42, step=1)
    factory.patch_dbversion(source, 0x31)  # VFP autoincrement-enabled version

    info = inspect_table(source)
    autoincrement = info.fields[0]
    assert autoincrement.is_autoincrement is True
    assert autoincrement.nocptrans is False  # bit 0x04 in the mask is not NOCPTRANS
    assert autoincrement.is_binary is False
    assert autoincrement.autoincrement_next_value == 42
    assert autoincrement.autoincrement_step == 1

    record = next(iter(iter_records(source)))
    assert record.values["ID"] == 1

    result, rebuilt = _roundtrip(tmp_path, source)
    assert result.results[0].canonical_match is True
    assert result.results[0].raw_dbf_match is True

    schema = json.loads((tmp_path / "export" / "auto_schema.json").read_text(encoding="utf-8"))
    exported = _field_schema(schema, "ID")
    assert exported["dbf_type"] == "I"
    assert int(exported["field_flags"]["raw"], 16) == factory.FLAG_AUTOINCREMENT_MASK

    rebuilt_field = inspect_table(rebuilt / "auto.dbf").fields[0]
    assert rebuilt_field.is_autoincrement is True
    assert rebuilt_field.autoincrement_next_value == 42
    assert rebuilt_field.autoincrement_step == 1
    assert rebuilt_field.nocptrans is False


def _field_schema(schema: dict[str, Any], name: str) -> dict[str, Any]:
    return next(field for field in schema["fields"] if field["name"] == name)


# ---------------------------------------------------------------------------
# binary memo family: G / P
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stem", "field_spec", "field_name", "payload"),
    [
        pytest.param("general", "GEN G", "GEN", b"\x89PNG-bytes-\x00\x01", id="G-general"),
        pytest.param("picture", "PIC P", "PIC", b"\x00\x01\x02", id="P-picture"),
    ],
)
def test_general_and_picture_memo_roundtrip_keeps_canonical_identity(
    tmp_path: Path, stem: str, field_spec: str, field_name: str, payload: bytes
) -> None:
    """G (General) and P (Picture) are binary memo pointer fields: the
    inline read returns the exact payload bytes, JSONL carries base64, and
    the DBF reconstructs byte-identically with a canonical match.  The FPT
    block layout itself is not part of the raw-identity contract."""
    source = factory.create_vfp_table(
        tmp_path / f"{stem}.dbf", f"K N(4,0); {field_spec}", [{"K": 1, field_name: payload}]
    )
    memo_field = next(field for field in inspect_table(source).fields if field.is_memo)
    assert memo_field.dbf_type == type_code_of(stem)
    assert memo_field.is_binary is True

    record = next(iter(iter_records(source, memo="inline")))
    assert bytes(record.values[field_name]) == payload

    export_dir = tmp_path / "export"
    export_result = export_dbf(source, export_dir, formats=("jsonl",), overwrite=True)
    export_result.raise_for_errors()
    line = json.loads((export_dir / f"{stem}.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert base64.b64decode(line[field_name]) == payload

    rebuilt = tmp_path / "rebuilt"
    result = reconstruct_dbf(export_dir, rebuilt, input_format="jsonl", overwrite=True)
    result.raise_for_errors()
    report = result.results[0]
    assert report.canonical_match is True
    assert report.raw_dbf_match is True  # the DBF itself stays byte-identical

    rebuilt_record = next(iter(iter_records(rebuilt / f"{stem}.dbf", memo="inline")))
    assert bytes(rebuilt_record.values[field_name]) == payload


def type_code_of(stem: str) -> str:
    return "G" if stem == "general" else "P"


def test_memo_block_type_decides_text_vs_binary_decoding(tmp_path: Path) -> None:
    """The FPT per-block type byte (0x0 picture, 0x1 text) decides how the
    payload is decoded — two records of ONE logical M field may point at
    different block types, exactly as VFP allows."""
    source = factory.create_vfp_table(
        tmp_path / "mixed.dbf",
        "K N(4,0); NOTATKA M",
        [{"K": 1, "NOTATKA": "tekstowy"}, {"K": 2, "NOTATKA": "wariant binarny"}],
    )
    fpt_path = source.with_suffix(".fpt")
    _next_free, block_size = factory.fpt_layout(fpt_path)
    second_block = factory.read_memo_pointer(source, 1, 1)
    factory.patch_memo_block_header(fpt_path, second_block, block_size, block_type=0x0)

    records = list(iter_records(source, memo="inline"))
    assert records[0].values["NOTATKA"] == "tekstowy"  # block type 1 -> text
    assert isinstance(records[1].values["NOTATKA"], (bytes, bytearray))  # type 0 -> binary


# ---------------------------------------------------------------------------
# unsupported physical types: Q (Varbinary), W (Blob), binary C (NOCPTRANS)
# ---------------------------------------------------------------------------


def test_unsupported_varbinary_is_raw_readable_but_not_decoded(tmp_path: Path) -> None:
    """Q (Varbinary): the descriptor stays visible with a stable reason, a
    selected decoded read raises the typed error, and the forensic raw
    stream keeps the exact physical bytes.  No Q support is implied."""
    source = factory.create_vfp_table(
        tmp_path / "varbin.dbf", "K N(4,0); BIN C(6)", [{"K": 1, "BIN": "abcdef"}]
    )
    factory.patch_field_type(source, 1, "Q")  # same inline-bytes layout as C

    info = inspect_table(source)
    varbinary = info.fields[1]
    assert varbinary.dbf_type == "Q"
    assert varbinary.supported is False
    assert varbinary.unsupported_reason is not None

    with pytest.raises(FieldTypeUnsupportedError) as error:
        next(iter_records(source, fields=["BIN"]))
    assert "BIN" in str(error.value)

    fingerprint_before = factory.directory_fingerprint(tmp_path)
    raw = next(iter(iter_raw_records(source)))
    assert raw.raw_record is not None and raw.raw_record.endswith(b"abcdef")
    # The forensic read must not leave a single trace on disk.
    assert factory.directory_fingerprint(tmp_path) == fingerprint_before


def test_unsupported_blob_is_raw_readable_but_not_decoded(tmp_path: Path) -> None:
    """W (Blob) is an unsupported memo pointer field: a selected read raises
    the typed error (memo="skip" removing it is covered by
    test_skip_removes_unsupported_memo_field) and the raw stream keeps the
    physical pointer bytes."""
    source = factory.create_vfp_table(
        tmp_path / "blob.dbf", "K N(4,0); BLB M", [{"K": 1, "BLB": "blobtext"}]
    )
    factory.patch_field_type(source, 1, "W")

    blob = inspect_table(source).fields[1]
    assert blob.supported is False
    assert blob.unsupported_reason is not None

    with pytest.raises(FieldTypeUnsupportedError):
        next(iter_records(source, memo="lazy", fields=["BLB"]))

    raw = next(iter(iter_raw_records(source)))
    assert raw.raw_record is not None and raw.raw_record.endswith(b"\x04\x00\x00\x00")


def test_binary_character_nocptrans_is_raw_readable_but_not_decoded(tmp_path: Path) -> None:
    """A Character field carrying the 0x04 (NOCPTRANS/binary) bit requires a
    dedicated parser (explicit non-goal): typed unsupported for decoded
    access, raw bytes preserved, classification explicit in the schema."""
    source = factory.create_vfp_table(
        tmp_path / "binc.dbf", "K N(4,0); RAW C(6)", [{"K": 1, "RAW": "abcdef"}]
    )
    factory.patch_field_flags(source, 1, factory.FLAG_BINARY)

    info = inspect_table(source)
    binary_field = info.fields[1]
    assert binary_field.dbf_type == "C"
    assert binary_field.nocptrans is True
    assert binary_field.is_binary is True
    assert binary_field.supported is False
    assert "dedicated parser" in (binary_field.unsupported_reason or "")

    with pytest.raises(FieldTypeUnsupportedError):
        next(iter_records(source, fields=["RAW"]))

    raw = next(iter(iter_raw_records(source)))
    assert raw.raw_record is not None and raw.raw_record.endswith(b"abcdef")


def test_unsupported_table_export_reports_typed_unsupported_status(tmp_path: Path) -> None:
    """Migration of a Q table: the run completes, the table is reported
    UNSUPPORTED with a typed per-table reason, and no data/schema output is
    written for it — never a partial or falsely published file."""
    source = factory.create_vfp_table(
        tmp_path / "varbin.dbf", "K N(4,0); BIN C(6)", [{"K": 1, "BIN": "abcdef"}]
    )
    factory.patch_field_type(source, 1, "Q")

    export_dir = tmp_path / "export"
    result = export_dbf(source, export_dir, formats=("jsonl",), overwrite=True)
    assert result.ok == 0
    report = result.results[0]
    assert report.status == "UNSUPPORTED"
    assert "BIN" in " ".join(report.errors)
    # Only the run reports are written for the unsupported table — no data
    # file, no schema, and never a partial or falsely published output.
    assert sorted(item.name for item in export_dir.iterdir()) == [
        "conversion_checksums.json",
        "migration_report.csv",
        "migration_report.jsonl",
    ]


# ---------------------------------------------------------------------------
# system column 0 (hidden _NullFlags of nullable VFP fields)
# ---------------------------------------------------------------------------


def test_nullflags_system_column_is_raw_readable_and_roundtrips(tmp_path: Path) -> None:
    """A NULLable VFP field carries a hidden '_NullFlags' system column of
    physical type 0.  Contract: the schema reports a system field, Direct
    Read returns its raw flag bytes, JSONL carries base64, and the round
    trip is byte-identical.  The column is never presented as normal user
    data (it keeps its system/binary classification)."""
    source = factory.create_vfp_table(
        tmp_path / "nullable.dbf", "K N(4,0) NULL; TX C(10) NULL", [{"K": 1, "TX": "x"}]
    )
    nullflags = next(field for field in inspect_table(source).fields if field.dbf_type == "0")
    assert nullflags.dbf_type_name == "Null flags"
    assert nullflags.system is True
    assert nullflags.is_binary is True
    assert nullflags.supported is True
    assert nullflags.unsupported_reason is None

    record = next(iter(iter_records(source)))
    assert isinstance(record.values["_NULLFLAGS"], (bytes, bytearray))

    export_dir = tmp_path / "export"
    export_result = export_dbf(source, export_dir, formats=("jsonl",), overwrite=True)
    export_result.raise_for_errors()
    line = json.loads((export_dir / "nullable.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert base64.b64decode(line["_NULLFLAGS"]) == b"\xfc"  # both columns non-null

    result, rebuilt = _roundtrip(tmp_path, source)
    assert result.results[0].canonical_match is True
    assert result.results[0].raw_dbf_match is True
    assert (rebuilt / "nullable.dbf").read_bytes() == source.read_bytes()

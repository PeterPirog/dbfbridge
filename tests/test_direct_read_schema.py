"""Tests for the Phase 1A direct read core: inspection and schema API.

These are integration tests against real (small) DBF/FPT fixture files.
They never mock ``inspect_table``/``read_schema``; edge cases are produced
by patching individual header bytes of real tables.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any

import dbf
import pytest

import dbf_bridge
import dbfbridge
from dbfbridge import (
    DbfFormatUnsupportedError,
    DbfHeaderInvalidError,
    DbfIoError,
    DbfPathError,
    DbfTruncatedError,
    DirectReadError,
    EncodingUnknownError,
    ErrorCode,
    FieldInfo,
    TableInfo,
    TableSchema,
    inspect_table,
    read_schema,
)

SRC_ROOT = Path(__file__).parents[1] / "src"


# ---------------------------------------------------------------------------
# fixture helpers
# ---------------------------------------------------------------------------


def _create_vfp_table(
    path: Path,
    field_specs: str,
    records: list[dict[str, Any]],
    codepage: int = 0xC8,
) -> Path:
    for suffix in (".dbf", ".fpt", ".cdx", ".idx", ".dbc"):
        for candidate in (path.with_suffix(suffix),):
            if candidate.exists():
                candidate.unlink()
    table = dbf.Table(str(path), field_specs=field_specs, dbf_type="vfp", codepage=codepage)
    table.open(mode=dbf.READ_WRITE)
    for record in records:
        table.append(record)
    table.close()
    return path


def _patch_header_bytes(path: Path, patches: dict[int, bytes]) -> None:
    data = bytearray(path.read_bytes())
    for offset, value in patches.items():
        data[offset : offset + len(value)] = value
    path.write_bytes(bytes(data))


def _field_count(path: Path) -> int:
    """Count field descriptors; only 0x0D is a terminator (never 0x0A)."""
    data = path.read_bytes()
    count = 0
    offset = 32
    while data[offset : offset + 1] != b"\r":
        count += 1
        offset += 32
    return count


def _assert_json_safe(payload: Any) -> None:
    def walk(value: Any) -> None:
        if value is None or isinstance(value, (str, int, float, bool)):
            return
        if isinstance(value, list):
            for item in value:
                walk(item)
            return
        if isinstance(value, dict):
            for key, item in value.items():
                assert isinstance(key, str)
                walk(item)
            return
        raise AssertionError(f"non-JSON-safe value type: {type(value).__name__}")

    walk(payload)


def _directory_snapshot(directory: Path) -> dict[str, int]:
    return {entry.name: entry.stat().st_size for entry in os.scandir(directory)}


# ---------------------------------------------------------------------------
# public API surface
# ---------------------------------------------------------------------------


def test_public_symbols_are_exported_from_both_namespaces() -> None:
    names = (
        "inspect_table",
        "read_schema",
        "FieldInfo",
        "TableInfo",
        "TableSchema",
        "ErrorCode",
        "DirectReadError",
        "DbfPathError",
        "DbfHeaderInvalidError",
        "DbfTruncatedError",
        "DbfFormatUnsupportedError",
        "DbfIoError",
        "EncodingUnknownError",
    )
    for name in names:
        assert name in dbfbridge.__all__, name
        assert name in dbf_bridge.__all__, name
        assert getattr(dbfbridge, name) is getattr(dbf_bridge, name), name


def test_public_models_are_immutable_and_typed() -> None:
    import dataclasses

    for model in (TableInfo, TableSchema, FieldInfo):
        assert dataclasses.is_dataclass(model)
        assert model.__dataclass_params__.frozen is True


def test_error_codes_are_stable_machine_values() -> None:
    assert ErrorCode.PATH_NOT_FOUND.value == "PATH_NOT_FOUND"
    assert ErrorCode.DBF_HEADER_INVALID.value == "DBF_HEADER_INVALID"
    assert ErrorCode.DBF_TRUNCATED.value == "DBF_TRUNCATED"
    assert ErrorCode.DBF_FORMAT_UNSUPPORTED.value == "DBF_FORMAT_UNSUPPORTED"
    assert ErrorCode.ENCODING_UNKNOWN.value == "ENCODING_UNKNOWN"
    assert ErrorCode.DBF_IO_ERROR.value == "DBF_IO_ERROR"
    for exc_type in (
        DbfPathError,
        DbfHeaderInvalidError,
        DbfTruncatedError,
        DbfFormatUnsupportedError,
        DbfIoError,
        EncodingUnknownError,
    ):
        assert issubclass(exc_type, DirectReadError)
        assert issubclass(exc_type, ValueError)


# ---------------------------------------------------------------------------
# happy paths on real fixtures
# ---------------------------------------------------------------------------


def test_inspect_flat_table(sample_input_dir: Path) -> None:
    dbf_path = sample_input_dir / "zamowienia" / "zamowienia.dbf"
    info = inspect_table(dbf_path)

    assert isinstance(info, TableInfo)
    assert info.path == dbf_path
    assert info.record_count == 50
    assert info.header_length > 33
    assert info.record_length > 1
    assert info.language_driver == 0xC8
    assert info.encoding == "cp1250"
    assert info.has_memo is False
    assert info.has_structural_cdx is False
    assert info.dbc_bound is False
    assert info.warnings == ()
    assert [field.name for field in info.fields] == [
        "ID_ZAM",
        "ID_KL",
        "DATA_ZAM",
        "KWOTA",
        "STATUS",
    ]
    assert [field.ordinal for field in info.fields] == [1, 2, 3, 4, 5]
    assert all(field.supported for field in info.fields)
    data = dbf_path.read_bytes()
    assert info.record_length == 1 + sum(field.length for field in info.fields)
    assert struct.unpack_from("<H", data, 8)[0] == info.header_length


def test_inspect_memo_heavy_table_with_companion_fpt(sample_input_dir: Path) -> None:
    dbf_path = sample_input_dir / "klienci.dbf"
    info = inspect_table(dbf_path)
    schema = read_schema(dbf_path)

    assert isinstance(schema, TableSchema)
    assert info.record_count == 20
    assert info.has_memo is True
    memo_field = next(field for field in info.fields if field.name == "NOTATKA")
    assert memo_field.is_memo is True
    assert memo_field.dbf_type == "M"
    assert memo_field.dbf_type_name == "Memo"
    assert memo_field.length == 4

    assert schema.memo_companion_present is True
    assert schema.memo_companion_path is not None
    assert schema.memo_companion_size_bytes == (sample_input_dir / "klienci.fpt").stat().st_size
    assert schema.memo_block_size and schema.memo_block_size > 0
    assert schema.memo_next_free_block and schema.memo_next_free_block > 0
    assert schema.dbversion_byte in (0x30, 0x31, 0x32)
    assert "Visual FoxPro" in schema.dbversion_name
    assert schema.last_update is not None
    assert schema.companion_cdx_present is False


def test_missing_fpt_is_a_structured_warning_not_an_error(sample_input_dir: Path) -> None:
    dbf_path = sample_input_dir / "klienci.dbf"
    fpt_path = sample_input_dir / "klienci.fpt"
    backup = fpt_path.read_bytes()
    try:
        fpt_path.unlink()
        info = inspect_table(dbf_path)
        assert info.has_memo is True
        assert len(info.warnings) == 1
        assert "fpt" in info.warnings[0].lower()
        assert "NOTATKA" in info.warnings[0]

        schema = read_schema(dbf_path)
        # The missing companion is still described: the format follows the
        # DBF version while presence/path/size stay honest (separate fields).
        assert schema.has_memo is True
        assert schema.memo_companion_format == "FPT"
        assert schema.memo_companion_present is False
        assert schema.memo_companion_path is None
        assert schema.memo_companion_size_bytes is None
        assert schema.memo_block_size is None
        assert schema.memo_next_free_block is None
        assert schema.warnings == info.warnings
    finally:
        fpt_path.write_bytes(backup)


def test_memo_table_flag_reports_expected_companion_format(tmp_path: Path) -> None:
    # Memo table flag (0x02) without memo fields and without a companion:
    # the expected format from the DBF version is still reported.
    dbf_path = _create_vfp_table(tmp_path / "FLAGMEMO.dbf", "KOD N(6,0)", [{"KOD": 1}])
    _patch_header_bytes(dbf_path, {28: b"\x02"})
    schema = read_schema(dbf_path)
    assert schema.has_memo is False
    assert schema.has_memo_flag is True
    assert schema.memo_companion_format == "FPT"
    assert schema.memo_companion_present is False
    assert schema.memo_companion_path is None


def test_table_flags_raw_value_is_exposed(sample_input_dir: Path) -> None:
    dbf_path = sample_input_dir / "zamowienia" / "zamowienia.dbf"
    _patch_header_bytes(dbf_path, {28: b"\x05"})  # structural CDX + database
    try:
        info = inspect_table(dbf_path)
        schema = read_schema(dbf_path)
    finally:
        _patch_header_bytes(dbf_path, {28: b"\x00"})
    assert info.table_flags == 0x05
    assert schema.table_flags == 0x05
    info_dict = info.to_dict()
    schema_dict = schema.to_dict()
    for payload in (info_dict, schema_dict):
        assert payload["table_flags"] == 5
        assert payload["table_flags_hex"] == "0x05"
    # The derived booleans stay intact alongside the raw value.
    assert info.has_structural_cdx is True
    assert info.is_database_container is True
    assert info.has_memo_flag is False


def test_memo_field_and_companion_fpt_are_distinct(tmp_path: Path) -> None:
    # A table without memo fields but with a stray companion FPT present.
    dbf_path = _create_vfp_table(
        tmp_path / "FLAT.dbf",
        "KOD N(6,0); NAZWA C(40)",
        [{"KOD": 1, "NAZWA": "x"}],
    )
    (tmp_path / "FLAT.fpt").write_bytes(b"\x00" * 16)
    schema = read_schema(dbf_path)
    assert schema.has_memo is False
    assert schema.memo_companion_present is True
    assert schema.warnings == ()


def test_companions_are_recognized_case_insensitive(sample_input_dir: Path) -> None:
    fpt_path = sample_input_dir / "klienci.fpt"
    renamed = fpt_path.parent / "KLIENCI.FPT"
    try:
        os.replace(fpt_path, renamed)
        schema = read_schema(sample_input_dir / "klienci.dbf")
        assert schema.memo_companion_present is True
        assert schema.memo_companion_path is not None
        assert schema.memo_companion_path.casefold().endswith("klienci.fpt")
    finally:
        if renamed.exists():
            os.replace(renamed, fpt_path)


def test_table_flags_byte_is_a_bitmask_not_a_single_flag(sample_input_dir: Path) -> None:
    dbf_path = sample_input_dir / "zamowienia" / "zamowienia.dbf"
    for value in (0x00, 0x01, 0x02, 0x03, 0x04, 0x07):
        _patch_header_bytes(dbf_path, {28: bytes([value])})
        try:
            info = inspect_table(dbf_path)
        finally:
            _patch_header_bytes(dbf_path, {28: b"\x00"})
        assert info.has_structural_cdx is bool(value & 0x01), value
        assert info.has_memo_flag is bool(value & 0x02), value
        assert info.is_database_container is bool(value & 0x04), value
    # In particular: a memo-only flags byte must not imply a structural CDX.
    _patch_header_bytes(dbf_path, {28: b"\x02"})
    try:
        info = inspect_table(dbf_path)
    finally:
        _patch_header_bytes(dbf_path, {28: b"\x00"})
    assert info.has_structural_cdx is False
    assert info.has_memo_flag is True


def test_dbc_bound_comes_from_the_vfp_backlink_not_a_dbc_file(sample_input_dir: Path) -> None:
    dbf_path = sample_input_dir / "zamowienia" / "zamowienia.dbf"
    schema_before = read_schema(dbf_path)

    # 1) An all-zero backlink area means the table is standalone...
    assert schema_before.dbc_bound is False
    assert schema_before.dbc_backlink_path is None
    # ...and the mere existence of a neighbouring .dbc file must not change it.
    dbc_file = dbf_path.parent / (dbf_path.stem + ".dbc")
    try:
        dbc_file.write_bytes(b"\x00" * 64)
        assert inspect_table(dbf_path).dbc_bound is False

        terminator_offset = 32 + 32 * _field_count(dbf_path) + 1
        original = dbf_path.read_bytes()[terminator_offset : terminator_offset + 263]
        try:
            # 2) First byte zero, later bytes non-zero: still standalone (the
            #    backlink path is null-terminated, the first byte decides).
            _patch_header_bytes(dbf_path, {terminator_offset: b"\x00" + b"\xff" * 262})
            bounded = read_schema(dbf_path)
            assert bounded.dbc_bound is False
            assert bounded.dbc_backlink_path is None

            # 3) A real null-terminated relative DBC path means bound.
            path_bytes = b".." + b"\\" + b"data" + b"\\" + b"app.dbc" + b"\x00"
            _patch_header_bytes(
                dbf_path, {terminator_offset: path_bytes + b"\x00" * (263 - len(path_bytes))}
            )
            bounded = read_schema(dbf_path)
            assert bounded.dbc_bound is True
            assert bounded.dbc_backlink_path == "..\\data\\app.dbc"
            assert bounded.dbc_backlink_path.encode("cp1250") == path_bytes.rstrip(b"\x00")
        finally:
            _patch_header_bytes(dbf_path, {terminator_offset: original})
    finally:
        dbc_file.unlink(missing_ok=True)


def test_dbc_backlink_decodes_with_the_resolved_header_encoding(sample_input_dir: Path) -> None:
    dbf_path = sample_input_dir / "zamowienia" / "zamowienia.dbf"
    assert inspect_table(dbf_path).encoding == "cp1250"
    terminator_offset = 32 + 32 * _field_count(dbf_path) + 1
    original = dbf_path.read_bytes()[terminator_offset : terminator_offset + 263]
    try:
        # A backlink path with Polish characters stored in cp1250 (the
        # encoding resolved from the language driver) must decode correctly.
        unicode_path = "..\\dane\\ąęŚŹ\\baza.dbc"
        path_bytes = unicode_path.encode("cp1250") + b"\x00"
        assert path_bytes[: len(unicode_path.encode("cp1250"))] != unicode_path.encode("utf-8")
        _patch_header_bytes(
            dbf_path, {terminator_offset: path_bytes + b"\x00" * (263 - len(path_bytes))}
        )
        schema = read_schema(dbf_path)
        assert schema.dbc_bound is True
        assert schema.dbc_backlink_path == unicode_path
        assert schema.warnings == ()
        _assert_json_safe(schema.to_dict())
    finally:
        _patch_header_bytes(dbf_path, {terminator_offset: original})


def test_undecodable_backlink_still_reports_dbc_bound(sample_input_dir: Path) -> None:
    dbf_path = sample_input_dir / "zamowienia" / "zamowienia.dbf"
    terminator_offset = 32 + 32 * _field_count(dbf_path) + 1
    original = dbf_path.read_bytes()[terminator_offset : terminator_offset + 263]
    try:
        # Bytes 0x81/0x83/0x88 are undefined in cp1250: decoding must fail,
        # but the DBC binding itself must stay visible.
        _patch_header_bytes(dbf_path, {terminator_offset: b"\x81\x83\x88app.dbc" + b"\x00" * 253})
        schema = read_schema(dbf_path)
        assert schema.dbc_bound is True
        assert schema.dbc_backlink_path is None
        assert len(schema.warnings) == 1
        assert "backlink" in schema.warnings[0].lower()
        assert "cp1250" in schema.warnings[0]
        payload = json.loads(json.dumps(schema.to_dict()))
        assert payload["dbc_bound"] is True
        assert payload["dbc_backlink_path"] is None
        _assert_json_safe(schema.to_dict())
    finally:
        _patch_header_bytes(dbf_path, {terminator_offset: original})


def test_vfp_field_flags_nullable_binary_and_memo(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "FLAGS.dbf",
        "KOD N(6,0) NULL; DANE C(20) BINARY; TEKST C(60); NOTATKA M",
        [{"KOD": 7, "DANE": b"xy", "TEKST": "abc", "NOTATKA": "memo"}],
    )
    info = inspect_table(dbf_path)
    fields = {field.name: field for field in info.fields}

    assert fields["KOD"].nullable is True
    assert fields["KOD"].flags & 0x02
    assert fields["KOD"].nocptrans is False
    assert fields["DANE"].nocptrans is True
    assert fields["DANE"].flags & 0x04
    assert fields["DANE"].is_binary is True
    assert fields["DANE"].supported is False
    assert fields["DANE"].unsupported_reason
    assert fields["TEKST"].supported is True
    assert fields["NOTATKA"].is_memo is True
    assert any("DANE" in warning for warning in info.warnings)

    # A real 0x04 flag on a Memo field keeps the NOCPTRANS semantics.
    notatkaflags_offset = 32 + 3 * 32 + 18
    original_flags = dbf_path.read_bytes()[notatkaflags_offset]
    try:
        _patch_header_bytes(dbf_path, {notatkaflags_offset: b"\x04"})
        fields = {field.name: field for field in inspect_table(dbf_path).fields}
        assert fields["NOTATKA"].nocptrans is True
        assert fields["NOTATKA"].is_memo is True
    finally:
        _patch_header_bytes(dbf_path, {notatkaflags_offset: bytes([original_flags])})


def test_language_drivers_cp1250_cp852_and_mazovia(tmp_path: Path) -> None:
    base_specs = "KOD N(6,0); NAZWA C(40)"
    base_records = [{"KOD": 1, "NAZWA": "x"}]

    cp1250 = _create_vfp_table(tmp_path / "WIN1250.dbf", base_specs, base_records, codepage=0xC8)
    assert inspect_table(cp1250).encoding == "cp1250"

    cp852 = _create_vfp_table(tmp_path / "CP852.dbf", base_specs, base_records, codepage=0x23)
    assert inspect_table(cp852).encoding == "cp852"

    mazovia = _create_vfp_table(tmp_path / "MAZOVIA.dbf", base_specs, base_records, codepage=0x01)
    _patch_header_bytes(mazovia, {29: b"\x69"})
    assert inspect_table(mazovia).encoding == "mazovia"


def test_mazovia_fallback_decoding_is_available_in_core() -> None:
    from dbf_bridge.core.codecs import decode_with_polish_fallback, driver_to_encoding

    # The Mazovia driver byte (0x69) resolves to the custom codec...
    assert driver_to_encoding(0x69) == "mazovia"
    # ...which is registered on demand and decodes its own layout.
    assert b"\x80".decode("mazovia") == "\u0105"
    # The fallback chain walks cp1250 first for a byte invalid in ASCII.
    text, source = decode_with_polish_fallback(b"\x80", "ascii")
    assert source == "cp1250"
    assert text == "\u20ac"


def test_historical_polish_codecs_import_stays_compatible() -> None:
    from dbf_bridge.core import codecs as core_codecs
    from dbf_bridge.exporter import polish_codecs

    assert polish_codecs.register_polish_codecs is core_codecs.register_polish_codecs
    assert polish_codecs.POLISH_FALLBACK_ENCODINGS is core_codecs.POLISH_FALLBACK_ENCODINGS
    assert len(polish_codecs.POLISH_FALLBACK_ENCODINGS) == 4


# ---------------------------------------------------------------------------
# JSON serialization
# ---------------------------------------------------------------------------


def test_json_serialization_of_both_models(sample_input_dir: Path) -> None:
    dbf_path = sample_input_dir / "klienci.dbf"
    info = inspect_table(dbf_path)
    schema = read_schema(dbf_path)

    for payload in (info.to_dict(), schema.to_dict()):
        _assert_json_safe(payload)
        round_tripped = json.loads(json.dumps(payload))
        assert round_tripped == payload

    info_dict = info.to_dict()
    assert info_dict["path"] == dbf_path.as_posix()
    assert info_dict["fields"][0]["dbf_type_name"]
    schema_dict = schema.to_dict()
    assert "header_base64" not in json.dumps(schema_dict)
    assert "descriptor_base64" not in json.dumps(schema_dict)


# ---------------------------------------------------------------------------
# structured errors on damaged headers
# ---------------------------------------------------------------------------


def test_missing_path_and_directory_are_path_errors(tmp_path: Path) -> None:
    for bad in (tmp_path / "missing.dbf", tmp_path):
        with pytest.raises(DbfPathError) as error:
            inspect_table(bad)
        assert error.value.code is ErrorCode.PATH_NOT_FOUND
        assert error.value.path is not None
        _assert_json_safe(error.value.to_dict())


def test_truncated_fixed_header(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "T.dbf", "KOD N(6,0); NAZWA C(40)", [{"KOD": 1, "NAZWA": "x"}]
    )
    data = dbf_path.read_bytes()[:20]
    dbf_path.write_bytes(data)
    with pytest.raises(DbfTruncatedError) as error:
        inspect_table(dbf_path)
    assert error.value.code is ErrorCode.DBF_TRUNCATED
    assert error.value.context["available_bytes"] == 20


def _descriptor(name: bytes, field_length: int) -> bytes:
    descriptor = bytearray(32)
    descriptor[0:11] = name.ljust(11, b"\0")
    descriptor[11] = ord("C")
    struct.pack_into("<L", descriptor, 12, 1)
    descriptor[16] = field_length
    return bytes(descriptor)


def test_truncated_field_descriptor(tmp_path: Path) -> None:
    path = tmp_path / "TD.dbf"
    # Two declared fields (headerlen = 32 + 2*32 + 1 = 97) but the file only
    # contains a third non-terminator byte plus filler: the descriptor scan
    # must fail before the terminator is seen.
    fixed = bytearray(32)
    fixed[0] = 0x30  # Visual FoxPro 6+
    fixed[1:4] = b"\x1e\x08\x30"
    struct.pack_into("<L", fixed, 4, 1)
    struct.pack_into("<H", fixed, 8, 97)
    struct.pack_into("<H", fixed, 10, 3)
    fixed[29] = 0xC8
    path.write_bytes(
        bytes(fixed) + _descriptor(b"A", 1) + _descriptor(b"B", 1) + b"\xff" + b"\x00" * 3
    )
    with pytest.raises(DbfTruncatedError) as error:
        inspect_table(path)
    assert error.value.code is ErrorCode.DBF_TRUNCATED
    assert error.value.context["field_ordinal"] == 3


def test_header_length_below_minimum_is_invalid(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "H.dbf", "KOD N(6,0); NAZWA C(40)", [{"KOD": 1, "NAZWA": "x"}]
    )
    _patch_header_bytes(dbf_path, {8: struct.pack("<H", 32)})
    with pytest.raises(DbfHeaderInvalidError) as error:
        inspect_table(dbf_path)
    assert error.value.code is ErrorCode.DBF_HEADER_INVALID
    assert error.value.context["header_length"] == 32


def test_header_length_beyond_file_size_is_truncated(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "H.dbf", "KOD N(6,0); NAZWA C(40)", [{"KOD": 1, "NAZWA": "x"}]
    )
    file_size = dbf_path.stat().st_size
    _patch_header_bytes(dbf_path, {8: struct.pack("<H", file_size + 1000)})
    with pytest.raises(DbfTruncatedError) as error:
        inspect_table(dbf_path)
    assert error.value.code is ErrorCode.DBF_TRUNCATED


def test_zero_record_length_is_invalid(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "R.dbf", "KOD N(6,0); NAZWA C(40)", [{"KOD": 1, "NAZWA": "x"}]
    )
    _patch_header_bytes(dbf_path, {10: struct.pack("<H", 0)})
    with pytest.raises(DbfHeaderInvalidError) as error:
        inspect_table(dbf_path)
    assert error.value.code is ErrorCode.DBF_HEADER_INVALID


def test_field_lengths_must_match_record_length(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "R.dbf", "KOD N(6,0); NAZWA C(40)", [{"KOD": 1, "NAZWA": "x"}]
    )
    record_length = struct.unpack_from("<H", dbf_path.read_bytes(), 10)[0]
    _patch_header_bytes(dbf_path, {10: struct.pack("<H", record_length + 1)})
    with pytest.raises(DbfHeaderInvalidError) as error:
        inspect_table(dbf_path)
    assert error.value.code is ErrorCode.DBF_HEADER_INVALID
    assert error.value.context["expected_record_length"] == record_length


def test_truncated_physical_record_area(tmp_path: Path) -> None:
    records = [{"KOD": i, "NAZWA": f"x{i}"} for i in range(1, 4)]
    dbf_path = _create_vfp_table(tmp_path / "T.dbf", "KOD N(6,0); NAZWA C(40)", records)
    data = dbf_path.read_bytes()
    header_length = struct.unpack_from("<H", data, 8)[0]
    record_length = struct.unpack_from("<H", data, 10)[0]
    dbf_path.write_bytes(data[: header_length + record_length * 2])
    with pytest.raises(DbfTruncatedError) as error:
        inspect_table(dbf_path)
    assert error.value.code is ErrorCode.DBF_TRUNCATED
    assert error.value.context["record_count"] == 3


def test_unknown_language_driver_is_a_typed_error(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "U.dbf", "KOD N(6,0); NAZWA C(40)", [{"KOD": 1, "NAZWA": "x"}]
    )
    _patch_header_bytes(dbf_path, {29: b"\x99"})
    with pytest.raises(EncodingUnknownError) as error:
        inspect_table(dbf_path)
    assert error.value.code is ErrorCode.ENCODING_UNKNOWN
    assert error.value.context["language_driver"] == 0x99
    _assert_json_safe(error.value.to_dict())


def test_unsupported_dbf_version_is_a_typed_error(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "V.dbf", "KOD N(6,0); NAZWA C(40)", [{"KOD": 1, "NAZWA": "x"}]
    )
    _patch_header_bytes(dbf_path, {0: b"\x99"})
    with pytest.raises(DbfFormatUnsupportedError) as error:
        inspect_table(dbf_path)
    assert error.value.code is ErrorCode.DBF_FORMAT_UNSUPPORTED
    assert error.value.context["dbversion_byte"] == 0x99


def test_unsupported_field_type_is_reported_per_field(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "Q.dbf", "KOD N(6,0); ZAWARTOSC C(20)", [{"KOD": 1, "ZAWARTOSC": "x"}]
    )
    _patch_header_bytes(dbf_path, {32 + 32 + 11: b"Q"})
    info = inspect_table(dbf_path)
    field = next(item for item in info.fields if item.name == "ZAWARTOSC")
    assert field.dbf_type == "Q"
    assert field.dbf_type_name == "Varbinary"
    assert field.supported is False
    assert field.unsupported_reason
    assert any("ZAWARTOSC" in warning for warning in info.warnings)


# ---------------------------------------------------------------------------
# VFP header semantics: table flags, backlink, date, field descriptors
# ---------------------------------------------------------------------------


def test_header_year_is_1900_plus_byte_without_pivot(sample_input_dir: Path) -> None:
    dbf_path = sample_input_dir / "zamowienia" / "zamowienia.dbf"
    cases = {
        0: "1900-",
        79: "1979-",
        100: "2000-",
        126: "2026-",
        255: "2155-",
    }
    original_year = dbf_path.read_bytes()[1]
    try:
        for year_byte, expected_prefix in cases.items():
            _patch_header_bytes(dbf_path, {1: bytes([year_byte])})
            schema = read_schema(dbf_path)
            assert schema.last_update is not None
            assert schema.last_update.startswith(expected_prefix), (year_byte, schema.last_update)
    finally:
        _patch_header_bytes(dbf_path, {1: bytes([original_year])})


def test_invalid_header_date_is_none_with_warning(sample_input_dir: Path) -> None:
    dbf_path = sample_input_dir / "zamowienia" / "zamowienia.dbf"
    original_month = dbf_path.read_bytes()[2]
    try:
        _patch_header_bytes(dbf_path, {2: b"\x0d"})  # month 13: invalid
        schema = read_schema(dbf_path)
        assert schema.last_update is None
        assert any("last-update date is invalid" in warning for warning in schema.warnings)
    finally:
        _patch_header_bytes(dbf_path, {2: bytes([original_month])})


def test_newline_byte_is_not_a_descriptor_terminator(sample_input_dir: Path) -> None:
    dbf_path = sample_input_dir / "zamowienia" / "zamowienia.dbf"
    terminator_offset = 32 + 32 * _field_count(dbf_path)
    original = dbf_path.read_bytes()[terminator_offset]
    try:
        _patch_header_bytes(dbf_path, {terminator_offset: b"\x0a"})
        with pytest.raises(DbfHeaderInvalidError) as error:
            inspect_table(dbf_path)
        assert error.value.code is ErrorCode.DBF_HEADER_INVALID
    finally:
        _patch_header_bytes(dbf_path, {terminator_offset: bytes([original])})


def test_terminator_must_be_inside_the_declared_header(sample_input_dir: Path) -> None:
    dbf_path = sample_input_dir / "zamowienia" / "zamowienia.dbf"
    data = bytearray(dbf_path.read_bytes())
    header_length = struct.unpack_from("<H", data, 8)[0]
    terminator_offset = 32 + 32 * _field_count(dbf_path)
    original_terminator = data[terminator_offset]
    record_byte = data[header_length + 5]
    try:
        # Remove the terminator entirely; a 0x0D byte exists later in the
        # record area and must not be mistaken for it.
        data[terminator_offset] = 0x00
        data[header_length + 5] = 0x0D
        dbf_path.write_bytes(bytes(data))
        with pytest.raises(DbfTruncatedError) as error:
            inspect_table(dbf_path)
        assert error.value.code is ErrorCode.DBF_TRUNCATED
        # Corrupting (or clearing) the record area must change nothing: the
        # parser is bounded by the declared header length.
        data[header_length + 5] = 0x00
        dbf_path.write_bytes(bytes(data))
        with pytest.raises(DbfTruncatedError) as second:
            inspect_table(dbf_path)
        assert second.value.code is ErrorCode.DBF_TRUNCATED
    finally:
        data[terminator_offset] = original_terminator
        data[header_length + 5] = record_byte
        dbf_path.write_bytes(bytes(data))


def test_descriptor_crossing_header_length_is_rejected(sample_input_dir: Path) -> None:
    dbf_path = sample_input_dir / "zamowienia" / "zamowienia.dbf"
    # Trim the declared header so the last descriptor would run past it.
    header_length = struct.unpack_from("<H", dbf_path.read_bytes(), 8)[0]
    too_short = 32 + 32 * (_field_count(dbf_path) - 1) + 1
    try:
        _patch_header_bytes(dbf_path, {8: struct.pack("<H", too_short)})
        with pytest.raises(DbfTruncatedError) as error:
            inspect_table(dbf_path)
        assert error.value.code is ErrorCode.DBF_TRUNCATED
        assert error.value.context["field_ordinal"] == _field_count(dbf_path)
    finally:
        _patch_header_bytes(dbf_path, {8: struct.pack("<H", header_length)})


def test_truncated_vfp_backlink_area_is_rejected(sample_input_dir: Path) -> None:
    dbf_path = sample_input_dir / "zamowienia" / "zamowienia.dbf"
    header_length = struct.unpack_from("<H", dbf_path.read_bytes(), 8)[0]
    # A VFP header without room for the 263-byte backlink area is truncated.
    no_backlink = 32 + 32 * _field_count(dbf_path) + 1
    try:
        _patch_header_bytes(dbf_path, {8: struct.pack("<H", no_backlink)})
        with pytest.raises(DbfTruncatedError) as error:
            inspect_table(dbf_path)
        assert error.value.code is ErrorCode.DBF_TRUNCATED
        assert error.value.context["required_backlink_size"] == 263
    finally:
        _patch_header_bytes(dbf_path, {8: struct.pack("<H", header_length)})


def test_vfp_autoincrement_integer_with_mask_is_exposed(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "AINC.dbf", "KOD N(4,0); NAZWA C(40)", [{"KOD": 7, "NAZWA": "x"}]
    )
    # Properly constructed VFP autoincrement descriptor for field 1 (KOD):
    # version 0x31 (autoincrement enabled), physical type Integer 'I',
    # field-flags mask 0x0C, next value in bytes 19-22 (LE), step in byte 23.
    _patch_header_bytes(
        dbf_path,
        {
            0: b"\x31",
            32 + 11: b"I",
            32 + 18: b"\x0c",
            32 + 19: struct.pack("<L", 42),
            32 + 23: b"\x02",
        },
    )
    info = inspect_table(dbf_path)
    fields = {field.name: field for field in info.fields}

    assert fields["KOD"].is_autoincrement is True
    assert fields["KOD"].dbf_type == "I"
    assert fields["KOD"].flags & 0x0C == 0x0C
    assert fields["KOD"].autoincrement_next_value == 42
    assert fields["KOD"].autoincrement_step == 2
    # Bit 0x04 inside the autoincrement mask is not NOCPTRANS/binary.
    assert fields["KOD"].nocptrans is False
    assert fields["KOD"].is_binary is False
    assert fields["KOD"].supported is True
    # A plain Character field in the same table is not autoincrement.
    assert fields["NAZWA"].is_autoincrement is False
    assert fields["NAZWA"].autoincrement_next_value == 0
    assert fields["NAZWA"].autoincrement_step == 0
    payload = json.loads(json.dumps(info.to_dict()))
    autoinc = next(item for item in payload["fields"] if item["name"] == "KOD")
    assert autoinc["autoincrement_next_value"] == 42
    assert autoinc["autoincrement_step"] == 2
    assert autoinc["is_autoincrement"] is True


def test_vfp_integer_without_autoincrement_mask_is_not_autoincrement(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "PLAINI.dbf", "KOD N(4,0); NAZWA C(40)", [{"KOD": 7, "NAZWA": "x"}]
    )
    _patch_header_bytes(dbf_path, {32 + 11: b"I"})  # Integer type, no 0x0C mask
    fields = {field.name: field for field in inspect_table(dbf_path).fields}
    assert fields["KOD"].dbf_type == "I"
    assert fields["KOD"].flags & 0x0C == 0
    assert fields["KOD"].is_autoincrement is False


def test_plus_type_is_never_vfp_autoincrement_evidence(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(tmp_path / "PLUS.dbf", "KOD N(6,0)", [{"KOD": 1}])
    _patch_header_bytes(dbf_path, {0: b"\x31", 32 + 11: b"+"})
    fields = {field.name: field for field in inspect_table(dbf_path).fields}
    # The dBASE Level 7 '+' type is not evidence of VFP autoincrement.
    assert fields["KOD"].dbf_type == "+"
    assert fields["KOD"].is_autoincrement is False

    # Even with the autoincrement mask set, VFP requires the Integer type.
    _patch_header_bytes(dbf_path, {32 + 18: b"\x0c"})
    fields = {field.name: field for field in inspect_table(dbf_path).fields}
    assert fields["KOD"].is_autoincrement is False

    # Outside VFP (dBASE Level 7 territory), physical type '+' remains the
    # autoincrement marker (migration compatibility, not VFP semantics).
    _patch_header_bytes(dbf_path, {0: b"\x03"})
    fields = {field.name: field for field in inspect_table(dbf_path).fields}
    assert fields["KOD"].dbf_type == "+"
    assert fields["KOD"].is_autoincrement is True


def test_general_and_picture_memos_are_semantic_binary(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "BINARYMEMO.dbf",
        "KOD N(6,0); ZALACZNIK M; OBRAZ M",
        [{"KOD": 1, "ZALACZNIK": "file", "OBRAZ": "img"}],
    )
    # Properly constructed General/Picture memo descriptors (type byte 11).
    _patch_header_bytes(dbf_path, {32 + 32 + 11: b"G", 32 + 64 + 11: b"P"})
    info = inspect_table(dbf_path)
    fields = {field.name: field for field in info.fields}

    for name in ("ZALACZNIK", "OBRAZ"):
        assert fields[name].is_memo is True
        assert fields[name].is_binary is True
        # The NOCPTRANS flag is a separate, descriptor-level property.
        assert fields[name].nocptrans is False
    assert fields["KOD"].is_binary is False


# ---------------------------------------------------------------------------
# memo companion consistency
# ---------------------------------------------------------------------------


def test_dbt_memo_version_is_marked_unsupported_for_direct_read(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "DBT.dbf", "KOD N(6,0); NOTATKA M", [{"KOD": 1, "NOTATKA": "x"}]
    )
    _patch_header_bytes(dbf_path, {0: b"\x8b"})  # dBASE IV (with memo)
    (tmp_path / "DBT.dbt").write_bytes(b"\x00" * 16)
    schema = read_schema(dbf_path)
    assert schema.memo_companion_format == "DBT"
    assert schema.memo_companion_present is True
    assert schema.memo_companion_path is not None
    assert schema.memo_companion_size_bytes == 16
    assert schema.memo_block_size is None  # DBT: never interpreted as an FPT header
    assert schema.memo_next_free_block is None
    # Exactly one diagnostic: the format report, no FPT-header warnings.
    assert schema.warnings == (
        "Memo companion format DBT ('DBT.dbt') is not supported for reading in "
        "Direct Read; only FPT (VFP/FoxPro) is supported.",
    )


def test_smt_memo_version_uses_smt_companion(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "SMT.dbf", "KOD N(6,0); NOTATKA M", [{"KOD": 1, "NOTATKA": "x"}]
    )
    _patch_header_bytes(dbf_path, {0: b"\xe5"})  # HiPer-Six (SMT memo)
    (tmp_path / "SMT.smt").write_bytes(b"\x00" * 16)
    schema = read_schema(dbf_path)
    assert schema.memo_companion_format == "SMT"
    assert schema.memo_companion_present is True
    assert schema.memo_block_size is None  # SMT: never interpreted as an FPT header
    # Exactly one diagnostic: the format report, no FPT-header warnings.
    assert schema.warnings == (
        "Memo companion format SMT ('SMT.smt') is not supported for reading in "
        "Direct Read; only FPT (VFP/FoxPro) is supported.",
    )


def test_short_fpt_header_is_a_diagnostic_warning(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "SHORTFPT.dbf",
        "KOD N(6,0); NOTATKA M",
        [{"KOD": 1, "NOTATKA": "x"}],
    )
    (tmp_path / "SHORTFPT.fpt").write_bytes(b"\x00" * 4)  # shorter than the 8-byte prefix
    schema = read_schema(dbf_path)
    assert schema.memo_companion_present is True
    assert schema.memo_companion_size_bytes == 4
    assert schema.memo_block_size is None
    # Exactly one warning: the unreadable 8-byte prefix.
    assert len(schema.warnings) == 1
    assert "FPT" in schema.warnings[0] and "8-byte" in schema.warnings[0]


def test_sub_512_fpt_file_is_structurally_suspicious(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "THINfpt.dbf",
        "KOD N(6,0); NOTATKA M",
        [{"KOD": 1, "NOTATKA": "x"}],
    )
    # 8-byte prefix readable (valid block size), but the full FPT header
    # record is 512 bytes: a shorter file is structurally suspicious.
    (tmp_path / "THINfpt.fpt").write_bytes(struct.pack(">LHH", 1, 0, 64) + b"\x00" * 56)
    schema = read_schema(dbf_path)
    assert schema.memo_companion_size_bytes == 64
    assert schema.memo_block_size == 64
    # Exactly one warning: the structurally suspicious length.
    assert len(schema.warnings) == 1
    assert "FPT" in schema.warnings[0] and "512" in schema.warnings[0]


def test_fpt_block_sizes_are_not_restricted_to_powers_of_two(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "BLOCKS.dbf", "KOD N(6,0); NOTATKA M", [{"KOD": 1, "NOTATKA": "x"}]
    )
    # SET BLOCKSIZE TO 0 stores 1; values 1-32 select 512-byte units and
    # values above 32 are plain byte sizes (e.g. 64, 96).  A full FPT header
    # record is 512 bytes; accepted sizes stay warning-free.
    for block_size in (1, 64, 96, 512, 4096, 16384):
        (tmp_path / "BLOCKS.fpt").write_bytes(struct.pack(">LHH", 5, 0, block_size) + b"\x00" * 504)
        schema = read_schema(dbf_path)
        assert schema.memo_block_size == block_size, block_size
        assert schema.memo_next_free_block == 5, block_size
        assert schema.warnings == (), (block_size, schema.warnings)


def test_fpt_block_size_zero_is_a_diagnostic_warning(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "ZEROBLOCK.dbf", "KOD N(6,0); NOTATKA M", [{"KOD": 1, "NOTATKA": "x"}]
    )
    (tmp_path / "ZEROBLOCK.fpt").write_bytes(struct.pack(">LHH", 1, 0, 0) + b"\x00" * 504)
    schema = read_schema(dbf_path)
    assert schema.memo_block_size == 0
    # Exactly one warning: the invalid block size.
    assert len(schema.warnings) == 1
    assert "block size 0" in schema.warnings[0]


def test_structural_cdx_flag_without_companion_warns(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(tmp_path / "CDX1.dbf", "KOD N(6,0)", [{"KOD": 1}])
    _patch_header_bytes(dbf_path, {28: b"\x01"})
    schema = read_schema(dbf_path)
    assert schema.has_structural_cdx is True
    assert schema.companion_cdx_present is False
    assert any("structural CDX" in w for w in schema.warnings)


def test_cdx_companion_without_flag_is_reported_but_not_flagged(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(tmp_path / "CDX2.dbf", "KOD N(6,0)", [{"KOD": 1}])
    (tmp_path / "CDX2.cdx").write_bytes(b"\x00" * 32)
    schema = read_schema(dbf_path)
    assert schema.has_structural_cdx is False
    assert schema.companion_cdx_present is True
    assert schema.companion_cdx_path is not None
    assert not any("structural CDX" in w for w in schema.warnings)


# ---------------------------------------------------------------------------
# typed I/O errors and JSON-safe error payloads
# ---------------------------------------------------------------------------


def test_scandir_failure_is_a_typed_io_error(tmp_path: Path, monkeypatch) -> None:
    dbf_path = _create_vfp_table(tmp_path / "IO.dbf", "KOD N(6,0)", [{"KOD": 1}])
    real_scandir = os.scandir

    def broken_scandir(path=None, *args, **kwargs):
        raise PermissionError(13, "access denied", str(path))

    monkeypatch.setattr(os, "scandir", broken_scandir)
    try:
        with pytest.raises(DbfIoError) as error:
            inspect_table(dbf_path)
    finally:
        monkeypatch.setattr(os, "scandir", real_scandir)
    assert error.value.code is ErrorCode.DBF_IO_ERROR
    assert error.value.path is not None
    _assert_json_safe(error.value.to_dict())


def test_forced_fpt_stat_failure_is_a_typed_io_error(tmp_path: Path, monkeypatch) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "IOSTAT.dbf", "KOD N(6,0); NOTATKA M", [{"KOD": 1, "NOTATKA": "x"}]
    )
    (tmp_path / "IOSTAT.fpt").write_bytes(struct.pack(">LHH", 1, 0, 64) + b"\x00" * 504)
    real_stat = Path.stat

    def broken_stat(self: Path, *args, **kwargs):
        if self.suffix.lower() == ".fpt":
            raise PermissionError(13, "access denied", str(self))
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", broken_stat)
    with pytest.raises(DbfIoError) as error:
        read_schema(dbf_path)
    assert error.value.code is ErrorCode.DBF_IO_ERROR
    # A typed DirectReadError, never the raw OSError.
    assert not isinstance(error.value, OSError)
    assert error.value.path is not None
    assert "IOSTAT.fpt" in error.value.path
    _assert_json_safe(error.value.to_dict())


def test_read_schema_opens_fpt_header_at_most_once(tmp_path: Path, monkeypatch) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "ONCE.dbf", "KOD N(6,0); NOTATKA M", [{"KOD": 1, "NOTATKA": "x"}]
    )
    fpt_path = tmp_path / "ONCE.fpt"
    fpt_path.write_bytes(struct.pack(">LHH", 1, 0, 64) + b"\x00" * 504)

    opened: list[str] = []
    real_open = Path.open

    def counting_open(self: Path, *args, **kwargs):
        if self.suffix.lower() == ".fpt":
            opened.append(str(self))
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counting_open)
    schema = read_schema(dbf_path)
    assert len(opened) == 1, opened  # one read_schema -> one FPT header read
    assert schema.memo_block_size == 64
    assert schema.warnings == ()


def test_error_to_dict_is_json_safe_with_hostile_context(tmp_path: Path) -> None:
    error = DbfIoError(
        "synthetic hostile context",
        path=tmp_path / "x.dbf",
        context={
            "raw": b"\xde\xad\xbe\xef",
            "codes": (ErrorCode.DBF_TRUNCATED, "a"),
            "path": Path("C:/temp/x.dbf"),
            "nested": {"t": (1, 2), "flag": True},
        },
    )
    payload = json.loads(json.dumps(error.to_dict(), ensure_ascii=False))
    assert payload["code"] == "DBF_IO_ERROR"
    assert payload["path"] == (tmp_path / "x.dbf").as_posix()
    assert payload["context"]["raw"] == "deadbeef"
    assert payload["context"]["codes"] == ["DBF_TRUNCATED", "a"]
    assert payload["context"]["path"] == "C:/temp/x.dbf"
    assert payload["context"]["nested"]["t"] == [1, 2]


# ---------------------------------------------------------------------------
# Mazovia export end-to-end (language driver 0x69)
# ---------------------------------------------------------------------------


def test_mazovia_ldid_table_exports_polish_characters(tmp_path: Path) -> None:
    from dbfbridge import export_dbf

    dbf_path = _create_vfp_table(
        tmp_path / "MAZ.dbf", "KOD N(6,0); NAZWA C(40)", [{"KOD": 1, "NAZWA": "x"}]
    )
    _patch_header_bytes(dbf_path, {29: b"\x69"})  # Mazovia language driver
    # Put a Mazovia-encoded byte (0x80 -> 'a') into NAZWA of the first record.
    data = bytearray(dbf_path.read_bytes())
    header_length = struct.unpack_from("<H", data, 8)[0]
    nazwa_offset = header_length + 1 + 6  # delete flag + KOD N(6)
    data[nazwa_offset] = 0x80
    dbf_path.write_bytes(bytes(data))

    output = tmp_path / "out"
    result = export_dbf(dbf_path, output, formats=("jsonl",), overwrite=True)
    result.raise_for_errors()

    jsonl_files = list(output.rglob("MAZ.jsonl"))
    assert len(jsonl_files) == 1
    line = next(
        line for line in jsonl_files[0].read_text(encoding="utf-8").splitlines() if line.strip()
    )
    record = json.loads(line)
    assert record["NAZWA"] == "\u0105"
    assert record["KOD"] in (1, "1")


# ---------------------------------------------------------------------------
# read-only guarantees
# ---------------------------------------------------------------------------


def test_inspection_creates_no_outputs_or_temp_files(sample_input_dir: Path) -> None:
    dbf_path = sample_input_dir / "klienci.dbf"
    before = _directory_snapshot(sample_input_dir)
    inspect_table(dbf_path)
    read_schema(dbf_path)
    assert _directory_snapshot(sample_input_dir) == before


def test_source_stays_byte_identical_with_same_mtime(sample_input_dir: Path) -> None:
    dbf_path = sample_input_dir / "zamowienia" / "zamowienia.dbf"
    sha_before = hashlib.sha256(dbf_path.read_bytes()).hexdigest()
    mtime_before = dbf_path.stat().st_mtime_ns

    inspect_table(dbf_path)
    read_schema(dbf_path)

    assert hashlib.sha256(dbf_path.read_bytes()).hexdigest() == sha_before
    assert dbf_path.stat().st_mtime_ns == mtime_before


def test_inspection_does_not_pass_over_the_record_area(sample_input_dir: Path) -> None:
    dbf_path = sample_input_dir / "zamowienia" / "zamowienia.dbf"
    original = dbf_path.read_bytes()
    info_before = inspect_table(dbf_path)

    data = bytearray(original)
    header_length = struct.unpack_from("<H", data, 8)[0]
    corrupted = min(64, len(data) - header_length)
    data[header_length : header_length + corrupted] = b"\xff" * corrupted
    dbf_path.write_bytes(bytes(data))
    try:
        # Corrupted record bytes must not matter: inspection reads the header only.
        info_after = inspect_table(dbf_path)
        assert info_after.record_count == info_before.record_count
        assert [f.name for f in info_after.fields] == [f.name for f in info_before.fields]
    finally:
        dbf_path.write_bytes(original)


# ---------------------------------------------------------------------------
# import side effects
# ---------------------------------------------------------------------------


def test_fresh_interpreter_import_has_no_side_effects() -> None:
    code = (
        "import codecs, sys\n"
        "import dbfbridge  # noqa: F401\n"
        "heavy = [m for m in ('polars', 'orjson', 'openpyxl', 'xlsxwriter', 'dbf')"
        " if m in sys.modules]\n"
        "assert not heavy, heavy\n"
        "try:\n"
        "    info = codecs.lookup('mazovia')\n"
        "    registered = info.encode is not None\n"
        "except LookupError:\n"
        "    registered = False\n"
        "assert not registered, 'Polish codec must not be pre-registered on import'\n"
        "print('CLEAN')\n"
    )
    env = dict(os.environ, PYTHONPATH=str(SRC_ROOT))
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "CLEAN" in result.stdout


def test_mazovia_codec_is_registered_on_demand() -> None:
    code = (
        "import codecs\n"
        "import dbfbridge\n"
        "from dbf_bridge.core.codecs import decode_with_polish_fallback, driver_to_encoding\n"
        "assert driver_to_encoding(0x69) == 'mazovia'\n"
        "try:\n"
        "    info = codecs.lookup('mazovia')\n"
        "    assert info.encode is not None\n"
        "except LookupError as exc:\n"
        "    raise AssertionError('mazovia codec must be registered on demand') from exc\n"
        "assert b'\\x80'.decode('mazovia') == '\\u0105'\n"
        "text, source = decode_with_polish_fallback(b'\\x80', 'ascii')\n"
        "assert source == 'cp1250'\n"
        "print('ON_DEMAND_OK')\n"
    )
    env = dict(os.environ, PYTHONPATH=str(SRC_ROOT))
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "ON_DEMAND_OK" in result.stdout


def test_existing_export_still_uses_the_shared_parser(
    sample_input_dir: Path, tmp_path: Path
) -> None:
    from dbf_bridge.exporter.config import make_config
    from dbf_bridge.exporter.discovery import discover_tables
    from dbf_bridge.exporter.reader import read_raw_header

    config = make_config(source=sample_input_dir, output=tmp_path / "out")
    discovered = next(
        item for item in discover_tables(sample_input_dir) if item.source_path.name == "klienci.dbf"
    )
    raw = read_raw_header(discovered.source_path, config)
    assert raw.header_bytes
    assert raw.encoding == "cp1250"
    assert [field.name for field in raw.fields] == [
        "ID_KL",
        "NAZWA",
        "EMAIL",
        "VIP",
        "NOTATKA",
    ]

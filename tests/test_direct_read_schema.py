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
    data = path.read_bytes()
    count = 0
    offset = 32
    while data[offset : offset + 1] not in (b"\r", b"\n"):
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
    for exc_type in (
        DbfPathError,
        DbfHeaderInvalidError,
        DbfTruncatedError,
        DbfFormatUnsupportedError,
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
    finally:
        fpt_path.write_bytes(backup)


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


def test_structural_cdx_flag_comes_from_the_header(sample_input_dir: Path) -> None:
    dbf_path = sample_input_dir / "zamowienia" / "zamowienia.dbf"
    assert inspect_table(dbf_path).has_structural_cdx is False
    _patch_header_bytes(dbf_path, {28: b"\x01"})
    try:
        assert inspect_table(dbf_path).has_structural_cdx is True
    finally:
        _patch_header_bytes(dbf_path, {28: b"\x00"})


def test_dbc_bound_comes_from_the_vfp_backlink_not_a_dbc_file(sample_input_dir: Path) -> None:
    dbf_path = sample_input_dir / "zamowienia" / "zamowienia.dbf"
    # No .dbc file exists next to the table: the flag must stay False.
    assert inspect_table(dbf_path).dbc_bound is False

    terminator_offset = 32 + 32 * _field_count(dbf_path) + 1
    backlink = dbf_path.read_bytes()[terminator_offset : terminator_offset + 2]
    try:
        _patch_header_bytes(dbf_path, {terminator_offset: b"\x07\x00"})
        assert inspect_table(dbf_path).dbc_bound is True
    finally:
        _patch_header_bytes(dbf_path, {terminator_offset: backlink})


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
    assert fields["DANE"].binary is True
    assert fields["DANE"].flags & 0x04
    assert fields["DANE"].supported is False
    assert fields["DANE"].unsupported_reason
    assert fields["TEKST"].supported is True
    assert fields["NOTATKA"].is_memo is True
    assert any("DANE" in warning for warning in info.warnings)


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

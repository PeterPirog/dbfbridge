"""Phase 1B tests: streaming direct record read (iter_records and friends).

Record-level integration tests against real DBF/FPT fixtures.  Edge cases are
produced by patching real fixture bytes or by controlled monkeypatching, so the
suite is portable across Windows and POSIX (no real permission changes).
"""

from __future__ import annotations

import builtins
import gc
import hashlib
import json
import os
import struct
import subprocess
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import dbf
import pytest

import dbf_bridge
import dbfbridge
from dbfbridge import (
    ArgumentInvalidError,
    DbfIoError,
    DbfRecordInvalidError,
    DbfTruncatedError,
    DirectRecord,
    ErrorCode,
    FieldProjectionInvalidError,
    FieldTypeUnsupportedError,
    FptInvalidError,
    FptRequiredMissingError,
    LazyMemoValue,
    TableSchema,
    TextDecodeError,
    iter_raw_records,
    iter_records,
    read_records,
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
    for suffix in (".dbf", ".fpt"):
        candidate = path.with_suffix(suffix)
        if candidate.exists():
            candidate.unlink()
    table = dbf.Table(str(path), field_specs=field_specs, dbf_type="vfp", codepage=codepage)
    table.open(mode=dbf.READ_WRITE)
    for record in records:
        table.append(record)
    table.close()
    return path


def _mark_deleted(dbf_path: Path, zero_based_index: int) -> None:
    """Flip one physical record's delete marker to ``0x2A`` in place."""
    data = bytearray(dbf_path.read_bytes())
    header_length = struct.unpack_from("<H", data, 8)[0]
    record_length = struct.unpack_from("<H", data, 10)[0]
    data[header_length + zero_based_index * record_length] = 0x2A
    dbf_path.write_bytes(bytes(data))


def _patch_field_type(dbf_path: Path, field_index: int, type_code: str) -> None:
    data = bytearray(dbf_path.read_bytes())
    data[32 + field_index * 32 + 11] = ord(type_code)
    dbf_path.write_bytes(bytes(data))


def _layout(dbf_path: Path) -> tuple[int, int, int]:
    data = dbf_path.read_bytes()
    header_length = struct.unpack_from("<H", data, 8)[0]
    record_length = struct.unpack_from("<H", data, 10)[0]
    record_count = int.from_bytes(data[4:8], "little")
    return header_length, record_length, record_count


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as infile:
        for chunk in iter(lambda: infile.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def _dir_snapshot(directory: Path) -> dict[str, int]:
    return {entry.name: entry.stat().st_size for entry in os.scandir(directory)}


def _forbid_fpt_open(monkeypatch, *, forbid: bool) -> None:
    real_open = Path.open

    def guarded_open(self: Path, *args, **kwargs):
        if forbid and Path(self).suffix.lower() == ".fpt":
            raise AssertionError("the memo companion must not be opened")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)


@pytest.fixture()
def memo_table(tmp_path: Path) -> Path:
    """cp1250 table with two active records and a memo field."""
    return _create_vfp_table(
        tmp_path / "MEMO.dbf",
        "KOD N(6,0); NAZWA C(30); NOTATKA M",
        [
            {"KOD": 1, "NAZWA": "ala", "NOTATKA": "notatka pierwsza"},
            {"KOD": 2, "NAZWA": "Żółć ąęł", "NOTATKA": "notatka druga ąę"},
        ],
    )


@pytest.fixture()
def deleted_table(memo_table: Path) -> Path:
    _mark_deleted(memo_table, 1)
    return memo_table


# ---------------------------------------------------------------------------
# public API surface
# ---------------------------------------------------------------------------


def test_new_public_symbols_are_exported_from_both_namespaces() -> None:
    names = (
        "iter_records",
        "read_records",
        "iter_raw_records",
        "DirectRecord",
        "RecordPage",
        "LazyMemoValue",
        "ArgumentInvalidError",
        "FieldProjectionInvalidError",
        "FieldTypeUnsupportedError",
        "FptRequiredMissingError",
        "FptInvalidError",
        "TextDecodeError",
        "DbfRecordInvalidError",
        "DbfTruncatedError",
    )
    for name in names:
        assert name in dbfbridge.__all__, name
        assert name in dbf_bridge.__all__, name
        assert getattr(dbfbridge, name) is getattr(dbf_bridge, name), name


def test_direct_read_models_are_immutable() -> None:
    import dataclasses

    for model in (DirectRecord, dbf_bridge.RecordPage, dbf_bridge.LazyMemoValue):
        assert dataclasses.is_dataclass(model)
        assert model.__dataclass_params__.frozen is True


def test_fresh_interpreter_record_import_has_no_side_effects() -> None:
    code = (
        "import sys\n"
        "from dbfbridge import iter_records, read_records, iter_raw_records\n"
        "heavy = [m for m in ('polars', 'orjson', 'openpyxl', 'xlsxwriter', 'dbf')"
        " if m in sys.modules]\n"
        "assert not heavy, heavy\n"
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


def test_new_error_codes_are_stable_machine_values() -> None:
    assert ErrorCode.ARGUMENT_INVALID.value == "ARGUMENT_INVALID"
    assert ErrorCode.FIELD_PROJECTION_INVALID.value == "FIELD_PROJECTION_INVALID"
    assert ErrorCode.FIELD_TYPE_UNSUPPORTED.value == "FIELD_TYPE_UNSUPPORTED"
    assert ErrorCode.FPT_REQUIRED_MISSING.value == "FPT_REQUIRED_MISSING"
    assert ErrorCode.FPT_INVALID.value == "FPT_INVALID"
    assert ErrorCode.TEXT_DECODE_ERROR.value == "TEXT_DECODE_ERROR"
    assert ErrorCode.DBF_RECORD_INVALID.value == "DBF_RECORD_INVALID"


# ---------------------------------------------------------------------------
# streaming semantics
# ---------------------------------------------------------------------------


def test_empty_dbf_streams_nothing(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(tmp_path / "EMPTY.dbf", "KOD N(6,0)", [])
    assert list(iter_records(dbf_path)) == []
    page = read_records(dbf_path, limit=10)
    assert page.records == ()
    assert page.scanned == 0
    assert page.next_offset is None
    assert page.exhausted is True
    assert list(iter_raw_records(dbf_path)) == []


def test_single_and_multiple_records_in_physical_order(tmp_path: Path) -> None:
    records = [
        {"KOD": i, "NAZWA": f"wiersz-{i}", "AKTYWNY": i % 2 == 0, "KWOTA": float(i)}
        for i in range(1, 13)
    ]
    dbf_path = _create_vfp_table(
        tmp_path / "MANY.dbf", "KOD N(6,0); NAZWA C(20); AKTYWNY L; KWOTA N(8,2)", records
    )
    streamed = list(iter_records(dbf_path))
    assert [record.physical_index for record in streamed] == list(range(12))
    assert [record.values["KOD"] for record in streamed] == list(range(1, 13))
    assert all(record.deleted is False for record in streamed)
    assert all(record.raw_record is None for record in streamed)
    assert streamed[0].values["NAZWA"] == "wiersz-1"
    assert streamed[11].values["NAZWA"] == "wiersz-12"


def test_active_and_deleted_in_physical_order_one_pass(deleted_table: Path) -> None:
    active = list(iter_records(deleted_table, include_deleted=False))
    everything = list(iter_records(deleted_table, include_deleted=True))
    assert [record.physical_index for record in everything] == [0, 1]
    assert [record.deleted for record in everything] == [False, True]
    assert [record.physical_index for record in active] == [0]
    assert [record.values["KOD"] for record in active] == [1]


def test_read_records_walks_physical_pages(tmp_path: Path) -> None:
    records = [{"KOD": i, "NAZWA": f"n{i}"} for i in range(10)]
    dbf_path = _create_vfp_table(tmp_path / "PAGES.dbf", "KOD N(6,0); NAZWA C(10)", records)
    collected: list[DirectRecord] = []
    offset = 0
    pages = 0
    while True:
        page = read_records(dbf_path, offset=offset, limit=3)
        pages += 1
        assert page.offset == offset
        assert page.limit == 3
        collected.extend(page.records)
        if page.exhausted:
            assert page.next_offset is None
            break
        assert page.next_offset == offset + len(page.records)
        offset = page.next_offset  # next_offset is a physical record index
    assert pages == 4
    assert [record.values["KOD"] for record in collected] == list(range(10))


def test_include_deleted_paging_skips_deleted_in_same_pass(deleted_table: Path) -> None:
    page = read_records(deleted_table, offset=0, limit=1, include_deleted=False)
    assert [record.physical_index for record in page.records] == [0]
    # The page filled before reaching the deleted record...
    assert page.scanned == 1
    assert page.next_offset == 1
    assert page.exhausted is False
    # ...and the next page skips the deleted record in the same pass.
    page2 = read_records(deleted_table, offset=1, limit=2, include_deleted=False)
    assert page2.records == ()
    assert page2.scanned == 1  # deleted record scanned without parsing
    assert page2.exhausted is True
    assert page2.next_offset is None

    page_all = read_records(deleted_table, offset=0, limit=5, include_deleted=True)
    assert [record.deleted for record in page_all.records] == [False, True]
    assert page_all.next_offset is None
    assert page_all.exhausted is True


def test_limit_does_not_materialize_further_records(tmp_path: Path, monkeypatch) -> None:
    from dbfread.field_parser import FieldParser

    dbf_path = _create_vfp_table(
        tmp_path / "LIMIT.dbf", "KOD N(6,0)", [{"KOD": i} for i in range(8)]
    )
    parse_calls: list[int] = []
    real_parse_n = FieldParser.parseN

    def counting_parse_n(self, field, data):
        parse_calls.append(1)
        return real_parse_n(self, field, data)

    monkeypatch.setattr(FieldParser, "parseN", counting_parse_n)
    try:
        page = read_records(dbf_path, offset=0, limit=2)
    finally:
        monkeypatch.setattr(FieldParser, "parseN", real_parse_n)
    assert page.scanned == 2
    assert len(parse_calls) == 2, "records beyond the page must not be parsed"


# ---------------------------------------------------------------------------
# field projection
# ---------------------------------------------------------------------------


def test_projection_uses_schema_names_in_user_order(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "PROJ.dbf", "NAZWA C(10); KOD N(4,0)", [{"NAZWA": "x", "KOD": 7}]
    )
    it = iter_records(dbf_path, fields=["kod", "Nazwa"])
    record = next(it)
    assert list(record.values.keys()) == ["KOD", "NAZWA"]
    assert record.values == {"KOD": 7, "NAZWA": "x"}


def test_projection_really_skips_the_parser_for_unselected_fields(
    tmp_path: Path, monkeypatch
) -> None:
    from dbfread.field_parser import FieldParser

    dbf_path = _create_vfp_table(
        tmp_path / "SKIP.dbf",
        "KOD N(4,0); NAZWA C(10)",
        [{"KOD": 1, "NAZWA": "x"}, {"KOD": 2, "NAZWA": "y"}],
    )
    real_parse_n = FieldParser.parseN

    def forbidden_parse_n(self, field, data):
        raise AssertionError(f"unselected field {field.name!r} must not be parsed")

    monkeypatch.setattr(FieldParser, "parseN", forbidden_parse_n)
    try:
        values = [record.values for record in iter_records(dbf_path, fields=["NAZWA"])]
    finally:
        monkeypatch.setattr(FieldParser, "parseN", real_parse_n)
    assert values == [{"NAZWA": "x"}, {"NAZWA": "y"}]


def test_unknown_duplicate_and_string_projections_are_typed_errors(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(tmp_path / "BADF.dbf", "KOD N(4,0)", [{"KOD": 1}])

    with pytest.raises(FieldProjectionInvalidError) as unknown:
        next(iter_records(dbf_path, fields=["NOSUCH"]))
    assert unknown.value.code is ErrorCode.FIELD_PROJECTION_INVALID
    assert unknown.value.context["unknown"] == ["NOSUCH"]
    assert "KOD" in unknown.value.context["available"]
    _assert_json_safe(unknown.value.to_dict())

    with pytest.raises(FieldProjectionInvalidError) as duplicate:
        next(iter_records(dbf_path, fields=["kod", "KOD"]))
    assert duplicate.value.context["duplicates"] == ["KOD"]

    with pytest.raises(ArgumentInvalidError):
        next(iter_records(dbf_path, fields="KOD"))


def test_unselected_unsupported_field_does_not_block_reading(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "UNSUP.dbf", "KOD N(4,0); DANE C(10)", [{"KOD": 1, "DANE": "x"}]
    )
    _patch_field_type(dbf_path, 1, "Q")  # VFP Varbinary: unsupported for decode

    records = list(iter_records(dbf_path, fields=["KOD"]))
    assert [record.values["KOD"] for record in records] == [1]

    with pytest.raises(FieldTypeUnsupportedError):
        next(iter_records(dbf_path, fields=["DANE"]))
    with pytest.raises(FieldTypeUnsupportedError):
        next(iter_records(dbf_path))  # implicit projection selects every field


# ---------------------------------------------------------------------------
# supported VFP types, NULL/empty values, codepages
# ---------------------------------------------------------------------------


def test_supported_vfp_types_null_and_empty_values(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "TYPES.dbf",
        "NM N(4,0); N8 N(8,0); NF F(10,2); ND D; NT T; NY Y; NL L",
        [
            {
                "NM": 7,
                "N8": 12,
                "NF": 1.5,
                "ND": date(2026, 8, 31),
                "NT": datetime(2026, 8, 31, 12, 30, 45),
                "NY": 19.99,
                "NL": True,
            },
            {
                "NM": None,
                "N8": None,
                "NF": None,
                "ND": None,
                "NT": None,
                "NY": None,
                "NL": None,
            },
        ],
    )
    # Rewrite the first record with genuine VFP payloads and type codes:
    # NM -> Integer (4-byte LE int), N8 -> Double (8-byte IEEE), keeping the
    # ASCII floats, dates, and the currency/logical fields intact.
    _patch_field_type(dbf_path, 0, "I")
    _patch_field_type(dbf_path, 1, "B")
    data = bytearray(dbf_path.read_bytes())
    header_length, _record_length, _count = _layout(dbf_path)
    record_start = header_length
    data[record_start + 1 : record_start + 5] = struct.pack("<i", 7)
    data[record_start + 5 : record_start + 13] = struct.pack("<d", 12.0)
    dbf_path.write_bytes(bytes(data))

    first, second = list(iter_records(dbf_path))
    assert first.values["NM"] == 7  # Integer
    assert first.values["N8"] == 12.0  # Double
    assert first.values["NF"] == 1.5  # Float
    assert first.values["ND"] == date(2026, 8, 31)
    assert first.values["NT"] == datetime(2026, 8, 31, 12, 30, 45)
    assert isinstance(first.values["NY"], Decimal)
    assert format(first.values["NY"], "f") == "19.99"
    assert first.values["NL"] is True

    assert second.values["ND"] is None
    assert second.values["NT"] is None
    assert isinstance(second.values["NY"], Decimal)  # currency zeros stay a number
    assert second.values["NL"] is None


@pytest.mark.parametrize("ldid,codec", ((0xC8, "cp1250"), (0x23, "cp852")))
def test_polish_codepages_keep_diacritics(tmp_path: Path, ldid: int, codec: str) -> None:
    from dbf_bridge.core.codecs import driver_to_encoding

    assert driver_to_encoding(ldid) == codec
    dbf_path = _create_vfp_table(
        tmp_path / f"ENC{ldid}.dbf",
        "NAZWA C(30)",
        [{"NAZWA": "Żółw ąęłóńśćźż"}],
        codepage=ldid,
    )
    record = next(iter(iter_records(dbf_path)))
    assert record.values["NAZWA"] == "Żółw ąęłóńśćźż"


def test_mazovia_language_driver_decodes_records(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "MAZ.dbf", "NAZWA C(30)", [{"NAZWA": "x"}], codepage=0x01
    )
    data = bytearray(dbf_path.read_bytes())
    data[29] = 0x69  # Mazovia language driver byte
    header_length = struct.unpack_from("<H", data, 8)[0]
    # NAZWA (first field after the delete marker): Mazovia 'a' + 'x'.
    data[header_length + 1] = 0x80
    data[header_length + 2] = ord("x")
    dbf_path.write_bytes(bytes(data))

    record = next(iter(iter_records(dbf_path)))
    assert record.values["NAZWA"] == "ąx"


def test_decode_errors_policies_strict_replace_ignore(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(tmp_path / "DEC.dbf", "NAZWA C(20)", [{"NAZWA": "ok"}])
    # Append cp1250-undefined bytes (0x81/0x83/0x88) right after the 'ok'
    # payload inside the character field.
    data = bytearray(dbf_path.read_bytes())
    header_length, _record_length, _count = _layout(dbf_path)
    data[header_length + 3 : header_length + 6] = b"\x81\x83\x88"
    dbf_path.write_bytes(bytes(data))

    with pytest.raises(TextDecodeError) as strict:
        next(iter_records(dbf_path, decode_errors="strict"))
    assert strict.value.code is ErrorCode.TEXT_DECODE_ERROR
    assert strict.value.context["field"] == "NAZWA"
    assert not isinstance(strict.value, UnicodeDecodeError)
    _assert_json_safe(strict.value.to_dict())

    replaced = next(iter(iter_records(dbf_path, decode_errors="replace")))
    assert replaced.values["NAZWA"] == "ok\ufffd\ufffd\ufffd"

    ignored = next(iter(iter_records(dbf_path, decode_errors="ignore")))
    assert ignored.values["NAZWA"] == "ok"


# ---------------------------------------------------------------------------
# memo policies
# ---------------------------------------------------------------------------


def test_memo_skip_and_null_do_not_touch_the_fpt(memo_table: Path, monkeypatch) -> None:
    real_open = Path.open

    def forbidden_fpt_open(self: Path, *args, **kwargs):
        if Path(self).suffix.lower() == ".fpt":
            raise AssertionError("skip/null must not open the memo companion")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", forbidden_fpt_open)
    try:
        skipped = list(iter_records(memo_table, memo="skip"))
        nulled = list(iter_records(memo_table, memo="null"))
    finally:
        monkeypatch.setattr(Path, "open", real_open)
    assert [record.values["KOD"] for record in skipped] == [1, 2]
    assert all("NOTATKA" not in record.values for record in skipped)
    assert [record.values["KOD"] for record in nulled] == [1, 2]
    assert all(record.values["NOTATKA"] is None for record in nulled)


def test_lazy_memo_values_never_open_fpt_during_iteration(memo_table: Path, monkeypatch) -> None:
    real_open = Path.open

    def forbidden_fpt_open(self: Path, *args, **kwargs):
        if Path(self).suffix.lower() == ".fpt":
            raise AssertionError("lazy iteration must not open the memo companion")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", forbidden_fpt_open)
    try:
        records = list(iter_records(memo_table, memo="lazy"))
    finally:
        monkeypatch.setattr(Path, "open", real_open)

    lazy_values = [record.values["NOTATKA"] for record in records]
    assert all(isinstance(value, LazyMemoValue) for value in lazy_values)
    payload = json.dumps([value.to_dict() for value in lazy_values])
    decoded = json.loads(payload)
    assert decoded[0]["field"] == "NOTATKA"
    assert decoded[0]["memo_format"] == "FPT"
    assert decoded[0]["block"] > 0


def test_lazy_load_and_inline_match_the_exporter(memo_table: Path, tmp_path: Path) -> None:
    lazy = [record.values["NOTATKA"] for record in iter_records(memo_table, memo="lazy")]
    inline = [record.values["NOTATKA"] for record in iter_records(memo_table, memo="inline")]
    loaded = [value.load() if isinstance(value, LazyMemoValue) else value for value in lazy]
    assert loaded == inline
    assert inline[1] == "notatka druga ąę"

    output = tmp_path / "exported"
    result = dbfbridge.export_dbf(
        str(memo_table), str(output), formats=("jsonl",), memo="inline", overwrite=True
    )
    result.raise_for_errors()
    jsonl = next(output.rglob("*MEMO.jsonl"))
    exported = [
        json.loads(line)["NOTATKA"]
        for line in jsonl.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert exported == inline


def test_lazy_to_dict_does_not_read(memo_table: Path, monkeypatch) -> None:
    real_open = Path.open

    def forbidden_fpt_open(self: Path, *args, **kwargs):
        if Path(self).suffix.lower() == ".fpt":
            raise AssertionError("to_dict() must not read the memo payload")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", forbidden_fpt_open)
    try:
        lazy = next(iter(iter_records(memo_table, memo="lazy"))).values["NOTATKA"]
        _assert_json_safe(lazy.to_dict())
        json.dumps(lazy.to_dict())
    finally:
        monkeypatch.setattr(Path, "open", real_open)


def test_missing_fpt_inline_is_fpt_required_missing(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "NOFPT.dbf", "KOD N(4,0); NOTATKA M", [{"KOD": 1, "NOTATKA": "x"}]
    )
    (tmp_path / "NOFPT.fpt").unlink()
    with pytest.raises(FptRequiredMissingError) as error:
        next(iter_records(dbf_path, memo="inline"))
    assert error.value.code is ErrorCode.FPT_REQUIRED_MISSING
    _assert_json_safe(error.value.to_dict())


def test_lazy_load_missing_companion_is_typed(memo_table: Path) -> None:
    fpt_path = memo_table.with_suffix(".fpt")
    backup = fpt_path.read_bytes()
    fpt_path.unlink()
    try:
        records = list(iter_records(memo_table, memo="lazy"))
        lazy = records[0].values["NOTATKA"]
        assert isinstance(lazy, LazyMemoValue)
        with pytest.raises(FptRequiredMissingError) as error:
            lazy.load()
        assert error.value.code is ErrorCode.FPT_REQUIRED_MISSING
        _assert_json_safe(error.value.to_dict())
    finally:
        fpt_path.write_bytes(backup)


def test_broken_fpt_block_size_zero_is_fpt_invalid(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "BADFPT.dbf", "KOD N(4,0); NOTATKA M", [{"KOD": 1, "NOTATKA": "x"}]
    )
    fpt_path = tmp_path / "BADFPT.fpt"
    fpt_data = bytearray(fpt_path.read_bytes())
    fpt_data[6:8] = b"\x00\x00"  # invalid block size 0
    fpt_path.write_bytes(bytes(fpt_data))

    with pytest.raises(FptInvalidError) as error:
        next(iter_records(dbf_path, memo="inline"))
    assert error.value.code is ErrorCode.FPT_INVALID
    _assert_json_safe(error.value.to_dict())


def test_truncated_memo_payload_is_fpt_invalid(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "CUTFPT.dbf",
        "KOD N(4,0); NOTATKA M",
        [{"KOD": 1, "NOTATKA": "dłuższy opis memo"}],
    )
    fpt_path = tmp_path / "CUTFPT.fpt"
    full = fpt_path.read_bytes()
    # Keep an 8-byte prefix that reads fine, cut the payload blocks away.
    fpt_path.write_bytes(full[:12])

    # The inline path reads through the same typed error boundary.
    with pytest.raises(FptInvalidError) as error:
        next(iter_records(dbf_path, memo="inline"))
    assert error.value.code is ErrorCode.FPT_INVALID
    _assert_json_safe(error.value.to_dict())


def test_binary_memo_inline_returns_bytes(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "BINMEMO.dbf", "KOD N(4,0); ZALACZNIK M", [{"KOD": 1, "ZALACZNIK": "x"}]
    )
    _patch_field_type(dbf_path, 1, "G")  # General: binary memo (dbfread keeps bytes)

    record = next(iter(iter_records(dbf_path, memo="inline")))
    binary_value = record.values["ZALACZNIK"]
    assert isinstance(binary_value, (bytes, bytearray))
    payload = record.to_dict()
    _assert_json_safe(payload)
    assert payload["values"]["ZALACZNIK"] == json.loads(json.dumps(payload))["values"]["ZALACZNIK"]


# ---------------------------------------------------------------------------
# raw split
# ---------------------------------------------------------------------------


def test_raw_false_stores_no_raw_data(memo_table: Path) -> None:
    records = list(iter_records(memo_table, memo="skip", raw=False))
    assert all(record.raw_record is None for record in records)
    for payload in (record.to_dict() for record in records):
        _assert_json_safe(payload)
        assert "raw_record" not in json.dumps(payload)


def test_raw_true_keeps_exact_physical_bytes(memo_table: Path) -> None:
    data = memo_table.read_bytes()
    header_length, record_length, _count = _layout(memo_table)
    for record in iter_records(memo_table, raw=True, memo="skip"):
        start = header_length + record.physical_index * record_length
        assert record.raw_record == data[start : start + record_length]
    assert next(iter(iter_raw_records(memo_table))).raw_record is not None


def test_iter_raw_records_returns_everything_in_physical_order(
    deleted_table: Path, monkeypatch
) -> None:
    real_open = Path.open

    def forbidden_fpt_open(self: Path, *args, **kwargs):
        if Path(self).suffix.lower() == ".fpt":
            raise AssertionError("iter_raw_records must not open the memo companion")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", forbidden_fpt_open)
    try:
        raws = list(iter_raw_records(deleted_table))
    finally:
        monkeypatch.setattr(Path, "open", real_open)
    assert [(record.physical_index, record.deleted) for record in raws] == [(0, False), (1, True)]
    assert all(record.raw_record is not None for record in raws)
    # Memo fields are not decoded here: the raw image carries them.
    assert "NOTATKA" not in raws[0].values
    assert raws[0].values["NAZWA"] == "ala"


def test_iter_raw_records_bytes_match_the_file(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(tmp_path / "RAW.dbf", "KOD N(6,0)", [{"KOD": i} for i in range(5)])
    data = dbf_path.read_bytes()
    header_length, record_length, _count = _layout(dbf_path)
    for record in iter_raw_records(dbf_path):
        start = header_length + record.physical_index * record_length
        assert record.raw_record == data[start : start + record_length]


# ---------------------------------------------------------------------------
# damaged streams and typed I/O errors
# ---------------------------------------------------------------------------


def test_invalid_record_marker_is_a_typed_error(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "MARKER.dbf", "KOD N(6,0)", [{"KOD": i} for i in range(3)]
    )
    data = bytearray(dbf_path.read_bytes())
    header_length, record_length, _count = _layout(dbf_path)
    data[header_length + record_length] = 0x07  # neither active nor deleted nor EOF
    dbf_path.write_bytes(bytes(data))

    stream = iter_records(dbf_path)
    assert next(stream).physical_index == 0
    with pytest.raises(DbfRecordInvalidError) as error:
        next(stream)
    assert error.value.code is ErrorCode.DBF_RECORD_INVALID
    assert error.value.context["record_index"] == 1
    _assert_json_safe(error.value.to_dict())


def test_truncated_record_during_iteration_is_a_typed_error(tmp_path: Path, monkeypatch) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "TRUNC.dbf", "KOD N(6,0)", [{"KOD": i} for i in range(3)]
    )
    real_open = builtins.open
    header_length, record_length, record_count = _layout(dbf_path)
    cut_pos = header_length + record_length * (record_count - 1) + record_length - 2

    class _ShortTailReader:
        """Wraps the real DBF handle; the last field read comes up short."""

        def __init__(self, handle: Any) -> None:
            self._handle = handle
            self._short = False

        def read(self, size: int = -1):
            if size > 0 and not self._short and self._handle.tell() + size >= cut_pos:
                self._short = True
                return self._handle.read(max(0, cut_pos - self._handle.tell()))
            return self._handle.read(size)

        def seek(self, *args, **kwargs):
            return self._handle.seek(*args, **kwargs)

        def tell(self) -> int:
            return self._handle.tell()

        def close(self) -> None:
            self._handle.close()

        def __enter__(self) -> _ShortTailReader:
            return self

        def __exit__(self, *args) -> bool:
            self._handle.close()
            return False

    def cutting_open(file, mode="r", *args, **kwargs):
        handle = real_open(file, mode, *args, **kwargs)
        if str(file).lower().endswith(".dbf") and "b" in mode:
            return _ShortTailReader(handle)
        return handle

    monkeypatch.setattr(builtins, "open", cutting_open)
    try:
        stream = iter_records(dbf_path)
        assert next(stream).physical_index == 0
        assert next(stream).physical_index == 1
        with pytest.raises(DbfTruncatedError) as error:
            next(stream)  # the last record's field read comes up short
    finally:
        monkeypatch.setattr(builtins, "open", real_open)
    assert error.value.code is ErrorCode.DBF_TRUNCATED
    assert error.value.context["record_index"] == 2
    _assert_json_safe(error.value.to_dict())


def test_typed_io_error_when_open_fails_during_iteration(tmp_path: Path, monkeypatch) -> None:
    dbf_path = _create_vfp_table(tmp_path / "IO2.dbf", "KOD N(4,0)", [{"KOD": 1}])
    real_open = Path.open

    def broken_open(self: Path, *args, **kwargs):
        if Path(self).suffix.lower() == ".dbf":
            raise PermissionError(13, "access denied", str(self))
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", broken_open)
    with pytest.raises(DbfIoError) as error:
        next(iter_records(dbf_path))
    monkeypatch.undo()
    assert error.value.code is ErrorCode.DBF_IO_ERROR
    assert not isinstance(error.value, OSError)
    _assert_json_safe(error.value.to_dict())


# ---------------------------------------------------------------------------
# resource guarantees
# ---------------------------------------------------------------------------


def test_early_break_releases_handles_on_windows(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "BREAK.dbf", "KOD N(4,0)", [{"KOD": i} for i in range(5)]
    )
    stream = iter_records(dbf_path)
    first = next(stream)
    assert first.physical_index == 0
    stream.close()  # explicit close() releases the DBF handle

    # On Windows the file can be moved only after the handle is gone.
    renamed = tmp_path / "BREAK-moved.dbf"
    os.replace(dbf_path, renamed)
    assert renamed.is_file()
    os.replace(renamed, dbf_path)


def test_del_and_gc_close_the_generator(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(tmp_path / "GC.dbf", "KOD N(4,0)", [{"KOD": i} for i in range(4)])
    stream = iter_records(dbf_path)
    next(stream)
    del stream
    gc.collect()
    moved = tmp_path / "GC-moved.dbf"
    os.replace(dbf_path, moved)
    os.replace(moved, dbf_path)


def test_source_byte_identical_and_no_files_created(memo_table: Path) -> None:
    dbf_sha = _sha256(memo_table)
    fpt_sha = _sha256(memo_table.with_suffix(".fpt"))
    before = _dir_snapshot(memo_table.parent)

    list(iter_records(memo_table, memo="lazy"))
    list(iter_records(memo_table, memo="inline", raw=True))
    list(iter_raw_records(memo_table))

    assert _sha256(memo_table) == dbf_sha
    assert _sha256(memo_table.with_suffix(".fpt")) == fpt_sha
    assert _dir_snapshot(memo_table.parent) == before


# ---------------------------------------------------------------------------
# JSON safety and exporter parity
# ---------------------------------------------------------------------------


def test_public_to_dict_payloads_are_json_safe(memo_table: Path) -> None:
    page = read_records(memo_table, limit=2)
    for record in page.records:
        _assert_json_safe(record.to_dict())
    _assert_json_safe(page.to_dict())
    json.dumps(page.to_dict())
    json.dumps(next(iter(iter_records(memo_table, raw=True))).to_dict())


def test_exporter_parity_klienci_memo(sample_input_dir: Path, tmp_path: Path) -> None:
    from dbfbridge import export_dbf

    dbf_path = sample_input_dir / "klienci.dbf"
    output = tmp_path / "exported"
    result = export_dbf(
        str(dbf_path), str(output), formats=("jsonl",), memo="inline", overwrite=True
    )
    result.raise_for_errors()
    jsonl = next(output.rglob("klienci.jsonl"))
    exported = [
        json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    fields = ["ID_KL", "NAZWA", "EMAIL", "VIP", "NOTATKA"]
    direct = list(iter_records(dbf_path, fields=fields, memo="inline"))
    assert len(direct) == len(exported)
    for record, exported_values in zip(direct, exported, strict=True):
        for name in fields:
            assert record.values[name] == exported_values[name]


def test_exporter_parity_zamowienia_types(sample_input_dir: Path, tmp_path: Path) -> None:
    from dbfbridge import export_dbf

    dbf_path = sample_input_dir / "zamowienia" / "zamowienia.dbf"
    output = tmp_path / "exported"
    result = export_dbf(str(dbf_path), str(output), formats=("jsonl",), overwrite=True)
    result.raise_for_errors()
    jsonl = next(output.rglob("zamowienia.jsonl"))
    exported = [
        json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    fields = ["ID_ZAM", "KWOTA", "DATA_ZAM", "STATUS"]
    direct = list(iter_records(dbf_path, fields=fields, memo="lazy"))
    assert len(direct) == len(exported)
    for record, exported_values in zip(direct, exported, strict=True):
        assert record.values["ID_ZAM"] == exported_values["ID_ZAM"]
        assert str(record.values["KWOTA"]) == str(exported_values["KWOTA"])
        assert record.values["DATA_ZAM"].isoformat() == exported_values["DATA_ZAM"]
        assert record.values["STATUS"] == exported_values["STATUS"]


# ---------------------------------------------------------------------------
# argument validation and physical offset semantics
# ---------------------------------------------------------------------------


def test_read_records_argument_validation(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(tmp_path / "ARGS.dbf", "KOD N(4,0)", [{"KOD": 1}])
    for extra in (
        {"offset": -1},
        {"offset": 0.5},
        {"limit": 0},
        {"limit": -3},
        {"limit": True},
    ):
        with pytest.raises(ArgumentInvalidError) as error:
            read_records(dbf_path, **extra)
        assert error.value.code is ErrorCode.ARGUMENT_INVALID
        _assert_json_safe(error.value.to_dict())

    with pytest.raises(ArgumentInvalidError):
        next(iter_records(dbf_path, memo="nonsense"))
    with pytest.raises(ArgumentInvalidError):
        next(iter_records(dbf_path, decode_errors="bogus"))
    with pytest.raises(ArgumentInvalidError):
        next(iter_records(dbf_path, encoding=7))


def test_offset_beyond_end_is_exhausted_and_empty(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(tmp_path / "OFF.dbf", "KOD N(4,0)", [{"KOD": 1}])
    page = read_records(dbf_path, offset=42, limit=10)
    assert page.records == ()
    assert page.scanned == 0
    assert page.exhausted is True
    assert page.next_offset is None


def test_offset_is_physical_not_active(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "PHYS.dbf", "KOD N(4,0)", [{"KOD": i} for i in range(4)]
    )
    _mark_deleted(dbf_path, 0)
    # offset=1 means the second PHYSICAL record; record 0 (deleted) is skipped
    # by the seek without parsing, and the page streams the active remainder.
    page = read_records(dbf_path, offset=1, limit=10, include_deleted=False)
    assert [record.physical_index for record in page.records] == [1, 2, 3]
    assert all(record.values["KOD"] == index + 1 for index, record in enumerate(page.records))
    assert page.scanned == 3
    assert page.exhausted is True

    page_all = read_records(dbf_path, offset=0, limit=10, include_deleted=True)
    assert [(record.physical_index, record.deleted) for record in page_all.records] == [
        (0, True),
        (1, False),
        (2, False),
        (3, False),
    ]


def test_direct_read_matches_schema_headers(sample_input_dir: Path) -> None:
    dbf_path = sample_input_dir / "zamowienia" / "zamowienza.dbf"
    dbf_path = sample_input_dir / "zamowienia" / "zamowienia.dbf"
    schema = read_schema(dbf_path)
    assert isinstance(schema, TableSchema)
    page = read_records(dbf_path, offset=48, limit=2, fields=["ID_ZAM"])
    assert [record.physical_index for record in page.records] == [48, 49]
    assert page.exhausted is True
    assert page.scanned == 2
    assert page.records[0].values["ID_ZAM"] == 10000 + 49

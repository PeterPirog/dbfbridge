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
from dbf_bridge import export_dbf
from dbfbridge import (
    ArgumentInvalidError,
    CancellationCheck,
    DbfHeaderInvalidError,
    DbfIoError,
    DbfPathError,
    DbfRecordInvalidError,
    DbfTruncatedError,
    DirectRecord,
    ErrorCode,
    FieldProjectionInvalidError,
    FieldTypeUnsupportedError,
    FptInvalidError,
    FptRequiredMissingError,
    LazyMemoValue,
    ProgressCallback,
    ProgressEvent,
    ReadCancelledError,
    RecordPage,
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


def test_empty_table_progress_on_every_entry_point(tmp_path: Path) -> None:
    """Empty tables emit exactly one final progress event, never a KeyError."""
    dbf_path = _create_vfp_table(tmp_path / "EMPTY_P.dbf", "KOD N(6,0)", [])

    events: list[ProgressEvent] = []
    assert list(iter_records(dbf_path, progress=events.append)) == []
    assert len(events) == 1
    assert (events[-1].current, events[-1].total, events[-1].records) == (0, 0, 0)
    assert events[-1].message == "completed"

    raw_events: list[ProgressEvent] = []
    assert list(iter_raw_records(dbf_path, progress=raw_events.append)) == []
    assert len(raw_events) == 1
    assert (raw_events[-1].current, raw_events[-1].total, raw_events[-1].records) == (0, 0, 0)
    assert raw_events[-1].message == "completed"

    page_events: list[ProgressEvent] = []
    page = read_records(dbf_path, limit=5, progress=page_events.append)
    assert page.records == () and page.exhausted is True
    assert len(page_events) == 1  # exactly one final event — no duplicates
    assert page_events[-1].message == "page completed"
    assert (page_events[-1].current, page_events[-1].total, page_events[-1].records) == (0, 0, 0)


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


# ---------------------------------------------------------------------------
# projection x memo dependency (hardened contracts)
# ---------------------------------------------------------------------------


def test_inline_without_memo_fields_needs_no_fpt(tmp_path: Path, monkeypatch) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "NOFPT2.dbf", "KOD N(4,0); NAZWA C(20); NOTATKA M", [{"KOD": 1, "NAZWA": "x"}]
    )
    # The companion is genuinely missing; the projection only selects
    # non-memo fields, so memo="inline" must not require or open the FPT.
    (tmp_path / "NOFPT2.fpt").unlink()
    real_open = Path.open

    def forbidden_fpt_open(self: Path, *args, **kwargs):
        if Path(self).suffix.lower() == ".fpt":
            raise AssertionError("inline without memo fields must not open the FPT")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", forbidden_fpt_open)
    try:
        page = read_records(dbf_path, limit=5, fields=["KOD", "NAZWA"], memo="inline")
    finally:
        monkeypatch.setattr(Path, "open", real_open)
    assert page.records[0].values == {"KOD": 1, "NAZWA": "x"}
    assert "NOTATKA" not in page.records[0].values


def test_inline_race_missing_fpt_after_eager_check_is_typed(memo_table: Path) -> None:
    # The eager companion check passes at iter_records() time; the companion
    # then vanishes before the first next(): the race must raise
    # FPT_REQUIRED_MISSING (never a silent null read).
    stream = iter_records(memo_table, memo="inline")
    memo_table.with_suffix(".fpt").unlink()
    with pytest.raises(FptRequiredMissingError) as error:
        next(stream)
    assert error.value.code is ErrorCode.FPT_REQUIRED_MISSING
    assert error.value.context["policy"] == "inline"
    _assert_json_safe(error.value.to_dict())


def test_skip_removes_unsupported_memo_field(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "WFIELD.dbf",
        "KOD N(4,0); BLOTEK C(10); NOTATKA M",
        [{"KOD": 1, "BLOTEK": "x", "NOTATKA": "memo"}],
    )
    # VFP Blob: an unsupported memo pointer field that lives in the FPT.
    _patch_field_type(dbf_path, 2, "W")

    # memo="skip" removes memo fields from the effective projection BEFORE
    # the supported-type validation, and memo="skip" needs no FPT.
    with_fpt = tmp_path / "WFIELD.fpt"
    fpt_backup = with_fpt.read_bytes()
    with_fpt.unlink()
    try:
        records = list(iter_records(dbf_path, memo="skip", fields=None))
    finally:
        with_fpt.write_bytes(fpt_backup)
    assert [record.values["KOD"] for record in records] == [1]
    assert "NOTATKA" not in records[0].values

    # A really selected unsupported field still raises the typed error.
    with pytest.raises(FieldTypeUnsupportedError):
        next(iter_records(dbf_path, memo="lazy", fields=["NOTATKA"]))
    with pytest.raises(FieldTypeUnsupportedError):
        next(iter_records(dbf_path, memo="null", fields=None))


def test_null_memo_field_is_none_without_fpt_read(memo_table: Path, monkeypatch) -> None:
    real_open = Path.open

    def forbidden_fpt_open(self: Path, *args, **kwargs):
        if Path(self).suffix.lower() == ".fpt":
            raise AssertionError("memo=null must not open the FPT")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", forbidden_fpt_open)
    try:
        records = list(iter_records(memo_table, memo="null", fields=["KOD", "NOTATKA"]))
    finally:
        monkeypatch.setattr(Path, "open", real_open)
    assert [record.values for record in records] == [
        {"KOD": 1, "NOTATKA": None},
        {"KOD": 2, "NOTATKA": None},
    ]


# ---------------------------------------------------------------------------
# premature EOF is truncation (hardened record boundary)
# ---------------------------------------------------------------------------


def _marker_patch_reader(monkeypatch, dbf_path: Path, marker_replacement: bytes | None) -> Any:
    """Wrap the DBF handle so the marker read at *record 1* is controlled.

    ``marker_replacement=None`` feeds EOF (b"") for that marker read; any
    other value replaces the marker bytes.  Works on Windows and POSIX.
    """
    import builtins

    real_open = builtins.open
    header_length, record_length, record_count = _layout(dbf_path)
    target = header_length + record_length  # record 1's marker

    class _ControlledReader:
        def __init__(self, handle: Any) -> None:
            self._handle = handle

        def read(self, size: int = -1):
            if size == 1 and self._handle.tell() == target:
                return marker_replacement if marker_replacement is not None else b""
            return self._handle.read(size)

        def seek(self, *args, **kwargs):
            return self._handle.seek(*args, **kwargs)

        def tell(self) -> int:
            return self._handle.tell()

        def close(self) -> None:
            self._handle.close()

        def __enter__(self) -> _ControlledReader:
            return self

        def __exit__(self, *args) -> bool:
            self._handle.close()
            return False

    def controlled_open(file, mode="r", *args, **kwargs):
        handle = real_open(file, mode, *args, **kwargs)
        if str(file).lower().endswith(".dbf") and "b" in mode:
            return _ControlledReader(handle)
        return handle

    monkeypatch.setattr(builtins, "open", controlled_open)
    return monkeypatch


def test_premature_empty_marker_is_truncation(tmp_path: Path, monkeypatch) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "PREOF.dbf", "KOD N(6,0)", [{"KOD": i} for i in range(3)]
    )
    _marker_patch_reader(monkeypatch, dbf_path, marker_replacement=None)
    stream = iter_records(dbf_path)
    assert next(stream).physical_index == 0
    with pytest.raises(DbfTruncatedError) as error:
        next(stream)  # record 1: marker read returns EOF before the last record
    monkeypatch.undo()
    assert error.value.code is ErrorCode.DBF_TRUNCATED
    assert error.value.context["record_index"] == 1
    assert error.value.context["declared_records"] == 3
    assert error.value.context["records_read"] == 1
    _assert_json_safe(error.value.to_dict())


def test_premature_eof_marker_0x1a_is_truncation(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "PRE1A.dbf", "KOD N(6,0)", [{"KOD": i} for i in range(3)]
    )
    data = bytearray(dbf_path.read_bytes())
    header_length, record_length, _count = _layout(dbf_path)
    data[header_length + record_length] = 0x1A  # terminator before the last record
    dbf_path.write_bytes(bytes(data))

    stream = iter_records(dbf_path)
    assert next(stream).physical_index == 0
    with pytest.raises(DbfTruncatedError) as error:
        next(stream)
    assert error.value.code is ErrorCode.DBF_TRUNCATED
    assert error.value.context["declared_records"] == 3
    assert error.value.context["records_read"] == 1
    _assert_json_safe(error.value.to_dict())


def test_short_field_payload_is_truncation_with_context(tmp_path: Path, monkeypatch) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "TRUNC.dbf", "KOD N(6,0)", [{"KOD": i} for i in range(3)]
    )
    real_open = builtins.open
    header_length, record_length, record_count = _layout(dbf_path)
    cut_pos = header_length + record_length * (record_count - 1) + record_length - 2

    class _ShortTailReader:
        def __init__(self, handle: Any) -> None:
            self._handle = handle
            self._short = False

        def read(self, size: int = -1):
            if size > 0 and not self._short and self._handle.tell() + size >= cut_pos:
                self._short = True
                return self._handle.read(max(1, size - 2))
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
    assert error.value.context["record_index"] == 2
    assert error.value.context["declared_records"] == 3
    assert error.value.context["records_read"] == 2
    _assert_json_safe(error.value.to_dict())


def test_full_record_area_reads_to_normal_eof(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "FULLEOF.dbf", "KOD N(6,0)", [{"KOD": i} for i in range(4)]
    )
    # The file may end with a normal 0x1A terminator after ALL records; the
    # declared record count matches the file, so the stream ends cleanly.
    page = read_records(dbf_path, limit=10)
    assert len(page.records) == 4
    assert page.exhausted is True
    assert page.next_offset is None


# ---------------------------------------------------------------------------
# public model immutability (hardened contracts)
# ---------------------------------------------------------------------------


def test_direct_record_values_are_read_only_and_decoupled(tmp_path: Path) -> None:
    source: dict[str, Any] = {"KOD": 1, "NAZWA": "a"}
    record = DirectRecord(physical_index=0, deleted=False, values=source)

    with pytest.raises(TypeError):
        record.values["X"] = 42  # type: ignore[index]
    with pytest.raises(TypeError):
        del record.values["KOD"]  # type: ignore[attr-defined]

    source["INJECTED"] = "later"  # mutating the input dict must not leak in
    assert dict(record.values) == {"KOD": 1, "NAZWA": "a"}
    assert "INJECTED" not in record.values

    payload = record.to_dict()
    payload["values"]["KOD"] = 999  # to_dict copies are independent
    assert record.values["KOD"] == 1
    assert isinstance(payload, dict)
    _assert_json_safe(payload)


def test_record_page_records_stay_a_tuple(memo_table: Path) -> None:
    page = read_records(memo_table, limit=2)
    assert isinstance(page.records, tuple)

    coerced = RecordPage(
        offset=0,
        limit=2,
        records=list(page.records),  # any sequence must be snapshotted as a tuple
        scanned=2,
        next_offset=2,
        exhausted=False,
    )
    assert isinstance(coerced.records, tuple)
    assert list(coerced.records) == list(page.records)
    with pytest.raises(TypeError):
        coerced.records[0].values["X"] = 1  # type: ignore[index]


# ---------------------------------------------------------------------------
# forensic raw split (hardened contracts)
# ---------------------------------------------------------------------------


def test_iter_raw_records_never_decodes_damaged_text(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(tmp_path / "RECOVER.dbf", "NAZWA C(20)", [{"NAZWA": "ok"}])
    data = bytearray(dbf_path.read_bytes())
    header_length, record_length, _count = _layout(dbf_path)
    data[header_length + 3 : header_length + 6] = b"\x81\x83\x88"  # bad bytes after 'ok'
    dbf_path.write_bytes(bytes(data))

    with pytest.raises(TextDecodeError):
        next(iter_records(dbf_path, decode_errors="strict"))

    # The forensic path never decodes: the raw record is fully recoverable.
    raw = next(iter(iter_raw_records(dbf_path)))
    full = dbf_path.read_bytes()
    assert raw.physical_index == 0
    assert raw.raw_record == full[header_length : header_length + record_length]
    assert dict(raw.values) == {}


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
    # Pure forensic streaming: no field is parsed or decoded at all.
    assert dict(raws[0].values) == {}
    _assert_json_safe(raws[0].to_dict())


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


# ---------------------------------------------------------------------------
# 0.3 direct-read control: progress + cooperative cancellation
# ---------------------------------------------------------------------------


def test_progress_normal_exhaustion(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "progress.dbf", "ID N(3,0)", [{"ID": index} for index in range(1, 11)]
    )
    events: list[ProgressEvent] = []
    records = list(iter_records(dbf_path, progress=events.append))
    assert len(records) == 10
    assert len(events) == 1  # below cadence: only the final event
    final = events[-1]
    assert final.operation == "read"
    assert final.current == 10 and final.total == 10
    assert final.records == 10
    assert final.table == str(dbf_path)
    assert final.message == "completed"


def test_progress_pagination_uses_physical_positions(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "page.dbf", "ID N(3,0)", [{"ID": index} for index in range(1, 11)]
    )
    events: list[ProgressEvent] = []
    page = read_records(dbf_path, offset=5, limit=3, progress=events.append)
    assert [record.physical_index for record in page.records] == [5, 6, 7]
    assert page.next_offset == 8
    final = events[-1]
    assert final.current == 8 and final.records == 3
    assert final.message == "page completed"


def test_progress_deleted_records(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "deleted.dbf", "ID N(3,0)", [{"ID": index} for index in range(1, 11)]
    )
    _mark_deleted(dbf_path, 3)
    events: list[ProgressEvent] = []
    active = list(iter_records(dbf_path, progress=events.append))
    assert len(active) == 9
    final = events[-1]
    # Deleted records count toward the physical position, not toward records.
    assert final.current == 10 and final.records == 9

    include_events: list[ProgressEvent] = []
    included = list(iter_records(dbf_path, include_deleted=True, progress=include_events.append))
    assert len(included) == 10
    assert include_events[-1].records == 10


def test_progress_raw_records(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "raw_progress.dbf", "ID N(3,0)", [{"ID": index} for index in range(1, 11)]
    )
    _mark_deleted(dbf_path, 3)
    events: list[ProgressEvent] = []
    total = sum(1 for _ in iter_raw_records(dbf_path, progress=events.append))
    assert total == 10
    assert events[-1].current == 10 and events[-1].records == 10


def test_progress_and_cancellation_are_independent(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "independent.dbf", "ID N(3,0)", [{"ID": index} for index in range(1, 11)]
    )
    events: list[ProgressEvent] = []
    assert len(list(iter_records(dbf_path, progress=events.append))) == 10
    assert events  # progress without cancel_check works

    collected: list[DirectRecord] = []
    with pytest.raises(ReadCancelledError):
        for record in iter_records(dbf_path, cancel_check=lambda: len(collected) >= 4):
            collected.append(record)
    assert len(collected) == 4  # cancellation without progress works too


def test_progress_cadence_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dbf_bridge.core import records as records_module

    dbf_path = _create_vfp_table(
        tmp_path / "cadence.dbf", "ID N(3,0)", [{"ID": index} for index in range(1, 11)]
    )
    monkeypatch.setattr(records_module, "_PROGRESS_EVERY", 3)
    events: list[ProgressEvent] = []
    records = list(iter_records(dbf_path, progress=events.append))
    assert len(records) == 10
    # Events at scanned == 3, 6, 9 plus the final event.
    assert [event.current for event in events] == [3, 6, 9, 10]
    assert events[-1].message == "completed"


def test_read_records_progress_and_semantics(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "page.dbf", "ID N(3,0)", [{"ID": index} for index in range(1, 11)]
    )
    events: list[ProgressEvent] = []
    page = read_records(dbf_path, offset=4, limit=3, progress=events.append)
    assert [record.physical_index for record in page.records] == [4, 5, 6]
    final = events[-1]
    assert final.current == 7 and final.records == 3
    assert page.next_offset == 7 and page.exhausted is False


def test_cancel_before_first_record_reads_zero_records(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "early.dbf", "ID N(3,0)", [{"ID": index} for index in range(1, 11)]
    )
    source_sha = _sha256(dbf_path)
    with pytest.raises(ReadCancelledError) as error:
        list(iter_records(dbf_path, cancel_check=lambda: True))
    payload = error.value.to_dict()
    assert payload["code"] == "READ_CANCELLED"
    assert payload["context"] == {
        "offset": 0,
        "next_physical_index": 0,
        "scanned": 0,
        "yielded": 0,
        "record_count": 10,
    }
    assert json.dumps(payload)  # JSON-safe
    assert _sha256(dbf_path) == source_sha
    assert sorted(path.name for path in tmp_path.iterdir()) == ["early.dbf"]


def test_cancel_after_deterministic_n_records(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "cancel_n.dbf", "ID N(4,0)", [{"ID": index} for index in range(1, 51)]
    )
    collected: list[DirectRecord] = []

    def _cancel_after_five() -> bool:
        return len(collected) >= 5

    with pytest.raises(ReadCancelledError) as error:
        for record in iter_records(dbf_path, cancel_check=_cancel_after_five):
            collected.append(record)
    # Records already returned remain correctly returned.
    assert [record.values["ID"] for record in collected] == [1, 2, 3, 4, 5]
    assert [record.physical_index for record in collected] == [0, 1, 2, 3, 4]
    payload = error.value.to_dict()
    assert payload["context"] == {
        "offset": 0,
        "next_physical_index": 5,
        "scanned": 5,
        "yielded": 5,
        "record_count": 50,
    }


def test_cancel_read_records_never_returns_partial_page(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "page_cancel.dbf", "ID N(4,0)", [{"ID": index} for index in range(1, 51)]
    )
    calls = {"count": 0}

    def _cancel_at_three() -> bool:
        # probes: 1 before the stream + 1 after each consumed frame;
        # the 4th probe is the boundary before the 4th physical record.
        calls["count"] += 1
        return calls["count"] >= 4

    with pytest.raises(ReadCancelledError) as error:
        read_records(dbf_path, offset=0, limit=100, cancel_check=_cancel_at_three)
    payload = error.value.to_dict()["context"]
    assert payload == {
        "offset": 0,
        "next_physical_index": 3,
        "scanned": 3,
        "yielded": 3,
        "record_count": 50,
    }


def test_cancel_raw_reader_keeps_forensic_semantics(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "raw_cancel.dbf", "ID N(3,0)", [{"ID": index} for index in range(1, 11)]
    )
    _mark_deleted(dbf_path, 3)
    seen: list[DirectRecord] = []

    def _cancel_at_four() -> bool:
        return len(seen) >= 4

    with pytest.raises(ReadCancelledError):
        for record in iter_raw_records(dbf_path, cancel_check=_cancel_at_four):
            seen.append(record)
    assert len(seen) == 4
    assert seen[3].deleted is True and seen[3].values == {}
    assert isinstance(seen[3].raw_record, bytes)


def test_cancel_before_first_record_with_inline_memo(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "inline.dbf",
        "ID N(3,0); NOTATKA M",
        [{"ID": index, "NOTATKA": f"m{index}"} for index in range(1, 11)],
    )
    fpt = dbf_path.with_suffix(".fpt")
    with pytest.raises(ReadCancelledError):
        list(iter_records(dbf_path, memo="inline", cancel_check=lambda: True))
    # Zero side effects, no FPT hold: the companion can be renamed at once.
    renamed = tmp_path / "moved.fpt"
    os.rename(fpt, renamed)
    os.rename(renamed, fpt)


def test_cancel_check_probe_count_is_exactly_one_per_boundary(tmp_path: Path) -> None:
    """Exactly one cancellation probe per prospective physical record read."""
    dbf_path = _create_vfp_table(
        tmp_path / "probe.dbf", "ID N(4,0)", [{"ID": index} for index in range(1, 51)]
    )
    # (a) cancel before the first record: ONE probe, zero frames.
    first_calls = {"count": 0}

    def _always() -> bool:
        first_calls["count"] += 1
        return True

    with pytest.raises(ReadCancelledError) as error:
        list(iter_records(dbf_path, cancel_check=_always))
    assert first_calls["count"] == 1, first_calls
    assert error.value.to_dict()["context"]["scanned"] == 0

    # (b) cancel at the boundary before the 4th physical record:
    # 3 records consumed and exactly 4 probe invocations (1 pre-stream +
    # 1 after each consumed frame).
    boundary_calls = {"count": 0}

    def _cancel_before_fourth() -> bool:
        boundary_calls["count"] += 1
        return boundary_calls["count"] >= 4

    collected: list[DirectRecord] = []
    with pytest.raises(ReadCancelledError):
        for record in iter_records(dbf_path, cancel_check=_cancel_before_fourth):
            collected.append(record)
    assert len(collected) == 3
    assert boundary_calls["count"] == 4


def test_read_records_final_current_is_physical_scanned(tmp_path: Path) -> None:
    """`current` follows scanned physical records, not returned records."""
    dbf_path = _create_vfp_table(
        tmp_path / "trail.dbf", "ID N(3,0)", [{"ID": index} for index in range(1, 5)]
    )
    _mark_deleted(dbf_path, 2)
    _mark_deleted(dbf_path, 3)
    events: list[ProgressEvent] = []
    page = read_records(
        dbf_path, offset=0, limit=100, include_deleted=False, progress=events.append
    )
    assert len(page.records) == 2
    assert page.scanned == 4 and page.exhausted is True
    assert len(events) == 1
    final = events[-1]
    # Deleted records AFTER the last yielded record still advance `current`.
    assert final.current == 4 and final.total == 4 and final.records == 2
    assert final.message == "page completed"


def test_read_records_all_deleted_page(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "all_deleted.dbf", "ID N(3,0)", [{"ID": index} for index in range(1, 5)]
    )
    for index in range(4):
        _mark_deleted(dbf_path, index)
    events: list[ProgressEvent] = []
    page = read_records(
        dbf_path, offset=0, limit=100, include_deleted=False, progress=events.append
    )
    assert page.records == () and page.scanned == 4 and page.exhausted is True
    assert len(events) == 1
    final = events[-1]
    assert final.current == 4 and final.total == 4 and final.records == 0


def test_offset_at_and_beyond_eof_clamps_progress_current(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "eof.dbf", "ID N(3,0)", [{"ID": index} for index in range(1, 11)]
    )
    # offset == record_count: existing empty exhausted-page semantics kept.
    at_eof = read_records(dbf_path, offset=10, limit=5)
    assert at_eof.records == () and at_eof.exhausted is True and at_eof.next_offset is None
    events: list[ProgressEvent] = []
    read_records(dbf_path, offset=10, limit=5, progress=events.append)
    assert len(events) == 1
    assert (events[-1].current, events[-1].total) == (10, 10)

    # offset beyond EOF: progress invariant 0 <= current <= total still holds.
    beyond = read_records(dbf_path, offset=20, limit=5)
    assert beyond.records == () and beyond.exhausted is True
    beyond_events: list[ProgressEvent] = []
    read_records(dbf_path, offset=20, limit=5, progress=beyond_events.append)
    assert len(beyond_events) == 1
    assert (beyond_events[-1].current, beyond_events[-1].total) == (10, 10)
    assert 0 <= beyond_events[-1].current <= beyond_events[-1].total


def test_progress_event_invariants_across_fixtures(tmp_path: Path) -> None:
    """Every final event satisfies 0 <= current <= total and records >= 0."""
    fixtures: list[Path] = []
    empty = _create_vfp_table(tmp_path / "empty.dbf", "ID N(3,0)", [])
    fixtures.append(empty)
    normal = _create_vfp_table(
        tmp_path / "normal.dbf", "ID N(3,0)", [{"ID": index} for index in range(1, 11)]
    )
    fixtures.append(normal)
    deleted = _create_vfp_table(
        tmp_path / "deleted.dbf", "ID N(3,0)", [{"ID": index} for index in range(1, 11)]
    )
    _mark_deleted(deleted, 2)
    _mark_deleted(deleted, 3)
    fixtures.append(deleted)
    all_deleted = _create_vfp_table(
        tmp_path / "all_deleted.dbf", "ID N(3,0)", [{"ID": index} for index in range(1, 5)]
    )
    for index in range(4):
        _mark_deleted(all_deleted, index)
    fixtures.append(all_deleted)

    for fixture in fixtures:
        for include_deleted in (False, True):
            events: list[ProgressEvent] = []
            list(iter_records(fixture, include_deleted=include_deleted, progress=events.append))
            assert events
            for event in events:
                assert 0 <= event.current <= event.total
                assert event.records is not None and event.records >= 0
            page_events: list[ProgressEvent] = []
            page = read_records(
                fixture,
                offset=0,
                limit=3,
                include_deleted=include_deleted,
                progress=page_events.append,
            )
            assert page_events
            for event in page_events:
                assert 0 <= event.current <= event.total
                assert event.records is not None and event.records >= 0
            raw_events: list[ProgressEvent] = []
            list(iter_raw_records(fixture, progress=raw_events.append))
            for event in raw_events:
                assert 0 <= event.current <= event.total
                assert event.records is not None and event.records >= 0


def test_cancel_before_first_record_error_context(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "zero.dbf", "ID N(3,0)", [{"ID": index} for index in range(1, 11)]
    )
    source_sha = _sha256(dbf_path)
    with pytest.raises(ReadCancelledError) as error:
        read_records(dbf_path, offset=5, limit=4, cancel_check=lambda: True)
    payload = error.value.to_dict()
    assert payload["code"] == "READ_CANCELLED"
    assert payload["context"] == {
        "offset": 5,
        "next_physical_index": 5,
        "scanned": 0,
        "yielded": 0,
        "record_count": 10,
    }
    assert json.dumps(payload)
    assert _sha256(dbf_path) == source_sha


def test_resource_release_windows_rename_delete(tmp_path: Path) -> None:
    """After cancellation/close/exceptions, DBF and FPT can be renamed at once
    (the Windows-critical proof that no handle is left open)."""
    dbf_path = _create_vfp_table(
        tmp_path / "handles.dbf",
        "ID N(3,0); NOTATKA M",
        [{"ID": index, "NOTATKA": f"m{index}"} for index in range(1, 11)],
    )
    fpt = dbf_path.with_suffix(".fpt")

    def _assert_releasable() -> None:
        moved_dbf = tmp_path / "moved.dbf"
        os.rename(dbf_path, moved_dbf)
        os.rename(moved_dbf, dbf_path)
        moved_fpt = tmp_path / "moved.fpt"
        os.rename(fpt, moved_fpt)
        os.rename(moved_fpt, fpt)

    # 1) manual close.
    iterator = iter_records(dbf_path, memo="inline")
    next(iterator)
    iterator.close()
    _assert_releasable()
    # 2) cancellation.
    with pytest.raises(ReadCancelledError):
        for _ in iter_records(dbf_path, memo="inline", cancel_check=lambda: True):
            break
        list(iter_records(dbf_path, memo="inline", cancel_check=lambda: True))
    _assert_releasable()

    # 3) progress callback exception.
    def _broken_progress(event: ProgressEvent) -> None:
        raise RuntimeError("progress failed")

    with pytest.raises(RuntimeError, match="progress failed"):
        for _ in iter_records(dbf_path, progress=_broken_progress):
            pass
    _assert_releasable()

    # 4) cancel-check exception propagates unchanged (never READ_CANCELLED).
    def _broken_cancel() -> bool:
        raise KeyError("cancel failure")

    with pytest.raises(KeyError, match="cancel failure"):
        for _ in iter_records(dbf_path, cancel_check=_broken_cancel):
            break
        list(iter_records(dbf_path, cancel_check=_broken_cancel))
    _assert_releasable()
    _assert_releasable()


def test_cancel_lazy_memo_never_reads_memo_payloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "lazy.dbf",
        "ID N(3,0); NOTATKA M",
        [{"ID": index, "NOTATKA": f"m{index}"} for index in range(1, 11)],
    )
    from dbf_bridge.core.backend import dbfread_backend

    calls = {"memo_payload_reads": 0}
    original_read = dbfread_backend.read_memo_payload

    def _counting(*args: Any, **kwargs: Any) -> bytes:
        calls["memo_payload_reads"] += 1
        return original_read(*args, **kwargs)

    monkeypatch.setattr(dbfread_backend, "read_memo_payload", _counting)
    with pytest.raises(ReadCancelledError):
        list(iter_records(dbf_path, memo="lazy", cancel_check=lambda: True))
    assert calls["memo_payload_reads"] == 0


def test_source_immutability_across_control_modes(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "immutable.dbf",
        "ID N(3,0); NOTATKA M",
        [{"ID": index, "NOTATKA": f"m{index}"} for index in range(1, 11)],
    )
    fpt = dbf_path.with_suffix(".fpt")
    before_dbf = _sha256(dbf_path)
    before_fpt = _sha256(fpt)

    def _assert_unchanged() -> None:
        assert _sha256(dbf_path) == before_dbf
        assert _sha256(fpt) == before_fpt

    # normal read
    assert len(list(iter_records(dbf_path))) == 10
    _assert_unchanged()
    # progress read
    assert len(list(iter_records(dbf_path, progress=lambda event: None))) == 10
    _assert_unchanged()
    # cancelled read
    with pytest.raises(ReadCancelledError):
        list(iter_records(dbf_path, cancel_check=lambda: True))
    _assert_unchanged()
    # raw cancelled read
    with pytest.raises(ReadCancelledError):
        list(iter_raw_records(dbf_path, cancel_check=lambda: True))
    _assert_unchanged()
    # inline-memo cancelled read (DBF + FPT both untouched)
    with pytest.raises(ReadCancelledError):
        list(iter_records(dbf_path, memo="inline", cancel_check=lambda: True))
    _assert_unchanged()
    # no output artifacts anywhere
    assert sorted(path.name for path in tmp_path.iterdir()) == ["immutable.dbf", "immutable.fpt"]


def test_emitted_direct_read_events_use_the_canonical_class(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "canonical.dbf", "ID N(3,0)", [{"ID": index} for index in range(1, 11)]
    )
    events: list[Any] = []
    records = list(iter_records(dbf_path, progress=events.append))
    assert records and events
    # The ACTUALLY emitted event object is the canonical public class.
    assert type(events[0]) is dbfbridge.ProgressEvent
    assert isinstance(events[0], dbfbridge.ProgressEvent)


def test_migration_progress_events_use_the_canonical_class(
    tmp_path: Path, sample_input_dir: Path
) -> None:
    """export_dbf keeps emitting the same canonical ProgressEvent class."""
    from dbf_bridge.api_models import ProgressEvent as ApiModelsEvent
    from dbf_bridge.progress import ProgressEvent as ProgressModuleEvent

    events: list[Any] = []
    result = export_dbf(
        str(sample_input_dir / "klienci.dbf"),
        str(tmp_path / "out"),
        formats=("jsonl",),
        progress=events.append,
    )
    result.raise_for_errors()
    assert events, "migration API must still emit progress events"
    for event in events:
        assert isinstance(event, dbfbridge.ProgressEvent)
        assert type(event) is dbfbridge.ProgressEvent
        assert type(event) is ApiModelsEvent is ProgressModuleEvent
        assert event.operation in {"export", "convert", "read"}


def test_core_exports_have_no_accidental_removal() -> None:
    """core.__all__ may only GROW; the PR must not shrink the public layer."""
    from dbf_bridge import core

    for name in (
        "inspect_table",
        "read_schema",
        "memo_companion_extension",
        "memo_companion_format",
        "ReadCancelledError",
    ):
        assert name in core.__all__, name
        assert hasattr(core, name), name


def test_eager_validation_not_masked_by_cancellation(tmp_path: Path) -> None:
    """Argument/header validation stays eager; cancellation cannot hide it."""
    dbf_path = _create_vfp_table(
        tmp_path / "eager.dbf", "ID N(3,0)", [{"ID": index} for index in range(1, 11)]
    )
    # Unknown field projection: eager FIELD_PROJECTION_INVALID, not READ_CANCELLED.
    with pytest.raises(FieldProjectionInvalidError):
        list(iter_records(dbf_path, fields=["NOPE"], cancel_check=lambda: True))
    # Unknown memo policy: eager ARGUMENT_INVALID.
    with pytest.raises(ArgumentInvalidError):
        list(iter_records(dbf_path, memo="bogus", cancel_check=lambda: True))
    # A missing header: eager typed error, still not masked.
    with pytest.raises((DbfHeaderInvalidError, DbfPathError, DbfIoError)):
        list(iter_records(tmp_path / "missing.dbf", cancel_check=lambda: True))


def test_read_cancelled_error_is_json_safe(tmp_path: Path) -> None:
    dbf_path = _create_vfp_table(
        tmp_path / "jsonsafe.dbf", "ID N(3,0)", [{"ID": index} for index in range(1, 11)]
    )
    with pytest.raises(ReadCancelledError) as error:
        list(iter_records(dbf_path, cancel_check=lambda: True))
    payload = error.value.to_dict()
    restored = json.loads(json.dumps(payload))
    assert restored["code"] == "READ_CANCELLED"
    assert restored["path"].endswith("jsonsafe.dbf")
    assert restored["context"]["record_count"] == 10


def test_public_control_exports() -> None:
    from dbfbridge import CancellationCheck, ProgressCallback, ReadCancelledError  # noqa: F401

    assert dbfbridge.ReadCancelledError is dbf_bridge.ReadCancelledError
    assert dbfbridge.CancellationCheck is dbf_bridge.CancellationCheck
    assert dbfbridge.ProgressCallback is dbf_bridge.ProgressCallback
    assert dbfbridge.ProgressEvent is dbf_bridge.ProgressEvent
    assert dbfbridge.ProgressEvent is dbf_bridge.api_models.ProgressEvent


def test_single_canonical_progress_event_identity() -> None:
    """progress.py is the ONLY source of the shared progress contract."""
    import dbf_bridge as bridge_module
    from dbf_bridge import api_models, progress
    from dbf_bridge.api import ProgressCallback as ApiCallback
    from dbf_bridge.progress import ProgressEvent as ProgressModuleEvent

    # Four-way runtime identity across every historical import path.
    canonical = progress.ProgressEvent
    assert canonical is api_models.ProgressEvent
    assert canonical is dbfbridge.ProgressEvent
    assert canonical is dbf_bridge.ProgressEvent
    assert ProgressModuleEvent is canonical
    # api_models no longer defines a second class; it re-exports.
    assert "class ProgressEvent" not in Path(api_models.__file__).read_text(encoding="utf-8")
    # ProgressCallback has one canonical source (api.py re-exports it).
    assert ApiCallback is progress.ProgressCallback
    # The Direct Read API and public facade agree on the runtime class.
    assert bridge_module.ReadCancelledError is ReadCancelledError
    assert dbfbridge.CancellationCheck is CancellationCheck
    assert dbfbridge.ProgressCallback is ProgressCallback
    assert dbfbridge.ProgressEvent is ProgressEvent
    assert "CancellationCheck" in dbfbridge.__all__
    assert "ReadCancelledError" in dbfbridge.__all__
    assert "ProgressCallback" in dbfbridge.__all__

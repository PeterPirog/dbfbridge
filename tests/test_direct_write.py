"""Internal Direct Write contract tests (v1.1 Phase C).

Covers the internal ``dbf_bridge.write.write_table`` contract:

- exact internal signature, ``overwrite=False`` default, canonical
  ``progress``/``cancel_check`` parameters, root-public freeze;
- ``WriteResult`` shape, JSON safety, final digests, payload privacy;
- schema adapter validation (typed, before any output);
- record contract (DirectRecord/mapping, unknown keys, missing fields,
  physical order, deleted markers);
- memo contract (text, binary, explicit ``LazyMemoValue.load()``);
- bounded streaming (one-shot iterables, no O(N) materialization, spool
  lifecycle);
- cooperative cancellation at record boundaries and before publication;
- transaction-safe publication (failure injection at fsync/replace points,
  pair restoration);
- error privacy sentinels;
- canonical Direct Read -> Direct Write -> Direct Read equivalence for the
  supported VFP type/codepage matrix;
- shared physical writer evidence (same backend object as reconstruction).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from vfp_fixture_factory import build_vfp32_table, mark_deleted

from dbf_bridge import OptionalDependencyMissingError
from dbf_bridge import write_table as dbf_bridge_write_table
from dbf_bridge.core import iter_records, read_records, read_schema
from dbf_bridge.core.errors import (
    DestinationIoError,
    DirectReadError,
    ErrorCode,
    OperationOutputExistsError,
    WriteCancelledError,
    WriteFieldUnsupportedError,
    WriteMemoFailedError,
    WritePublicationFailedError,
    WriteSchemaInvalidError,
    WriteValueInvalidError,
)
from dbf_bridge.core.models import FieldInfo, TableSchema
from dbf_bridge.core.records import DirectRecord, LazyMemoValue
from dbf_bridge.write import WriteResult as _WriteResultImplementation
from dbf_bridge.write import backend as write_backend
from dbf_bridge.write import write_table

REPO_ROOT = Path(__file__).parents[1]

SECRET_RECORD_VALUE = "SECRET_RECORD_VALUE_9f92c1"
SECRET_MEMO_PAYLOAD = b"SECRET_MEMO_PAYLOAD_4a71b8"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _field(name: str, dbf_type: str, length: int, *, decimals: int = 0, flags: int = 0) -> FieldInfo:
    return FieldInfo(
        ordinal=len(_fields_by_ordinal) + 1,
        name=name,
        dbf_type=dbf_type,
        length=length,
        decimal_count=decimals,
        address=0,
        flags=flags,
        index_field_flag=0,
        autoincrement_next_value=0,
        autoincrement_step=1,
        is_memo=dbf_type in {"M", "G", "P"},
        is_binary=False,
        supported=dbf_type not in {"Q", "W"},
        dbversion_byte=0x30,
    )


_fields_by_ordinal: list[FieldInfo] = []


def _schema(fields: tuple[FieldInfo, ...], **overrides: Any) -> TableSchema:
    _fields_by_ordinal.clear()
    _fields_by_ordinal.extend(fields)
    base: dict[str, Any] = {
        "path": Path("memory:fixture"),
        "record_count": 0,
        "header_length": 32 + 32 * len(fields) + 1,
        "record_length": sum(field.length for field in fields) + 1,
        "language_driver": 0x03,
        "encoding": "cp1250",
        "has_memo": any(field.is_memo for field in fields),
        "has_memo_flag": any(field.is_memo for field in fields),
        "has_structural_cdx": False,
        "is_database_container": False,
        "dbc_bound": False,
        "dbc_backlink_path": None,
        "table_flags": 0,
        "fields": fields,
        "warnings": (),
        "dbversion_byte": 0x30,
        "dbversion_name": "Visual FoxPro",
        "last_update": None,
        "incomplete_transaction": False,
        "encryption_flag": False,
        "memo_companion_format": "FoxPro FPF",
        "memo_companion_present": False,
        "memo_companion_path": None,
        "memo_companion_size_bytes": None,
        "memo_block_size": 64,
        "memo_next_free_block": None,
        "companion_cdx_present": False,
        "companion_cdx_path": None,
    }
    base.update(overrides)
    return TableSchema(**base)


_PLAIN_FIELDS = (
    _field("CODE", "C", 10),
    _field("AMOUNT", "N", 10, decimals=2),
    _field("WHEN", "D", 8),
    _field("FLAG", "L", 1),
)


def _plain_records() -> list[DirectRecord]:
    return [
        DirectRecord(physical_index=0, deleted=False, values={
            "CODE": "A1", "AMOUNT": 1.5, "WHEN": "20240101", "FLAG": True}),
        DirectRecord(physical_index=1, deleted=True, values={
            "CODE": "B2", "AMOUNT": 2.5, "WHEN": "20240102", "FLAG": False}),
        DirectRecord(physical_index=2, deleted=False, values={
            "CODE": "C3", "AMOUNT": 3.5, "WHEN": "20240103", "FLAG": None}),
    ]


# ---------------------------------------------------------------------------
# signature / public freeze
# ---------------------------------------------------------------------------


def test_internal_signature_is_exact() -> None:
    import inspect

    import dbf_bridge as alias
    import dbfbridge as root

    signature = inspect.signature(root.write_table)
    parameters = signature.parameters
    assert list(parameters) == [
        "destination",
        "schema",
        "records",
        "overwrite",
        "staging_directory",
        "progress",
        "cancel_check",
    ]
    assert parameters["overwrite"].default is False
    assert parameters["progress"].default is None
    assert parameters["cancel_check"].default is None
    assert all(
        parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        for name in ("schema", "records", "overwrite", "progress", "cancel_check")
    )
    # v1.1 promotion: both public facades expose the SAME promoted object,
    # re-exported from the shared writer package (no parallel implementation).
    assert "write_table" in root.__all__
    assert root.write_table is alias.write_table
    assert root.write_table is dbf_bridge_write_table


def test_write_result_is_public_and_immutable() -> None:
    import dbf_bridge as alias
    import dbfbridge as root

    assert "WriteResult" in root.__all__
    assert root.WriteResult is alias.WriteResult is _WriteResultImplementation
    result = _WriteResultImplementation(
        destination=Path("x/a.dbf"),
        fpt_path=None,
        fpt_published=False,
        records_written=1,
        deleted_records=0,
        structural_cdx=False,
        index_rebuild_required=False,
        dbc_bound=False,
        dbf_sha256="0" * 64,
        fpt_sha256=None,
        warnings=(),
    )
    with pytest.raises(AttributeError):  # frozen dataclass: assignment raises
        result.records_written = 2  # type: ignore[misc]


# ---------------------------------------------------------------------------
# result
# ---------------------------------------------------------------------------


def test_write_result_fields_json_safety_and_final_digests(
    tmp_path: Path, sample_input_dir: Path
) -> None:
    destination = tmp_path / "out" / "klienci.dbf"
    schema = read_schema(sample_input_dir / "klienci.dbf")
    records = list(iter_records(sample_input_dir / "klienci.dbf", memo="inline"))
    result = write_table(destination, schema=schema, records=records)

    assert result.records_written == len(records)
    assert result.deleted_records == 0
    assert result.fpt_published is True
    assert result.fpt_path is not None and result.fpt_path.suffix == ".fpt"
    assert result.dbf_sha256 == _sha256(destination)
    assert result.fpt_sha256 == _sha256(result.fpt_path)
    payload = result.to_dict()
    json.dumps(payload)  # never raises
    assert isinstance(payload["warnings"], list)
    assert "\\" not in payload["destination"] and ":" not in payload["destination"].replace(
        ":/", "", 1
    )
    assert payload["destination"] == destination.as_posix()
    # no record values, no memo payloads (meaningful strings only — tiny
    # values like "1" appear coincidentally inside digests/paths)
    flattened = json.dumps(payload)
    for record in records[:5]:
        for value in record.values.values():
            if isinstance(value, str) and len(value) >= 5:
                assert value not in flattened


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# schema validation
# ---------------------------------------------------------------------------


def test_schema_must_be_typed_table_schema(tmp_path: Path) -> None:
    with pytest.raises(WriteSchemaInvalidError) as error:
        write_table(tmp_path / "a.dbf", schema={"fields": []}, records=iter(()))
    assert error.value.code is ErrorCode.WRITE_SCHEMA_INVALID
    assert not (tmp_path / "a.dbf").exists()


def test_empty_schema_is_rejected_before_output(tmp_path: Path) -> None:
    empty = replace(_schema(_PLAIN_FIELDS), fields=())
    with pytest.raises(WriteSchemaInvalidError):
        write_table(tmp_path / "a.dbf", schema=empty, records=iter(()))
    assert not (tmp_path / "a.dbf").exists()


def test_unsupported_type_is_typed_refusal(tmp_path: Path) -> None:
    bad = _schema(
        (
            _field("CODE", "C", 5),
            _field("BLOB", "Q", 10),
        )
    )
    with pytest.raises(WriteFieldUnsupportedError) as error:
        write_table(tmp_path / "a.dbf", schema=bad, records=iter(()))
    assert error.value.code is ErrorCode.WRITE_FIELD_UNSUPPORTED
    assert error.value.context["field"] == "BLOB"
    assert not (tmp_path / "a.dbf").exists()


@pytest.mark.parametrize(
    ("name", "dbf_type", "length", "decimals"),
    [
        ("TOOLONG", "C", 0, 0),
        ("NARROW", "N", 30, 2),
        ("DECIMALS", "N", 4, 4),
        ("NEGDEC", "F", 5, -1),
    ],
)
def test_illegal_dimensions_are_typed_schema_errors(
    tmp_path: Path, name: str, dbf_type: str, length: int, decimals: int
) -> None:
    bad = _schema((_field(name, dbf_type, length, decimals=decimals),))
    with pytest.raises(WriteSchemaInvalidError) as error:
        write_table(tmp_path / "a.dbf", schema=bad, records=iter(()))
    assert error.value.code is ErrorCode.WRITE_SCHEMA_INVALID
    assert error.value.context["field"] == name


def test_varchar_requires_nullflags_system_column(tmp_path: Path) -> None:
    bad = _schema((_field("TXT", "V", 12),))
    with pytest.raises(WriteSchemaInvalidError):
        write_table(tmp_path / "a.dbf", schema=bad, records=iter(()))


# ---------------------------------------------------------------------------
# schema consistency: dialect gate + fixed-width layout (DBFB-SCHEMA-003/005)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dbversion_byte",
    [0x02, 0x03, 0x31, 0x43, 0x83, 0x8B, 0xCB, 0xE5, 0xF5, 0xFB],
)
def test_unproven_dialects_are_rejected_before_output(
    tmp_path: Path, dbversion_byte: int
) -> None:
    """The shared backend creates tables through the VFP writer mode; only
    dialects its output actually preserves (plain VFP 0x30 and the
    Varchar-enabled 0x32, plus the unknown default) may be declared."""
    schema = _schema(_PLAIN_FIELDS, dbversion_byte=dbversion_byte)
    with pytest.raises(WriteSchemaInvalidError) as error:
        write_table(tmp_path / "a.dbf", schema=schema, records=_plain_records())
    assert error.value.code is ErrorCode.WRITE_SCHEMA_INVALID
    assert error.value.context["dbversion_byte"] == dbversion_byte
    assert not (tmp_path / "a.dbf").exists()


def test_proven_dialects_are_accepted(tmp_path: Path) -> None:
    destination = tmp_path / "a.dbf"
    assert _schema(_PLAIN_FIELDS).dbversion_byte == 0x30
    write_table(destination, schema=_schema(_PLAIN_FIELDS), records=_plain_records())
    unknown = _schema(_PLAIN_FIELDS, dbversion_byte=0)
    write_table(
        tmp_path / "default.dbf", schema=unknown, records=_plain_records()
    )
    assert (tmp_path / "default.dbf").is_file()


@pytest.mark.parametrize(
    ("dbf_type", "length"),
    [
        ("L", 2),
        ("D", 10),
        ("T", 14),
        ("@", 4),
        ("I", 2),
        ("+", 8),
        ("Y", 4),
        ("B", 4),
        ("O", 16),
        ("M", 10),
        ("G", 10),
        ("P", 10),
    ],
)
def test_contradictory_fixed_widths_are_rejected_before_output(
    tmp_path: Path, dbf_type: str, length: int
) -> None:
    """Types with a format-defined physical width must declare it exactly
    (evidence: docs/compatibility-vfp.md + the reference writer's own
    descriptors); contradictory declarations fail before any output exists."""
    bad = _schema((_field("F", dbf_type, length),))
    with pytest.raises(WriteSchemaInvalidError) as error:
        write_table(tmp_path / "a.dbf", schema=bad, records=iter(()))
    assert error.value.code is ErrorCode.WRITE_SCHEMA_INVALID
    assert error.value.context["field"] == "F"
    assert error.value.context["dbf_type"] == dbf_type
    assert not (tmp_path / "a.dbf").exists()


def test_fixed_width_canonical_widths_write_smoke(tmp_path: Path) -> None:
    import datetime as dt
    from decimal import Decimal

    destination = tmp_path / "fixed.dbf"
    fields = (
        _field("CODE", "C", 5),
        _field("FLAG", "L", 1),
        _field("WHEN", "D", 8),
        _field("AT", "T", 8),
        _field("COUNT", "I", 4),
        _field("MONEY", "Y", 8),
        _field("REAL", "B", 8),
        _field("ALIAS", "O", 8),
        _field("NOTE", "M", 4),
        _field("PICTURE", "G", 4),
        _field("IMG", "P", 4),
    )
    schema = _schema(fields)
    result = write_table(
        destination,
        schema=schema,
        records=[
            {
                "CODE": "A1",
                "FLAG": True,
                "WHEN": dt.date(2024, 1, 1),
                "AT": dt.datetime(2024, 1, 1, 8, 30),
                "COUNT": 7,
                "MONEY": Decimal("12.3456"),
                "REAL": 2.5,
                "ALIAS": 3.5,
                "NOTE": "text",
                "PICTURE": b"\x00\x01",
                "IMG": b"\xfe",
            }
        ],
    )
    assert result.records_written == 1
    page = read_records(destination, memo="inline")
    record = page.records[0].values
    assert record["WHEN"] == dt.date(2024, 1, 1)
    assert record["AT"] == dt.datetime(2024, 1, 1, 8, 30)
    assert record["COUNT"] == 7
    assert record["NOTE"] == "text"
    assert record["PICTURE"] == b"\x00\x01"
    assert record["IMG"] == b"\xfe"


def test_nullflags_width_must_match_the_canonical_bitmap(tmp_path: Path) -> None:
    nullable = _field("NOTE", "C", 5, flags=0x02)
    # One nullable field implies two allocated bits (varlength? no — C has no
    # varlength bit) → one NULL bit → one bitmap byte is canonical; a wider
    # declared bitmap contradicts the canonical allocation.
    too_wide = _schema((
        _field("CODE", "C", 5),
        nullable,
        _field("NULFLAGS", "0", 2, flags=0x05),
    ))
    with pytest.raises(WriteSchemaInvalidError):
        write_table(tmp_path / "a.dbf", schema=too_wide, records=iter(()))
    assert not (tmp_path / "a.dbf").exists()


# ---------------------------------------------------------------------------
# records
# ---------------------------------------------------------------------------


def test_mapping_records_with_deleted_marker_round_trip(tmp_path: Path) -> None:
    destination = tmp_path / "flat.dbf"
    schema = _schema(_PLAIN_FIELDS)
    records = [
        {"CODE": "A1", "AMOUNT": 1.5, "WHEN": "20240101", "FLAG": True},
        {"CODE": "B2", "AMOUNT": 2.5, "WHEN": "20240102", "FLAG": False, "__deleted__": True},
    ]
    result = write_table(destination, schema=schema, records=records)
    assert (result.records_written, result.deleted_records) == (2, 1)
    page = read_records(destination, include_deleted=True)
    assert [record.deleted for record in page.records] == [False, True]
    assert [record.values["CODE"] for record in page.records] == ["A1", "B2"]


def test_date_values_accept_both_iso_spellings_on_every_python(tmp_path: Path) -> None:
    """Regression: compact ``YYYYMMDD`` mapping strings failed on Python 3.10.

    ``date.fromisoformat`` rejects the compact DBF-style form only on 3.10
    (relaxed in 3.11); the shared checksum and the physical writer now parse
    both spellings through one helper, so mapping-input dates are
    interpreter-independent (DBFB-REC-001/002, DBFB-VFP-001).
    """
    import datetime as dt

    from dbf_bridge.common import parse_iso_date

    assert parse_iso_date("20240101") == dt.date(2024, 1, 1)
    assert parse_iso_date("2024-01-01") == dt.date(2024, 1, 1)
    assert parse_iso_date(dt.date(2024, 1, 1)) == dt.date(2024, 1, 1)

    destination = tmp_path / "dates.dbf"
    schema = _schema(_PLAIN_FIELDS)
    records = [
        {"CODE": "A1", "AMOUNT": 1.5, "WHEN": "20240101", "FLAG": True},
        {"CODE": "B2", "AMOUNT": 2.5, "WHEN": "2024-01-02", "FLAG": True},
        {"CODE": "C3", "AMOUNT": 3.5, "WHEN": dt.date(2024, 1, 3), "FLAG": True},
    ]
    write_table(destination, schema=schema, records=records)
    page = read_records(destination)
    assert [record.values["WHEN"] for record in page.records] == [
        dt.date(2024, 1, 1), dt.date(2024, 1, 2), dt.date(2024, 1, 3),
    ]


def test_unknown_field_key_is_rejected_with_field_metadata(tmp_path: Path) -> None:
    schema = _schema(_PLAIN_FIELDS)
    with pytest.raises(WriteValueInvalidError) as error:
        write_table(
            tmp_path / "a.dbf",
            schema=schema,
            records=iter([{"CODE": "A1", "AMOUNT": 1, "WHEN": "20240101", "FLAG": True,
                           "NOPE": "SECRET_RECORD_VALUE_9f92c1"}]),
        )
    assert error.value.context["unknown_fields"] == ["NOPE"]
    assert SECRET_RECORD_VALUE not in str(error.value)
    assert SECRET_RECORD_VALUE not in json.dumps(error.value.to_dict())
    assert not (tmp_path / "a.dbf").exists()


def test_missing_required_field_is_explicit_never_blank(tmp_path: Path) -> None:
    schema = _schema(_PLAIN_FIELDS)
    with pytest.raises(WriteValueInvalidError) as error:
        write_table(
            tmp_path / "a.dbf",
            schema=schema,
            records=iter([{"CODE": "A1", "WHEN": "20240101", "FLAG": True}]),
        )
    assert error.value.context["field"] == "AMOUNT"
    assert error.value.context["reason"] == "missing_required_field"


def test_missing_nullable_field_is_explicit_null(tmp_path: Path) -> None:
    nullable = _field("NOTE", "C", 5, flags=0x02)
    schema = _schema((_field("CODE", "C", 5), nullable, _field("NULFLAGS", "0", 1, flags=0x05)))
    result = write_table(
        tmp_path / "null.dbf",
        schema=schema,
        records=[{"CODE": "A1"}, {"CODE": "B2", "NOTE": "x"}],
    )
    assert result.records_written == 2
    page = read_records(tmp_path / "null.dbf", include_deleted=True)
    assert page.records[0].values["NOTE"] is None
    assert page.records[1].values["NOTE"] == "x"


def test_input_physical_order_is_output_physical_order(tmp_path: Path) -> None:
    destination = tmp_path / "order.dbf"
    schema = _schema(_PLAIN_FIELDS)
    records = _plain_records()
    write_table(destination, schema=schema, records=records)
    page = read_records(destination, include_deleted=True)
    assert [record.values["CODE"] for record in page.records] == ["A1", "B2", "C3"]
    assert [record.deleted for record in page.records] == [False, True, False]


# ---------------------------------------------------------------------------
# memo contract
# ---------------------------------------------------------------------------


def test_text_memo_round_trip(tmp_path: Path) -> None:
    destination = tmp_path / "memo.dbf"
    fields = (_field("CODE", "C", 5), _field("NOTE", "M", 4))
    schema = _schema(fields)
    records = [
        {"CODE": "A1", "NOTE": "text memo"},
        {"CODE": "B2", "NOTE": "text memo 2"},
    ]
    result = write_table(destination, schema=schema, records=records)
    assert result.fpt_published is True
    page = read_records(destination, memo="inline")
    assert [record.values["NOTE"] for record in page.records] == ["text memo", "text memo 2"]


def test_binary_memo_bytes_round_trip(tmp_path: Path) -> None:
    destination = tmp_path / "binmemo.dbf"
    fields = (_field("CODE", "C", 5), _field("PICTURE", "G", 4))
    schema = _schema(fields)
    payload = b"\x00\x01\xfe\xffbinary"
    write_table(
        destination,
        schema=schema,
        records=[{"CODE": "A1", "PICTURE": payload}],
    )
    page = read_records(destination, memo="inline")
    assert page.records[0].values["PICTURE"] == payload
    # Regression (CI ENOSPC): the block-type patch used to read a
    # Character payload as the memo block pointer when the supplied schema
    # carried placeholder addresses, `seek()`ing the FPT ~34 GB past its end
    # (real allocation on NTFS, sparse on ext4).  The patcher must use the
    # generated table's OWN descriptor offsets.
    fpt = destination.with_suffix(".fpt")
    assert fpt.stat().st_size < 1_000_000, fpt.stat().st_size


def test_lazy_memo_value_resolved_only_explicitly(tmp_path: Path) -> None:
    from dbf_bridge.core.records import LazyMemoValue

    destination = tmp_path / "lazy.dbf"
    schema = _schema((_field("CODE", "C", 5), _field("NOTE", "M", 4)))
    loaded: list[str] = []

    def _loader() -> str:
        loaded.append("loaded")
        return "lazy memo"

    value = LazyMemoValue(
        dbf_path=Path("memory:source.dbf"),
        field_name="NOTE",
        block=1,
        memo_format="FoxPro FPF",
        _loader=_loader,
    )

    result = write_table(
        destination,
        schema=schema,
        records=[{"CODE": "A1", "NOTE": value}],
        progress=None,
    )
    assert result.records_written == 1
    assert loaded == ["loaded"]
    page = read_records(destination, memo="inline")
    assert page.records[0].values["NOTE"] == "lazy memo"


def test_lazy_memo_load_failure_is_typed(tmp_path: Path) -> None:
    destination = tmp_path / "lazyfail.dbf"
    schema = _schema((_field("CODE", "C", 5), _field("NOTE", "M", 4)))

    def _boom() -> str:
        raise RuntimeError("broken loader")

    value = LazyMemoValue(
        dbf_path=Path("memory:source.dbf"),
        field_name="NOTE",
        block=1,
        memo_format="FoxPro FPF",
        _loader=_boom,
    )
    with pytest.raises(WriteMemoFailedError) as error:
        write_table(destination, schema=schema, records=iter([{"CODE": "A1", "NOTE": value}]))
    assert error.value.context["field"] == "NOTE"
    assert "broken loader" not in str(error.value)
    assert not destination.exists()


# ---------------------------------------------------------------------------
# streaming
# ---------------------------------------------------------------------------


class _OneShot:
    """Iterable that raises when iterated more than once."""

    def __init__(self, records: list[DirectRecord]) -> None:
        self._records = records
        self.iterations = 0

    def __iter__(self) -> Any:
        self.iterations += 1
        if self.iterations > 1:
            raise AssertionError("the caller iterable was consumed twice")
        return iter(self._records)


def test_one_shot_iterable_consumed_exactly_once_flat(tmp_path: Path) -> None:
    destination = tmp_path / "flat.dbf"
    one_shot = _OneShot(_plain_records())
    result = write_table(destination, schema=_schema(_PLAIN_FIELDS), records=one_shot)
    assert one_shot.iterations == 1
    assert result.records_written == 3


def test_varchar_second_pass_uses_private_spool_not_the_caller_iterable(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "varchar.dbf"
    source = tmp_path / "vsource.dbf"
    build_vfp32_table(
        source,
        columns=[{"name": "TXT", "type": "V", "width": 10, "nullable": True}],
        rows=[{"TXT": "short"}, {"TXT": "0123456789"}, {"TXT": None}],
    )
    schema = read_schema(source)
    one_shot = _OneShot(list(iter_records(source)))
    result = write_table(destination, schema=schema, records=one_shot)
    assert one_shot.iterations == 1
    assert result.records_written == 3
    # canonical equivalence: values + varlength layout survive the round trip
    back = list(iter_records(destination))
    assert [record.values["TXT"] for record in back] == ["short", "0123456789", None]
    # the private spool is gone after success
    assert list(tmp_path.glob("*.spool")) == []
    assert list(tmp_path.glob(".*spool*")) == []


def test_flat_table_never_creates_a_spool(tmp_path: Path) -> None:
    destination = tmp_path / "flat.dbf"
    write_table(destination, schema=_schema(_PLAIN_FIELDS), records=_plain_records())
    assert not list(tmp_path.glob("*spool*"))


def test_spool_is_cleaned_after_handled_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destination = tmp_path / "fail.dbf"
    schema = _schema(_PLAIN_FIELDS)

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected staging failure")

    monkeypatch.setattr(write_backend, "_fsync_file", _boom)
    with pytest.raises(WritePublicationFailedError):
        write_table(destination, schema=schema, records=_plain_records())
    assert list(tmp_path.glob("*spool*")) == []


# ---------------------------------------------------------------------------
# cancellation
# ---------------------------------------------------------------------------


def test_cancellation_before_first_record_publishes_nothing(tmp_path: Path) -> None:
    destination = tmp_path / "c1.dbf"
    schema = _schema(_PLAIN_FIELDS)
    consumed: list[int] = []

    def _records() -> Any:
        for index, record in enumerate(_plain_records()):
            consumed.append(index)
            yield record

    with pytest.raises(WriteCancelledError) as error:
        write_table(
            destination,
            schema=schema,
            records=_records(),
            cancel_check=lambda: True,
        )
    assert error.value.code is ErrorCode.WRITE_CANCELLED
    assert consumed == []
    assert not destination.exists()
    assert list(tmp_path.glob("*.partial*")) == []


def test_cancellation_mid_stream_stops_at_record_boundary(tmp_path: Path) -> None:
    destination = tmp_path / "c2.dbf"
    schema = _schema(_PLAIN_FIELDS)
    seen = {"n": 0}

    def _check() -> bool:
        return seen["n"] >= 2

    def _records() -> Any:
        for record in _plain_records():
            seen["n"] += 1
            yield record

    with pytest.raises(WriteCancelledError):
        write_table(destination, schema=schema, records=_records(), cancel_check=_check)
    assert not destination.exists()
    assert list(tmp_path.glob("*.partial*")) == []


def test_cancellation_before_publication_keeps_old_pair_intact(tmp_path: Path) -> None:
    destination = tmp_path / "c3.dbf"
    schema = _schema(_PLAIN_FIELDS)
    first = list(_plain_records())
    result = write_table(destination, schema=schema, records=first)
    original_sha = result.dbf_sha256

    flipped = {"done": False}

    def _flip_after_records() -> bool:
        return flipped["done"]

    class _FlipOnce:
        def __init__(self) -> None:
            self.iterations = 0

        def __iter__(self) -> Any:
            self.iterations += 1
            if self.iterations > 1:
                raise AssertionError("double iteration")
            return self._gen()

        def _gen(self) -> Any:
            yield from first
            flipped["done"] = True

    with pytest.raises(WriteCancelledError):
        write_table(
            destination,
            schema=schema,
            records=_FlipOnce(),
            overwrite=True,
            cancel_check=_flip_after_records,
        )
    assert destination.exists()
    assert _sha256(destination) == original_sha


# ---------------------------------------------------------------------------
# transaction / publication safety
# ---------------------------------------------------------------------------


def _memo_records() -> list[dict[str, Any]]:
    return [
        {"CODE": "A1", "NOTE": "memo one"},
        {"CODE": "B2", "NOTE": "memo two", "__deleted__": True},
        {"CODE": "C3", "NOTE": "memo three"},
    ]


@pytest.fixture()
def published_pair(tmp_path: Path) -> tuple[Path, TableSchema, str]:
    destination = tmp_path / "pair.dbf"
    fields = (_field("CODE", "C", 5), _field("NOTE", "M", 4))
    schema = _schema(fields)
    result = write_table(destination, schema=schema, records=_memo_records())
    return destination, schema, result.dbf_sha256


def _fail_on_nth_partial_replace(replace_number: int) -> Any:
    """Fail the Nth ``os.replace`` whose SOURCE is a staged ``.partial`` file
    (1 = the staged FPT replace, 2 = the staged DBF replace)."""
    original = write_backend.os.replace
    state = {"n": 0}

    def _replace(source: Any, destination: Any, /, **kwargs: Any) -> None:
        if ".partial" in str(source):
            state["n"] += 1
            if state["n"] == replace_number:
                raise OSError(f"injected failure at final replace #{replace_number}")
        return original(source, destination, **kwargs)

    return _replace


@pytest.mark.parametrize("call_number", [1, 2])
def test_handled_publication_failure_restores_the_previous_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    published_pair: tuple[Path, TableSchema, str],
    call_number: int,
) -> None:
    """Inject at the first and the second FINAL replace (FPT then DBF): the
    previous DBF/FPT pair is restored exactly and no residue remains."""
    destination, schema, original_sha = published_pair
    monkeypatch.setattr(
        write_backend.os, "replace", _fail_on_nth_partial_replace(call_number)
    )
    with pytest.raises(WritePublicationFailedError):
        write_table(
            destination,
            schema=schema,
            records=_memo_records(),
            overwrite=True,
        )
    assert destination.exists()
    assert _sha256(destination) == original_sha
    assert (destination.with_suffix(".fpt")).exists()
    assert list(tmp_path.glob("*.partial*")) == []
    assert list(tmp_path.glob("*.publish-backup*")) == []


@pytest.mark.parametrize("suffix", [".dbf", ".fpt"])
def test_fsync_failure_before_publication_leaves_old_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    published_pair: tuple[Path, TableSchema, str],
    suffix: str,
) -> None:
    """Inject a fsync failure on the DBF and on the FPT staging file: a
    pre-publication failure is a typed destination I/O error and neither the
    previous pair nor the residue state is disturbed."""
    destination, schema, original_sha = published_pair
    original = write_backend._fsync_file

    def _boom(path: Any) -> None:
        if path.suffix == suffix:
            raise OSError(f"injected fsync failure ({suffix})")
        original(path)

    monkeypatch.setattr(write_backend, "_fsync_file", _boom)
    # A pre-publication fsync failure is a typed destination I/O error;
    # nothing was published, so the previous pair stays intact.
    with pytest.raises(DestinationIoError):
        write_table(
            destination,
            schema=schema,
            records=_memo_records(),
            overwrite=True,
        )
    assert destination.exists()
    assert _sha256(destination) == original_sha
    assert list(tmp_path.glob("*.partial*")) == []


def _flat_pair(tmp_path: Path) -> tuple[Path, TableSchema, str, str | None]:
    destination = tmp_path / "pair.dbf"
    schema = _schema(_PLAIN_FIELDS)
    result = write_table(destination, schema=schema, records=_plain_records())
    fpt = destination.with_suffix(".fpt")
    return (
        destination,
        schema,
        result.dbf_sha256,
        _sha256(fpt) if fpt.exists() else None,
    )


def test_overwrite_dbf_fpt_to_dbf_only_failure_restores_the_full_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DBFB-PUB-004/005 (DBF+FPT -> DBF-only): the old FPT must stay
    restorable until the whole replacement succeeds.

    The new schema has NO memo column, so the old FPT disappears only after
    the new DBF publication succeeded.  With the final staged DBF replace
    failing, BOTH previous files must be back byte-for-byte and no residue
    may remain (regression written RED: the old code unlinked the previous
    FPT before the DBF replace without any backup)."""
    destination = tmp_path / "pair.dbf"
    memo_schema = _schema((_field("CODE", "C", 5), _field("NOTE", "M", 4)))
    first = write_table(destination, schema=memo_schema, records=_memo_records())
    old_dbf_sha, old_fpt_sha = first.dbf_sha256, first.fpt_sha256

    flat_schema = _schema(_PLAIN_FIELDS)
    monkeypatch.setattr(write_backend.os, "replace", _fail_on_nth_partial_replace(1))
    with pytest.raises(WritePublicationFailedError):
        write_table(
            destination,
            schema=flat_schema,
            records=_plain_records(),
            overwrite=True,
        )

    fpt = destination.with_suffix(".fpt")
    assert destination.exists()
    assert _sha256(destination) == old_dbf_sha
    assert fpt.exists(), "the previous FPT must be restored on handled failure"
    assert _sha256(fpt) == old_fpt_sha
    assert list(tmp_path.glob("*.partial*")) == []
    assert list(tmp_path.glob("*.publish-backup*")) == []


def test_overwrite_dbf_only_to_dbf_only_failure_restores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destination = tmp_path / "pair.dbf"
    schema = _schema(_PLAIN_FIELDS)
    first = write_table(destination, schema=schema, records=_plain_records())
    old_dbf_sha = first.dbf_sha256

    monkeypatch.setattr(write_backend.os, "replace", _fail_on_nth_partial_replace(1))
    with pytest.raises(WritePublicationFailedError):
        write_table(
            destination,
            schema=schema,
            records=_plain_records(),
            overwrite=True,
        )
    assert destination.exists()
    assert _sha256(destination) == old_dbf_sha
    assert list(tmp_path.glob("*.partial*")) == []
    assert list(tmp_path.glob("*.publish-backup*")) == []


def test_overwrite_dbf_only_to_dbf_fpt_failure_restores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """DBF-only -> DBF+FPT: a failure at either staged replace (FPT or DBF)
    must leave exactly the previous DBF and no residue."""
    destination = tmp_path / "pair.dbf"
    flat_schema = _schema(_PLAIN_FIELDS)
    first = write_table(destination, schema=flat_schema, records=_plain_records())
    old_dbf_sha = first.dbf_sha256

    memo_schema = _schema((_field("CODE", "C", 5), _field("NOTE", "M", 4)))
    for replace_number in (1, 2):
        monkeypatch.setattr(
            write_backend.os, "replace", _fail_on_nth_partial_replace(replace_number)
        )
        with pytest.raises(WritePublicationFailedError):
            write_table(
                destination,
                schema=memo_schema,
                records=_memo_records(),
                overwrite=True,
            )
        assert destination.exists()
        assert _sha256(destination) == old_dbf_sha
        assert not destination.with_suffix(".fpt").exists()
        assert list(tmp_path.glob("*.partial*")) == []
        assert list(tmp_path.glob("*.publish-backup*")) == []


def test_staging_volume_identity_helper_accepts_the_same_device(tmp_path: Path) -> None:
    destination = tmp_path / "out" / "pair.dbf"
    staging = tmp_path / "staging"
    write_backend.ensure_staging_same_volume(destination, staging)
    write_backend.ensure_staging_same_volume(destination, None)
    write_backend.ensure_staging_same_volume(destination, destination.parent)


def test_staging_volume_identity_refuses_a_different_device(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The device-identity policy must refuse a foreign staging directory
    without needing a second real filesystem: the nearest existing ancestor
    of the staging path reports a different ``st_dev``."""
    destination = tmp_path / "out" / "pair.dbf"
    staging = tmp_path / "staging"
    staging.mkdir()
    real_stat = write_backend.os.stat
    foreign_device = (real_stat(tmp_path).st_dev or 0) + 1

    def _stat(path: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        result = real_stat(path, *args, **kwargs)
        if str(staging) in str(path):
            return os.stat_result(
                (result.st_mode, result.st_ino, foreign_device, result.st_nlink,
                 result.st_uid, result.st_gid, result.st_size, result.st_atime,
                 result.st_mtime, result.st_ctime),
            )
        return result

    monkeypatch.setattr(write_backend.os, "stat", _stat)
    with pytest.raises(Exception) as error:
        write_backend.ensure_staging_same_volume(destination, staging)
    assert error.value.code == ErrorCode.ARGUMENT_INVALID.value


def test_write_table_refuses_foreign_volume_staging_before_staging_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal must happen BEFORE the spool/staging directories are
    created (DBFB-PUB-003): no spool, DBF, FPT or partial may exist on either
    volume afterwards."""
    destination = tmp_path / "pair.dbf"
    staging = tmp_path / "staging"
    staging.mkdir()
    real_stat = write_backend.os.stat
    foreign_device = (real_stat(tmp_path).st_dev or 0) + 1

    def _stat(path: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        result = real_stat(path, *args, **kwargs)
        if str(staging) in str(path):
            return os.stat_result(
                (result.st_mode, result.st_ino, foreign_device, result.st_nlink,
                 result.st_uid, result.st_gid, result.st_size, result.st_atime,
                 result.st_mtime, result.st_ctime),
            )
        return result

    monkeypatch.setattr(write_backend.os, "stat", _stat)
    # A schema that would need the private spool (Varchar/_NullFlags second
    # pass) proves the check precedes spool creation.
    varchar_schema = _schema((
        _field("CODE", "C", 5),
        _field("TXT", "V", 12, flags=0x02),
        _field("NULFLAGS", "0", 1, flags=0x05),
    ))
    with pytest.raises(Exception) as error:
        write_table(
            destination,
            schema=varchar_schema,
            records=_plain_records(),
            staging_directory=staging,
        )
    assert error.value.code == ErrorCode.ARGUMENT_INVALID.value
    assert list(staging.iterdir()) == [], "no spool may be created in foreign staging"
    assert not destination.exists()
    assert list(tmp_path.glob("*.partial*")) == []
    assert list(tmp_path.glob("*spool*")) == []


def test_staging_on_a_different_volume_is_refused_without_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    destination = tmp_path / "vol.dbf"
    schema = _schema(_PLAIN_FIELDS)

    def _fake_splitdrive(path: Any, *args: Any, **kwargs: Any) -> tuple[str, str]:
        text = str(path).replace("\\", "/")
        if text.endswith("/staging"):
            return ("\\\\?\\Volume{staging}", text)
        return ("\\\\?\\Volume{destination}", text)

    monkeypatch.setattr(write_backend.os.path, "splitdrive", _fake_splitdrive)
    with pytest.raises(Exception) as error:
        write_table(
            destination,
            schema=schema,
            records=_plain_records(),
            staging_directory=tmp_path / "staging",
        )
    assert error.value.code == ErrorCode.ARGUMENT_INVALID.value
    assert error.value.code == "ARGUMENT_INVALID"
    assert not destination.exists()
    assert list(tmp_path.glob("*.partial*")) == []


# ---------------------------------------------------------------------------
# overwrite / OUTPUT_EXISTS
# ---------------------------------------------------------------------------


def test_overwrite_default_false_reuses_output_exists(tmp_path: Path) -> None:
    destination = tmp_path / "exists.dbf"
    schema = _schema(_PLAIN_FIELDS)
    write_table(destination, schema=schema, records=_plain_records())
    original_sha = _sha256(destination)
    with pytest.raises(OperationOutputExistsError) as error:
        write_table(destination, schema=schema, records=_plain_records())
    assert error.value.code == "OUTPUT_EXISTS"
    assert _sha256(destination) == original_sha
    # nothing was modified and no residue appeared
    assert list(tmp_path.glob("*.partial*")) == []


def test_overwrite_true_replaces_the_pair(tmp_path: Path) -> None:
    destination = tmp_path / "replaced.dbf"
    schema = _schema(_PLAIN_FIELDS)
    write_table(destination, schema=schema, records=_plain_records())
    write_table(
        destination,
        schema=schema,
        records=_plain_records()[:1],
        overwrite=True,
    )
    page = read_records(destination)
    assert [record.values["CODE"] for record in page.records] == ["A1"]
    assert len(page.records) == 1


# ---------------------------------------------------------------------------
# progress
# ---------------------------------------------------------------------------


def test_progress_uses_the_canonical_progress_event(tmp_path: Path) -> None:
    from dbf_bridge.progress import ProgressEvent

    destination = tmp_path / "progress.dbf"
    schema = _schema(_PLAIN_FIELDS)
    events: list[ProgressEvent] = []
    result = write_table(
        destination,
        schema=schema,
        records=_plain_records(),
        progress=events.append,
    )
    assert events, "progress events were emitted"
    assert all(isinstance(event, ProgressEvent) for event in events)
    assert all(event.operation == "write" for event in events)
    assert events[-1].current == result.records_written
    assert events[-1].total == result.records_written
    assert events[0].table == destination.as_posix()


# ---------------------------------------------------------------------------
# error / privacy sentinels
# ---------------------------------------------------------------------------


def test_backend_failure_payloads_never_leak_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "privacy.dbf"
    schema = _schema((_field("CODE", "C", 5),))

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError(f"injected leak {SECRET_RECORD_VALUE} {SECRET_MEMO_PAYLOAD!r}")

    monkeypatch.setattr(write_backend, "_fsync_file", _boom)
    with pytest.raises(WritePublicationFailedError) as error:
        write_table(destination, schema=schema, records=iter([{"CODE": "A1"}]))
    text = str(error.value)
    payload = json.dumps(error.value.to_dict())
    assert SECRET_RECORD_VALUE not in text
    assert SECRET_RECORD_VALUE not in payload
    assert SECRET_MEMO_PAYLOAD.decode() not in payload


def test_write_family_is_not_caught_by_direct_read_error() -> None:
    from dbf_bridge.core.errors import DirectWriteError

    for cls in (
        WriteSchemaInvalidError,
        WriteValueInvalidError,
        WriteMemoFailedError,
        WritePublicationFailedError,
        WriteCancelledError,
        WriteFieldUnsupportedError,
    ):
        assert issubclass(cls, DirectWriteError)
        assert not issubclass(cls, DirectReadError)
        assert cls(  # every payload is JSON-safe
            "boom", context={"field": "x"}
        ).to_dict()["code"].startswith("WRITE_") or cls.__name__ in {
            "WriteCancelledError",
            "WritePublicationFailedError",
        }


def test_every_new_write_code_is_registered() -> None:
    expected = {
        "DESTINATION_IO_ERROR",
        "WRITE_SCHEMA_INVALID",
        "WRITE_FIELD_UNSUPPORTED",
        "WRITE_VALUE_INVALID",
        "WRITE_MEMO_FAILED",
        "WRITE_PUBLICATION_FAILED",
        "WRITE_CANCELLED",
    }
    current = {member.value for member in ErrorCode}
    assert expected <= current


# ---------------------------------------------------------------------------
# VFP canonical equivalence (Direct Read -> Direct Write -> Direct Read)
# ---------------------------------------------------------------------------


def _assert_canonical_equivalence(source: Path, destination: Path, *, include_deleted: bool) -> None:
    original = (
        list(iter_records(source, memo="inline", include_deleted=True))
        if include_deleted
        else list(iter_records(source, memo="inline"))
    )
    rebuilt = (
        list(iter_records(destination, memo="inline", include_deleted=True))
        if include_deleted
        else list(iter_records(destination, memo="inline"))
    )
    assert len(rebuilt) == len(original)
    for before, after in zip(original, rebuilt, strict=True):
        assert after.deleted == before.deleted
        for name, value in before.values.items():
            if name.startswith("_") or name.startswith("__dbfbridge"):
                continue
            assert after.values.get(name) == value, name


def test_round_trip_plain_table_with_codepage_and_deleted(
    tmp_path: Path, sample_input_dir: Path
) -> None:
    source = sample_input_dir / "klienci.dbf"
    schema = read_schema(source)
    records = list(iter_records(source, memo="inline", include_deleted=True))
    destination = tmp_path / "klienci-copy.dbf"
    result = write_table(destination, schema=schema, records=records)
    assert result.records_written == schema.record_count
    _assert_canonical_equivalence(source, destination, include_deleted=True)


def test_round_trip_varchar_nullflags_and_nulls(tmp_path: Path) -> None:
    source = tmp_path / "v.dbf"
    build_vfp32_table(
        source,
        columns=[
            {"name": "TXT", "type": "V", "width": 12, "nullable": True},
            {"name": "NOTE", "type": "C", "width": 6, "nullable": True},
        ],
        rows=[
            {"TXT": "short", "NOTE": None},
            {"TXT": "0123456789", "NOTE": "full"},
            {"TXT": None, "NOTE": "x"},
        ],
    )
    schema = read_schema(source)
    records = list(iter_records(source))
    destination = tmp_path / "v-copy.dbf"
    write_table(destination, schema=schema, records=records)
    _assert_canonical_equivalence(source, destination, include_deleted=False)


@pytest.mark.parametrize(
    ("codepage", "encoding"),
    [(0xC8, "cp1250"), (0x23, "cp852"), (0x69, "mazovia")],
)
def test_round_trip_polish_codepages_with_varchar_and_nulls(
    tmp_path: Path, codepage: int, encoding: str
) -> None:
    """Canonical Direct Read -> Direct Write -> Direct Read equivalence for
    the supported Polish codepages (cp1250, cp852, Mazovia/PIAST) including
    Varchar, NULL and deleted markers (DBFB-VFP-002/005)."""
    source = tmp_path / f"cp-{codepage:x}.dbf"
    polish = "Żółw ąęł" if codepage == 0x69 else "Żółw ąę łó Ńź"
    build_vfp32_table(
        source,
        columns=[
            {"name": "TXT", "type": "V", "width": 24, "nullable": True},
            {"name": "NOTKA", "type": "C", "width": 12, "nullable": True},
        ],
        rows=[
            {"TXT": polish, "NOTKA": None},
            {"TXT": None, "NOTKA": "ąę łó"},
        ],
        codepage=codepage,
    )
    schema = read_schema(source)
    assert schema.language_driver == codepage
    records = list(iter_records(source, include_deleted=True))
    destination = tmp_path / f"cp-{codepage:x}-copy.dbf"
    write_table(destination, schema=schema, records=records)
    _assert_canonical_equivalence(source, destination, include_deleted=True)


def test_round_trip_deleted_physical_order_with_varchar(tmp_path: Path) -> None:
    source = tmp_path / "vd.dbf"
    build_vfp32_table(
        source,
        columns=[{"name": "TXT", "type": "V", "width": 10, "nullable": True}],
        rows=[{"TXT": "r1"}, {"TXT": "r2"}, {"TXT": "r3"}, {"TXT": "r4"}],
    )
    mark_deleted(source, 1)
    mark_deleted(source, 3)
    schema = read_schema(source)
    records = list(iter_records(source, memo="inline", include_deleted=True))
    destination = tmp_path / "vd-copy.dbf"
    result = write_table(destination, schema=schema, records=records)
    assert result.deleted_records == 2
    _assert_canonical_equivalence(source, destination, include_deleted=True)
    page = read_records(destination, include_deleted=True)
    assert [(record.values["TXT"], record.deleted) for record in page.records] == [
        ("r1", False),
        ("r2", True),
        ("r3", False),
        ("r4", True),
    ]


# ---------------------------------------------------------------------------
# structural CDX / DBC truthfulness
# ---------------------------------------------------------------------------


def test_structural_cdx_is_reported_as_rebuild_required(tmp_path: Path) -> None:
    schema = _schema(_PLAIN_FIELDS, has_structural_cdx=True)
    result = write_table(tmp_path / "cdx.dbf", schema=schema, records=_plain_records())
    assert result.structural_cdx is True
    assert result.index_rebuild_required is True
    assert result.to_dict()["structural_cdx"] is True
    assert any("CDX" in warning for warning in result.warnings)
    assert not (tmp_path / "cdx.cdx").exists()


def test_structural_cdx_warning_is_single_source_and_truthful(tmp_path: Path) -> None:
    """DBFB-WRITE-005 / DBFB-CDX-002..004 / DBFB-DOC-002: the ONE authoritative
    structural-CDX warning states the limitation and the external rebuild
    requirement WITHOUT any byte/binary-identity claim, and Direct Write does
    not append a second, overlapping CDX warning.

    Regression written RED: the shared backend warning used to say the
    structural-index flag is "preserved for binary identity" — a promise
    Direct Write does not make."""
    schema = _schema(_PLAIN_FIELDS, has_structural_cdx=True)
    result = write_table(tmp_path / "cdx.dbf", schema=schema, records=_plain_records())
    assert result.structural_cdx is True
    assert result.index_rebuild_required is True
    assert result.warnings, "the CDX limitation must be explicit in the result"
    lowered = " ".join(result.warnings).casefold()
    # Semantic tokens, not frozen prose: limitation + external rebuild.
    assert "structural cdx" in lowered
    assert "rebuilt externally" in lowered
    # No identity claim of any kind.
    assert "binary identity" not in lowered
    assert "byte identity" not in lowered
    assert "identity" not in lowered
    # No CDX file was created, copied or fabricated.
    assert not (tmp_path / "cdx.cdx").exists()
    # ONE warning source: exactly one CDX warning reaches the result.
    cdx_warnings = [
        warning for warning in result.warnings if "cdx" in warning.casefold()
    ]
    assert len(cdx_warnings) == 1


def test_dbc_bound_is_truthful_with_warning(tmp_path: Path) -> None:
    schema = _schema(_PLAIN_FIELDS, dbc_bound=True, is_database_container=True)
    result = write_table(tmp_path / "dbc.dbf", schema=schema, records=_plain_records())
    assert result.dbc_bound is True
    assert any("triggers" in warning or "DBC" in warning for warning in result.warnings)


# ---------------------------------------------------------------------------
# shared physical writer evidence
# ---------------------------------------------------------------------------


def test_direct_write_delegates_to_the_shared_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "delegation.dbf"
    schema = _schema(_PLAIN_FIELDS)
    calls: list[str] = []
    original = write_backend.write_dbf

    def _spy(*args: Any, **kwargs: Any) -> Any:
        calls.append("write_dbf")
        return original(*args, **kwargs)

    monkeypatch.setattr(write_backend, "write_dbf", _spy)
    write_table(destination, schema=schema, records=_plain_records())
    assert calls == ["write_dbf"]


# ---------------------------------------------------------------------------
# optional dependency gate
# ---------------------------------------------------------------------------


def test_missing_write_extra_fails_before_any_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    def _blocked(name: str, **kwargs: Any) -> object:
        raise OptionalDependencyMissingError(
            dependency="dbf", extra="write", operation="write_table"
        )

    monkeypatch.setattr("dbf_bridge.write.api.require_optional", _blocked)
    destination = tmp_path / "fresh" / "blocked.dbf"
    with pytest.raises(OptionalDependencyMissingError):
        write_table(destination, schema=_schema(_PLAIN_FIELDS), records=_plain_records())
    assert not destination.parent.exists()


def test_fresh_interpreter_root_import_stays_lazy() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import dbfbridge; import dbf_bridge.write;"
            " assert 'dbf' not in sys.modules;"
            " assert 'dbf_bridge.write.backend' not in sys.modules;"
            " assert 'write_table' in dbfbridge.__all__;"
            " assert dbfbridge.write_table is not None; print('PASS')",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "PASS" in completed.stdout

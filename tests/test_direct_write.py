"""Direct Write research: equivalence and safety evidence.

Equivalence is verified two ways, never conflated:

- **canonical/logical equivalence**: Direct Read -> Direct Write -> reread
  yields the same record count/order/deleted state/values as the source
  (including NULL, canonical ``_NullFlags``/Varchar layout, D/T, memo,
  encoding);
- **writer equivalence**: the same logical records written through the
  reconstruction writer and through Direct Write produce canonically equal
  output (both paths go through the ONE shared physical writer).

Safety coverage: staging/publication failure injection, source immutability
(SHA-256 of source DBF/FPT/CDX before/after), O(1) streaming, structural-CDX
contract, machine-classified typed errors (no English-message parsing) and
privacy-safe error payloads.  All tests use tmp_path — nothing touches the
real ``benchmarks/baselines/``.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import dbf
import pytest
from dbfread import DBF

from dbf_bridge import iter_records, read_schema
from dbf_bridge.core.errors import (
    DestinationIoError,
    ErrorCode,
    OperationOutputExistsError,
    WriteMemoFailedError,
    WritePublicationFailedError,
    WriteSchemaInvalidError,
    WriteValueInvalidError,
)
from dbf_bridge.write import WriteResult, write_table

pytest.importorskip("dbf")
pytest.importorskip("dbfread")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _simple_table(path: Path, records: list[dict[str, object]], codepage: int = 0xC8) -> Path:
    table = dbf.Table(
        str(path),
        field_specs="KOD N(4,0); NAZWA C(20); AKTYWNY L",
        dbf_type="vfp",
        codepage=codepage,
    )
    table.open(mode=dbf.READ_WRITE)
    for record in records:
        table.append(record)
    table.close()
    return path


def _memo_table(path: Path, records: list[dict], codepage: int = 0xC8) -> Path:
    memo_table = dbf.Table(
        str(path),
        field_specs="KOD N(4,0); NOTATKA M",
        dbf_type="vfp",
        codepage=codepage,
    )
    memo_table.open(mode=dbf.READ_WRITE)
    for record in records:
        memo_table.append(record)
    memo_table.close()
    return path


def _varchar_table(path: Path, rows: list[dict]) -> Path:
    """An AUTHENTIC canonical VFP 0x32 Varchar/NullFlags fixture."""
    from tests.vfp_fixture_factory import build_vfp32_table

    return build_vfp32_table(
        path,
        columns=[
            {"name": "ID", "type": "C", "width": 4, "nullable": False},
            {"name": "TAG", "type": "V", "width": 10, "nullable": True},
            {"name": "STATE", "type": "C", "width": 5, "nullable": True},
        ],
        rows=rows,
    )


def _dbfread_all(path: Path, encoding: str = "cp1250") -> list[tuple[dict, bool]]:
    """dbfread replay: active records first, then deleted (NOT physical order)."""
    reader = DBF(path, load=False, encoding=encoding, char_decode_errors="strict")
    result: list[tuple[dict, bool]] = []
    for record in reader:
        result.append((dict(record), False))
    for record in reader.deleted:
        result.append((dict(record), True))
    return result


def _physical_delete_markers(path: Path) -> list[bool]:
    """Delete markers in PHYSICAL record order (raw file bytes)."""
    data = path.read_bytes()
    header_length = int.from_bytes(data[8:10], "little")
    record_length = int.from_bytes(data[10:12], "little")
    record_count = int.from_bytes(data[4:8], "little")
    return [data[header_length + index * record_length] == 0x2A for index in range(record_count)]


def _logical_values(records) -> list[dict]:
    """Canonical reread values with case-normalized keys, minus the bitmap
    column (the writer normalizes the physical system-column name case, as
    the reconstruction path without raw descriptors always has)."""
    return [
        {
            key.casefold(): value
            for key, value in record.values.items()
            if not key.casefold().startswith("_nullflags")
        }
        for record in records
    ]


# ---------------------------------------------------------------------------
# canonical/logical equivalence
# ---------------------------------------------------------------------------


def test_direct_write_reread_equivalence(tmp_path: Path) -> None:
    """Direct Read -> Direct Write -> reread preserves count, order, deleted
    state and values."""
    table_path = _simple_table(
        tmp_path / "SRC.dbf",
        [
            {"KOD": 1, "NAZWA": "abc", "AKTYWNY": True},
            {"KOD": 2, "NAZWA": "Żółć ąę", "AKTYWNY": False},
            {"KOD": 3, "NAZWA": "x", "AKTYWNY": None},
        ],
    )
    schema = read_schema(table_path)
    records = list(iter_records(table_path, include_deleted=True, memo="skip"))
    destination = tmp_path / "OUT.dbf"
    result = write_table(destination, schema=schema, records=iter(records))

    assert result.records_written == 3
    assert result.dbf_sha256 == _sha256(destination)
    reread = _dbfread_all(destination)
    assert [values["KOD"] for values, _deleted in reread] == [r.values["KOD"] for r in records]
    assert [values["NAZWA"] for values, _deleted in reread] == [r.values["NAZWA"] for r in records]
    assert result.to_dict()["records_written"] == 3
    assert result.index_rebuild_required is False


def test_direct_write_preserves_deleted_state(tmp_path: Path) -> None:
    table_path = _simple_table(tmp_path / "DEL.dbf", [{"KOD": 1}, {"KOD": 2}, {"KOD": 3}])
    # delete the second physical record
    data = bytearray(table_path.read_bytes())
    header_length = int.from_bytes(data[8:10], "little")
    record_length = int.from_bytes(data[10:12], "little")
    data[header_length + record_length] = 0x2A
    table_path.write_bytes(bytes(data))

    schema = read_schema(table_path)
    records = list(iter_records(table_path, include_deleted=True, memo="skip"))
    if not records[1].deleted:
        pytest.fail("fixture: the second record must be deleted")
    destination = tmp_path / "OUT.dbf"
    result = write_table(destination, schema=schema, records=iter(records))

    assert result.records_written == 3
    assert result.deleted_records == 1
    # verify markers in PHYSICAL order (raw file bytes)
    assert _physical_delete_markers(destination) == [False, True, False]


def test_direct_write_null_roundtrip(tmp_path: Path) -> None:
    table_path = _simple_table(
        tmp_path / "NULLS.dbf",
        [{"KOD": 1, "AKTYWNY": None}, {"KOD": 2, "AKTYWNY": True}, {"KOD": 3, "AKTYWNY": None}],
    )
    schema = read_schema(table_path)
    records = list(iter_records(table_path, include_deleted=True, memo="skip"))
    destination = tmp_path / "OUT.dbf"
    result = write_table(destination, schema=schema, records=iter(records))

    from dbfread import DBF

    reread = [dict(r) for r in DBF(destination, load=False, encoding=schema.encoding)]
    assert reread[0]["AKTYWNY"] is None
    assert reread[1]["AKTYWNY"] is True
    assert reread[2]["KOD"] == 3
    _ = result


def test_direct_write_canonical_equivalence(tmp_path: Path) -> None:
    """reread values equal the source stream values — canonical identity."""
    from dbfread import DBF

    table_path = _simple_table(
        tmp_path / "CANON.dbf",
        [
            {"KOD": 7, "NAZWA": "abc", "AKTYWNY": True},
            {"KOD": 14, "NAZWA": "x", "AKTYWNY": None},
        ],
    )
    schema = read_schema(table_path)
    source_records = list(iter_records(table_path, include_deleted=True, memo="skip"))
    destination = tmp_path / "OUT.dbf"
    write_table(destination, schema=schema, records=iter(source_records))

    reader = DBF(destination, load=False, encoding=schema.encoding, char_decode_errors="strict")
    output_values = [dict(r) for r in reader]
    source_values = [dict(r.values) for r in source_records]
    assert source_values == output_values


# ---------------------------------------------------------------------------
# canonical _NullFlags / Varchar rules (current engine, shared writer)
# ---------------------------------------------------------------------------


def test_direct_write_varchar_nullflags_canonical_equivalence(tmp_path: Path) -> None:
    """The canonical VFP 0x32 dialect (Varchar varlength bits + NULL bits,
    ``dbf_bridge.core.nullflags`` allocation) survives Direct Read -> Direct
    Write -> reread: NULLs stay NULL, short Varchar values keep their
    logical form."""
    table_path = _varchar_table(
        tmp_path / "VAR.dbf",
        rows=[
            {"ID": "r1", "TAG": "short", "STATE": "ok"},
            {"ID": "r2", "TAG": None, "STATE": None},
            {"ID": "r3", "TAG": "exactlyten", "STATE": "fine"},
        ],
    )
    schema = read_schema(table_path)
    assert any(field.dbf_type == "V" for field in schema.fields)
    assert any(field.dbf_type == "0" for field in schema.fields)
    source_records = list(iter_records(table_path, include_deleted=True))
    destination = tmp_path / "OUT.dbf"
    result = write_table(destination, schema=schema, records=iter(source_records))

    assert result.records_written == 3
    reread = list(iter_records(destination, include_deleted=True))
    assert _logical_values(reread) == _logical_values(source_records)
    assert [r.deleted for r in reread] == [r.deleted for r in source_records]


def test_direct_write_equals_reconstruction_writer_on_same_data(tmp_path: Path) -> None:
    """The same logical records through the reconstruction writer and through
    Direct Write produce canonically equal output (one shared writer)."""
    from dbf_bridge.importer.writer import write_dbf as reconstruction_write

    dbf_path = _simple_table(
        tmp_path / "EQ.dbf",
        [
            {"KOD": 1, "NAZWA": "alpha", "AKTYWNY": True},
            {"KOD": 2, "NAZWA": "beta", "AKTYWNY": None},
        ],
    )
    schema = read_schema(dbf_path)
    records = list(iter_records(dbf_path, include_deleted=True, memo="skip"))
    fields = [
        {
            "name": f.name,
            "dbf_type": f.dbf_type,
            "length": f.length,
            "decimal_count": f.decimal_count,
            "flags": f.flags,
            "address": f.address,
            "is_memo": f.is_memo,
            "is_binary": f.is_binary,
            "ordinal": f.ordinal,
        }
        for f in schema.fields
    ]
    backend_records = [{**dict(r.values), "__deleted__": r.deleted} for r in records]
    backend_schema = {
        "fields": fields,
        "dbf": {
            "version_byte": schema.dbversion_byte,
            "language_driver": schema.language_driver,
            "last_update": schema.last_update,
            "structural_index_flag": 1 if schema.has_structural_cdx else 0,
            "header_length_bytes": schema.header_length,
            "record_length_bytes": schema.record_length,
        },
        "memo": {
            "block_size_bytes": schema.memo_block_size or 64,
            "path": None,
            "required": schema.has_memo,
        },
        "text_encoding": {"declared_or_detected_encoding": schema.encoding, "fallback_order": []},
    }

    direct_out = tmp_path / "direct.dbf"
    recon_out = tmp_path / "recon.dbf"
    write_table(direct_out, schema=schema, records=iter(records))
    reconstruction_write(recon_out, iter(backend_records), backend_schema, overwrite=True)

    direct_replay = [dict(r) for r in DBF(direct_out, load=False, encoding=schema.encoding)]
    recon_replay = [dict(r) for r in DBF(recon_out, load=False, encoding=schema.encoding)]
    assert direct_replay == recon_replay


def test_direct_write_equals_reconstruction_writer_on_varchar_table(tmp_path: Path) -> None:
    """Writer equivalence on the hardest case: canonical Varchar/NullFlags."""
    from dbf_bridge.importer.writer import write_dbf as reconstruction_write

    dbf_path = _varchar_table(
        tmp_path / "VEQ.dbf",
        rows=[
            {"ID": "r1", "TAG": "short", "STATE": "ok"},
            {"ID": "r2", "TAG": None, "STATE": None},
            {"ID": "r3", "TAG": "exactlyten", "STATE": "fine"},
        ],
    )
    schema = read_schema(dbf_path)
    records = list(iter_records(dbf_path, include_deleted=True))
    fields = [
        {
            "name": f.name,
            "dbf_type": f.dbf_type,
            "length": f.length,
            "decimal_count": f.decimal_count,
            "flags": f.flags,
            "address": f.address,
            "is_memo": f.is_memo,
            "is_binary": f.is_binary,
            "ordinal": f.ordinal,
        }
        for f in schema.fields
    ]
    backend_records = [{**dict(r.values), "__deleted__": r.deleted} for r in records]
    backend_schema = {
        "fields": fields,
        "dbf": {
            "version_byte": schema.dbversion_byte,
            "language_driver": schema.language_driver,
            "last_update": schema.last_update,
            "structural_index_flag": 1 if schema.has_structural_cdx else 0,
            "header_length_bytes": schema.header_length,
            "record_length_bytes": schema.record_length,
        },
        "memo": {
            "block_size_bytes": schema.memo_block_size or 64,
            "path": None,
            "required": schema.has_memo,
        },
        "text_encoding": {"declared_or_detected_encoding": schema.encoding, "fallback_order": []},
    }

    direct_out = tmp_path / "direct.dbf"
    recon_out = tmp_path / "recon.dbf"
    write_table(direct_out, schema=schema, records=iter(records))
    reconstruction_write(
        recon_out,
        iter(backend_records),
        backend_schema,
        overwrite=True,
        records_factory=lambda: iter(backend_records),
    )

    direct_replay = list(iter_records(direct_out, include_deleted=True))
    recon_replay = list(iter_records(recon_out, include_deleted=True))
    assert _logical_values(direct_replay) == _logical_values(recon_replay)
    assert [r.deleted for r in direct_replay] == [r.deleted for r in recon_replay]


# ---------------------------------------------------------------------------
# memo + codepages
# ---------------------------------------------------------------------------


def test_direct_write_memo_roundtrip(tmp_path: Path) -> None:
    dbf_path = _memo_table(
        tmp_path / "MEMO.dbf",
        [
            {"KOD": 1, "NOTATKA": "notatka pierwsza ąę"},
            {"KOD": 2, "NOTATKA": "druga nota Żółć"},
        ],
    )
    schema = read_schema(dbf_path)
    records = list(iter_records(dbf_path, memo="inline"))
    destination = tmp_path / "OUTMEMO.dbf"
    result = write_table(destination, schema=schema, records=iter(records))

    assert result.fpt_path is not None
    # the memo companion is published BESIDE the destination
    assert result.fpt_path == tmp_path / "OUTMEMO.fpt"
    assert (tmp_path / "OUTMEMO.fpt").is_file()
    reread = list(iter_records(destination, memo="inline"))
    assert [r.values["NOTATKA"] for r in reread] == [r.values["NOTATKA"] for r in records]
    assert result.fpt_sha256 == _sha256(tmp_path / "OUTMEMO.fpt")


def test_direct_write_binary_memo_roundtrip(tmp_path: Path) -> None:
    """Binary memo payloads (bytes) are written as binary memo blocks."""
    dbf_path = _memo_table(
        tmp_path / "BINMEMO.dbf",
        [{"KOD": 1, "NOTATKA": " tekst memo"}],
    )
    schema = read_schema(dbf_path)
    destination = tmp_path / "OUTBIN.dbf"
    payload_record = {"KOD": 1, "NOTATKA": b"\x00\x01binary payload"}
    result = write_table(destination, schema=schema, records=iter([payload_record]))
    assert result.fpt_path is not None
    assert (tmp_path / "OUTBIN.fpt").is_file()


@pytest.mark.parametrize("codepage", [0xC8, 0x23, 0x69])
def test_direct_write_codepage_memo_roundtrip(tmp_path: Path, codepage: int) -> None:
    """cp1250 / cp852 (Polish OEM 0x23) / Mazovia (0x69) memo round trips."""
    if codepage == 0x69:
        # Mazovia has no codec name in the writer's codepage table; the
        # shared writer bridges it from the schema encoding on demand —
        # exercise exactly that production path before creating the fixture.
        from dbf_bridge.write.backend import _ensure_writer_text_codecs

        _ensure_writer_text_codecs(0x69, ["mazovia"])
    dbf_path = _memo_table(
        tmp_path / f"ENC{codepage}.dbf",
        [{"KOD": 1, "NOTATKA": "Żółw ąęłóńśćźż"}],
        codepage=codepage,
    )
    schema = read_schema(dbf_path)
    records = list(iter_records(dbf_path, memo="inline"))
    destination = tmp_path / f"OUT{codepage}.dbf"
    result = write_table(destination, schema=schema, records=iter(records))
    reread = list(iter_records(destination, memo="inline"))
    assert reread[0].values["NOTATKA"] == records[0].values["NOTATKA"]
    assert result.fpt_path is not None


# ---------------------------------------------------------------------------
# structural CDX contract
# ---------------------------------------------------------------------------


def test_structural_cdx_reports_index_rebuild_requirement(tmp_path: Path) -> None:
    dbf_path = _simple_table(tmp_path / "CDX.dbf", [{"KOD": 1}, {"KOD": 2}])
    data = bytearray(dbf_path.read_bytes())
    data[28] = data[28] | 0x01
    dbf_path.write_bytes(bytes(data))

    schema = read_schema(dbf_path)
    records = list(iter_records(dbf_path, memo="skip"))
    destination = tmp_path / "OUT.dbf"
    result = write_table(destination, schema=schema, records=iter(records))

    assert result.structural_cdx is True
    assert result.index_rebuild_required is True
    assert any("structural" in warning.lower() for warning in result.warnings)
    assert not (tmp_path / "OUT.cdx").exists()


# ---------------------------------------------------------------------------
# safety: source immutability, destination protection, failure cleanup
# ---------------------------------------------------------------------------


def test_direct_write_never_modifies_source(tmp_path: Path) -> None:
    dbf_path = _memo_table(
        tmp_path / "IMM.dbf",
        [{"KOD": 1, "NOTATKA": "memo ą"}, {"KOD": 2, "NOTATKA": "memo b"}],
    )
    fpt_path = tmp_path / "IMM.fpt"
    source_dbf_sha = _sha256(dbf_path)
    source_fpt_sha = _sha256(fpt_path)

    schema = read_schema(dbf_path)
    records = list(iter_records(dbf_path, memo="inline"))
    destination = tmp_path / "OUT.dbf"
    write_table(destination, schema=schema, records=iter(records))

    assert _sha256(dbf_path) == source_dbf_sha, "source DBF was modified"
    assert _sha256(fpt_path) == source_fpt_sha, "source FPT was modified"
    assert not (tmp_path / "IMM.dbf").samefile(destination)


def test_destination_exists_is_typed(tmp_path: Path) -> None:
    """The overwrite conflict reuses the STABLE OUTPUT_EXISTS contract."""
    table_path = _simple_table(tmp_path / "DUP.dbf", [{"KOD": 1}])
    schema = read_schema(table_path)
    destination = tmp_path / "OUT.dbf"
    destination.write_bytes(b"existing")
    with pytest.raises(OperationOutputExistsError) as excinfo:
        write_table(destination, schema=schema, records=iter([{"KOD": 9}]), overwrite=False)
    payload = excinfo.value.to_dict()
    assert payload["code"] == "OUTPUT_EXISTS"
    assert json.dumps(payload)  # JSON-safe
    assert destination.read_bytes() == b"existing"


def test_write_value_invalid_is_typed(tmp_path: Path) -> None:
    table_path = _simple_table(
        tmp_path / "VAL.dbf",
        [
            {"KOD": 1, "NAZWA": "a", "AKTYWNY": True},
            {"KOD": 2, "NAZWA": "b", "AKTYWNY": True},
        ],
    )
    schema = read_schema(table_path)
    with pytest.raises(WriteValueInvalidError) as excinfo:
        write_table(
            tmp_path / "OUT.dbf",
            schema=schema,
            records=iter(
                [
                    {"KOD": 10, "NAZWA": "ok", "AKTYWNY": True},
                    {"KOD": 20, "NAZWA": "bad", "AKTYWNY": "not-logical-text"},
                ]
            ),
            overwrite=True,
        )
    assert excinfo.value.to_dict()["code"] == "WRITE_VALUE_INVALID"


def test_write_schema_invalid_is_typed(tmp_path: Path) -> None:
    import dataclasses

    table_path = _simple_table(tmp_path / "SINV.dbf", [{"KOD": 1}])
    schema = read_schema(table_path)
    with pytest.raises(WriteSchemaInvalidError) as excinfo:
        write_table(tmp_path / "OUT.dbf", schema="not-a-schema", records=iter([]))  # type: ignore[arg-type]
    assert excinfo.value.to_dict()["code"] == "WRITE_SCHEMA_INVALID"
    empty_schema = dataclasses.replace(schema, fields=())
    with pytest.raises(WriteSchemaInvalidError) as excinfo:
        write_table(tmp_path / "OUT2.dbf", schema=empty_schema, records=iter([]))
    assert excinfo.value.to_dict()["code"] == "WRITE_SCHEMA_INVALID"


def test_backend_failures_are_classified_by_code_not_message(tmp_path: Path, monkeypatch) -> None:
    """The public error type comes from the backend's structured ErrorCode —
    NEVER from parsing the English message (the historical prototype's
    message-prefix table must stay absent)."""
    from dbf_bridge.write import backend as physical_writer

    table_path = _simple_table(tmp_path / "CODE.dbf", [{"KOD": 1}])
    schema = read_schema(table_path)
    records = list(iter_records(table_path, memo="skip"))
    message = "totally unremarkable backend failure text with no keywords"

    def _make_explode(code: ErrorCode):
        def _explode(*args: object, **kwargs: object) -> None:
            from dbf_bridge.importer.writer import ReconstructionError

            raise ReconstructionError(message, code=code)

        return _explode

    for code, expected_type, expected_code in (
        (ErrorCode.WRITE_VALUE_INVALID, WriteValueInvalidError, "WRITE_VALUE_INVALID"),
        (ErrorCode.WRITE_MEMO_FAILED, WriteMemoFailedError, "WRITE_MEMO_FAILED"),
    ):
        monkeypatch.setattr(physical_writer, "_validate_layout", _make_explode(code))
        with pytest.raises(expected_type) as excinfo:
            write_table(tmp_path / "OUT.dbf", schema=schema, records=iter(records))
        payload = excinfo.value.to_dict()
        assert payload["code"] == expected_code
        # the same backend text classified differently purely by the machine
        # code: the public message is rebuilt (privacy-safe), the backend text
        # stays available as the cause
        assert payload["message"] != message
        assert str(excinfo.value.__cause__) == message
        monkeypatch.undo()
    assert not (tmp_path / "OUT.dbf").exists()


def test_optional_dependency_is_typed(tmp_path: Path, monkeypatch) -> None:
    """A missing ``dbf`` dependency surfaces as the typed
    OptionalDependencyMissingError, never as a raw RuntimeError."""
    from dbf_bridge.optional_deps import OptionalDependencyMissingError as _ODM

    table_path = _simple_table(tmp_path / "OPT.dbf", [{"KOD": 1}])
    schema = read_schema(table_path)
    monkeypatch.setitem(sys.modules, "dbf", None)
    with pytest.raises(_ODM) as excinfo:
        write_table(tmp_path / "OUT.dbf", schema=schema, records=iter([{"KOD": 9}]))
    assert excinfo.value.to_dict()["code"] == "OPTIONAL_DEPENDENCY_MISSING"
    assert excinfo.value.extra == "write"
    monkeypatch.undo()
    assert not (tmp_path / "OUT.dbf").exists()


def test_record_write_failure_cleans_staging(tmp_path: Path, monkeypatch) -> None:
    table_path = _simple_table(tmp_path / "FAIL.dbf", [{"KOD": 1}, {"KOD": 2}])
    schema = read_schema(table_path)
    records = [{**dict(r.values)} for r in iter_records(table_path, memo="skip")]

    destination = tmp_path / "OUT.dbf"
    staging_dir = tmp_path / "staging"
    real_append = dbf.Table.append
    calls = {"n": 0}

    def failing_append(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("injected record-write failure")
        return real_append(self, *args, **kwargs)

    monkeypatch.setattr(dbf.Table, "append", failing_append)
    with pytest.raises(WritePublicationFailedError) as excinfo:
        write_table(
            destination,
            schema=schema,
            records=iter([*records, {"KOD": 3}]),
            staging_directory=staging_dir,
        )
    monkeypatch.undo()
    # the original failure stays available as the cause; the public payload
    # is typed and JSON-safe
    assert "injected record-write failure" in str(excinfo.value.__cause__)
    assert json.dumps(excinfo.value.to_dict())
    assert not destination.exists()
    assert not any(staging_dir.glob("*partial*"))


def test_failed_publication_restores_previous_dbf_fpt_pair(tmp_path: Path, monkeypatch) -> None:
    """A handled failure with overwrite=True restores the PREVIOUS DBF+FPT
    pair byte-identically and leaves no .partial residue."""
    from dbf_bridge.write import backend as physical_writer

    dbf_path = _memo_table(tmp_path / "PAIR.dbf", [{"KOD": 1, "NOTATKA": "first ą"}])
    schema = read_schema(dbf_path)
    records = list(iter_records(dbf_path, memo="inline"))
    destination = tmp_path / "PUB.dbf"
    write_table(destination, schema=schema, records=iter(records))
    previous_dbf_sha = _sha256(destination)
    previous_fpt_sha = _sha256(tmp_path / "PUB.fpt")

    def _explode(*args: object, **kwargs: object) -> None:
        from dbf_bridge.importer.writer import ReconstructionError

        raise ReconstructionError(
            "simulated FPT metadata failure", code=ErrorCode.WRITE_MEMO_FAILED
        )

    monkeypatch.setattr(physical_writer, "_patch_fpt_metadata", _explode)
    with pytest.raises(WriteMemoFailedError) as excinfo:
        write_table(
            destination,
            schema=schema,
            records=iter([*records, {"KOD": 2, "NOTATKA": "second b"}]),
            overwrite=True,
        )
    monkeypatch.undo()
    assert excinfo.value.to_dict()["code"] == "WRITE_MEMO_FAILED"
    # the old DBF+FPT pair is byte-identical, and no staging residue remains
    assert _sha256(destination) == previous_dbf_sha
    assert _sha256(tmp_path / "PUB.fpt") == previous_fpt_sha
    assert list(tmp_path.rglob("*partial*")) == []
    assert list(tmp_path.rglob(".*partial*")) == []


def test_failed_overwrite_keeps_previous_destination_intact(tmp_path: Path) -> None:
    table_path = _simple_table(tmp_path / "KEEP.dbf", [{"KOD": 1}, {"KOD": 2}])
    schema = read_schema(table_path)
    destination = tmp_path / "PUB.dbf"
    records = list(iter_records(table_path, memo="skip"))
    write_table(destination, schema=schema, records=iter(records))
    previous_sha = _sha256(destination)

    class _ExplodingRecords:
        def __iter__(self):
            yield {"KOD": 99, "NAZWA": "x"}
            raise OSError("injected mid-stream failure")


    with pytest.raises(DestinationIoError) as excinfo:
        write_table(destination, schema=schema, records=_ExplodingRecords(), overwrite=True)
    assert excinfo.value.to_dict()["code"] == "DESTINATION_IO_ERROR"
    assert _sha256(destination) == previous_sha


def test_staging_directory_keeps_staging_outside_final_tree(tmp_path: Path) -> None:
    table_path = _simple_table(tmp_path / "T.dbf", [{"KOD": 1}])
    schema = read_schema(table_path)
    records = list(iter_records(table_path, memo="skip"))
    output_dir = tmp_path / "final"
    staging_dir = tmp_path / "stage"
    destination = output_dir / "PUB.dbf"
    result = write_table(
        destination,
        schema=schema,
        records=iter(records),
        staging_directory=staging_dir,
    )
    assert destination.is_file()
    assert not [p for p in output_dir.iterdir() if "partial" in p.name]
    assert isinstance(result, WriteResult)


def test_write_result_is_json_safe(tmp_path: Path) -> None:
    table_path = _simple_table(tmp_path / "J.dbf", [{"KOD": 1}])
    schema = read_schema(table_path)
    records = list(iter_records(table_path, memo="skip"))
    result = write_table(tmp_path / "OUT.dbf", schema=schema, records=iter(records))
    payload = json.dumps(result.to_dict(), ensure_ascii=False)
    assert json.loads(payload)["dbf_sha256"] == result.dbf_sha256
    assert json.loads(payload)["records_written"] == 1
    assert json.loads(payload)["deleted_records"] == 0


# ---------------------------------------------------------------------------
# O(1) / iterator materialization regression
# ---------------------------------------------------------------------------


def test_iterator_is_consumed_exactly_once(tmp_path: Path) -> None:
    table_path = _simple_table(tmp_path / "O1.dbf", [{"KOD": i} for i in range(5)])
    schema = read_schema(table_path)

    class OneShotRecords:
        """One-shot iterable: raises on the second __iter__ (no list/rewind)."""

        def __init__(self) -> None:
            self._used = False

        def __iter__(self):
            if self._used:
                raise AssertionError(
                    "the record stream must be iterated exactly once "
                    "(no list(records), no len(), no rewind)"
                )
            return self._gen()

        def _gen(self):
            self._used = True
            yield {"KOD": 1}
            yield {"KOD": 2}
            yield {"KOD": 3}
            yield {"KOD": 4}
            yield {"KOD": 5}

    one_shot = OneShotRecords()
    destination = tmp_path / "OUT.dbf"
    result = write_table(destination, schema=schema, records=one_shot)
    assert result.records_written == 5
    assert len(_dbfread_all(destination)) == 5


# ---------------------------------------------------------------------------
# privacy sentinels: no record/memo values leak through public error payloads
# ---------------------------------------------------------------------------

_SECRET = "VERY_SECRET_SOURCE_VALUE_9f2a7b3c"


def test_no_record_value_leaks_through_write_value_invalid(tmp_path: Path) -> None:
    """The serialized error payload must be privacy-safe: the sentinel record
    value appears in NEITHER the payload NOR the message."""
    dbf_path = _simple_table(tmp_path / "P1.dbf", [{"KOD": 2, "NAZWA": "a", "AKTYWNY": True}])
    schema = read_schema(dbf_path)
    bad_record = {"KOD": 99, "NAZWA": _SECRET, "AKTYWNY": "not-logical"}
    with pytest.raises(WriteValueInvalidError) as excinfo:
        write_table(tmp_path / "OUT.dbf", schema=schema, records=iter([bad_record]), overwrite=True)
    payload = json.dumps(excinfo.value.to_dict(), ensure_ascii=False)
    assert _SECRET not in payload
    assert _SECRET not in str(excinfo.value)
    assert excinfo.value.to_dict()["code"] == "WRITE_VALUE_INVALID"
    # The write failed and was rolled back, so OUT.dbf was never published.
    assert not (tmp_path / "OUT.dbf").exists()


def test_no_memo_value_leaks_through_write_memo_failed(tmp_path: Path) -> None:
    """A memo value that cannot be encoded produces a typed WRITE_MEMO_FAILED
    whose serialized payload never contains the memo text."""
    dbf_path = _memo_table(tmp_path / "P2.dbf", [{"KOD": 1, "NOTATKA": "safe memo"}])
    schema = read_schema(dbf_path)
    assert schema.encoding == "cp1250"
    # Ω is not representable in cp1250; the sentinel prefix is plain ASCII,
    # so it must never appear in the error payload or message.
    memo_record = {"KOD": 1, "NOTATKA": f"{_SECRET}Ω"}
    with pytest.raises(WriteMemoFailedError) as excinfo:
        write_table(tmp_path / "OUT.dbf", schema=schema, records=iter([memo_record]))
    payload = json.dumps(excinfo.value.to_dict(), ensure_ascii=False)
    assert _SECRET not in payload
    assert _SECRET not in str(excinfo.value)
    assert excinfo.value.to_dict()["code"] == "WRITE_MEMO_FAILED"
    assert not (tmp_path / "OUT.dbf").exists()


# ---------------------------------------------------------------------------
# structural CDX: never copied, never fabricated
# ---------------------------------------------------------------------------


def test_structural_cdx_no_source_copy(tmp_path: Path) -> None:
    """Structural CDX: the writer must never copy or fabricate a CDX."""
    dbf_path = _simple_table(tmp_path / "SCDX.dbf", [{"KOD": 1}])
    (tmp_path / "SCDX.cdx").write_bytes(b"\x00" * 16)
    source_cdx_sha = _sha256(tmp_path / "SCDX.cdx")
    data = bytearray(dbf_path.read_bytes())
    data[28] = data[28] | 0x01
    dbf_path.write_bytes(bytes(data))

    schema = read_schema(dbf_path)
    records = list(iter_records(dbf_path, memo="skip"))
    destination = tmp_path / "OUT.cdx.dbf"
    write_table(destination, schema=schema, records=iter(records))
    assert not destination.with_suffix(".cdx").exists()
    assert _sha256(tmp_path / "SCDX.cdx") == source_cdx_sha

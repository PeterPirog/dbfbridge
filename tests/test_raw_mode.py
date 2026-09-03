"""RawMode public contract (architecture §7, closure BLK-02).

Mode semantics (one explicit contract):

- ``full-record`` (default, backward compatible): per-record raw physical
  record images (``__dbfbridge_raw_record__``) + full schema structural
  metadata — the historical forensic migration path; canonical reconstruction
  includes nullable Varchar tables (raw-layout restoration);
- ``metadata``: logical values + full schema structural metadata, no
  per-record raw record images;
- ``none``: logical values + loss-aware text fallback, no per-record raw
  record images AND no replay-only physical header blobs in the schema.

Loss-aware guarantees (``__dbfbridge_raw_text_fields__``,
``__dbfbridge_binary_memo_fields__``) are logical/canonical aids and are
retained in ALL modes — dropping them would silently break canonical
reconstruction for encoding-fallback and binary-memo tables.

Capability boundary (explicit, tested): the schema-driven writer cannot yet
rebuild the variable-length Varchar layout without the per-record raw image
(documented writer limitation — compatibility matrix row ``V``).  Nullable
Varchar tables therefore reconstruct canonically only in ``full-record``
mode; in ``none``/``metadata`` the reconstruction fails with the TYPED
``DBF_RECORD_INVALID`` physical error (never an untyped crash).
"""

from __future__ import annotations

import json
import shutil
import struct
from pathlib import Path

import pytest
import vfp_fixture_factory as factory

from dbf_bridge import RawMode, export_dbf, reconstruct_dbf
from dbf_bridge.cli import main as cli_main
from dbf_bridge.core.backend import dbfread_backend
from dbf_bridge.exporter.incremental import CHECKSUM_MANIFEST_NAME
from dbf_bridge.exporter.serialization import (
    BINARY_MEMO_FIELDS_KEY,
    RAW_RECORD_KEY,
    RAW_TEXT_FIELDS_KEY,
)

RAW_MODES: tuple[RawMode, ...] = ("none", "metadata", "full-record")
RESERVED_KEYS = {RAW_RECORD_KEY, RAW_TEXT_FIELDS_KEY, BINARY_MEMO_FIELDS_KEY}


def _jsonl_records(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _schema(output: Path, table: str) -> dict[str, object]:
    schema_path = output / table
    return json.loads(schema_path.read_text(encoding="utf-8"))


def _export(
    source: Path,
    output: Path,
    *,
    raw_mode: str = "full-record",
    **kwargs: object,
) -> object:
    return export_dbf(source, output, formats=("jsonl",), raw_mode=raw_mode, **kwargs)  # type: ignore[arg-type]


def _reconstruct(exported: Path, rebuilt: Path) -> object:
    return reconstruct_dbf(exported, rebuilt, overwrite=True)


# ---------------------------------------------------------------------------
# defaults and mode visibility
# ---------------------------------------------------------------------------


def test_raw_mode_is_public_and_typed() -> None:
    import dbf_bridge
    import dbfbridge

    assert "RawMode" in dbf_bridge.__all__
    assert dbfbridge.RawMode is RawMode


def test_raw_mode_argument_is_validated(sample_input_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError) as error:
        _export(sample_input_dir, tmp_path / "out", raw_mode="bogus")
    assert getattr(error.value, "code", None) == "ARGUMENT_INVALID"


def test_default_is_full_record(sample_input_dir: Path, tmp_path: Path) -> None:
    output = tmp_path / "default"
    run = export_dbf(sample_input_dir, output, formats=("jsonl",), overwrite=True)
    assert run.ok == 3
    records = _jsonl_records(output / "klienci.jsonl")
    assert records
    assert all(RAW_RECORD_KEY in record for record in records)
    schema = _schema(output, "klienci_schema.json")
    assert schema["dbf"]["header_base64"]


# ---------------------------------------------------------------------------
# per-mode semantics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw_mode", RAW_MODES)
def test_mode_matrix_record_images_and_schema(
    sample_input_dir: Path, tmp_path: Path, raw_mode: str
) -> None:
    output = tmp_path / f"out-{raw_mode}"
    run = export_dbf(
        sample_input_dir, output, formats=("jsonl",), raw_mode=raw_mode, overwrite=True
    )
    assert run.ok == 3
    records = _jsonl_records(output / "klienci.jsonl")
    schema = _schema(output, "klienci_schema.json")

    if raw_mode == "full-record":
        assert all(RAW_RECORD_KEY in record for record in records)
        assert schema["dbf"]["header_base64"]
    else:
        assert all(RAW_RECORD_KEY not in record for record in records)

    if raw_mode == "none":
        assert "header_base64" not in schema["dbf"]
        assert "header_base64" not in schema["memo"]
        # logical/structural schema facts survive
        assert schema["dbf"]["record_count_from_header"] is not None
        assert schema["text_encoding"]["declared_or_detected_encoding"]
        assert schema["fields"]
        assert any(field.get("descriptor_base64") for field in schema["fields"])
    else:
        assert schema["dbf"]["header_base64"]


def test_metadata_keeps_schema_structural_metadata(
    sample_input_dir: Path, tmp_path: Path
) -> None:
    """``metadata`` adds the schema-level structural metadata that ``none``
    omits (DBF header region incl. backlink, raw FPT header block)."""
    output = tmp_path / "meta"
    assert export_dbf(
        sample_input_dir, output, formats=("jsonl",), raw_mode="metadata", overwrite=True
    ).ok == 3
    schema = _schema(output, "klienci_schema.json")
    assert schema["dbf"]["header_base64"]
    assert schema["memo"]["header_base64"]


@pytest.mark.parametrize("raw_mode", RAW_MODES)
def test_no_raw_bytes_allocated_when_not_requested(
    sample_input_dir: Path, tmp_path: Path, raw_mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``none``/``metadata`` must not allocate raw record images at all
    (the shared backend loop runs with ``keep_raw=False``)."""
    observed: list[bool] = []
    original = dbfread_backend.iter_physical_records

    def _spy(table: object, **kwargs: object) -> object:
        observed.append(bool(kwargs.get("keep_raw")))
        return original(table, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(dbfread_backend, "iter_physical_records", _spy)
    assert export_dbf(
        sample_input_dir, tmp_path / f"spy-{raw_mode}", formats=("jsonl",), raw_mode=raw_mode,
        overwrite=True,
    ).ok == 3
    assert observed
    assert all(entry is (raw_mode == "full-record") for entry in observed)


# ---------------------------------------------------------------------------
# canonical reconstruction per mode
# ---------------------------------------------------------------------------


def _varchar_fixture(tmp_path: Path) -> Path:
    """Authentic nullable Varchar + nullable Char table (mixed bitmaps)."""
    return factory.build_vfp32_table(
        tmp_path / "varchar.dbf",
        columns=[
            {"name": "V1", "type": "V", "width": 10, "nullable": True},
            {"name": "C1", "type": "C", "width": 6, "nullable": True},
            {"name": "K", "type": "N", "width": 4},
        ],
        rows=[
            {"V1": "abc", "C1": None, "K": 1},
            {"V1": None, "C1": "x", "K": 2},
        ],
    )


@pytest.mark.parametrize("raw_mode", RAW_MODES)
def test_canonical_reconstruction_without_raw_records(
    sample_input_dir: Path, tmp_path: Path, raw_mode: str
) -> None:
    exported = tmp_path / f"exp-{raw_mode}"
    run = export_dbf(
        sample_input_dir, exported, formats=("jsonl",), raw_mode=raw_mode, overwrite=True
    )
    assert run.ok == 3
    rebuilt = tmp_path / f"reb-{raw_mode}"
    result = _reconstruct(exported, rebuilt)
    assert result.failed == 0
    for table_result in result.results:
        assert table_result.canonical_match is True
    # physical raw identity is only expected in full-record mode
    if raw_mode == "full-record":
        assert all(table_result.raw_layout_restored is True for table_result in result.results)
    else:
        assert all(table_result.raw_layout_restored is False for table_result in result.results)


@pytest.mark.parametrize("raw_mode", RAW_MODES)
def test_canonical_reconstruction_nullable_varchar(tmp_path: Path, raw_mode: str) -> None:
    """Nullable-Varchar canonical reconstruction — RED-first contract gate.

    Target contract (architecture contract 20260903-164824): every SUPPORTED
    Varchar case reconstructs canonically in ALL raw modes.  ``raw_mode`` may
    change the physical raw-record retention, never the canonical correctness.
    """
    source = _varchar_fixture(tmp_path)
    exported = tmp_path / f"exp-v-{raw_mode}"
    run = export_dbf(source, exported, formats=("jsonl",), raw_mode=raw_mode, overwrite=True)
    assert run.ok == 1
    rebuilt = tmp_path / f"reb-v-{raw_mode}"
    result = _reconstruct(exported, rebuilt)
    assert result.failed == 0
    assert result.results[0].canonical_match is True


# ---------------------------------------------------------------------------
# Macro A correctness gate: Varchar value matrix (no raw record images)
# ---------------------------------------------------------------------------


def _read_values(dbf_path: Path) -> list[dict[str, object]]:
    from dbfbridge import read_records

    page = read_records(dbf_path, memo="inline")
    return [
        {name: value for name, value in record.values.items() if name != "_NullFlags"}
        for record in page.records
    ]


_VARCHAR_VALUE_ROWS: list[dict[str, object]] = [
    {"V1": "abc", "V2": "short", "C1": None},
    {"V1": "abc ", "V2": "0123456789", "C1": "ok"},
    {"V1": "", "V2": None, "C1": None},
    {"V1": None, "V2": "tail ", "C1": "ę"},
]


def _varchar_matrix_fixture(tmp_path: Path) -> Path:
    """V1 nullable + V2 non-nullable + C1 nullable (mixed bitmap, 4 bits)."""
    return factory.build_vfp32_table(
        tmp_path / "matrix.dbf",
        columns=[
            {"name": "V1", "type": "V", "width": 10, "nullable": True},
            {"name": "V2", "type": "V", "width": 10},
            {"name": "C1", "type": "C", "width": 6, "nullable": True},
        ],
        rows=_VARCHAR_VALUE_ROWS,
    )


@pytest.mark.parametrize("raw_mode", RAW_MODES)
def test_varchar_value_matrix_reconstructs_logically_identical(
    tmp_path: Path, raw_mode: str
) -> None:
    """short / full-width / trailing-space / empty / NULL / non-nullable V /
    mixed V+V+C bitmap — canonical PASS and identical logical values via
    the public Direct Read surface."""
    source = _varchar_matrix_fixture(tmp_path)
    source_sha = source.read_bytes()
    exported = tmp_path / f"exp-m-{raw_mode}"
    run = export_dbf(source, exported, formats=("jsonl",), raw_mode=raw_mode, overwrite=True)
    assert run.ok == 1

    rebuilt = tmp_path / f"reb-m-{raw_mode}"
    result = _reconstruct(exported, rebuilt)
    assert result.failed == 0
    assert result.results[0].canonical_match is True

    expected = _read_values(source)
    actual = _read_values(rebuilt / "matrix.dbf")
    assert actual == expected
    # source immutability (§27)
    assert source.read_bytes() == source_sha


def test_varchar_non_nullable_consumes_varlength_bit(tmp_path: Path) -> None:
    """A non-nullable ``V`` still consumes a varlength bit; short values keep
    the length-byte form and reconstruct canonically in every raw mode."""
    source = factory.build_vfp32_table(
        tmp_path / "nonnull.dbf",
        columns=[
            {"name": "V", "type": "V", "width": 8},
            {"name": "K", "type": "N", "width": 4},
        ],
        rows=[{"V": "abc", "K": 1}, {"V": "01234567", "K": 2}],
    )
    for raw_mode in RAW_MODES:
        exported = tmp_path / f"exp-nn-{raw_mode}"
        assert export_dbf(
            source, exported, formats=("jsonl",), raw_mode=raw_mode, overwrite=True
        ).ok == 1
        rebuilt = tmp_path / f"reb-nn-{raw_mode}"
        result = _reconstruct(exported, rebuilt)
        assert result.failed == 0
        assert result.results[0].canonical_match is True
        assert _read_values(rebuilt / "nonnull.dbf") == [
            {"V": "abc", "K": 1.0},
            {"V": "01234567", "K": 2.0},
        ]


@pytest.mark.parametrize("raw_mode", RAW_MODES)
def test_varchar_deleted_record_reconstructs(tmp_path: Path, raw_mode: str) -> None:
    """deleted Varchar rows keep physical order, markers, and values."""
    source = factory.build_vfp32_table(
        tmp_path / "delv.dbf",
        columns=[
            {"name": "V1", "type": "V", "width": 10, "nullable": True},
            {"name": "KOD", "type": "N", "width": 3},
        ],
        rows=[{"V1": "first", "KOD": 1}, {"V1": None, "KOD": 2}, {"V1": "third", "KOD": 3}],
    )
    source_bytes = bytearray(source.read_bytes())
    header_length = int.from_bytes(source_bytes[8:10], "little")
    record_length = int.from_bytes(source_bytes[10:12], "little")
    # mark the third physical record deleted (VFP deletion marker '*')
    source_bytes[header_length + 2 * record_length] = 0x2A
    source.write_bytes(bytes(source_bytes))

    exported = tmp_path / f"exp-dv-{raw_mode}"
    run = export_dbf(
        source,
        exported,
        formats=("jsonl",),
        raw_mode=raw_mode,
        deleted="include",  # type: ignore[arg-type]
        overwrite=True,
    )
    assert run.ok == 1
    rebuilt = tmp_path / f"reb-dv-{raw_mode}"
    result = _reconstruct(exported, rebuilt)
    assert result.failed == 0
    assert result.results[0].canonical_match is True
    assert result.results[0].deleted_records == 1

    exported = tmp_path / f"exp-dv-{raw_mode}"
    run = export_dbf(
        source,
        exported,
        formats=("jsonl",),
        raw_mode=raw_mode,
        deleted="include",  # type: ignore[arg-type]
        overwrite=True,
    )
    assert run.ok == 1
    rebuilt = tmp_path / f"reb-dv-{raw_mode}"
    result = _reconstruct(exported, rebuilt)
    assert result.failed == 0
    assert result.results[0].canonical_match is True
    assert result.results[0].deleted_records == 1


@pytest.mark.parametrize("raw_mode", RAW_MODES)
@pytest.mark.parametrize(
    ("codepage", "text"),
    [
        (0xC8, "Zażółć gęślą"),
        (0x23, "ąćęłńóśźż"),
        (0x69, "Zażółć"),
    ],
)
def test_varchar_polish_codepages_reconstruct(
    tmp_path: Path, raw_mode: str, codepage: int, text: str
) -> None:
    """cp1250 / cp852 / Mazovia Varchar round trips keep the exact Unicode."""
    source = factory.build_vfp32_table(
        tmp_path / f"cp{codepage:x}.dbf",
        columns=[{"name": "T", "type": "V", "width": 20, "nullable": True}],
        rows=[{"T": text}, {"T": None}],
        codepage=codepage,
    )
    exported = tmp_path / f"exp-cp-{codepage:x}-{raw_mode}"
    assert export_dbf(
        source, exported, formats=("jsonl",), raw_mode=raw_mode, overwrite=True
    ).ok == 1
    rebuilt = tmp_path / f"reb-cp-{codepage:x}-{raw_mode}"
    result = _reconstruct(exported, rebuilt)
    assert result.failed == 0
    assert result.results[0].canonical_match is True
    assert _read_values(rebuilt / f"cp{codepage:x}.dbf") == [{"T": text}, {"T": None}]


def test_varchar_repair_failure_is_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing Varchar logical-layout repair leaves no published output and
    no staging residue, with a typed structured failure (Macro A contract)."""
    from dbf_bridge.importer import writer as importer_writer
    from dbf_bridge.importer.writer import ReconstructionError

    source = _varchar_fixture(tmp_path)
    exported = tmp_path / "exp-fail"
    assert export_dbf(source, exported, formats=("jsonl",), overwrite=True).ok == 1
    rebuilt = tmp_path / "reb-fail"

    def _explode(*args: object, **kwargs: object) -> None:
        raise ReconstructionError("simulated Varchar layout repair failure")

    monkeypatch.setattr(
        importer_writer, "_repair_varchar_logical_layout", _explode
    )
    result = _reconstruct(exported, rebuilt)
    assert result.failed == 1
    table_result = result.results[0]
    assert {detail.code for detail in table_result.error_details} == {
        "RECONSTRUCTION_FAILED"
    }
    # atomic publish boundary: no final DBF/FPT and no staging residue
    assert not (rebuilt / "varchar.dbf").exists()
    assert not (rebuilt / "varchar.fpt").exists()
    assert list(rebuilt.rglob("*.partial*")) == []
    assert list(rebuilt.rglob(".*partial*")) == []
    assert list(rebuilt.rglob(".*raw-layout*")) == []


# ---------------------------------------------------------------------------
# loss-aware raw-text fallback retained in every mode
# ---------------------------------------------------------------------------


def _fallback_fixture(tmp_path: Path) -> tuple[Path, bytes]:
    import struct

    import dbf

    source = tmp_path / "fallback-src"
    source.mkdir()
    dbf_path = source / "fallback.dbf"
    table = dbf.Table(str(dbf_path), "TEXT C(4)", dbf_type="vfp", codepage=0xC8)
    table.open(dbf.READ_WRITE)
    table.append({"TEXT": "x"})
    table.close()
    raw = dbf_path.read_bytes()
    header_length = struct.unpack_from("<H", raw, 8)[0]
    with dbf_path.open("r+b") as handle:
        handle.seek(header_length + 1)
        handle.write(b"\x81   ")  # undefined in cp1250, valid in cp852
    return dbf_path, raw


@pytest.mark.parametrize("raw_mode", RAW_MODES)
def test_raw_text_fallback_retained_in_every_mode(tmp_path: Path, raw_mode: str) -> None:
    source, original_bytes = _fallback_fixture(tmp_path)
    exported = tmp_path / f"exp-fb-{raw_mode}"
    run = export_dbf(source, exported, formats=("jsonl",), raw_mode=raw_mode, overwrite=True)
    assert run.ok == 1
    records = _jsonl_records(exported / "fallback.jsonl")
    assert RAW_TEXT_FIELDS_KEY in records[0]
    assert records[0][RAW_TEXT_FIELDS_KEY]["TEXT"]

    rebuilt = tmp_path / f"reb-fb-{raw_mode}"
    result = _reconstruct(exported, rebuilt)
    assert result.failed == 0
    assert result.results[0].canonical_match is True
    # the reconstructed value bytes are the ORIGINAL raw bytes (loss-aware)
    rebuilt_bytes = (rebuilt / "fallback.dbf").read_bytes()
    header_length = struct.unpack_from("<H", original_bytes, 8)[0]
    assert rebuilt_bytes[header_length + 1 : header_length + 5] == b"\x81   "


# ---------------------------------------------------------------------------
# deleted policies x raw modes
# ---------------------------------------------------------------------------


def _deleted_fixture(tmp_path: Path) -> Path:
    import dbf

    source = tmp_path / "deleted-src"
    source.mkdir()
    dbf_path = source / "del.dbf"
    table = dbf.Table(str(dbf_path), "KOD N(3,0)", dbf_type="vfp", codepage=0xC8)
    table.open(dbf.READ_WRITE)
    table.append({"KOD": 1})
    table.append({"KOD": 2})
    table.append({"KOD": 3})
    dbf.delete(table[1])
    dbf.delete(table[2])
    table.close()
    return dbf_path


@pytest.mark.parametrize("raw_mode", RAW_MODES)
@pytest.mark.parametrize("deleted", ["skip", "include", "separate"])
def test_deleted_policies_by_raw_mode(
    tmp_path: Path, raw_mode: str, deleted: str
) -> None:
    source = _deleted_fixture(tmp_path)
    output = tmp_path / f"del-{deleted}-{raw_mode}"
    run = export_dbf(
        source,
        output,
        formats=("jsonl",),
        raw_mode=raw_mode,
        deleted=deleted,  # type: ignore[arg-type]
        overwrite=True,
    )
    result = run.results[0]
    assert result.status in {"OK", "WARNING"}

    data_records = _jsonl_records(output / "del.jsonl")
    deleted_path = output / "del.deleted.jsonl"

    if deleted == "skip":
        assert len(data_records) == 1
        assert not deleted_path.exists()
    elif deleted == "include":
        assert len(data_records) == 3
        assert sum(1 for record in data_records if record.get("__deleted__")) == 2
        if raw_mode == "full-record":
            assert all(RAW_RECORD_KEY in record for record in data_records)
        else:
            assert all(RAW_RECORD_KEY not in record for record in data_records)
    else:  # separate
        assert len(data_records) == 1  # active only
        assert deleted_path.is_file()
        deleted_records = _jsonl_records(deleted_path)
        assert len(deleted_records) == 2
        assert all(record.get("__deleted__") for record in deleted_records)
        if raw_mode == "full-record":
            assert all(RAW_RECORD_KEY in record for record in deleted_records)
        else:
            assert all(RAW_RECORD_KEY not in record for record in deleted_records)


def test_deleted_reconstruction_counts_are_unchanged(
    tmp_path: Path,
) -> None:
    """deleted=include round trip keeps deleted counts and physical order."""
    source = _deleted_fixture(tmp_path)
    exported = tmp_path / "exp-del"
    run = export_dbf(
        source, exported, formats=("jsonl",), deleted="include", overwrite=True
    )
    assert run.ok == 1
    rebuilt = tmp_path / "reb-del"
    result = _reconstruct(exported, rebuilt)
    assert result.ok == 1
    table_result = result.results[0]
    assert table_result.canonical_match is True
    assert table_result.deleted_records == 2
    assert table_result.raw_dbf_match is True


# ---------------------------------------------------------------------------
# incremental identity
# ---------------------------------------------------------------------------


def _cli(source: Path, output: Path, *, raw_mode: str | None = None) -> int:
    args = [
        "--source",
        str(source),
        "--output",
        str(output),
        "--formats",
        "jsonl",
        "--overwrite",
        "--no-progress",
        "--incremental",
    ]
    if raw_mode is not None:
        args += ["--raw-mode", raw_mode]
    return cli_main(args)


def test_incremental_cache_invalidated_by_raw_mode(
    sample_input_dir: Path, tmp_path: Path
) -> None:
    source = tmp_path / "source"
    output = tmp_path / "output"
    shutil.copytree(sample_input_dir, source)

    def _report() -> list[dict[str, object]]:
        return [
            json.loads(line)
            for line in (output / "migration_report.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]

    # first run with none: converts everything, records the mode in the signature
    assert _cli(source, output, raw_mode="none") == 0
    manifest = json.loads((output / CHECKSUM_MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["signature"]["raw_mode"] == "none"

    # same mode again -> all tables skipped
    assert _cli(source, output, raw_mode="none") == 0
    assert _report()[0]["skipped"] == 3

    # mode change -> no cached reuse (artifacts must be regenerated)
    assert _cli(source, output, raw_mode="full-record") == 0
    new_manifest = json.loads((output / CHECKSUM_MANIFEST_NAME).read_text(encoding="utf-8"))
    assert new_manifest["signature"]["raw_mode"] == "full-record"
    assert _report()[0]["ok"] == 3
    assert _report()[0]["skipped"] == 0


def test_incremental_same_mode_reuses_cache(tmp_path: Path, sample_input_dir: Path) -> None:
    source = tmp_path / "source"
    output = tmp_path / "output"
    shutil.copytree(sample_input_dir, source)
    assert _cli(source, output, raw_mode="metadata") == 0
    assert _cli(source, output, raw_mode="metadata") == 0
    report = [
        json.loads(line)
        for line in (output / "migration_report.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert report[0]["skipped"] == 3


# ---------------------------------------------------------------------------
# converted formats never expose reserved keys as data columns
# ---------------------------------------------------------------------------


def test_converted_csv_has_no_reserved_columns(tmp_path: Path, sample_input_dir: Path) -> None:
    output = tmp_path / "out"
    run = export_dbf(
        sample_input_dir,
        output,
        formats=("jsonl", "csv"),
        raw_mode="full-record",
        overwrite=True,
    )
    assert run.failed == 0
    csv_path = output / "klienci.csv"
    header = csv_path.read_text(encoding="utf-8").splitlines()[0]
    for key in RESERVED_KEYS:
        assert key not in header


# ---------------------------------------------------------------------------
# full-record regression (forensic default unchanged)
# ---------------------------------------------------------------------------


def test_full_record_keeps_forensic_reconstruction(tmp_path: Path, sample_input_dir: Path) -> None:
    exported = tmp_path / "exp-full"
    run = export_dbf(sample_input_dir, exported, formats=("jsonl",), overwrite=True)
    assert run.ok == 3
    rebuilt = tmp_path / "reb-full"
    result = _reconstruct(exported, rebuilt)
    assert result.ok == 3
    for table_result in result.results:
        assert table_result.canonical_match is True
        assert table_result.raw_layout_restored is True

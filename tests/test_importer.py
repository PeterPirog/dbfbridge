from __future__ import annotations

import hashlib
import json
import struct
from datetime import date, datetime
from pathlib import Path

import dbf
import pytest

from dbf_bridge.cli import main as export_main
from dbf_bridge.exporter.serialization import BINARY_MEMO_FIELDS_KEY, RAW_TEXT_FIELDS_KEY
from dbf_bridge.import_cli import main as import_main
from dbf_bridge.quality import _compare_jsonl, _different_offsets, run_quality_check


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def source_tree(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    path = source / "DANE" / "example.dbf"
    path.parent.mkdir(parents=True)
    table = dbf.Table(
        str(path),
        field_specs=(
            "ID N(6,0); NAME C(30) NULL; AMOUNT N(12,2); ACTIVE L; CREATED D; CHANGED T; NOTES M"
        ),
        memo_size=64,
        dbf_type="vfp",
        codepage=0xC8,
    )
    table.open(mode=dbf.READ_WRITE)
    table.append(
        {
            "ID": 1,
            "NAME": "Zażółć gęślą",
            "AMOUNT": 123.45,
            "ACTIVE": True,
            "CREATED": date(2024, 1, 2),
            "CHANGED": datetime(2024, 1, 2, 3, 4, 5),
            "NOTES": "Pierwsza linia\nDruga linia",
        }
    )
    table.append(
        {
            "ID": 2,
            "NAME": dbf.Null,
            "AMOUNT": -10.5,
            "ACTIVE": None,
            "CREATED": date(2024, 2, 3),
            "CHANGED": datetime(2024, 2, 3, 4, 5, 6),
            "NOTES": None,
        }
    )
    dbf.delete(table[-1])
    table.append(
        {
            "ID": 3,
            "NAME": "Po rekordzie usuniętym",
            "AMOUNT": 7.5,
            "ACTIVE": False,
            "CREATED": date(2024, 3, 4),
            "CHANGED": datetime(2024, 3, 4, 5, 6, 7),
            "NOTES": "Trzeci rekord",
        }
    )
    table.close()
    return source


@pytest.mark.parametrize("input_format", ["jsonl", "json", "csv", "xlsx"])
def test_reconstructs_each_format_with_schema_and_directory_tree(
    source_tree: Path,
    tmp_path: Path,
    input_format: str,
) -> None:
    exported = tmp_path / "exported"
    reconstructed = tmp_path / f"reconstructed-{input_format}"
    assert (
        export_main(
            [
                "--source",
                str(source_tree),
                "--output",
                str(exported),
                "--formats",
                "csv,json,jsonl,xlsx",
                "--memo",
                "inline",
                "--deleted",
                "include",
                "--overwrite",
                "--no-progress",
            ]
        )
        == 0
    )

    code = import_main(
        [
            "--source",
            str(exported),
            "--output",
            str(reconstructed),
            "--formats",
            input_format,
            "--memo",
            "inline",
            "--overwrite",
            "--no-progress",
        ]
    )

    assert code == 0
    original_dbf = source_tree / "DANE" / "example.dbf"
    rebuilt_dbf = reconstructed / "DANE" / "example.dbf"
    assert rebuilt_dbf.is_file()
    assert (reconstructed / "DANE" / "example.fpt").is_file()
    assert _sha256(original_dbf) == _sha256(rebuilt_dbf)
    assert _sha256(original_dbf.with_suffix(".fpt")) == _sha256(rebuilt_dbf.with_suffix(".fpt"))
    report = (reconstructed / "reconstruction_report.jsonl").read_text(encoding="utf-8")
    assert '"canonical_match":true' in report
    assert '"raw_dbf_match":true' in report
    assert '"raw_fpt_match":true' in report


def test_quality_report_keeps_all_stages_and_pinpoints_success(
    source_tree: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "quality"

    reports, summary = run_quality_check(
        source_tree,
        output,
        overwrite=True,
        progress=False,
    )

    assert summary["tables"] == 1
    assert summary["ok"] == 1
    assert summary["failed"] == 0
    assert summary["raw_dbf_matches"] == 1
    assert summary["raw_fpt_matches"] == 1
    assert summary["canonical_matches"] == 1
    assert reports[0]["status"] == "OK"
    assert reports[0]["differences"] == []
    assert (output / "01_forward_jsonl" / "DANE" / "example.jsonl").is_file()
    assert (output / "02_reconstructed_dbf" / "DANE" / "example.dbf").is_file()
    assert (output / "03_reexported_jsonl" / "DANE" / "example.jsonl").is_file()
    assert (output / "conversion_quality_report.jsonl").is_file()


def test_import_rejects_more_than_one_selected_format(tmp_path: Path) -> None:
    assert (
        import_main(
            [
                "--source",
                str(tmp_path),
                "--output",
                str(tmp_path / "out"),
                "--formats",
                "json,jsonl",
            ]
        )
        == 1
    )


def test_quality_diagnostics_identify_record_field_and_binary_area(tmp_path: Path) -> None:
    expected = tmp_path / "expected.jsonl"
    actual = tmp_path / "actual.jsonl"
    expected.write_text('{"ID":1,"OPIS":"oryginał"}\n', encoding="utf-8")
    actual.write_text('{"ID":1,"OPIS":"zmiana"}\n', encoding="utf-8")

    differences = _compare_jsonl(expected, actual, max_differences=5)

    assert differences[0]["scope"] == "field"
    assert differences[0]["record"] == 1
    assert differences[0]["field"] == "OPIS"
    assert differences[0]["expected"]["preview"] == "oryginał"
    assert differences[0]["actual"]["preview"] == "zmiana"

    source_dbf = tmp_path / "source.dbf"
    rebuilt_dbf = tmp_path / "rebuilt.dbf"
    source_dbf.write_bytes(bytes(64))
    changed = bytearray(64)
    changed[28] = 3
    rebuilt_dbf.write_bytes(changed)

    binary = _different_offsets(source_dbf, rebuilt_dbf, 5, kind="dbf")

    assert binary == [
        {
            "offset": 28,
            "source_byte": 0,
            "reconstructed_byte": 3,
            "area": "header.structural_index_flag",
        }
    ]


def test_jsonl_roundtrip_preserves_narrow_negative_numeric_and_complete_header(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    exported = tmp_path / "exported"
    rebuilt = tmp_path / "rebuilt"
    source.mkdir()
    dbf_path = source / "narrow.dbf"
    table = dbf.Table(str(dbf_path), "VALUE N(4,2)", dbf_type="vfp", codepage=0xC8)
    table.open(dbf.READ_WRITE)
    table.append({"VALUE": 0.25})
    table.close()

    with dbf_path.open("r+b") as handle:
        header = bytearray(handle.read(32))
        header_length = struct.unpack_from("<H", header, 8)[0]
        header[28] = 3
        handle.seek(0)
        handle.write(header)
        handle.seek(header_length - 1)
        handle.write(b"Z")
        handle.seek(header_length + 1)
        handle.write(b"-.25")

    assert (
        export_main(
            [
                "--source",
                str(source),
                "--output",
                str(exported),
                "--formats",
                "jsonl",
                "--overwrite",
                "--no-progress",
            ]
        )
        == 0
    )
    assert (
        import_main(
            [
                "--source",
                str(exported),
                "--output",
                str(rebuilt),
                "--formats",
                "jsonl",
                "--overwrite",
                "--no-progress",
            ]
        )
        == 2
    )

    assert _sha256(dbf_path) == _sha256(rebuilt / "narrow.dbf")
    report = [
        json.loads(line)
        for line in (rebuilt / "reconstruction_report.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert report[1]["raw_dbf_match"] is True
    assert report[1]["canonical_match"] is True


def test_jsonl_roundtrip_distinguishes_binary_content_in_text_memo(tmp_path: Path) -> None:
    source = tmp_path / "source"
    exported = tmp_path / "exported"
    rebuilt = tmp_path / "rebuilt"
    source.mkdir()
    dbf_path = source / "mixed.dbf"
    table = dbf.Table(
        str(dbf_path), "ID N(3,0); NOTES M", memo_size=64, dbf_type="vfp", codepage=0xC8
    )
    table.open(dbf.READ_WRITE)
    table.append({"ID": 1, "NOTES": "binarny blok"})
    table.close()

    raw = dbf_path.read_bytes()
    header_length = struct.unpack_from("<H", raw, 8)[0]
    memo_address = struct.unpack_from("<I", raw, 32 + 32 + 12)[0]
    memo_pointer = struct.unpack_from("<I", raw, header_length + memo_address)[0]
    fpt_path = dbf_path.with_suffix(".fpt")
    with fpt_path.open("r+b") as handle:
        prefix = handle.read(8)
        block_size = struct.unpack_from(">H", prefix, 6)[0]
        handle.seek(memo_pointer * block_size)
        handle.write(struct.pack(">I", 0))

    assert (
        export_main(
            [
                "--source",
                str(source),
                "--output",
                str(exported),
                "--formats",
                "jsonl",
                "--memo",
                "inline",
                "--overwrite",
                "--no-progress",
            ]
        )
        == 0
    )
    exported_record = json.loads((exported / "mixed.jsonl").read_text(encoding="utf-8"))
    assert exported_record[BINARY_MEMO_FIELDS_KEY] == ["NOTES"]

    assert (
        import_main(
            [
                "--source",
                str(exported),
                "--output",
                str(rebuilt),
                "--formats",
                "jsonl",
                "--memo",
                "inline",
                "--overwrite",
                "--no-progress",
            ]
        )
        == 0
    )
    assert _sha256(dbf_path) == _sha256(rebuilt / "mixed.dbf")
    assert _sha256(fpt_path) == _sha256(rebuilt / "mixed.fpt")


def test_jsonl_roundtrip_retains_raw_text_bytes_selected_by_encoding_fallback(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    exported = tmp_path / "exported"
    rebuilt = tmp_path / "rebuilt"
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

    assert (
        export_main(
            [
                "--source",
                str(source),
                "--output",
                str(exported),
                "--formats",
                "jsonl",
                "--overwrite",
                "--no-progress",
            ]
        )
        == 0
    )
    exported_record = json.loads((exported / "fallback.jsonl").read_text(encoding="utf-8"))
    assert exported_record["TEXT"] == "ü"
    assert exported_record[RAW_TEXT_FIELDS_KEY]["TEXT"] == "gQ=="

    assert (
        import_main(
            [
                "--source",
                str(exported),
                "--output",
                str(rebuilt),
                "--formats",
                "jsonl",
                "--overwrite",
                "--no-progress",
            ]
        )
        == 0
    )
    assert _sha256(dbf_path) == _sha256(rebuilt / "fallback.dbf")

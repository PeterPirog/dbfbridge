from __future__ import annotations

import json
from pathlib import Path

from dbf_bridge.cli import _convert_jsonl_outputs
from dbf_bridge.converters import EXCEL_MAX_STRING_LENGTH
from dbf_bridge.exporter.models import TableResult
from dbf_bridge.exporter.reporting import write_jsonl_report


def test_report_summary_tracks_each_format_and_failed_output(tmp_path: Path) -> None:
    results = [
        TableResult(
            table="DANE/example.dbf",
            output="DANE/example.jsonl",
            status="OK",
            encoding="cp1250",
            format="jsonl",
            active_records=2,
            sha256="a" * 64,
            size_bytes=20,
            schema="DANE/example_schema.json",
            schema_sha256="b" * 64,
        ),
        TableResult(
            table="DANE/example.dbf",
            output="DANE/example.xlsx",
            status="FAILED",
            encoding="cp1250",
            format="xlsx",
            active_records=2,
            errors=["primary: value exceeds Excel cell limit"],
            schema="DANE/example_schema.json",
            schema_sha256="b" * 64,
        ),
    ]
    report = tmp_path / "migration_report.jsonl"

    write_jsonl_report(
        report,
        results,
        run_metadata={"requested_formats": ["jsonl", "xlsx"], "exit_code": 1},
    )

    lines = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    summary = lines[0]
    assert summary["tables"] == 1
    assert summary["outputs"] == 2
    assert summary["ok"] == 1
    assert summary["failed"] == 1
    assert summary["complete_tables"] == 0
    assert summary["incomplete_tables"] == 1
    assert summary["format_summary"]["jsonl"]["ok"] == 1
    assert summary["format_summary"]["xlsx"]["failed"] == 1
    assert summary["run"]["exit_code"] == 1
    assert lines[2]["status"] == "FAILED"
    assert "Excel cell limit" in lines[2]["errors"][0]


def test_xlsx_long_memo_overflow_is_returned_for_migration_report(tmp_path: Path) -> None:
    jsonl = tmp_path / "example.jsonl"
    jsonl.write_text(
        json.dumps({"OPIS": "x" * (EXCEL_MAX_STRING_LENGTH + 1)}) + "\n",
        encoding="utf-8",
    )
    schema = tmp_path / "example_schema.json"
    schema.write_text(
        json.dumps(
            {
                "fields": [
                    {
                        "name": "OPIS",
                        "is_memo": True,
                        "target_representation": "string-or-base64",
                        "decimal_count": 0,
                        "dbf_type": "M",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    source_result = TableResult(
        table="example.dbf",
        output="example.jsonl",
        status="OK",
        encoding="cp1250",
        format="jsonl",
        active_records=1,
        memo_fields=["OPIS"],
        schema="example_schema.json",
        schema_sha256="b" * 64,
    )

    results = _convert_jsonl_outputs(
        tmp_path,
        [source_result],
        ["jsonl", "xlsx"],
        memo_arg="inline",
        deleted="skip",
        overwrite=True,
    )

    assert len(results) == 1
    assert results[0].format == "xlsx"
    assert results[0].status == "OK"
    assert results[0].sha256 is not None
    assert results[0].errors == []
    assert results[0].overflow_value_count == 1
    assert results[0].overflow_chunk_count == 2
    assert results[0].overflow_sheet_count == 1
    assert (tmp_path / "example.xlsx").exists()

    report = tmp_path / "overflow_report.jsonl"
    write_jsonl_report(
        report,
        [source_result, results[0]],
        run_metadata={"requested_formats": ["jsonl", "xlsx"]},
    )
    lines = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["report_version"] == 3
    assert lines[0]["format_summary"]["xlsx"]["overflow_values"] == 1
    assert lines[0]["format_summary"]["xlsx"]["overflow_chunks"] == 2
    assert lines[0]["format_summary"]["xlsx"]["overflow_sheets"] == 1
    assert lines[2]["overflow_value_count"] == 1
    assert lines[2]["overflow_chunk_count"] == 2
    assert lines[2]["overflow_sheet_count"] == 1

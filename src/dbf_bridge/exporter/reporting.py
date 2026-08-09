from __future__ import annotations

import csv
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import TableResult

REPORT_FIELDS = [
    "table",
    "output",
    "status",
    "encoding",
    "format",
    "active_records",
    "deleted_records",
    "memo_fields",
    "null_counts",
    "empty_string_counts",
    "memo_hashes",
    "sha256",
    "size_bytes",
    "schema",
    "schema_sha256",
    "deleted_output",
    "deleted_sha256",
    "engine",
    "sheet_count",
    "overflow_value_count",
    "overflow_chunk_count",
    "overflow_sheet_count",
    "elapsed_seconds",
    "warnings",
    "errors",
]


def write_reports(
    output_root: Path,
    results: list[TableResult],
    *,
    run_metadata: dict[str, Any] | None = None,
) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    write_jsonl_report(
        output_root / "migration_report.jsonl",
        results,
        run_metadata=run_metadata,
    )
    write_csv_report(output_root / "migration_report.csv", results)


def write_jsonl_report(
    path: Path,
    results: list[TableResult],
    *,
    run_metadata: dict[str, Any] | None = None,
) -> None:
    run = dict(run_metadata or {})
    requested_formats = list(run.get("requested_formats") or sorted({r.format for r in results}))
    table_names = sorted({result.table for result in results})
    statuses_by_format: dict[str, Counter[str]] = defaultdict(Counter)
    statuses_by_table: dict[str, dict[str, str]] = defaultdict(dict)
    for result in results:
        statuses_by_format[result.format][result.status] += 1
        statuses_by_table[result.table][result.format] = result.status

    complete_tables = sum(
        all(
            statuses_by_table[table].get(fmt) in {"OK", "WARNING", "SKIPPED"}
            for fmt in requested_formats
        )
        for table in table_names
    )
    format_summary = {
        fmt: {
            "ok": statuses_by_format[fmt]["OK"],
            "warning": statuses_by_format[fmt]["WARNING"],
            "skipped": statuses_by_format[fmt]["SKIPPED"],
            "failed": statuses_by_format[fmt]["FAILED"],
            "unsupported": statuses_by_format[fmt]["UNSUPPORTED"],
            "overflow_values": sum(
                result.overflow_value_count for result in results if result.format == fmt
            ),
            "overflow_chunks": sum(
                result.overflow_chunk_count for result in results if result.format == fmt
            ),
            "overflow_sheets": sum(
                result.overflow_sheet_count for result in results if result.format == fmt
            ),
        }
        for fmt in sorted(statuses_by_format)
    }
    run.setdefault("finished_at", datetime.now(timezone.utc).isoformat())
    run.setdefault("exit_code", exit_code(results))
    summary = {
        "type": "summary",
        "report_version": 4,
        "tables": len(table_names),
        "outputs": len(results),
        "formats": sorted({result.format for result in results}),
        "requested_formats": requested_formats,
        "ok": sum(1 for result in results if result.status == "OK"),
        "warning": sum(1 for result in results if result.status == "WARNING"),
        "skipped": sum(1 for result in results if result.status == "SKIPPED"),
        "failed": sum(1 for result in results if result.status == "FAILED"),
        "unsupported": sum(1 for result in results if result.status == "UNSUPPORTED"),
        "complete_tables": complete_tables,
        "incomplete_tables": len(table_names) - complete_tables,
        "format_summary": format_summary,
        "run": run,
    }
    lines = [summary]
    lines.extend({"type": "table", **result.to_report_dict()} for result in results)
    atomic_write_text(
        path,
        "".join(
            json.dumps(line, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"
            for line in lines
        ),
    )


def write_csv_report(path: Path, results: list[TableResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.partial")
    with partial.open("w", encoding="utf-8", newline="") as outfile:
        writer = csv.DictWriter(outfile, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        for result in results:
            row = result.to_report_dict()
            writer.writerow({name: _csv_value(row[name]) for name in REPORT_FIELDS})
        outfile.flush()
        os.fsync(outfile.fileno())
    os.replace(partial, path)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.partial")
    with partial.open("w", encoding="utf-8", newline="\n") as outfile:
        outfile.write(text)
        outfile.flush()
        os.fsync(outfile.fileno())
    os.replace(partial, path)


def exit_code(results: list[TableResult]) -> int:
    if any(result.status in {"FAILED", "UNSUPPORTED"} for result in results):
        return 1
    if any(result.status == "WARNING" or result.warnings for result in results):
        return 2
    return 0


def _csv_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
    if value is None:
        return ""
    return str(value)

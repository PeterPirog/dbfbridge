from __future__ import annotations

import csv
import json
import os
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
    "warnings",
    "errors",
]


def write_reports(output_root: Path, results: list[TableResult]) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    write_jsonl_report(output_root / "migration_report.jsonl", results)
    write_csv_report(output_root / "migration_report.csv", results)


def write_jsonl_report(path: Path, results: list[TableResult]) -> None:
    summary = {
        "type": "summary",
        "tables": len(results),
        "formats": sorted({result.format for result in results}),
        "ok": sum(1 for result in results if result.status == "OK"),
        "warning": sum(1 for result in results if result.status == "WARNING"),
        "failed": sum(1 for result in results if result.status == "FAILED"),
        "unsupported": sum(1 for result in results if result.status == "UNSUPPORTED"),
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

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import ExportFormat, FieldMetadata, StreamStats


@dataclass
class ValidationResult:
    record_count: int = 0
    null_counts: dict[str, int] = field(default_factory=dict)
    empty_string_counts: dict[str, int] = field(default_factory=dict)
    memo_hashes: dict[str, str] = field(default_factory=dict)
    sha256: str | None = None
    size_bytes: int = 0
    errors: list[str] = field(default_factory=list)


class StatsCollector:
    def __init__(self, fields: list[FieldMetadata]) -> None:
        self.field_names = [field.name for field in fields]
        self.memo_fields = [field.name for field in fields if field.is_memo]
        self.stats = StreamStats(
            null_counts=dict.fromkeys(self.field_names, 0),
            empty_string_counts=dict.fromkeys(self.field_names, 0),
            memo_hashes={name: hashlib.sha256().hexdigest() for name in self.memo_fields},
        )
        self._memo_hashers = {name: hashlib.sha256() for name in self.memo_fields}

    def add(self, record: dict[str, Any]) -> None:
        self.stats.record_count += 1
        for name in self.field_names:
            value = record.get(name)
            if value is None:
                self.stats.null_counts[name] += 1
            elif value == "":
                self.stats.empty_string_counts[name] += 1

            if name in self._memo_hashers:
                update_value_hash(self._memo_hashers[name], value)

    def finish(self) -> StreamStats:
        self.stats.memo_hashes = {
            name: hasher.hexdigest() for name, hasher in self._memo_hashers.items()
        }
        return self.stats


def update_value_hash(hasher: hashlib._Hash, value: Any) -> None:
    if value is None:
        payload = b""
        marker = b"N"
    elif isinstance(value, bool):
        payload = b"true" if value else b"false"
        marker = b"L"
    elif isinstance(value, int):
        payload = str(value).encode("ascii")
        marker = b"I"
    elif isinstance(value, float):
        payload = json.dumps(value, allow_nan=False).encode("ascii")
        marker = b"F"
    elif isinstance(value, str):
        payload = value.encode("utf-8")
        marker = b"S"
    else:
        payload = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        marker = b"J"

    hasher.update(marker)
    hasher.update(len(payload).to_bytes(8, "big"))
    hasher.update(payload)


def validate_output(
    path: Path,
    export_format: ExportFormat,
    fields: list[FieldMetadata],
    expected: StreamStats,
) -> ValidationResult:
    result = ValidationResult()
    result.sha256 = sha256_file(path)
    result.size_bytes = path.stat().st_size

    parsed = (
        _parse_jsonl(path, fields)
        if export_format == "jsonl"
        else _parse_json(path, fields)
        if export_format == "json"
        else _parse_csv(path, fields)
    )
    result.record_count = parsed.record_count
    result.null_counts = parsed.null_counts
    result.empty_string_counts = parsed.empty_string_counts
    result.memo_hashes = parsed.memo_hashes
    result.errors.extend(parsed.errors)

    if result.record_count != expected.record_count:
        result.errors.append(
            f"Record count mismatch: expected {expected.record_count}, got {result.record_count}."
        )
    if result.null_counts != expected.null_counts:
        result.errors.append("NULL counts differ after parsing the output file.")
    if result.empty_string_counts != expected.empty_string_counts:
        result.errors.append("Empty-string counts differ after parsing the output file.")
    if result.memo_hashes != expected.memo_hashes:
        result.errors.append("MEMO hashes differ after parsing the output file.")

    return result


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as infile:
        for chunk in iter(lambda: infile.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_jsonl(path: Path, fields: list[FieldMetadata]) -> ValidationResult:
    collector = StatsCollector(fields)
    result = ValidationResult()
    with path.open("r", encoding="utf-8", newline="") as infile:
        for line_number, line in enumerate(infile, start=1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                result.errors.append(f"Invalid JSONL at line {line_number}: {exc.msg}.")
                continue
            if not isinstance(record, dict):
                result.errors.append(f"JSONL line {line_number} is not a JSON object.")
                continue
            collector.add(record)

    stats = collector.finish()
    result.record_count = stats.record_count
    result.null_counts = stats.null_counts
    result.empty_string_counts = stats.empty_string_counts
    result.memo_hashes = stats.memo_hashes
    return result


def _parse_csv(path: Path, fields: list[FieldMetadata]) -> ValidationResult:
    collector = StatsCollector(fields)
    result = ValidationResult()
    with path.open("r", encoding="utf-8", newline="") as infile:
        reader = csv.DictReader(infile)
        for row_number, row in enumerate(reader, start=2):
            record: dict[str, Any] = {}
            for name in collector.field_names:
                try:
                    record[name] = json.loads(row[name])
                except (KeyError, json.JSONDecodeError) as exc:
                    result.errors.append(
                        f"Invalid CSV JSON cell at row {row_number}, field {name}."
                    )
                    if isinstance(exc, KeyError):
                        record[name] = None
            collector.add(record)

    stats = collector.finish()
    result.record_count = stats.record_count
    result.null_counts = stats.null_counts
    result.empty_string_counts = stats.empty_string_counts
    result.memo_hashes = stats.memo_hashes
    return result


def _parse_json(path: Path, fields: list[FieldMetadata]) -> ValidationResult:
    collector = StatsCollector(fields)
    result = ValidationResult()
    with path.open("r", encoding="utf-8", newline="") as infile:
        opened = False
        closed = False
        element_index = 0
        for line_number, line in enumerate(infile, start=1):
            text = line.strip()
            if not text:
                continue
            if not opened:
                if text != "[":
                    result.errors.append("JSON output is not a JSON array.")
                    return result
                opened = True
                continue
            if text == "]":
                closed = True
                continue
            if closed:
                result.errors.append(f"Unexpected JSON content at line {line_number}.")
                continue
            element_index += 1
            payload = text[:-1] if text.endswith(",") else text
            try:
                record = json.loads(payload)
            except json.JSONDecodeError as exc:
                result.errors.append(
                    f"Invalid JSON element {element_index} at line {line_number}: {exc.msg}."
                )
                continue
            if not isinstance(record, dict):
                result.errors.append(f"JSON element {element_index} is not a JSON object.")
                continue
            collector.add(record)
    if not opened or not closed:
        result.errors.append("JSON output is not a complete JSON array.")

    stats = collector.finish()
    result.record_count = stats.record_count
    result.null_counts = stats.null_counts
    result.empty_string_counts = stats.empty_string_counts
    result.memo_hashes = stats.memo_hashes
    return result

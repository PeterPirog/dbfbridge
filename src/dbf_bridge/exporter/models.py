from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

ExportFormat = Literal["jsonl", "csv", "json"]
DecodeErrors = Literal["strict", "ignore", "replace"]
DeletedPolicy = Literal["skip", "separate", "include"]
MissingMemoPolicy = Literal["fail", "null-with-warning"]
MemoPolicy = Literal["skip", "inline", "null"]
TableStatus = Literal["OK", "WARNING", "FAILED", "UNSUPPORTED"]


@dataclass(frozen=True)
class ExportConfig:
    source: Path
    output: Path
    format: ExportFormat = "jsonl"
    encoding: str | None = None
    decode_errors: DecodeErrors = "strict"
    deleted: DeletedPolicy = "skip"
    missing_memo: MissingMemoPolicy = "fail"
    memo: MemoPolicy = "inline"
    strip_spaces: bool = False
    validate: bool = True
    overwrite: bool = False


@dataclass(frozen=True)
class DiscoveredTable:
    source_path: Path
    relative_path: Path
    memo_path: Path | None
    memo_present: bool


@dataclass(frozen=True)
class FieldMetadata:
    name: str
    dbf_type: str
    length: int
    decimal_count: int
    target_representation: str
    is_memo: bool = False
    is_binary: bool = False
    supported: bool = True
    unsupported_reason: str | None = None
    flags: int = 0

    def to_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "dbf_type": self.dbf_type,
            "length": self.length,
            "decimal_count": self.decimal_count,
            "target_representation": self.target_representation,
            "is_memo": self.is_memo,
            "is_binary": self.is_binary,
            "flags": self.flags,
        }


@dataclass(frozen=True)
class TableMetadata:
    table_name: str
    relative_path: Path
    dbf_path: Path
    dbversion: str
    dbversion_byte: int
    language_driver: int
    language_driver_name: str | None
    encoding: str
    memo_path: Path | None
    memo_present: bool
    fields: list[FieldMetadata]
    warnings: list[str] = field(default_factory=list)

    @property
    def memo_fields(self) -> list[str]:
        return [field.name for field in self.fields if field.is_memo]

    @property
    def field_names(self) -> list[str]:
        return [field.name for field in self.fields]

    def to_schema(self) -> dict[str, Any]:
        return {
            "table": self.table_name,
            "relative_path": self.relative_path.as_posix(),
            "dbf": {
                "version": self.dbversion,
                "version_byte": f"0x{self.dbversion_byte:02x}",
                "language_driver": f"0x{self.language_driver:02x}",
                "language_driver_name": self.language_driver_name,
                "encoding": self.encoding,
            },
            "memo": {
                "path": self.memo_path.name if self.memo_path else None,
                "present": self.memo_present,
            },
            "fields": [field.to_schema() for field in self.fields],
        }


@dataclass
class StreamStats:
    record_count: int = 0
    null_counts: dict[str, int] = field(default_factory=dict)
    empty_string_counts: dict[str, int] = field(default_factory=dict)
    memo_hashes: dict[str, str] = field(default_factory=dict)


@dataclass
class TableResult:
    table: str
    output: str | None
    status: TableStatus
    encoding: str | None
    active_records: int = 0
    deleted_records: int = 0
    memo_fields: list[str] = field(default_factory=list)
    null_counts: dict[str, int] = field(default_factory=dict)
    empty_string_counts: dict[str, int] = field(default_factory=dict)
    memo_hashes: dict[str, dict[str, Any]] = field(default_factory=dict)
    sha256: str | None = None
    size_bytes: int | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    schema: str | None = None

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "output": self.output,
            "status": self.status,
            "encoding": self.encoding,
            "active_records": self.active_records,
            "deleted_records": self.deleted_records,
            "memo_fields": self.memo_fields,
            "null_counts": self.null_counts,
            "empty_string_counts": self.empty_string_counts,
            "memo_hashes": self.memo_hashes,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "warnings": self.warnings,
            "errors": self.errors,
        }

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

ExportFormat = Literal["jsonl", "csv", "json"]
OutputFormat = Literal["jsonl", "csv", "json", "xlsx"]
DecodeErrors = Literal["strict", "ignore", "replace"]
DeletedPolicy = Literal["skip", "separate", "include"]
MissingMemoPolicy = Literal["fail", "null-with-warning"]
MemoPolicy = Literal["skip", "inline", "null"]
TableStatus = Literal["OK", "WARNING", "SKIPPED", "FAILED", "UNSUPPORTED"]

DBF_FIELD_TYPE_NAMES = {
    "0": "Null flags",
    "+": "Autoincrement",
    "@": "Timestamp",
    "B": "Double or binary memo",
    "C": "Character",
    "D": "Date",
    "F": "Float",
    "G": "General/OLE memo",
    "I": "Integer",
    "L": "Logical",
    "M": "Memo",
    "N": "Numeric",
    "O": "Double",
    "P": "Picture memo",
    "Q": "Varbinary",
    "T": "DateTime",
    "V": "Varchar",
    "W": "Blob",
    "Y": "Currency",
}


@dataclass(frozen=True)
class ExportConfig:
    source: Path
    output: Path
    format: OutputFormat = "jsonl"
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
    ordinal: int | None = None
    address: int | None = None
    index_field_flag: int = 0
    descriptor_bytes: bytes | None = None

    def to_schema(self) -> dict[str, Any]:
        memo_storage = None
        if self.is_memo:
            memo_storage = {
                "file_format": "FPT",
                "pointer_length_bytes": self.length,
                "pointer_byte_order": "little-endian" if self.length == 4 else "ASCII index",
                "content_kind": "binary" if self.is_binary else "text-or-binary",
            }
        return {
            "ordinal": self.ordinal,
            "name": self.name,
            "dbf_type": self.dbf_type,
            "dbf_type_name": DBF_FIELD_TYPE_NAMES.get(self.dbf_type, "Unknown"),
            "length": self.length,
            "address": self.address,
            "decimal_count": self.decimal_count,
            "target_representation": self.target_representation,
            "is_memo": self.is_memo,
            "is_binary": self.is_binary,
            "flags": self.flags,
            "field_flags": {
                "raw": f"0x{self.flags:02x}",
                "system": bool(self.flags & 0x01),
                "nullable": bool(self.flags & 0x02),
                "binary": bool(self.flags & 0x04),
            },
            "index_field_flag": self.index_field_flag,
            "descriptor_base64": base64.b64encode(self.descriptor_bytes).decode("ascii")
            if self.descriptor_bytes is not None
            else None,
            "memo_storage": memo_storage,
            "unsupported_reason": self.unsupported_reason,
            "supported": self.supported,
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
    source_size_bytes: int | None = None
    record_count: int | None = None
    header_length: int | None = None
    record_length: int | None = None
    last_update: str | None = None
    incomplete_transaction: int = 0
    encryption_flag: int = 0
    structural_index_flag: int = 0
    encoding_override: str | None = None
    decode_errors: str = "strict"
    encoding_fallbacks: list[str] = field(default_factory=list)
    memo_size_bytes: int | None = None
    memo_next_free_block: int | None = None
    memo_block_size: int | None = None
    memo_export_policy: str = "inline"
    header_bytes: bytes | None = None
    source_sha256: str | None = None
    memo_header_bytes: bytes | None = None
    memo_sha256: str | None = None

    @property
    def memo_fields(self) -> list[str]:
        return [field.name for field in self.fields if field.is_memo]

    @property
    def field_names(self) -> list[str]:
        return [field.name for field in self.fields]

    def to_schema(self) -> dict[str, Any]:
        is_vfp = self.dbversion_byte in {0x30, 0x31, 0x32}
        is_fpt = self.memo_path is not None and self.memo_path.suffix.lower() == ".fpt"
        return {
            "schema_format": "dbfbridge-vfp-table-schema",
            "schema_version": 1,
            "table": self.table_name,
            "relative_path": self.relative_path.as_posix(),
            "source": {
                "filename": self.dbf_path.name,
                "relative_path": self.relative_path.as_posix(),
                "size_bytes": self.source_size_bytes,
                "sha256": self.source_sha256,
            },
            "dbf": {
                "format_family": "Microsoft Visual FoxPro" if is_vfp else self.dbversion,
                "recreation_target": "Microsoft Visual FoxPro 9.0 SP2" if is_vfp else None,
                "version": self.dbversion,
                "version_byte": f"0x{self.dbversion_byte:02x}",
                "last_update": self.last_update,
                "record_count_from_header": self.record_count,
                "header_length_bytes": self.header_length,
                "record_length_bytes": self.record_length,
                "incomplete_transaction": bool(self.incomplete_transaction),
                "encrypted": bool(self.encryption_flag),
                "structural_index_flag": self.structural_index_flag,
                "language_driver": f"0x{self.language_driver:02x}",
                "language_driver_name": self.language_driver_name,
                "encoding": self.encoding,
                "header_base64": base64.b64encode(self.header_bytes).decode("ascii")
                if self.header_bytes is not None
                else None,
            },
            "text_encoding": {
                "language_driver_byte": f"0x{self.language_driver:02x}",
                "language_driver_name": self.language_driver_name,
                "declared_or_detected_encoding": self.encoding,
                "user_override": self.encoding_override,
                "decode_errors": self.decode_errors,
                "fallback_order": self.encoding_fallbacks,
                "applies_to": ["Character", "Varchar", "text Memo"],
            },
            "memo": {
                "path": self.memo_path.name if self.memo_path else None,
                "present": self.memo_present,
                "required": bool(self.memo_fields),
                "format": "FPT" if is_fpt else None,
                "size_bytes": self.memo_size_bytes,
                "sha256": self.memo_sha256,
                "header_base64": base64.b64encode(self.memo_header_bytes).decode("ascii")
                if self.memo_header_bytes is not None
                else None,
                "file_header_bytes": 512 if self.memo_present and is_fpt else None,
                "block_size_bytes": self.memo_block_size,
                "next_free_block": self.memo_next_free_block,
                "block_header_bytes": 8 if self.memo_present and is_fpt else None,
                "block_header_byte_order": "big-endian"
                if self.memo_present and is_fpt
                else None,
                "block_types": {"0": "picture", "1": "text", "2": "object"}
                if self.memo_present and is_fpt
                else None,
                "dbf_pointer_byte_order": "little-endian"
                if self.memo_fields and is_fpt
                else None,
                "text_encoding": self.encoding if self.memo_fields else None,
                "field_names": self.memo_fields,
                "export_policy": self.memo_export_policy,
                "values_in_data_output": self.memo_export_policy == "inline",
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
    format: OutputFormat = "jsonl"
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
    schema_sha256: str | None = None
    deleted_output: str | None = None
    deleted_sha256: str | None = None
    engine: str | None = None
    sheet_count: int = 0
    overflow_value_count: int = 0
    overflow_chunk_count: int = 0
    overflow_sheet_count: int = 0
    elapsed_seconds: float | None = None

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "output": self.output,
            "status": self.status,
            "encoding": self.encoding,
            "format": self.format,
            "active_records": self.active_records,
            "deleted_records": self.deleted_records,
            "memo_fields": self.memo_fields,
            "null_counts": self.null_counts,
            "empty_string_counts": self.empty_string_counts,
            "memo_hashes": self.memo_hashes,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "schema": self.schema,
            "schema_sha256": self.schema_sha256,
            "deleted_output": self.deleted_output,
            "deleted_sha256": self.deleted_sha256,
            "engine": self.engine,
            "sheet_count": self.sheet_count,
            "overflow_value_count": self.overflow_value_count,
            "overflow_chunk_count": self.overflow_chunk_count,
            "overflow_sheet_count": self.overflow_sheet_count,
            "elapsed_seconds": self.elapsed_seconds,
            "warnings": self.warnings,
            "errors": self.errors,
        }

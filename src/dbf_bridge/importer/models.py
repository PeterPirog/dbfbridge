from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

InputFormat = Literal["jsonl", "json", "csv", "xlsx"]


@dataclass(frozen=True)
class ImportConfig:
    source: Path
    output: Path
    format: InputFormat
    memo: str = "inline"
    overwrite: bool = False
    progress: bool = True


@dataclass
class ReconstructionResult:
    source: str
    schema: str | None
    output: str | None
    status: str
    format: str
    record_count: int = 0
    active_records: int = 0
    deleted_records: int = 0
    schema_sha256: str | None = None
    input_canonical_sha256: str | None = None
    reconstructed_canonical_sha256: str | None = None
    canonical_match: bool | None = None
    dbf_sha256: str | None = None
    expected_source_dbf_sha256: str | None = None
    raw_dbf_match: bool | None = None
    fpt_output: str | None = None
    fpt_sha256: str | None = None
    expected_source_fpt_sha256: str | None = None
    raw_fpt_match: bool | None = None
    raw_layout_restored: bool = False
    differences: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    elapsed_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "schema": self.schema,
            "output": self.output,
            "status": self.status,
            "format": self.format,
            "record_count": self.record_count,
            "active_records": self.active_records,
            "deleted_records": self.deleted_records,
            "schema_sha256": self.schema_sha256,
            "input_canonical_sha256": self.input_canonical_sha256,
            "reconstructed_canonical_sha256": self.reconstructed_canonical_sha256,
            "canonical_match": self.canonical_match,
            "dbf_sha256": self.dbf_sha256,
            "expected_source_dbf_sha256": self.expected_source_dbf_sha256,
            "raw_dbf_match": self.raw_dbf_match,
            "fpt_output": self.fpt_output,
            "fpt_sha256": self.fpt_sha256,
            "expected_source_fpt_sha256": self.expected_source_fpt_sha256,
            "raw_fpt_match": self.raw_fpt_match,
            "raw_layout_restored": self.raw_layout_restored,
            "differences": self.differences,
            "warnings": self.warnings,
            "errors": self.errors,
            "elapsed_seconds": self.elapsed_seconds,
        }

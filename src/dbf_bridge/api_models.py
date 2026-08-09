"""Public result models shared by the Python API and command-line adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .exporter.models import (
    DecodeErrors,
    DeletedPolicy,
    MemoPolicy,
    MissingMemoPolicy,
    OutputFormat,
    TableResult,
)
from .importer.models import InputFormat, ReconstructionResult

Operation = Literal["export", "convert", "reconstruct", "verify", "quality"]


@dataclass(frozen=True)
class ProgressEvent:
    """A structured progress notification emitted by long-running API calls."""

    operation: Operation
    current: int
    total: int
    table: str | None = None
    format: str | None = None
    records: int | None = None
    message: str | None = None


class DBFBridgeRunError(RuntimeError):
    """Raised by ``raise_for_errors()`` when a completed run contains failures."""

    def __init__(self, message: str, result: Any) -> None:
        super().__init__(message)
        self.result = result


@dataclass(frozen=True)
class ExportRunResult:
    """Complete result of one multi-format DBF export run."""

    source: Path
    output: Path
    formats: tuple[OutputFormat, ...]
    results: tuple[TableResult, ...]
    exit_code: int
    elapsed_seconds: float
    migration_report_jsonl: Path | None = None
    migration_report_csv: Path | None = None
    checksum_manifest: Path | None = None
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> int:
        return sum(result.status == "OK" for result in self.results)

    @property
    def warning(self) -> int:
        return sum(result.status == "WARNING" for result in self.results)

    @property
    def skipped(self) -> int:
        return sum(result.status == "SKIPPED" for result in self.results)

    @property
    def failed(self) -> int:
        return sum(result.status in {"FAILED", "UNSUPPORTED"} for result in self.results)

    @property
    def successful(self) -> bool:
        return self.exit_code == 0

    def raise_for_errors(self) -> None:
        if self.failed:
            raise DBFBridgeRunError(f"Export failed for {self.failed} output(s).", self)


@dataclass(frozen=True)
class ReconstructionRunResult:
    """Complete result of reconstructing a directory tree into DBF/FPT files."""

    source: Path
    output: Path
    input_format: InputFormat
    results: tuple[ReconstructionResult, ...]
    exit_code: int
    report_path: Path

    @property
    def ok(self) -> int:
        return sum(result.status == "OK" for result in self.results)

    @property
    def warning(self) -> int:
        return sum(result.status == "WARNING" for result in self.results)

    @property
    def failed(self) -> int:
        return sum(result.status == "FAILED" for result in self.results)

    @property
    def successful(self) -> bool:
        return self.exit_code == 0

    def raise_for_errors(self) -> None:
        if self.failed:
            raise DBFBridgeRunError(f"Reconstruction failed for {self.failed} table(s).", self)


@dataclass(frozen=True)
class VerificationRunResult:
    """Structured result returned by :func:`verify_conversion`."""

    source: Path
    output: Path
    formats: tuple[OutputFormat, ...]
    checks: tuple[Any, ...]
    summary: dict[str, Any]
    global_errors: tuple[str, ...]
    exit_code: int
    report_path: Path | None = None

    @property
    def successful(self) -> bool:
        return self.exit_code == 0

    def raise_for_errors(self) -> None:
        failed = int(self.summary.get("failed", 0))
        if failed or self.global_errors:
            raise DBFBridgeRunError(
                f"Verification found {failed} failed table(s) and "
                f"{len(self.global_errors)} global error(s).",
                self,
            )


@dataclass(frozen=True)
class QualityRunResult:
    """Structured result of a retained DBF -> JSONL -> DBF quality check."""

    source: Path
    output: Path
    reports: tuple[dict[str, Any], ...]
    summary: dict[str, Any]
    exit_code: int
    report_path: Path

    @property
    def successful(self) -> bool:
        return self.exit_code == 0

    def raise_for_errors(self) -> None:
        failed = int(self.summary.get("failed", 0))
        if failed:
            raise DBFBridgeRunError(f"Quality check failed for {failed} table(s).", self)


@dataclass(frozen=True)
class ExportOptions:
    """Reusable configuration for :func:`export_dbf`."""

    formats: tuple[OutputFormat, ...] = ("jsonl",)
    memo: MemoPolicy | None = None
    strip_spaces: bool = False
    encoding: str = "auto"
    decode_errors: DecodeErrors = "strict"
    deleted: DeletedPolicy = "skip"
    missing_memo: MissingMemoPolicy = "fail"
    overwrite: bool = True
    validate: bool = True
    xlsx_long_text: Literal["overflow", "error"] = "overflow"
    incremental: bool = False


@dataclass(frozen=True)
class ReconstructionOptions:
    """Reusable configuration for :func:`reconstruct_dbf`."""

    input_format: InputFormat = "jsonl"
    memo: Literal["inline", "null"] = "inline"
    overwrite: bool = False

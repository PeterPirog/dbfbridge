"""Public result models shared by the Python API and command-line adapters.

The shared progress contract (:class:`Operation`, :class:`ProgressEvent`) is
defined **only** in :mod:`dbf_bridge.progress`; this module re-exports the
very same objects so historical ``from dbf_bridge.api_models import
ProgressEvent`` imports keep resolving to the canonical runtime class.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .core.errors import ErrorCode, OperationError
from .exporter.models import (
    DecodeErrors,
    DeletedPolicy,
    MemoPolicy,
    MissingMemoPolicy,
    OutputFormat,
    RawMode,
    TableResult,
)
from .importer.models import InputFormat, ReconstructionResult
from .progress import Operation, ProgressEvent

__all__ = [
    "DBFBridgeRunError",
    "ExportOptions",
    "ExportRunResult",
    "InputFormat",
    "Operation",
    "ProgressEvent",
    "QualityRunResult",
    "ReconstructionOptions",
    "ReconstructionRunResult",
    "VerificationRunResult",
]


class DBFBridgeRunError(RuntimeError):
    """Raised by ``raise_for_errors()`` when a completed run contains failures.

    Backward compatible: still a :class:`RuntimeError` carrying the original
    ``result`` object and message.  Additively, it now carries a stable
    machine ``code``, the structured per-table ``details`` and a JSON-safe
    ``to_dict()`` so callers never parse the message text to classify the
    failure.
    """

    def __init__(
        self,
        message: str,
        result: Any,
        *,
        details: tuple[OperationError, ...] = (),
    ) -> None:
        super().__init__(message)
        self.result = result
        self.details = details

    @property
    def code(self) -> str:
        """The primary machine code: the first structured detail's code."""
        if self.details:
            return self.details[0].code
        return ErrorCode.OPERATION_FAILED.value

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe payload (never parsed from the message)."""
        return {
            "code": self.code,
            "message": str(self),
            "details": [detail.to_dict() for detail in self.details],
        }


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
            raise DBFBridgeRunError(
                f"Export failed for {self.failed} output(s).",
                self,
                details=_run_error_details(self.results, "export_dbf", ErrorCode.OPERATION_FAILED),
            )

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe run payload (``json.dumps`` succeeds)."""
        return {
            "source": _path_str(self.source),
            "output": _path_str(self.output),
            "formats": list(self.formats),
            "results": [result.to_report_dict() for result in self.results],
            "exit_code": self.exit_code,
            "elapsed_seconds": self.elapsed_seconds,
            "migration_report_jsonl": _path_str(self.migration_report_jsonl),
            "migration_report_csv": _path_str(self.migration_report_csv),
            "checksum_manifest": _path_str(self.checksum_manifest),
            "warnings": list(self.warnings),
        }


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
            raise DBFBridgeRunError(
                f"Reconstruction failed for {self.failed} table(s).",
                self,
                details=_run_error_details(
                    self.results, "reconstruct_dbf", ErrorCode.RECONSTRUCTION_FAILED
                ),
            )

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe run payload (``json.dumps`` succeeds)."""
        return {
            "source": _path_str(self.source),
            "output": _path_str(self.output),
            "input_format": self.input_format,
            "results": [result.to_dict() for result in self.results],
            "exit_code": self.exit_code,
            "report_path": _path_str(self.report_path),
        }


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
            details = _verification_error_details(self)
            raise DBFBridgeRunError(
                f"Verification found {failed} failed table(s) and "
                f"{len(self.global_errors)} global error(s).",
                self,
                details=details,
            )

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe run payload (``json.dumps`` succeeds)."""
        return {
            "source": _path_str(self.source),
            "output": _path_str(self.output),
            "formats": list(self.formats),
            "checks": [_check_to_dict(check) for check in self.checks],
            "summary": self.summary,
            "global_errors": list(self.global_errors),
            "exit_code": self.exit_code,
            "report_path": _path_str(self.report_path),
        }


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
            raise DBFBridgeRunError(
                f"Quality check failed for {failed} table(s).",
                self,
                details=(
                    OperationError(
                        code=ErrorCode.ROUNDTRIP_MISMATCH.value,
                        message=f"Quality check failed for {failed} table(s).",
                        operation="check_conversion_quality",
                    ),
                ),
            )

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe run payload (``json.dumps`` succeeds)."""
        return {
            "source": _path_str(self.source),
            "output": _path_str(self.output),
            "reports": list(self.reports),
            "summary": self.summary,
            "exit_code": self.exit_code,
            "report_path": _path_str(self.report_path),
        }


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
    raw_mode: RawMode = "full-record"


def _path_str(value: Path | None) -> str | None:
    return None if value is None else Path(value).as_posix()


def _run_error_details(
    results: Any,
    operation: str,
    fallback: ErrorCode,
) -> tuple[OperationError, ...]:
    """Collect structured details from failed table results.

    Results whose ``error_details`` are empty get a synthesized detail from
    the fallback code so the payload always classifies the failure.
    """
    details: list[OperationError] = []
    for result in results:
        if getattr(result, "status", None) not in {"FAILED", "UNSUPPORTED"}:
            continue
        own = getattr(result, "error_details", None) or []
        details.extend(own)
        if not own:
            status = getattr(result, "status", "FAILED")
            errors = getattr(result, "errors", None) or []
            code = (
                ErrorCode.FIELD_TYPE_UNSUPPORTED
                if status == "UNSUPPORTED"
                else fallback
            )
            details.append(
                OperationError(
                    code=code.value,
                    message=str(errors[0]) if errors else f"{operation} failed.",
                    operation=operation,
                    table=getattr(result, "table", None),
                )
            )
    return tuple(details)


def _check_to_dict(check: Any) -> dict[str, Any]:
    to_dict = getattr(check, "to_dict", None)
    if callable(to_dict):
        payload: Any = to_dict()
        return dict(payload)
    return {"summary": str(check)}


def _verification_error_details(result: VerificationRunResult) -> tuple[OperationError, ...]:
    details: list[OperationError] = []
    for check in result.checks:
        table = getattr(check, "dbf_relative", None)
        for file_check in _iter_file_checks(check):
            for error in file_check.errors:
                details.append(
                    OperationError(
                        code=ErrorCode.ROUNDTRIP_MISMATCH.value,
                        message=error,
                        operation="verify_conversion",
                        table=table,
                    )
                )
    for error in result.global_errors:
        details.append(
            OperationError(
                code=ErrorCode.OPERATION_FAILED.value,
                message=error,
                operation="verify_conversion",
            )
        )
    return tuple(details)


def _iter_file_checks(check: Any) -> Any:
    yield from getattr(check, "formats", {}).values()
    schema_check = getattr(check, "schema", None)
    if schema_check is not None:
        yield schema_check


@dataclass(frozen=True)
class ReconstructionOptions:
    """Reusable configuration for :func:`reconstruct_dbf`."""

    input_format: InputFormat = "jsonl"
    memo: Literal["inline", "null"] = "inline"
    overwrite: bool = False

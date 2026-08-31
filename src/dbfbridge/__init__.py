"""Recommended public import name for the ``dbfbridge`` distribution.

This namespace is a thin alias of the historical ``dbf_bridge`` package.
Importing it has no side effects (no codec registration, no CLI, no optional
heavy dependencies); public symbols are resolved lazily from ``dbf_bridge``.
Static type checkers resolve the full typed public surface through the
``TYPE_CHECKING`` declarations below, which are never executed at runtime.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dbf_bridge import (  # noqa: F401
        ArgumentInvalidError,
        DBFBridgeRunError,
        DbfFormatUnsupportedError,
        DbfHeaderInvalidError,
        DbfIoError,
        DbfPathError,
        DbfRecordInvalidError,
        DbfTruncatedError,
        DecodeErrors,
        DeletedPolicy,
        DirectReadError,
        DirectRecord,
        EncodingUnknownError,
        ErrorCode,
        ExportOptions,
        ExportRunResult,
        FieldInfo,
        FieldProjectionInvalidError,
        FieldTypeUnsupportedError,
        FptInvalidError,
        FptRequiredMissingError,
        InputFormat,
        LazyMemoValue,
        MemoPolicy,
        MissingMemoPolicy,
        OutputFormat,
        ProgressCallback,
        ProgressEvent,
        QualityRunResult,
        ReconstructionOptions,
        ReconstructionResult,
        ReconstructionRunResult,
        RecordPage,
        TableInfo,
        TableResult,
        TableSchema,
        TableStatus,
        TextDecodeError,
        VerificationRunResult,
        check_conversion_quality,
        export_dbf,
        inspect_table,
        iter_raw_records,
        iter_records,
        read_records,
        read_schema,
        reconstruct_dbf,
        verify_conversion,
    )

import dbf_bridge

__version__ = dbf_bridge.__version__
__all__ = list(dbf_bridge.__all__)


def __getattr__(name: str) -> Any:
    return getattr(dbf_bridge, name)


def __dir__() -> list[str]:
    return sorted(set(dbf_bridge.__all__) | set(vars(dbf_bridge)))

"""Public Python API for Visual FoxPro migration and reconstruction.

The preferred import after installing the ``dbfbridge`` distribution is::

    from dbfbridge import export_dbf, reconstruct_dbf

The historical ``dbf_bridge`` package name exports the same API.

Importing this package has no side effects: no codepage is registered, no
files are created, and no CLI, reporting, or optional heavy dependency
(Polars, OpenPyXL, XlsxWriter, orjson, ``dbf``) is loaded.  Public symbols
are resolved lazily on first access; the Polish Mazovia/PIAST codec is
registered explicitly by the code paths that need it.  Static type checkers
resolve the full public surface through the ``TYPE_CHECKING`` declarations
below, which are never executed at runtime.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .api import (
        ProgressCallback,  # noqa: F401 - re-exported public symbol (shared contract)
        check_conversion_quality,
        export_dbf,
        reconstruct_dbf,
        verify_conversion,
    )
    from .api_models import (
        DBFBridgeRunError,
        ExportOptions,
        ExportRunResult,
        ProgressEvent,
        QualityRunResult,
        ReconstructionOptions,
        ReconstructionRunResult,
        VerificationRunResult,
    )
    from .core import (
        ArgumentInvalidError,
        DbfFormatUnsupportedError,
        DbfHeaderInvalidError,
        DbfIoError,
        DbfPathError,
        DbfRecordInvalidError,
        DbfTruncatedError,
        DirectReadError,
        DirectRecord,
        EncodingUnknownError,
        ErrorCode,
        FieldInfo,
        FieldProjectionInvalidError,
        FieldTypeUnsupportedError,
        FptInvalidError,
        FptRequiredMissingError,
        LazyMemoValue,
        OperationArgumentError,
        OperationError,
        OperationOutputExistsError,
        OperationPathError,
        ReadCancelledError,
        RecordPage,
        TableInfo,
        TableSchema,
        TextDecodeError,
        inspect_table,
        iter_raw_records,
        iter_records,
        read_records,
        read_schema,
    )
    from .exporter.models import (
        DecodeErrors,
        DeletedPolicy,
        MemoPolicy,
        MissingMemoPolicy,
        OutputFormat,
        RawMode,
        TableResult,
        TableStatus,
    )
    from .importer.models import InputFormat, ReconstructionResult
    from .optional_deps import OptionalDependencyMissingError
    from .progress import CancellationCheck

__version__ = "0.2.0"

#: Lazily resolved public symbols, mapped to the module that defines them.
_LAZY_SYMBOLS: dict[str, str] = {
    # stable high-level operations
    "export_dbf": "dbf_bridge.api",
    "reconstruct_dbf": "dbf_bridge.api",
    "verify_conversion": "dbf_bridge.api",
    "check_conversion_quality": "dbf_bridge.api",
    # typed run results and options
    "DBFBridgeRunError": "dbf_bridge.api_models",
    "ExportOptions": "dbf_bridge.api_models",
    "ExportRunResult": "dbf_bridge.api_models",
    "ProgressEvent": "dbf_bridge.api_models",
    "QualityRunResult": "dbf_bridge.api_models",
    "ReconstructionOptions": "dbf_bridge.api_models",
    "ReconstructionRunResult": "dbf_bridge.api_models",
    "VerificationRunResult": "dbf_bridge.api_models",
    # migration/exporter option types
    "DecodeErrors": "dbf_bridge.exporter.models",
    "DeletedPolicy": "dbf_bridge.exporter.models",
    "MemoPolicy": "dbf_bridge.exporter.models",
    "MissingMemoPolicy": "dbf_bridge.exporter.models",
    "OutputFormat": "dbf_bridge.exporter.models",
    "RawMode": "dbf_bridge.exporter.models",
    "TableResult": "dbf_bridge.exporter.models",
    "TableStatus": "dbf_bridge.exporter.models",
    # reconstruction option types
    "InputFormat": "dbf_bridge.importer.models",
    "ReconstructionResult": "dbf_bridge.importer.models",
    # Phase 1 direct read core
    "inspect_table": "dbf_bridge.core",
    "read_schema": "dbf_bridge.core",
    "iter_records": "dbf_bridge.core",
    "read_records": "dbf_bridge.core",
    "iter_raw_records": "dbf_bridge.core",
    "FieldInfo": "dbf_bridge.core",
    "TableInfo": "dbf_bridge.core",
    "TableSchema": "dbf_bridge.core",
    "DirectRecord": "dbf_bridge.core",
    "RecordPage": "dbf_bridge.core",
    "LazyMemoValue": "dbf_bridge.core",
    "ErrorCode": "dbf_bridge.core",
    "DirectReadError": "dbf_bridge.core",
    "DbfPathError": "dbf_bridge.core",
    "DbfHeaderInvalidError": "dbf_bridge.core",
    "DbfTruncatedError": "dbf_bridge.core",
    "DbfFormatUnsupportedError": "dbf_bridge.core",
    "DbfIoError": "dbf_bridge.core",
    "DbfRecordInvalidError": "dbf_bridge.core",
    "EncodingUnknownError": "dbf_bridge.core",
    "TextDecodeError": "dbf_bridge.core",
    "FptRequiredMissingError": "dbf_bridge.core",
    "FptInvalidError": "dbf_bridge.core",
    "ArgumentInvalidError": "dbf_bridge.core",
    "FieldProjectionInvalidError": "dbf_bridge.core",
    "FieldTypeUnsupportedError": "dbf_bridge.core",
    "OperationArgumentError": "dbf_bridge.core",
    "OperationError": "dbf_bridge.core",
    "OperationOutputExistsError": "dbf_bridge.core",
    "OperationPathError": "dbf_bridge.core",
    # optional-dependency boundary
    "OptionalDependencyMissingError": "dbf_bridge.optional_deps",
    # direct-read control contract
    "ReadCancelledError": "dbf_bridge.core",
    # shared progress/cancellation contract (ProgressEvent is the canonical
    # class from dbf_bridge.progress, re-exported by api_models)
    "ProgressCallback": "dbf_bridge.progress",
    "CancellationCheck": "dbf_bridge.progress",
}

__all__ = [
    "ArgumentInvalidError",
    "DBFBridgeRunError",
    "DecodeErrors",
    "DeletedPolicy",
    "DbfFormatUnsupportedError",
    "DbfHeaderInvalidError",
    "DbfIoError",
    "DbfPathError",
    "DbfRecordInvalidError",
    "DbfTruncatedError",
    "DirectReadError",
    "DirectRecord",
    "EncodingUnknownError",
    "ErrorCode",
    "ExportOptions",
    "ExportRunResult",
    "FieldInfo",
    "FieldProjectionInvalidError",
    "FieldTypeUnsupportedError",
    "FptInvalidError",
    "FptRequiredMissingError",
    "InputFormat",
    "LazyMemoValue",
    "MemoPolicy",
    "MissingMemoPolicy",
    "OptionalDependencyMissingError",
    "OperationArgumentError",
    "OperationError",
    "OperationOutputExistsError",
    "OperationPathError",
    "OutputFormat",
    "ProgressCallback",
    "ProgressEvent",
    "QualityRunResult",
    "RawMode",
    "ReadCancelledError",
    "RecordPage",
    "ReconstructionOptions",
    "ReconstructionResult",
    "ReconstructionRunResult",
    "TableInfo",
    "TableResult",
    "TableSchema",
    "TableStatus",
    "TextDecodeError",
    "VerificationRunResult",
    "__version__",
    "check_conversion_quality",
    "export_dbf",
    "inspect_table",
    "iter_raw_records",
    "iter_records",
    "read_records",
    "read_schema",
    "reconstruct_dbf",
    "verify_conversion",
    "CancellationCheck",
]


def __getattr__(name: str) -> Any:
    module_name = _LAZY_SYMBOLS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value  # cache for subsequent attribute access
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))

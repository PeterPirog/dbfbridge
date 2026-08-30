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
        ProgressCallback,
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
        DbfFormatUnsupportedError,
        DbfHeaderInvalidError,
        DbfIoError,
        DbfPathError,
        DbfTruncatedError,
        DirectReadError,
        EncodingUnknownError,
        ErrorCode,
        FieldInfo,
        TableInfo,
        TableSchema,
        inspect_table,
        read_schema,
    )
    from .exporter.models import (
        DecodeErrors,
        DeletedPolicy,
        MemoPolicy,
        MissingMemoPolicy,
        OutputFormat,
        TableResult,
        TableStatus,
    )
    from .importer.models import InputFormat, ReconstructionResult

__version__ = "0.1.0"

#: Lazily resolved public symbols, mapped to the module that defines them.
_LAZY_SYMBOLS: dict[str, str] = {
    # stable high-level operations
    "export_dbf": "dbf_bridge.api",
    "reconstruct_dbf": "dbf_bridge.api",
    "verify_conversion": "dbf_bridge.api",
    "check_conversion_quality": "dbf_bridge.api",
    "ProgressCallback": "dbf_bridge.api",
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
    "TableResult": "dbf_bridge.exporter.models",
    "TableStatus": "dbf_bridge.exporter.models",
    # reconstruction option types
    "InputFormat": "dbf_bridge.importer.models",
    "ReconstructionResult": "dbf_bridge.importer.models",
    # Phase 1 direct read core
    "inspect_table": "dbf_bridge.core",
    "read_schema": "dbf_bridge.core",
    "FieldInfo": "dbf_bridge.core",
    "TableInfo": "dbf_bridge.core",
    "TableSchema": "dbf_bridge.core",
    "ErrorCode": "dbf_bridge.core",
    "DirectReadError": "dbf_bridge.core",
    "DbfPathError": "dbf_bridge.core",
    "DbfHeaderInvalidError": "dbf_bridge.core",
    "DbfTruncatedError": "dbf_bridge.core",
    "DbfFormatUnsupportedError": "dbf_bridge.core",
    "DbfIoError": "dbf_bridge.core",
    "EncodingUnknownError": "dbf_bridge.core",
}

__all__ = [
    "DBFBridgeRunError",
    "DecodeErrors",
    "DeletedPolicy",
    "DbfFormatUnsupportedError",
    "DbfHeaderInvalidError",
    "DbfIoError",
    "DbfPathError",
    "DbfTruncatedError",
    "DirectReadError",
    "EncodingUnknownError",
    "ErrorCode",
    "ExportOptions",
    "ExportRunResult",
    "FieldInfo",
    "InputFormat",
    "MemoPolicy",
    "MissingMemoPolicy",
    "OutputFormat",
    "ProgressCallback",
    "ProgressEvent",
    "QualityRunResult",
    "ReconstructionOptions",
    "ReconstructionResult",
    "ReconstructionRunResult",
    "TableInfo",
    "TableResult",
    "TableSchema",
    "TableStatus",
    "VerificationRunResult",
    "__version__",
    "check_conversion_quality",
    "export_dbf",
    "inspect_table",
    "read_schema",
    "reconstruct_dbf",
    "verify_conversion",
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

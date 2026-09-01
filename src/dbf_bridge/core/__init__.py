"""Direct read core: read-only DBF inspection with a clean import boundary.

This subpackage is the implementation home of the Phase 1 direct read API.
It has no CLI, reporting, migration, or reconstruction dependencies, requires
no optional heavy package, performs no network/COM access, and creates no
files.  The historical ``dbf_bridge.exporter`` delegates its shared header
parsing and Polish codepage logic to this package.
"""

from __future__ import annotations

from .errors import (
    ArgumentInvalidError,
    DbfFormatUnsupportedError,
    DbfHeaderInvalidError,
    DbfIoError,
    DbfPathError,
    DbfRecordInvalidError,
    DbfTruncatedError,
    DirectReadError,
    EncodingUnknownError,
    ErrorCode,
    FieldProjectionInvalidError,
    FieldTypeUnsupportedError,
    FptInvalidError,
    FptRequiredMissingError,
    ReadCancelledError,
    TextDecodeError,
)
from .header import (  # noqa: F401 - re-exported public surface
    SUPPORTED_MEMO_FORMATS,
    memo_companion_extension,  # noqa: F401 - re-exported
    memo_companion_format,  # noqa: F401 - re-exported
)
from .inspect import inspect_table, read_schema  # noqa: F401 - re-exported
from .models import CompanionFile, FieldInfo, TableInfo, TableSchema
from .records import (
    DirectRecord,
    LazyMemoValue,
    RecordPage,
    iter_raw_records,
    iter_records,
    read_records,
)

__all__ = [
    "SUPPORTED_MEMO_FORMATS",
    "ArgumentInvalidError",
    "CompanionFile",
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
    "FieldInfo",
    "FieldProjectionInvalidError",
    "FieldTypeUnsupportedError",
    "FptInvalidError",
    "FptRequiredMissingError",
    "LazyMemoValue",
    "ReadCancelledError",
    "RecordPage",
    "TableInfo",
    "TableSchema",
    "TextDecodeError",
    "iter_raw_records",
    "iter_records",
    "read_records",
    "read_schema",
]

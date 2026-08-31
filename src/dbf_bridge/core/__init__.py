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
    TextDecodeError,
)
from .header import SUPPORTED_MEMO_FORMATS, memo_companion_extension, memo_companion_format
from .inspect import inspect_table, read_schema
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
    "RecordPage",
    "SUPPORTED_MEMO_FORMATS",
    "TableInfo",
    "TableSchema",
    "TextDecodeError",
    "inspect_table",
    "iter_raw_records",
    "iter_records",
    "memo_companion_extension",
    "memo_companion_format",
    "read_records",
    "read_schema",
]

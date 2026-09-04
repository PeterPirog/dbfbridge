"""Typed, machine-readable error model for the direct read core.

Callers (including the MCP toolchain) must be able to classify failures
without parsing exception text: every error carries a stable
:class:`ErrorCode`, the offending path, and a JSON-safe context mapping.
"""

from __future__ import annotations

import contextlib
import dataclasses
import enum
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class ErrorCode(str, enum.Enum):
    """Stable machine codes for direct read failures.

    The vocabulary is shared with the high-level public operations:
    ``OPTIONAL_DEPENDENCY_MISSING``, ``OUTPUT_EXISTS``, ``RECONSTRUCTION_FAILED``,
    ``ROUNDTRIP_MISMATCH`` and ``OPERATION_FAILED`` extend it additively so the
    whole codebase classifies failures through ONE canonical set of codes.
    """

    PATH_NOT_FOUND = "PATH_NOT_FOUND"
    DBF_HEADER_INVALID = "DBF_HEADER_INVALID"
    DBF_TRUNCATED = "DBF_TRUNCATED"
    DBF_FORMAT_UNSUPPORTED = "DBF_FORMAT_UNSUPPORTED"
    ENCODING_UNKNOWN = "ENCODING_UNKNOWN"
    DBF_IO_ERROR = "DBF_IO_ERROR"
    DBF_RECORD_INVALID = "DBF_RECORD_INVALID"
    TEXT_DECODE_ERROR = "TEXT_DECODE_ERROR"
    FPT_REQUIRED_MISSING = "FPT_REQUIRED_MISSING"
    FPT_INVALID = "FPT_INVALID"
    ARGUMENT_INVALID = "ARGUMENT_INVALID"
    FIELD_PROJECTION_INVALID = "FIELD_PROJECTION_INVALID"
    FIELD_TYPE_UNSUPPORTED = "FIELD_TYPE_UNSUPPORTED"
    READ_CANCELLED = "READ_CANCELLED"
    OPTIONAL_DEPENDENCY_MISSING = "OPTIONAL_DEPENDENCY_MISSING"
    OUTPUT_EXISTS = "OUTPUT_EXISTS"
    RECONSTRUCTION_FAILED = "RECONSTRUCTION_FAILED"
    ROUNDTRIP_MISMATCH = "ROUNDTRIP_MISMATCH"
    OPERATION_FAILED = "OPERATION_FAILED"
    # direct-write research codes (RESEARCH / NEXT VERSION — not stable 1.x;
    # the write-conflict case reuses the stable OUTPUT_EXISTS code)
    DESTINATION_IO_ERROR = "DESTINATION_IO_ERROR"
    WRITE_SCHEMA_INVALID = "WRITE_SCHEMA_INVALID"
    WRITE_FIELD_UNSUPPORTED = "WRITE_FIELD_UNSUPPORTED"
    WRITE_VALUE_INVALID = "WRITE_VALUE_INVALID"
    WRITE_MEMO_FAILED = "WRITE_MEMO_FAILED"
    WRITE_PUBLICATION_FAILED = "WRITE_PUBLICATION_FAILED"


def _json_safe(value: Any) -> Any:
    """Recursively convert *value* into a JSON-serializable payload."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, enum.Enum):
        return _json_safe(getattr(value, "value", value))
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, os.PathLike):
        return os.fspath(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _json_safe(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_json_safe(item) for item in value)
    return repr(value)


class DirectReadError(ValueError):
    """Base class for typed direct read failures."""

    code: ErrorCode

    def __init__(
        self,
        message: str,
        *,
        path: str | os.PathLike[str] | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.path = os.fspath(path) if path is not None else None
        self.context = dict(context) if context else {}

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe structured description of the error.

        The payload is guaranteed to be serializable with ``json.dumps`` even
        when the context carries ``Path``, ``bytes``, enum, or tuple values.
        Paths are reported in POSIX form for transport stability.
        """
        path = _json_safe(self.path)
        if isinstance(path, str) and os.sep != "/":
            with contextlib.suppress(OSError, RuntimeError, ValueError):
                path = Path(path).as_posix()
        return {
            "code": self.code.value,
            "message": self.message,
            "path": path,
            "context": _json_safe(self.context),
        }


class DbfPathError(DirectReadError):
    """The path does not exist or is not a regular file."""

    code = ErrorCode.PATH_NOT_FOUND


class DbfHeaderInvalidError(DirectReadError):
    """The DBF header is structurally inconsistent."""

    code = ErrorCode.DBF_HEADER_INVALID


class DbfTruncatedError(DirectReadError):
    """The file is shorter than its own header describes."""

    code = ErrorCode.DBF_TRUNCATED


class DbfFormatUnsupportedError(DirectReadError):
    """The DBF version byte is not supported by dbfbridge."""

    code = ErrorCode.DBF_FORMAT_UNSUPPORTED


class EncodingUnknownError(DirectReadError):
    """The language driver byte does not map to a known encoding."""

    code = ErrorCode.ENCODING_UNKNOWN


class DbfIoError(DirectReadError):
    """A filesystem I/O failure (open, stat, read, directory scan)."""

    code = ErrorCode.DBF_IO_ERROR


class DbfRecordInvalidError(DirectReadError):
    """The physical record stream is inconsistent (invalid record marker)."""

    code = ErrorCode.DBF_RECORD_INVALID


class TextDecodeError(DirectReadError):
    """A text value cannot be decoded with the resolved encoding (strict)."""

    code = ErrorCode.TEXT_DECODE_ERROR


class FptRequiredMissingError(DirectReadError):
    """Memo values were requested but the memo companion file is missing."""

    code = ErrorCode.FPT_REQUIRED_MISSING


class FptInvalidError(DirectReadError):
    """The memo companion exists but its content cannot be trusted."""

    code = ErrorCode.FPT_INVALID


class ArgumentInvalidError(DirectReadError):
    """A call argument is invalid (offset/limit/policy/encoding value)."""

    code = ErrorCode.ARGUMENT_INVALID


class FieldProjectionInvalidError(DirectReadError):
    """A field projection lists an unknown or duplicate field name."""

    code = ErrorCode.FIELD_PROJECTION_INVALID


class FieldTypeUnsupportedError(DirectReadError):
    """A selected field uses a type unsafe to decode in Direct Read."""

    code = ErrorCode.FIELD_TYPE_UNSUPPORTED


class ReadCancelledError(DirectReadError):
    """A Direct Read was cooperatively cancelled by the caller.

    Raised when the caller-supplied ``cancel_check`` callable returned
    ``True`` at a physical record boundary — **before** the next physical
    record was read or decoded.  Records already yielded to the caller remain
    valid; no sentinel record is produced.  The structured ``context``
    carries the machine fields needed to resume or audit the read:

    - ``offset``              — physical start index of this call;
    - ``next_physical_index`` — physical index of the next unread record;
    - ``scanned``             — physical records consumed (deleted included);
    - ``yielded``             — records actually yielded/returned;
    - ``record_count``        — declared physical record count of the table.
    """

    code = ErrorCode.READ_CANCELLED


# --- direct-write research errors (RESEARCH / NEXT VERSION — not stable 1.x) ---
# The write-conflict case deliberately reuses the stable OUTPUT_EXISTS code
# (OperationOutputExistsError) instead of a parallel vocabulary.  These typed
# classes are classified from the structured machine code the shared physical
# writer attaches to its failures — never from English message text.


class DirectWriteError(DirectReadError):
    """Base class for the typed direct-write failures (RESEARCH)."""


class DestinationIoError(DirectWriteError):
    """A filesystem failure around DBF/FPT staging or publication."""

    code = ErrorCode.DESTINATION_IO_ERROR


class WriteSchemaInvalidError(DirectWriteError):
    """The schema is unusable for writing (no fields, bad combination)."""

    code = ErrorCode.WRITE_SCHEMA_INVALID


class WriteFieldUnsupportedError(DirectWriteError):
    """A field type the shared writer backend cannot represent."""

    code = ErrorCode.WRITE_FIELD_UNSUPPORTED


class WriteValueInvalidError(DirectWriteError):
    """A value (or record stream) does not fit or convert for its target field."""

    code = ErrorCode.WRITE_VALUE_INVALID


class WriteMemoFailedError(DirectWriteError):
    """A memo payload could not be written or finalized."""

    code = ErrorCode.WRITE_MEMO_FAILED


class WritePublicationFailedError(DirectWriteError):
    """Staging, fsync or the final publication replace failed."""

    code = ErrorCode.WRITE_PUBLICATION_FAILED


@dataclasses.dataclass(frozen=True)
class OperationError:
    """Structured, machine-readable description of one public failure.

    This is the single neutral payload model shared by every high-level
    operation (export, reconstruction, verification, quality).  It never
    replaces the human-readable ``str`` fields of existing result objects —
    it is the additive machine contract so callers (including the MCP
    toolchain) can classify a failure from ``code`` alone, without parsing
    the English message.
    """

    code: str
    message: str
    operation: str | None = None
    path: str | os.PathLike[str] | None = None
    table: str | None = None
    context: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe payload (``json.dumps`` never raises)."""
        path = _json_safe(self.path)
        if isinstance(path, str) and os.sep != "/":
            with contextlib.suppress(OSError, RuntimeError, ValueError):
                path = Path(path).as_posix()
        return {
            "code": self.code,
            "message": self.message,
            "operation": self.operation,
            "path": path,
            "table": self.table,
            "context": _json_safe(dict(self.context)) if self.context else {},
        }


def _operation_error_code(code: ErrorCode | str) -> str:
    return code.value if isinstance(code, ErrorCode) else str(code)


class _OperationErrorMixin(Exception):
    """Shared payload behaviour for public-boundary operation exceptions.

    Subclasses keep their historical builtin superclass (``ValueError``,
    ``FileNotFoundError``, ``FileExistsError``) so existing
    ``isinstance`` checks continue to work, and additionally expose the
    machine contract: a stable ``code`` and a JSON-safe ``to_dict()``.
    """

    error: OperationError

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode | str,
        operation: str | None = None,
        path: str | os.PathLike[str] | None = None,
        table: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error = OperationError(
            code=_operation_error_code(code),
            message=message,
            operation=operation,
            # POSIX form for transport stability (matches DirectReadError).
            path=Path(path).as_posix() if path is not None else None,
            table=table,
            context=context,
        )

    @property
    def code(self) -> str:
        return self.error.code

    def to_dict(self) -> dict[str, Any]:
        return self.error.to_dict()


class OperationArgumentError(_OperationErrorMixin, ValueError):
    """A public operation received an invalid argument (``ARGUMENT_INVALID``).

    Subclasses :class:`ValueError` for backward compatibility with callers
    that previously caught the bare ``ValueError`` raised at the public
    boundary.
    """

    default_code = ErrorCode.ARGUMENT_INVALID

    def __init__(
        self,
        message: str,
        *,
        operation: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            code=type(self).default_code,
            operation=operation,
            context=context,
        )


class OperationPathError(_OperationErrorMixin, FileNotFoundError):
    """A public operation received a missing path (``PATH_NOT_FOUND``).

    Subclasses :class:`FileNotFoundError` for backward compatibility with
    callers that previously caught the bare ``FileNotFoundError``.
    """

    default_code = ErrorCode.PATH_NOT_FOUND

    def __init__(
        self,
        message: str,
        *,
        operation: str | None = None,
        path: str | os.PathLike[str] | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            code=type(self).default_code,
            operation=operation,
            path=path,
            context=context,
        )


class OperationOutputExistsError(_OperationErrorMixin, FileExistsError):
    """A write operation refused to overwrite an existing output
    (``OUTPUT_EXISTS``).

    Subclasses :class:`FileExistsError` so callers that previously caught
    the codeless internal error keep working.
    """

    default_code = ErrorCode.OUTPUT_EXISTS

    def __init__(
        self,
        message: str,
        *,
        operation: str | None = None,
        path: str | os.PathLike[str] | None = None,
        table: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            code=type(self).default_code,
            operation=operation,
            path=path,
            table=table,
            context=context,
        )


__all__ = [
    "ArgumentInvalidError",
    "DbfFormatUnsupportedError",
    "DbfHeaderInvalidError",
    "DbfIoError",
    "DbfPathError",
    "DbfRecordInvalidError",
    "DbfTruncatedError",
    "DestinationIoError",
    "DirectReadError",
    "DirectWriteError",
    "EncodingUnknownError",
    "ErrorCode",
    "FieldProjectionInvalidError",
    "FieldTypeUnsupportedError",
    "FptInvalidError",
    "FptRequiredMissingError",
    "OperationArgumentError",
    "OperationError",
    "OperationOutputExistsError",
    "OperationPathError",
    "ReadCancelledError",
    "TextDecodeError",
    "WriteFieldUnsupportedError",
    "WriteMemoFailedError",
    "WritePublicationFailedError",
    "WriteSchemaInvalidError",
    "WriteValueInvalidError",
]

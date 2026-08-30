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
    """Stable machine codes for direct read failures."""

    PATH_NOT_FOUND = "PATH_NOT_FOUND"
    DBF_HEADER_INVALID = "DBF_HEADER_INVALID"
    DBF_TRUNCATED = "DBF_TRUNCATED"
    DBF_FORMAT_UNSUPPORTED = "DBF_FORMAT_UNSUPPORTED"
    ENCODING_UNKNOWN = "ENCODING_UNKNOWN"
    DBF_IO_ERROR = "DBF_IO_ERROR"


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


__all__ = [
    "DbfFormatUnsupportedError",
    "DbfHeaderInvalidError",
    "DbfIoError",
    "DbfPathError",
    "DbfTruncatedError",
    "DirectReadError",
    "EncodingUnknownError",
    "ErrorCode",
]

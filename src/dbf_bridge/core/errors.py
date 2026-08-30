"""Typed, machine-readable error model for the direct read core.

Callers (including the MCP toolchain) must be able to classify failures
without parsing exception text: every error carries a stable
:class:`ErrorCode`, the offending path, and a JSON-safe context mapping.
"""

from __future__ import annotations

import enum
import os
from collections.abc import Mapping
from typing import Any


class ErrorCode(str, enum.Enum):
    """Stable machine codes for direct read failures."""

    PATH_NOT_FOUND = "PATH_NOT_FOUND"
    DBF_HEADER_INVALID = "DBF_HEADER_INVALID"
    DBF_TRUNCATED = "DBF_TRUNCATED"
    DBF_FORMAT_UNSUPPORTED = "DBF_FORMAT_UNSUPPORTED"
    ENCODING_UNKNOWN = "ENCODING_UNKNOWN"


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
        """Return a JSON-safe structured description of the error."""
        return {
            "code": self.code.value,
            "message": self.message,
            "path": self.path,
            "context": self.context,
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


__all__ = [
    "DbfFormatUnsupportedError",
    "DbfHeaderInvalidError",
    "DbfPathError",
    "DbfTruncatedError",
    "DirectReadError",
    "EncodingUnknownError",
    "ErrorCode",
]

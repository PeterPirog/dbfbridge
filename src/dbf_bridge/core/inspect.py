"""Public direct read entry points: ``inspect_table`` and ``read_schema``.

Both functions are strictly read-only: the DBF read is bounded by the
declared ``header_length`` (independent of the number of records), companion
memo/CDX files are looked up in the table's directory, and immutable models
are returned.  They never iterate records, open memo payloads, hash the
source, or create any file.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import errors
from .header import (
    SUPPORTED_MEMO_FORMATS,
    ParsedHeader,
    fpt_header_details,
    last_update_date,
    memo_companion_extension,
    memo_companion_format,
    parse_header,
)
from .models import CompanionFile, FieldInfo, TableInfo, TableSchema  # noqa: F401 (re-export)

#: Valid FPT block sizes are powers of two between 64 and 4096 bytes.
_VALID_FPT_BLOCK_SIZES = frozenset(64 << shift for shift in range(0, 7))


def _find_companions(directory: Path, stem: str, extensions: tuple[str, ...]) -> dict[str, Path]:
    """Find ``<stem><ext>`` companions in *directory* (case-insensitive).

    Direct exact-name paths are checked first so the common case performs no
    directory scan; at most one case-insensitive scan is performed per call.
    A directory-scan failure is a typed I/O error, never a silent "missing".
    """
    found: dict[str, Path] = {}
    for ext in extensions:
        candidate = directory / f"{stem}{ext}"
        if candidate.is_file():
            found[ext] = candidate
    if len(found) == len(extensions):
        return found
    wanted = {f"{stem}{ext}".casefold(): ext for ext in extensions if ext not in found}
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                name = entry.name.casefold()
                ext = wanted.get(name)
                if ext is not None and entry.is_file():
                    found[ext] = Path(entry.path)
                    del wanted[name]
                    if not wanted:
                        break
    except OSError as exc:
        raise errors.DbfIoError(
            f"Cannot scan {directory} for companion files.",
            path=directory,
            context={"errno": exc.errno, "expected": [f"{stem}{ext}" for ext in extensions]},
        ) from exc
    return found


def _companions(
    dbf_path: Path, header: ParsedHeader
) -> tuple[CompanionFile | None, CompanionFile | None]:
    memo_format = memo_companion_format(header.dbversion_byte)
    extensions = tuple(
        ext for ext in (memo_companion_extension(header.dbversion_byte), ".cdx") if ext
    )
    found = _find_companions(dbf_path.parent, dbf_path.stem, extensions)
    memo = (
        CompanionFile(
            path=found[memo_companion_extension(header.dbversion_byte) or ""], format=memo_format
        )
        if memo_companion_extension(header.dbversion_byte) in found
        else None
    )
    cdx = CompanionFile(path=found[".cdx"], format="CDX") if ".cdx" in found else None
    return memo, cdx


def _fpt_health_warning(memo_companion: CompanionFile | None) -> str | None:
    """Diagnostic warning when an FPT companion header is unreadable/invalid."""
    if memo_companion is None:
        return None
    size, next_free, block = fpt_header_details(memo_companion.path)
    if size is None:
        return None
    if size < 8:
        return (
            f"FPT companion '{memo_companion.path.name}' is only {size} bytes long; "
            "its 8-byte file header is missing."
        )
    if next_free is None or block is None:
        return f"FPT companion '{memo_companion.path.name}' has an unreadable file header."
    if block not in _VALID_FPT_BLOCK_SIZES:
        return (
            f"FPT companion '{memo_companion.path.name}' declares an invalid block "
            f"size {block} (expected a power of two between 64 and 4096)."
        )
    return None


def _load(
    dbf_path: Path,
) -> tuple[ParsedHeader, CompanionFile | None, CompanionFile | None, tuple[str, ...]]:
    header = parse_header(dbf_path)
    memo_companion, cdx_companion = _companions(dbf_path, header)
    warnings: list[str] = []

    if last_update_date(header.year, header.month, header.day) is None:
        warnings.append(
            f"Header last-update date is invalid (month={header.month}, "
            f"day={header.day}); it is reported as null."
        )

    memo_format = memo_companion_format(header.dbversion_byte)
    if header.has_memo_fields:
        expected_ext = memo_companion_extension(header.dbversion_byte)
        if memo_companion is None:
            names = ", ".join(f"'{field.name}'" for field in header.fields if field.is_memo)
            warnings.append(
                f"Memo fields {names} require a {memo_format or 'memo'} companion file "
                f"({expected_ext or 'unknown extension'}) that was not found; "
                "memo values cannot be read."
            )
        else:
            if memo_format not in SUPPORTED_MEMO_FORMATS:
                warnings.append(
                    f"Memo companion format {memo_format} "
                    f"('{memo_companion.path.name}') is not supported for reading in "
                    "Direct Read; only FPT (VFP/FoxPro) is supported."
                )
            fpt_warning = _fpt_health_warning(memo_companion)
            if fpt_warning is not None:
                warnings.append(fpt_warning)
    if header.has_structural_cdx and cdx_companion is None:
        warnings.append(
            "The structural CDX flag is set in the header but no .cdx companion "
            "file was found next to the table."
        )
    for field in header.fields:
        if not field.supported:
            warnings.append(
                f"Field '{field.name}' ({field.dbf_type}) is not supported for export: "
                f"{field.unsupported_reason}"
            )
    return header, memo_companion, cdx_companion, tuple(warnings)


def _as_dbf_path(path: str | os.PathLike[str]) -> Path:
    return Path(os.fspath(path)) if not isinstance(path, Path) else path


def inspect_table(path: str | os.PathLike[str]) -> TableInfo:
    """Inspect one DBF table and return its header-only :class:`TableInfo`.

    Read-only and independent of the record count: the DBF read is bounded by
    the declared header length, plus a companion-file lookup in the table's
    directory.  No files are created and the source stays byte-identical.  A
    missing memo companion for memo fields is reported as a warning in
    ``TableInfo.warnings`` when the header itself is safe to describe.
    """
    dbf_path = _as_dbf_path(path)
    header, _memo, _cdx, warnings = _load(dbf_path)
    return TableInfo.from_parsed(header, warnings=warnings)


def read_schema(path: str | os.PathLike[str]) -> TableSchema:
    """Read the full safe header schema of one DBF table as :class:`TableSchema`.

    Includes the DBF/VFP version, last-update date, table flags, DBC backlink
    state, and companion (memo/CDX) metadata (format, name, size, FPT block
    size and next-free block).  It does not embed the raw header or memo
    payloads, and does not declare CDX tag-expression support: CDX presence
    is reported structurally only.
    """
    dbf_path = _as_dbf_path(path)
    header, memo_companion, cdx_companion, warnings = _load(dbf_path)
    return TableSchema.from_parsed(
        header,
        warnings=warnings,
        memo_companion=memo_companion,
        cdx_companion=cdx_companion,
    )


__all__ = [
    "FieldInfo",
    "TableInfo",
    "TableSchema",
    "inspect_table",
    "read_schema",
]

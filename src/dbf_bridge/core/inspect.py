"""Public direct read entry points: ``inspect_table`` and ``read_schema``.

Both functions are strictly read-only: they parse the DBF header (O(header)
work), discover companion ``.fpt``/``.cdx`` files case-insensitively, and
return immutable models.  They never iterate records, open memo payloads,
hash the source, or create any file.
"""

from __future__ import annotations

import os
from pathlib import Path

from .header import ParsedHeader, parse_header
from .models import CompanionFile, FieldInfo, TableInfo, TableSchema  # noqa: F401 (re-export)


def _find_companion(directory: str | os.PathLike[str], stem: str, ext: str) -> Path | None:
    """Find ``<stem><ext>`` in *directory*, matching the name case-insensitively."""
    target = f"{stem}{ext}".casefold()
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                try:
                    if entry.is_file() and entry.name.casefold() == target:
                        return Path(entry.path)
                except OSError:
                    continue
    except OSError:
        return None
    return None


def _companions(dbf_path: Path) -> tuple[CompanionFile | None, CompanionFile | None]:
    memo = _find_companion(dbf_path.parent, dbf_path.stem, ".fpt")
    cdx = _find_companion(dbf_path.parent, dbf_path.stem, ".cdx")
    return (
        CompanionFile(path=memo) if memo is not None else None,
        CompanionFile(path=cdx) if cdx is not None else None,
    )


def _load(
    dbf_path: Path,
) -> tuple[ParsedHeader, CompanionFile | None, CompanionFile | None, tuple[str, ...]]:
    header = parse_header(dbf_path)
    memo_companion, cdx_companion = _companions(dbf_path)
    warnings: list[str] = []
    if header.has_memo_fields and memo_companion is None:
        names = ", ".join(f"'{field.name}'" for field in header.fields if field.is_memo)
        warnings.append(
            f"Memo fields {names} require an FPT companion file that was not found; "
            "memo values cannot be read."
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

    Read-only and O(header): the record area is never read, no files are
    created, and the source stays byte-identical.  A missing FPT companion
    for memo fields is reported as a warning in ``TableInfo.warnings`` when
    the header itself is safe to describe.
    """
    dbf_path = _as_dbf_path(path)
    header, _memo, _cdx, warnings = _load(dbf_path)
    return TableInfo.from_parsed(header, warnings=warnings)


def read_schema(path: str | os.PathLike[str]) -> TableSchema:
    """Read the full safe header schema of one DBF table as :class:`TableSchema`.

    Includes the DBF/VFP version, last-update date, header flags, and
    companion ``.fpt``/``.cdx`` metadata (name, size, FPT block size and
    next-free block).  It does not embed the raw header or memo payloads, and
    does not declare CDX tag-expression support: CDX presence is reported
    structurally only.
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

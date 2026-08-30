"""Immutable, JSON-safe public models for the direct read core.

The models carry the physical DBF/VFP metadata an application (or the MCP
toolchain) needs.  They are read-only dataclasses with an explicit
``to_dict()`` method whose output is JSON-safe: no ``bytes``, no ``Path``.
They deliberately do not embed the raw header (Base64) — that belongs to the
forensic round-trip profile, not to a fast inspection.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .header import ParsedField, ParsedHeader, fpt_header_details, last_update_date


@dataclass(frozen=True)
class CompanionFile:
    """A companion file discovered next to the DBF (``.fpt`` or ``.cdx``)."""

    path: Path


@dataclass(frozen=True)
class FieldInfo:
    """Physical and logical properties of one DBF field."""

    ordinal: int
    name: str
    dbf_type: str
    length: int
    decimal_count: int
    address: int
    flags: int
    is_memo: bool
    supported: bool
    unsupported_reason: str | None = None

    @property
    def dbf_type_name(self) -> str:
        """Human-readable DBF/VFP type name (``Unknown`` for unmapped types)."""
        from .fields import type_name

        return type_name(self.dbf_type)

    @property
    def system(self) -> bool:
        """The descriptor's system-field flag (bit 0x01)."""
        return bool(self.flags & 0x01)

    @property
    def nullable(self) -> bool:
        """The descriptor's NULL flag (bit 0x02)."""
        return bool(self.flags & 0x02)

    @property
    def binary(self) -> bool:
        """The descriptor's binary/NOCPTRANS flag (bit 0x04)."""
        return bool(self.flags & 0x04)

    @classmethod
    def from_parsed(cls, field: ParsedField) -> FieldInfo:
        return cls(
            ordinal=field.ordinal,
            name=field.name,
            dbf_type=field.dbf_type,
            length=field.length,
            decimal_count=field.decimal_count,
            address=field.address,
            flags=field.flags,
            is_memo=field.is_memo,
            supported=field.supported,
            unsupported_reason=field.unsupported_reason,
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe representation (no bytes, no Path)."""
        return {
            "ordinal": self.ordinal,
            "name": self.name,
            "dbf_type": self.dbf_type,
            "dbf_type_name": self.dbf_type_name,
            "length": self.length,
            "decimal_count": self.decimal_count,
            "address": self.address,
            "flags": self.flags,
            "system": self.system,
            "nullable": self.nullable,
            "binary": self.binary,
            "is_memo": self.is_memo,
            "supported": self.supported,
            "unsupported_reason": self.unsupported_reason,
        }


@dataclass(frozen=True)
class TableInfo:
    """Compact, header-only description of a table (from ``inspect_table``)."""

    path: Path
    record_count: int
    header_length: int
    record_length: int
    language_driver: int
    encoding: str
    has_memo: bool
    has_structural_cdx: bool
    dbc_bound: bool
    fields: tuple[FieldInfo, ...]
    warnings: tuple[str, ...] = ()

    @classmethod
    def from_parsed(cls, header: ParsedHeader, *, warnings: tuple[str, ...] = ()) -> TableInfo:
        return cls(
            path=header.path,
            record_count=header.record_count,
            header_length=header.header_length,
            record_length=header.record_length,
            language_driver=header.language_driver,
            encoding=header.encoding,
            has_memo=header.has_memo_fields,
            has_structural_cdx=bool(header.structural_index_flag),
            dbc_bound=header.dbc_backlink_record > 0,
            fields=tuple(FieldInfo.from_parsed(field) for field in header.fields),
            warnings=warnings,
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe representation (no bytes, no Path)."""
        return {
            "path": self.path.as_posix(),
            "record_count": self.record_count,
            "header_length": self.header_length,
            "record_length": self.record_length,
            "language_driver": self.language_driver,
            "language_driver_hex": f"0x{self.language_driver:02x}",
            "encoding": self.encoding,
            "has_memo": self.has_memo,
            "has_structural_cdx": self.has_structural_cdx,
            "dbc_bound": self.dbc_bound,
            "fields": [field.to_dict() for field in self.fields],
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class TableSchema:
    """Full safe header schema (from ``read_schema``).

    Extends :class:`TableInfo` with the DBF/VFP version, last-update date,
    header flags, and companion ``.fpt`` / ``.cdx`` metadata.  It never
    contains the raw header bytes or any memo payload.
    """

    path: Path
    record_count: int
    header_length: int
    record_length: int
    language_driver: int
    encoding: str
    has_memo: bool
    has_structural_cdx: bool
    dbc_bound: bool
    fields: tuple[FieldInfo, ...]
    warnings: tuple[str, ...]
    dbversion_byte: int
    dbversion_name: str
    last_update: str | None
    incomplete_transaction: bool
    encryption_flag: bool
    memo_companion_present: bool
    memo_companion_path: str | None
    memo_companion_size_bytes: int | None
    memo_block_size: int | None
    memo_next_free_block: int | None
    companion_cdx_present: bool
    companion_cdx_path: str | None

    @classmethod
    def from_parsed(
        cls,
        header: ParsedHeader,
        *,
        warnings: tuple[str, ...] = (),
        memo_companion: CompanionFile | None = None,
        cdx_companion: CompanionFile | None = None,
    ) -> TableSchema:
        memo_size, memo_next_free, memo_block = fpt_header_details(
            memo_companion.path if memo_companion is not None else None
        )
        return cls(
            path=header.path,
            record_count=header.record_count,
            header_length=header.header_length,
            record_length=header.record_length,
            language_driver=header.language_driver,
            encoding=header.encoding,
            has_memo=header.has_memo_fields,
            has_structural_cdx=bool(header.structural_index_flag),
            dbc_bound=header.dbc_backlink_record > 0,
            fields=tuple(FieldInfo.from_parsed(field) for field in header.fields),
            warnings=warnings,
            dbversion_byte=header.dbversion_byte,
            dbversion_name=header.dbversion_name,
            last_update=last_update_date(header.year, header.month, header.day),
            incomplete_transaction=bool(header.incomplete_transaction),
            encryption_flag=bool(header.encryption_flag),
            memo_companion_present=memo_companion is not None,
            memo_companion_path=memo_companion.path.as_posix() if memo_companion else None,
            memo_companion_size_bytes=memo_size,
            memo_block_size=memo_block,
            memo_next_free_block=memo_next_free,
            companion_cdx_present=cdx_companion is not None,
            companion_cdx_path=cdx_companion.path.as_posix() if cdx_companion else None,
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe representation (no bytes, no Path)."""
        base = {
            "path": self.path.as_posix(),
            "record_count": self.record_count,
            "header_length": self.header_length,
            "record_length": self.record_length,
            "language_driver": self.language_driver,
            "language_driver_hex": f"0x{self.language_driver:02x}",
            "encoding": self.encoding,
            "has_memo": self.has_memo,
            "has_structural_cdx": self.has_structural_cdx,
            "dbc_bound": self.dbc_bound,
            "fields": [field.to_dict() for field in self.fields],
            "warnings": list(self.warnings),
        }
        base.update(
            {
                "dbversion_byte": self.dbversion_byte,
                "dbversion_hex": f"0x{self.dbversion_byte:02x}",
                "dbversion_name": self.dbversion_name,
                "last_update": self.last_update,
                "incomplete_transaction": self.incomplete_transaction,
                "encryption_flag": self.encryption_flag,
                "memo_companion": {
                    "present": self.memo_companion_present,
                    "path": self.memo_companion_path,
                    "size_bytes": self.memo_companion_size_bytes,
                    "block_size_bytes": self.memo_block_size,
                    "next_free_block": self.memo_next_free_block,
                },
                "companion_cdx": {
                    "present": self.companion_cdx_present,
                    "path": self.companion_cdx_path,
                },
            }
        )
        return base


__all__ = [
    "FieldInfo",
    "TableInfo",
    "TableSchema",
]

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

from .fields import is_autoincrement_field, is_nocptrans_field
from .header import (
    ParsedField,
    ParsedHeader,
    fpt_header_details,
    last_update_date,
    memo_companion_format,
)


@dataclass(frozen=True)
class CompanionFile:
    """A companion file discovered next to the DBF (memo or structural CDX)."""

    path: Path
    format: str | None = None


@dataclass(frozen=True)
class FieldInfo:
    """Physical and logical properties of one DBF field.

    ``index_field_flag`` (descriptor byte 31) is kept only for migration
    schema compatibility: VFP reserves descriptor bytes 24-31, so the byte
    is not reliable information about whether the field belongs to a CDX
    index.
    """

    ordinal: int
    name: str
    dbf_type: str
    length: int
    decimal_count: int
    address: int
    flags: int
    index_field_flag: int
    autoincrement_next_value: int
    autoincrement_step: int
    is_memo: bool
    is_binary: bool
    supported: bool
    unsupported_reason: str | None = None
    #: DBF version byte of the parent table (0 when unknown).  It decides
    #: how ``is_autoincrement`` is interpreted: Visual FoxPro marks
    #: autoincrement with the field-flags mask 0x0C on an Integer field,
    #: while dBASE Level 7 uses the physical type ``+``.
    dbversion_byte: int = 0

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
    def nocptrans(self) -> bool:
        """The descriptor's binary/NOCPTRANS flag (bit 0x04) where it is
        meaningful: for Character/Varchar and memo fields only.  Bit 0x04 is
        part of the VFP autoincrement mask 0x0C, so an autoincrement Integer
        (or other numeric column) is never reported as NOCPTRANS."""
        return is_nocptrans_field(self.dbf_type, self.flags)

    @property
    def is_autoincrement(self) -> bool:
        """Whether the field is an autoincrement field.

        Visual FoxPro derives this from the field-flags mask 0x0C on an
        Integer (``I``) field (``autoincrement_next_value``/..._step come
        from descriptor bytes 19-22 LE and 23).  The dBASE Level 7 type
        ``+`` is recognized only outside VFP and never proves VFP
        autoincrement semantics.
        """
        return is_autoincrement_field(
            dbf_type=self.dbf_type, flags=self.flags, dbversion_byte=self.dbversion_byte
        )

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
            index_field_flag=field.index_field_flag,
            autoincrement_next_value=field.autoincrement_next_value,
            autoincrement_step=field.autoincrement_step,
            is_memo=field.is_memo,
            is_binary=field.is_binary,
            supported=field.supported,
            unsupported_reason=field.unsupported_reason,
            dbversion_byte=field.dbversion_byte,
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
            "nocptrans": self.nocptrans,
            "index_field_flag": self.index_field_flag,
            "is_autoincrement": self.is_autoincrement,
            "autoincrement_next_value": self.autoincrement_next_value,
            "autoincrement_step": self.autoincrement_step,
            "is_memo": self.is_memo,
            "is_binary": self.is_binary,
            "supported": self.supported,
            "unsupported_reason": self.unsupported_reason,
            "dbversion_byte": self.dbversion_byte,
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
    has_memo_flag: bool
    has_structural_cdx: bool
    is_database_container: bool
    #: Raw table-flags byte (header offset 28); the bits are exposed through
    #: the derived booleans below.
    table_flags: int
    dbc_bound: bool
    fields: tuple[FieldInfo, ...]
    warnings: tuple[str, ...] = ()

    @property
    def table_flags_hex(self) -> str:
        """The raw table-flags byte as a ``0xNN`` hex string."""
        return f"0x{self.table_flags:02x}"

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
            has_memo_flag=header.has_memo_flag,
            has_structural_cdx=header.has_structural_cdx,
            is_database_container=header.is_database_container,
            table_flags=header.table_flags,
            dbc_bound=header.dbc_bound,
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
            "has_memo_flag": self.has_memo_flag,
            "has_structural_cdx": self.has_structural_cdx,
            "is_database_container": self.is_database_container,
            "table_flags": self.table_flags,
            "table_flags_hex": self.table_flags_hex,
            "dbc_bound": self.dbc_bound,
            "fields": [field.to_dict() for field in self.fields],
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class TableSchema:
    """Full safe header schema (from ``read_schema``).

    Extends :class:`TableInfo` with the DBF/VFP version, last-update date,
    table flags, the DBC backlink state, and companion (memo/CDX) metadata.
    It never contains the raw header bytes or any memo payload.
    """

    path: Path
    record_count: int
    header_length: int
    record_length: int
    language_driver: int
    encoding: str
    has_memo: bool
    has_memo_flag: bool
    has_structural_cdx: bool
    is_database_container: bool
    dbc_bound: bool
    dbc_backlink_path: str | None
    #: Raw table-flags byte (header offset 28); the bits are exposed through
    #: the derived booleans below.
    table_flags: int
    fields: tuple[FieldInfo, ...]
    warnings: tuple[str, ...]
    dbversion_byte: int
    dbversion_name: str
    last_update: str | None
    incomplete_transaction: bool
    encryption_flag: bool
    memo_companion_format: str | None
    memo_companion_present: bool
    memo_companion_path: str | None
    memo_companion_size_bytes: int | None
    memo_block_size: int | None
    memo_next_free_block: int | None
    companion_cdx_present: bool
    companion_cdx_path: str | None

    @property
    def table_flags_hex(self) -> str:
        """The raw table-flags byte as a ``0xNN`` hex string."""
        return f"0x{self.table_flags:02x}"

    @classmethod
    def from_parsed(
        cls,
        header: ParsedHeader,
        *,
        warnings: tuple[str, ...] = (),
        memo_companion: CompanionFile | None = None,
        cdx_companion: CompanionFile | None = None,
        memo_details: tuple[int | None, int | None, int | None] | None = None,
    ) -> TableSchema:
        """Build the schema from a parsed header (with pre-computed memo
        companion details so the FPT header is read at most once per run)."""
        if memo_details is None:
            memo_details = fpt_header_details(
                memo_companion.path if memo_companion is not None else None
            )
        memo_size, memo_next_free, memo_block = memo_details
        # Memo fields or the memo table flag mean a companion of the format
        # implied by the DBF version is expected — report that format even
        # when the companion file itself is missing (presence/path/size stay
        # separate, false-valued fields).
        expected_memo_format = memo_companion_format(header.dbversion_byte)
        if memo_companion is not None:
            memo_format: str | None = memo_companion.format
        else:
            memo_format = (
                expected_memo_format if (header.has_memo_fields or header.has_memo_flag) else None
            )
        return cls(
            path=header.path,
            record_count=header.record_count,
            header_length=header.header_length,
            record_length=header.record_length,
            language_driver=header.language_driver,
            encoding=header.encoding,
            has_memo=header.has_memo_fields,
            has_memo_flag=header.has_memo_flag,
            has_structural_cdx=header.has_structural_cdx,
            is_database_container=header.is_database_container,
            dbc_bound=header.dbc_bound,
            dbc_backlink_path=header.dbc_backlink_path,
            table_flags=header.table_flags,
            fields=tuple(FieldInfo.from_parsed(field) for field in header.fields),
            warnings=warnings,
            dbversion_byte=header.dbversion_byte,
            dbversion_name=header.dbversion_name,
            last_update=last_update_date(header.year, header.month, header.day),
            incomplete_transaction=bool(header.incomplete_transaction),
            encryption_flag=bool(header.encryption_flag),
            memo_companion_format=memo_format,
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
            "has_memo_flag": self.has_memo_flag,
            "has_structural_cdx": self.has_structural_cdx,
            "is_database_container": self.is_database_container,
            "table_flags": self.table_flags,
            "table_flags_hex": self.table_flags_hex,
            "dbc_bound": self.dbc_bound,
            "dbc_backlink_path": self.dbc_backlink_path,
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
                    "format": self.memo_companion_format,
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

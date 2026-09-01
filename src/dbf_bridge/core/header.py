"""Pure, read-only DBF header parsing for the direct read core.

This module is the single implementation of DBF header parsing.  The DBF read
is bounded by the declared ``header_length`` (independent of the record
count), the descriptor section is never scanned past it, no record is ever
parsed, no file is created, and no third-party package is required.  The
migration exporter delegates to :func:`parse_header` instead of keeping a
second copy of the parser.
"""

from __future__ import annotations

import errno
import os
import stat as stat_module
import struct
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import errors
from .codecs import _ensure_encoding_available, driver_to_encoding
from .fields import (
    VFP_VERSIONS,
    classify_field,
    is_autoincrement_field,
    is_nocptrans_field,
    type_name,
)

DBF_HEADER = struct.Struct("<BBBBLHHHBBLLLBBH")
FIELD_DESCRIPTOR = struct.Struct("<11scLBBHBBBB7sB")
FPT_HEADER_PREFIX = struct.Struct(">LHH")

DBF_HEADER_SIZE = DBF_HEADER.size
FIELD_DESCRIPTOR_SIZE = FIELD_DESCRIPTOR.size

#: The only valid byte terminating the field descriptor section.
FIELD_TERMINATOR = b"\r"

#: 32-byte fixed header plus at least the field terminator byte.
MINIMUM_HEADER_LENGTH = 33

#: Size of the Visual FoxPro header extension (DBC backlink + padding) that
#: must follow the field terminator in a VFP table.
VFP_BACKLINK_SIZE = 263

#: Size of a complete FPT header record.  The first 8 bytes carry the
#: next-free block and the block size; files shorter than the full header
#: record are structurally suspicious (warning, not an error).
FPT_HEADER_RECORD_SIZE = 512

DB_VERSION_NAMES: dict[int, str] = {
    0x02: "FoxBASE",
    0x03: "FoxBASE+/dBASE III Plus (no memo)",
    0x30: "Visual FoxPro 6+",
    0x31: "Visual FoxPro (autoincrement enabled)",
    0x32: "Visual FoxPro (Varchar/Varbinary enabled)",
    0x43: "dBASE IV SQL (no memo)",
    0x63: "dBASE IV SQL system (no memo)",
    0x83: "FoxBASE+/dBASE III Plus (with memo)",
    0x8B: "dBASE IV (with memo)",
    0xCB: "dBASE IV SQL (with memo)",
    0xE5: "HiPer-Six (SMT memo)",
    0xF5: "FoxPro 2.x or earlier (with memo)",
    0xFB: "FoxBASE",
}

SUPPORTED_VERSIONS = frozenset(DB_VERSION_NAMES)

#: Header "table flags" byte (offset 28) bit mask.
TABLE_FLAG_STRUCTURAL_CDX = 0x01
TABLE_FLAG_MEMO = 0x02
TABLE_FLAG_DATABASE_CONTAINER = 0x04

#: Memo companion file format per DBF version.  VFP/FoxPro use FPT; dBASE
#: III+/IV use DBT; HiPer-Six uses SMT.  Versions without memo support are
#: simply absent.
MEMO_COMPANION_EXTENSIONS: dict[int, str] = {
    0x30: ".fpt",
    0x31: ".fpt",
    0x32: ".fpt",
    0xF5: ".fpt",
    0x83: ".dbt",
    0x8B: ".dbt",
    0xCB: ".dbt",
    0xE5: ".smt",
}

#: Memo companion formats that the Direct Read core can read (FPT only).
SUPPORTED_MEMO_FORMATS = frozenset({"FPT"})


def memo_companion_extension(dbversion_byte: int) -> str | None:
    """Expected memo companion extension for a DBF version (``None`` if the
    version has no memo support)."""
    return MEMO_COMPANION_EXTENSIONS.get(dbversion_byte)


def memo_companion_format(dbversion_byte: int) -> str | None:
    """Expected memo companion format name (``FPT``/``DBT``/``SMT``)."""
    extension = memo_companion_extension(dbversion_byte)
    return extension.lstrip(".").upper() if extension else None


@dataclass(frozen=True)
class ParsedField:
    """One validated field descriptor plus its export classification."""

    ordinal: int
    name: str
    dbf_type: str
    length: int
    decimal_count: int
    address: int
    flags: int
    dbversion_byte: int
    index_field_flag: int
    autoincrement_next_value: int
    autoincrement_step: int
    descriptor_bytes: bytes
    target_representation: str
    is_memo: bool
    is_binary: bool
    supported: bool
    unsupported_reason: str | None

    @property
    def dbf_type_name(self) -> str:
        return type_name(self.dbf_type)

    @property
    def system(self) -> bool:
        return bool(self.flags & 0x01)

    @property
    def nullable(self) -> bool:
        return bool(self.flags & 0x02)

    @property
    def nocptrans(self) -> bool:
        """The descriptor's binary/NOCPTRANS flag (bit 0x04) where it is
        meaningful — Character/Varchar and memo fields only.  Bit 0x04 is
        part of the VFP autoincrement mask, so an autoincrement Integer
        (or any other numeric column) is never reported as NOCPTRANS."""
        return is_nocptrans_field(self.dbf_type, self.flags)

    @property
    def is_autoincrement(self) -> bool:
        """Whether the field is an autoincrement field.

        Visual FoxPro derives this from the field-flags mask 0x0C on an
        Integer (``I``) field.  The dBASE Level 7 type ``+`` is recognized
        outside VFP only and never proves VFP semantics.
        """
        return is_autoincrement_field(
            dbf_type=self.dbf_type, flags=self.flags, dbversion_byte=self.dbversion_byte
        )


@dataclass(frozen=True)
class ParsedHeader:
    """A fully validated DBF header (fixed part, descriptors, VFP extension)."""

    path: Path
    file_size: int
    dbversion_byte: int
    year: int
    month: int
    day: int
    record_count: int
    header_length: int
    record_length: int
    incomplete_transaction: int
    encryption_flag: int
    table_flags: int
    language_driver: int
    encoding: str
    fields: tuple[ParsedField, ...]
    header_region: bytes
    dbc_bound: bool
    dbc_backlink_path: str | None

    @property
    def dbversion_name(self) -> str:
        return DB_VERSION_NAMES.get(self.dbversion_byte, f"Unknown (0x{self.dbversion_byte:02x})")

    @property
    def is_vfp(self) -> bool:
        return self.dbversion_byte in VFP_VERSIONS

    @property
    def has_memo_fields(self) -> bool:
        return any(field.is_memo for field in self.fields)

    @property
    def structural_index_flag(self) -> int:
        """Raw table-flags byte, kept for migration-schema compatibility."""
        return self.table_flags

    @property
    def has_structural_cdx(self) -> bool:
        """Bit 0x01 of the table flags: the table has a structural CDX."""
        return bool(self.table_flags & TABLE_FLAG_STRUCTURAL_CDX)

    @property
    def has_memo_flag(self) -> bool:
        """Bit 0x02 of the table flags: the table uses memo."""
        return bool(self.table_flags & TABLE_FLAG_MEMO)

    @property
    def is_database_container(self) -> bool:
        """Bit 0x04 of the table flags: the file is a database container."""
        return bool(self.table_flags & TABLE_FLAG_DATABASE_CONTAINER)


def parse_header(
    path: str | os.PathLike[str],
    *,
    encoding: str | None = None,
    decode_errors: str = "strict",
) -> ParsedHeader:
    """Parse and validate the DBF header at *path* without touching records.

    Read-only: the file is opened for reading only, no output or temporary
    files are created, and the DBF read is bounded by the declared header
    length (independent of the record count).  Raises typed
    :class:`~dbf_bridge.core.errors.DirectReadError` subclasses with a stable
    machine code when the header cannot be trusted.
    """
    dbf_path = Path(path)
    try:
        stat_result = dbf_path.stat()
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOTDIR):
            raise errors.DbfPathError(
                f"DBF source does not exist or is not a file: {dbf_path}",
                path=dbf_path,
            ) from exc
        raise errors.DbfIoError(
            f"Cannot stat the DBF source: {dbf_path}",
            path=dbf_path,
            context={"errno": exc.errno},
        ) from exc
    except ValueError as exc:
        raise errors.DbfPathError(
            f"DBF source does not exist or is not a file: {dbf_path}",
            path=dbf_path,
        ) from exc
    if not stat_module.S_ISREG(stat_result.st_mode):
        raise errors.DbfPathError(
            f"DBF source is not a regular file: {dbf_path}",
            path=dbf_path,
        )
    file_size = stat_result.st_size

    try:
        infile = dbf_path.open("rb")
    except OSError as exc:
        raise errors.DbfIoError(
            f"Cannot open the DBF source for reading: {dbf_path}",
            path=dbf_path,
            context={"errno": exc.errno},
        ) from exc

    with infile:
        try:
            header_data = infile.read(DBF_HEADER_SIZE)
        except OSError as exc:
            raise errors.DbfIoError(
                f"Cannot read the DBF header: {dbf_path}",
                path=dbf_path,
                context={"errno": exc.errno},
            ) from exc
        if len(header_data) != DBF_HEADER_SIZE:
            raise errors.DbfTruncatedError(
                "DBF fixed header is shorter than 32 bytes.",
                path=dbf_path,
                context={"available_bytes": len(header_data), "required_bytes": DBF_HEADER_SIZE},
            )

        unpacked = DBF_HEADER.unpack(header_data)
        (
            dbversion_byte,
            year,
            month,
            day,
            record_count,
            header_length,
            record_length,
            _reserved1,
            incomplete_transaction,
            encryption_flag,
            _free_record_thread,
            _reserved2,
            _reserved3,
            table_flags,
            language_driver,
            _reserved4,
        ) = unpacked

        if dbversion_byte not in SUPPORTED_VERSIONS:
            raise errors.DbfFormatUnsupportedError(
                f"Unsupported DBF version byte 0x{dbversion_byte:02x}.",
                path=dbf_path,
                context={"dbversion_byte": dbversion_byte},
            )
        if header_length < MINIMUM_HEADER_LENGTH:
            raise errors.DbfHeaderInvalidError(
                "Declared header length is shorter than the fixed header plus terminator.",
                path=dbf_path,
                context={
                    "header_length": header_length,
                    "minimum_header_length": MINIMUM_HEADER_LENGTH,
                },
            )
        if header_length > file_size:
            raise errors.DbfTruncatedError(
                "Declared header length extends beyond the end of the file.",
                path=dbf_path,
                context={"header_length": header_length, "file_size": file_size},
            )

        resolved_encoding = encoding or driver_to_encoding(language_driver)
        if resolved_encoding is None:
            raise errors.EncodingUnknownError(
                f"Unknown language driver byte 0x{language_driver:02x} and no encoding override given.",
                path=dbf_path,
                context={"language_driver": language_driver},
            )
        # Validate/prepare the codec BEFORE any descriptor/backlink decoding:
        # an explicit custom Polish override (e.g. "mazovia") is registered on
        # demand here, and an unknown codec is a deterministic typed error
        # independent of the field content.
        try:
            resolved_encoding = _ensure_encoding_available(resolved_encoding)
        except LookupError as exc:
            # The context always names the ACTUAL offending codec: the user's
            # explicit override, or (for the auto path) the driver-resolved
            # name — never a meaningless None.
            raise errors.EncodingUnknownError(
                f"Unknown encoding {resolved_encoding!r} (no matching codec is available).",
                path=dbf_path,
                context={"encoding": resolved_encoding, "requested_encoding": encoding},
            ) from exc

        # Read the complete declared header region up-front so that the
        # descriptor scan can never run past it into the record area.
        infile.seek(0)
        try:
            header_region = infile.read(header_length)
        except OSError as exc:
            raise errors.DbfIoError(
                f"Cannot read the DBF header region: {dbf_path}",
                path=dbf_path,
                context={"errno": exc.errno},
            ) from exc
        if len(header_region) != header_length:
            raise errors.DbfTruncatedError(
                "The declared header region cannot be read in full.",
                path=dbf_path,
                context={
                    "header_length": header_length,
                    "available_bytes": len(header_region),
                },
            )

        fields: list[ParsedField] = []
        ordinal = 1
        offset = DBF_HEADER_SIZE
        terminator_offset: int | None = None
        while True:
            if offset >= len(header_region):
                raise errors.DbfTruncatedError(
                    "Field descriptor section ends before the terminator byte.",
                    path=dbf_path,
                    context={"field_ordinal": ordinal, "header_length": header_length},
                )
            marker = header_region[offset : offset + 1]
            if marker == FIELD_TERMINATOR:
                terminator_offset = offset
                break
            if marker == b"\n":
                raise errors.DbfHeaderInvalidError(
                    "Byte 0x0A is not a valid field section terminator (only 0x0D is).",
                    path=dbf_path,
                    context={"field_ordinal": ordinal, "offset": offset},
                )
            if offset + FIELD_DESCRIPTOR_SIZE > len(header_region):
                raise errors.DbfTruncatedError(
                    "A field descriptor extends past the declared header length.",
                    path=dbf_path,
                    context={"field_ordinal": ordinal, "header_length": header_length},
                )
            descriptor_data = header_region[offset : offset + FIELD_DESCRIPTOR_SIZE]
            fields.append(
                _parse_field_descriptor(
                    descriptor_data,
                    resolved_encoding,
                    decode_errors,
                    dbversion_byte,
                    ordinal,
                    dbf_path,
                )
            )
            ordinal += 1
            offset += FIELD_DESCRIPTOR_SIZE

        if record_length == 0:
            raise errors.DbfHeaderInvalidError(
                "Declared record length is zero.",
                path=dbf_path,
                context={"record_length": record_length},
            )
        expected_record_length = 1 + sum(field.length for field in fields)
        if record_length != expected_record_length:
            raise errors.DbfHeaderInvalidError(
                "Declared record length does not match the sum of field lengths plus the delete flag.",
                path=dbf_path,
                context={
                    "record_length": record_length,
                    "expected_record_length": expected_record_length,
                },
            )
        minimum_file_size = header_length + record_length * record_count
        if file_size < minimum_file_size:
            raise errors.DbfTruncatedError(
                "Physical record area is shorter than the header record count requires.",
                path=dbf_path,
                context={
                    "file_size": file_size,
                    "minimum_file_size": minimum_file_size,
                    "record_count": record_count,
                },
            )

    # Visual FoxPro tables carry a 263-byte DBC backlink area after the
    # field terminator; it must fit inside the declared header (i.e. before
    # the record area) to be trusted.
    dbc_bound = False
    dbc_backlink_path: str | None = None
    if dbversion_byte in VFP_VERSIONS and terminator_offset is not None:
        backlink_start = terminator_offset + 1
        backlink_end = backlink_start + VFP_BACKLINK_SIZE
        if backlink_end > header_length:
            raise errors.DbfTruncatedError(
                "The Visual FoxPro 263-byte DBC backlink area extends past the declared header.",
                path=dbf_path,
                context={
                    "terminator_offset": terminator_offset,
                    "header_length": header_length,
                    "required_backlink_size": VFP_BACKLINK_SIZE,
                },
            )
        backlink_area = header_region[backlink_start:backlink_end]
        null_index = backlink_area.find(b"\x00")
        path_bytes = backlink_area if null_index == -1 else backlink_area[:null_index]
        if path_bytes:
            # A non-empty backlink path means the table is DBC-bound even
            # when the bytes cannot be decoded with the resolved encoding;
            # the path is then reported as null (never raw bytes).
            dbc_bound = True
            try:
                dbc_backlink_path = path_bytes.decode(resolved_encoding, errors=decode_errors)
            except UnicodeDecodeError:
                dbc_backlink_path = None

    return ParsedHeader(
        path=dbf_path,
        file_size=file_size,
        dbversion_byte=dbversion_byte,
        year=year,
        month=month,
        day=day,
        record_count=record_count,
        header_length=header_length,
        record_length=record_length,
        incomplete_transaction=incomplete_transaction,
        encryption_flag=encryption_flag,
        table_flags=table_flags,
        language_driver=language_driver,
        encoding=resolved_encoding,
        fields=tuple(fields),
        header_region=header_region,
        dbc_bound=dbc_bound,
        dbc_backlink_path=dbc_backlink_path,
    )


def _parse_field_descriptor(
    descriptor_data: bytes,
    encoding: str,
    decode_errors: str,
    dbversion_byte: int,
    ordinal: int,
    dbf_path: Path,
) -> ParsedField:
    raw_name = descriptor_data[:11]
    type_byte = descriptor_data[11:12]
    try:
        dbf_type = type_byte.decode("ascii")
    except UnicodeDecodeError as exc:
        raise errors.DbfHeaderInvalidError(
            "Field type byte is not an ASCII type code.",
            path=dbf_path,
            context={"ordinal": ordinal, "type_byte": type_byte.hex()},
        ) from exc
    length = descriptor_data[16]
    decimal_count = descriptor_data[17]
    if dbf_type == "C":
        # Character fields longer than 255 bytes store the high byte in
        # the decimal-count position.
        length |= decimal_count << 8
        decimal_count = 0
    address = struct.unpack_from("<L", descriptor_data, 12)[0]
    flags = descriptor_data[18]
    # VFP autoincrement bookkeeping: next value (4 bytes LE) then step.
    autoincrement_next_value = struct.unpack_from("<L", descriptor_data, 19)[0]
    autoincrement_step = descriptor_data[23]
    # Bytes 24-31 are reserved in VFP.  Byte 31 was used by some dBASE-era
    # writers as an "index field" marker; it is kept only for migration
    # schema compatibility and is NOT reliable evidence that the field
    # belongs to a CDX index.
    index_field_flag = descriptor_data[31]

    name_bytes = raw_name.split(b"\0", 1)[0]
    try:
        name = name_bytes.decode(encoding, errors=decode_errors)
    except (UnicodeDecodeError, LookupError) as exc:
        raise errors.EncodingUnknownError(
            f"Field name bytes {name_bytes.hex()} cannot be decoded with {encoding!r}.",
            path=dbf_path,
            context={"ordinal": ordinal, "encoding": encoding},
        ) from exc

    classification = classify_field(
        dbf_type=dbf_type,
        length=length,
        decimal_count=decimal_count,
        dbversion_byte=dbversion_byte,
        flags=flags,
    )
    return ParsedField(
        ordinal=ordinal,
        name=name,
        dbf_type=dbf_type,
        length=length,
        decimal_count=decimal_count,
        address=address,
        flags=flags,
        dbversion_byte=dbversion_byte,
        index_field_flag=index_field_flag,
        autoincrement_next_value=autoincrement_next_value,
        autoincrement_step=autoincrement_step,
        descriptor_bytes=descriptor_data,
        target_representation=classification.target_representation,
        is_memo=classification.is_memo,
        is_binary=classification.is_binary,
        supported=classification.supported,
        unsupported_reason=classification.unsupported_reason,
    )


def last_update_date(year: int, month: int, day: int) -> str | None:
    """Expand the header's two-digit year to an ISO-8601 date.

    The year byte is interpreted as ``1900 + year`` (no century pivot).
    Returns ``None`` when the month/day values do not form a real date.
    """
    try:
        return date(1900 + year, month, day).isoformat()
    except ValueError:
        return None


def fpt_header_details(path: Path | None) -> tuple[int | None, int | None, int | None]:
    """Read one memo companion's size and header prefix (never its payload).

    For an ``.fpt`` companion the function reads the 8-byte header prefix —
    enough for the next-free block (bytes 0-3, big-endian) and the block
    size (bytes 6-7, big-endian).  A full FPT header record is 512 bytes,
    which only the caller can validate against.  DBT/SMT companions are
    stat-ed for their size but never interpreted as FPT headers.  Returns
    ``(size_bytes, next_free_block, block_size)`` with ``None`` entries when
    the companion is absent/is not regular or is shorter than the prefix.
    Every ``stat``/``open``/``read`` failure becomes :class:`DbfIoError`
    (``DBF_IO_ERROR``) with the path and a JSON-safe context.
    """
    if path is None:
        return None, None, None
    try:
        stat_result = path.stat()
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOTDIR):
            return None, None, None
        raise _companion_io_error(exc, path, "stat") from exc
    if not stat_module.S_ISREG(stat_result.st_mode):
        return None, None, None
    size: int = stat_result.st_size
    if path.suffix.lower() != ".fpt" or size < FPT_HEADER_PREFIX.size:
        return size, None, None
    try:
        with path.open("rb") as infile:
            header = infile.read(FPT_HEADER_PREFIX.size)
    except OSError as exc:
        raise _companion_io_error(exc, path, "read") from exc
    if len(header) != FPT_HEADER_PREFIX.size:
        return size, None, None
    next_free_block, _reserved, block_size = FPT_HEADER_PREFIX.unpack(header)
    return size, next_free_block, block_size


def _companion_io_error(exc: OSError, path: Path, operation: str) -> errors.DbfIoError:
    return errors.DbfIoError(
        f"Cannot {operation} the memo companion file: {path}",
        path=path,
        context={"errno": exc.errno, "operation": operation},
    )


__all__ = [
    "DBF_HEADER",
    "DBF_HEADER_SIZE",
    "DB_VERSION_NAMES",
    "FIELD_DESCRIPTOR",
    "FIELD_DESCRIPTOR_SIZE",
    "FIELD_TERMINATOR",
    "FPT_HEADER_PREFIX",
    "FPT_HEADER_RECORD_SIZE",
    "MEMO_COMPANION_EXTENSIONS",
    "MINIMUM_HEADER_LENGTH",
    "ParsedField",
    "ParsedHeader",
    "SUPPORTED_MEMO_FORMATS",
    "SUPPORTED_VERSIONS",
    "TABLE_FLAG_DATABASE_CONTAINER",
    "TABLE_FLAG_MEMO",
    "TABLE_FLAG_STRUCTURAL_CDX",
    "VFP_BACKLINK_SIZE",
    "VFP_VERSIONS",
    "driver_to_encoding",
    "fpt_header_details",
    "last_update_date",
    "memo_companion_extension",
    "memo_companion_format",
    "parse_header",
]

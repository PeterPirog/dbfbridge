"""Pure, read-only DBF header parsing for the direct read core.

This module is the single implementation of DBF header parsing.  It reads
only the header region (O(header) work), never iterates records, creates no
files, and requires no third-party package.  The migration exporter delegates
to :func:`parse_header` instead of keeping a second copy of the parser.
"""

from __future__ import annotations

import os
import stat as stat_module
import struct
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import errors
from .codecs import driver_to_encoding
from .fields import classify_field, type_name

DBF_HEADER = struct.Struct("<BBBBLHHHBBLLLBBH")
FIELD_DESCRIPTOR = struct.Struct("<11scLBBHBBBB7sB")
FPT_HEADER_PREFIX = struct.Struct(">LHH")

DBF_HEADER_SIZE = DBF_HEADER.size
FIELD_DESCRIPTOR_SIZE = FIELD_DESCRIPTOR.size

#: Bytes after the field terminator may end in these markers in legacy files.
FIELD_TERMINATORS = (b"\r", b"\n")

#: 32-byte fixed header plus at least the field terminator byte.
MINIMUM_HEADER_LENGTH = 33

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
VFP_VERSIONS = frozenset({0x30, 0x31, 0x32})


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
    index_field_flag: int
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
    def binary(self) -> bool:
        return bool(self.flags & 0x04)


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
    structural_index_flag: int
    language_driver: int
    encoding: str
    fields: tuple[ParsedField, ...]
    header_region: bytes
    dbc_backlink_record: int

    @property
    def dbversion_name(self) -> str:
        return DB_VERSION_NAMES.get(self.dbversion_byte, f"Unknown (0x{self.dbversion_byte:02x})")

    @property
    def is_vfp(self) -> bool:
        return self.dbversion_byte in VFP_VERSIONS

    @property
    def has_memo_fields(self) -> bool:
        return any(field.is_memo for field in self.fields)


def parse_header(
    path: str | os.PathLike[str],
    *,
    encoding: str | None = None,
    decode_errors: str = "strict",
) -> ParsedHeader:
    """Parse and validate the DBF header at *path* without touching records.

    Read-only: the file is opened for reading only, no output or temporary
    files are created, and the function performs O(header) work.  Raises
    typed :class:`~dbf_bridge.core.errors.DirectReadError` subclasses with a
    stable machine code when the header cannot be trusted.
    """
    dbf_path = Path(path)
    try:
        stat_result = dbf_path.stat()
    except (OSError, ValueError) as exc:
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

    with dbf_path.open("rb") as infile:
        header_data = infile.read(DBF_HEADER_SIZE)
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
            structural_index_flag,
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

        fields: list[ParsedField] = []
        ordinal = 1
        while True:
            marker = infile.read(1)
            if not marker:
                raise errors.DbfTruncatedError(
                    "Field descriptor section ends before the terminator byte.",
                    path=dbf_path,
                    context={"field_ordinal": ordinal},
                )
            if marker in FIELD_TERMINATORS:
                break
            descriptor_data = marker + infile.read(FIELD_DESCRIPTOR_SIZE - 1)
            if len(descriptor_data) != FIELD_DESCRIPTOR_SIZE:
                raise errors.DbfTruncatedError(
                    "A field descriptor is truncated.",
                    path=dbf_path,
                    context={"field_ordinal": ordinal},
                )
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

        # Keep the complete VFP header region, not only its 32-byte prefix:
        # the bytes after the field terminator hold the database-container
        # backlink and reserved padding that a byte-identical DBF must restore.
        infile.seek(0)
        header_region = infile.read(header_length)

    dbc_backlink_record = 0
    if dbversion_byte in VFP_VERSIONS and fields:
        terminator_offset = DBF_HEADER_SIZE + FIELD_DESCRIPTOR_SIZE * len(fields) + 1
        backlink = header_region[terminator_offset : terminator_offset + 2]
        if len(backlink) == 2:
            # First two bytes of the VFP 6+ header extension: the record
            # number of this table's structure record inside its DBC.
            # Zero means the table is standalone.
            dbc_backlink_record = int.from_bytes(backlink, "little")

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
        structural_index_flag=structural_index_flag,
        language_driver=language_driver,
        encoding=resolved_encoding,
        fields=tuple(fields),
        header_region=header_region,
        dbc_backlink_record=dbc_backlink_record,
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
        index_field_flag=index_field_flag,
        descriptor_bytes=descriptor_data,
        target_representation=classification.target_representation,
        is_memo=classification.is_memo,
        is_binary=classification.is_binary,
        supported=classification.supported,
        unsupported_reason=classification.unsupported_reason,
    )


def last_update_date(year: int, month: int, day: int) -> str | None:
    """Expand the header's two-digit date to an ISO-8601 string (``None`` if invalid)."""
    try:
        full_year = 2000 + year if year < 80 else 1900 + year
        return date(full_year, month, day).isoformat()
    except ValueError:
        return None


def fpt_header_details(path: Path | None) -> tuple[int | None, int | None, int | None]:
    """Read an FPT companion's 8-byte file header (never its payload).

    Returns ``(size_bytes, next_free_block, block_size)``; ``None`` entries
    when the companion is absent or shorter than an FPT header.
    """
    if path is None or not path.is_file():
        return None, None, None
    size = path.stat().st_size
    if path.suffix.lower() != ".fpt" or size < FPT_HEADER_PREFIX.size:
        return size, None, None
    with path.open("rb") as infile:
        header = infile.read(FPT_HEADER_PREFIX.size)
    if len(header) != FPT_HEADER_PREFIX.size:
        return size, None, None
    next_free_block, _reserved, block_size = FPT_HEADER_PREFIX.unpack(header)
    return size, next_free_block, block_size


__all__ = [
    "DBF_HEADER",
    "DBF_HEADER_SIZE",
    "DB_VERSION_NAMES",
    "FIELD_DESCRIPTOR",
    "FIELD_DESCRIPTOR_SIZE",
    "FIELD_TERMINATORS",
    "FPT_HEADER_PREFIX",
    "MINIMUM_HEADER_LENGTH",
    "ParsedField",
    "ParsedHeader",
    "SUPPORTED_VERSIONS",
    "VFP_VERSIONS",
    "driver_to_encoding",
    "fpt_header_details",
    "last_update_date",
    "parse_header",
]

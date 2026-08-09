from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from dbfread import DBF, MissingMemoFile
from dbfread.codepages import codepages, guess_encoding
from dbfread.dbversions import get_dbversion_string
from dbfread.field_parser import FieldParser

from .models import DiscoveredTable, ExportConfig, FieldMetadata, TableMetadata
from .polish_codecs import POLISH_FALLBACK_ENCODINGS, register_polish_codecs
from .serialization import field_metadata
from .validation import sha256_file

# Rejestrujemy polskie tabele kodowe (Mazovia/PIAST) przy imporcie modułu,
# aby były dostępne automatycznie — bez konieczności wywoływania przez
# użytkownika skryptu 05.
register_polish_codecs()

DBF_HEADER = struct.Struct("<BBBBLHHHBBLLLBBH")
FIELD_DESCRIPTOR = struct.Struct("<11scLBBHBBBB7sB")
FPT_HEADER_PREFIX = struct.Struct(">LHH")


class UnsupportedTableError(ValueError):
    """Raised when a table uses a field type that is not safe to export."""


class FieldParseError(ValueError):
    """Raised with field context when dbfread cannot parse a field."""


class LosslessText(str):
    """Decoded text retaining bytes when a fallback code page was required."""

    def __new__(cls, value: str, raw_bytes: bytes, encoding: str | None) -> LosslessText:
        instance = super().__new__(cls, value)
        instance.raw_bytes = raw_bytes
        instance.source_encoding = encoding
        return instance


class LosslessFieldParser(FieldParser):
    """FieldParser z automatycznym fallback dla polskich stron kodowych.

    Standardowy parser dbfread używa kodowania zadeklarowanego w nagłówku DBF
    (language driver byte). W praktyce dane w starych plikach FoxPro/Clipper
    bywają zapisane w innej stronie kodowej (np. Mazovia) mimo deklaracji
    cp1250 w nagłówku — co objawia się ``UnicodeDecodeError``.

    Ten parser przechwytuje błędy dekodowania tekstów (C, M) i próbuje
    odczytać te same bajty z alternatywnych polskich stron kodowych:
    cp1250 -> cp852 -> mazovia.  Dzięki temu użytkownik nie musi znać
    rzeczywistego kodowania danych — skrypt radzi sobie automatycznie.
    """

    def decode_text(self, text: bytes) -> str:
        """Dekoduje bajty z automatycznym fallback dla polskich stron kodowych."""
        if text is None:
            return ""
        primary = self.encoding or "cp1250"
        errors = self.char_decode_errors or "strict"

        # Szybka ścieżka: dekodowanie deklarowanym kodowaniem.
        if errors != "strict":
            # Tryb nie-strict (ignore/replace) — zostawiamy dbfread, bo
            # z założenia nie ma rzucać wyjątków.
            return text.decode(primary, errors=errors)

        try:
            return text.decode(primary, errors="strict")
        except UnicodeDecodeError:
            # Fallback: spróbuj polskich stron kodowych.
            for alt in POLISH_FALLBACK_ENCODINGS:
                if alt == primary:
                    continue
                try:
                    return LosslessText(text.decode(alt, errors="strict"), text, alt)
                except (UnicodeDecodeError, LookupError):
                    continue
            # Żadna strona kodowa nie pasuje — zwróć z replace, aby nie
            # przerywać eksportu całej tabeli.
            return LosslessText(text.decode(primary, errors="replace"), text, None)

    def parse(self, field: object, data: bytes) -> object:
        try:
            return super().parse(field, data)
        except Exception as exc:
            name = getattr(field, "name", "<unknown>")
            dbf_type = getattr(field, "type", "<unknown>")
            raise FieldParseError(f"field {name!r} type {dbf_type!r}: {exc}") from exc

    def parseY(self, field: object, data: bytes) -> Decimal:
        value = struct.unpack("<q", data)[0]
        return (Decimal(value) / Decimal("10000")).quantize(Decimal("0.0001"))


@dataclass(frozen=True)
class RawHeader:
    header_bytes: bytes
    dbversion_byte: int
    language_driver: int
    year: int
    month: int
    day: int
    record_count: int
    header_length: int
    record_length: int
    incomplete_transaction: int
    encryption_flag: int
    structural_index_flag: int
    fields: list[FieldMetadata]


def read_table_metadata(discovered: DiscoveredTable, config: ExportConfig) -> TableMetadata:
    raw = read_raw_header(discovered.source_path, config)
    unsupported = [field for field in raw.fields if not field.supported]
    if unsupported:
        details = "; ".join(
            f"{field.name} ({field.dbf_type}): {field.unsupported_reason}" for field in unsupported
        )
        raise UnsupportedTableError(details)

    table = open_table(discovered.source_path, config)
    fields = raw.fields
    warnings: list[str] = []
    if config.decode_errors in {"ignore", "replace"}:
        warnings.append(
            f"Character decode errors policy is {config.decode_errors!r}; decoding issues are not fatal."
        )
    if (
        config.missing_memo == "null-with-warning"
        and table.memofilename is None
        and any(field.is_memo for field in fields)
    ):
        warnings.append("Memo file is missing; memo values will be exported as null.")

    language_driver_name = codepages.get(table.header.language_driver, (None, None))[1]
    memo_path = Path(table.memofilename) if table.memofilename else discovered.memo_path
    memo_size, memo_next_block, memo_block_size = _memo_file_details(memo_path)
    fallbacks = []
    if config.decode_errors == "strict":
        fallbacks = list(dict.fromkeys([table.encoding, *POLISH_FALLBACK_ENCODINGS]))
    return TableMetadata(
        table_name=discovered.source_path.stem,
        relative_path=discovered.relative_path,
        dbf_path=discovered.source_path,
        dbversion=table.dbversion,
        dbversion_byte=table.header.dbversion,
        language_driver=table.header.language_driver,
        language_driver_name=language_driver_name,
        encoding=table.encoding,
        memo_path=memo_path,
        memo_present=memo_path is not None and memo_path.is_file(),
        fields=fields,
        warnings=warnings,
        source_size_bytes=discovered.source_path.stat().st_size,
        record_count=table.header.numrecords,
        header_length=table.header.headerlen,
        record_length=table.header.recordlen,
        last_update=_header_date(table.header.year, table.header.month, table.header.day),
        incomplete_transaction=table.header.incomplete_transaction,
        encryption_flag=table.header.encryption_flag,
        structural_index_flag=table.header.mdx_flag,
        encoding_override=config.encoding,
        decode_errors=config.decode_errors,
        encoding_fallbacks=fallbacks,
        memo_size_bytes=memo_size,
        memo_next_free_block=memo_next_block,
        memo_block_size=memo_block_size,
        memo_export_policy=config.memo,
        header_bytes=raw.header_bytes,
        source_sha256=sha256_file(discovered.source_path),
        memo_header_bytes=_read_prefix(memo_path, 512),
        memo_sha256=sha256_file(memo_path)
        if memo_path is not None and memo_path.is_file()
        else None,
    )


def open_table(dbf_path: Path, config: ExportConfig) -> DBF:
    return DBF(
        dbf_path,
        load=False,
        encoding=config.encoding,
        parserclass=LosslessFieldParser,
        char_decode_errors=config.decode_errors,
        ignore_missing_memofile=config.missing_memo == "null-with-warning",
    )


def iter_physical_records(table: DBF) -> Iterator[tuple[object, bool, bytes]]:
    """Yield active and deleted records in their original on-disk order."""

    with open(table.filename, "rb") as infile, table._open_memofile() as memofile:
        infile.seek(table.header.headerlen)
        parser = table.parserclass(table, memofile)
        parse = parser.parse
        for _record_index in range(table.header.numrecords):
            marker = infile.read(1)
            if marker not in {b" ", b"*"}:
                if marker in {b"\x1a", b""}:
                    break
                raise ValueError(
                    f"Unexpected DBF record marker {marker!r} in {Path(table.filename).name}."
                )
            raw_fields = [infile.read(field.length) for field in table.fields]
            if any(
                len(raw) != field.length
                for raw, field in zip(raw_fields, table.fields, strict=True)
            ):
                raise ValueError(f"Truncated DBF record in {Path(table.filename).name}.")
            items = [
                (field.name, parse(field, raw))
                for field, raw in zip(table.fields, raw_fields, strict=True)
            ]
            yield table.recfactory(items), marker == b"*", marker + b"".join(raw_fields)


def read_raw_header(dbf_path: Path, config: ExportConfig) -> RawHeader:
    with dbf_path.open("rb") as infile:
        header_data = infile.read(DBF_HEADER.size)
        if len(header_data) != DBF_HEADER.size:
            raise ValueError(f"DBF header is truncated in {dbf_path.name}.")

        unpacked = DBF_HEADER.unpack(header_data)
        dbversion_byte = unpacked[0]
        header_length = unpacked[5]
        record_length = unpacked[6]
        language_driver = unpacked[14]
        encoding = config.encoding or _guess_encoding(language_driver)

        fields: list[FieldMetadata] = []
        ordinal = 1
        while True:
            marker = infile.read(1)
            if marker in {b"\r", b"\n", b""}:
                break
            descriptor_data = marker + infile.read(FIELD_DESCRIPTOR.size - 1)
            if len(descriptor_data) != FIELD_DESCRIPTOR.size:
                raise ValueError(f"Field descriptor is truncated in {dbf_path.name}.")
            fields.append(
                _parse_field_descriptor(
                    descriptor_data,
                    encoding,
                    config,
                    dbversion_byte,
                    ordinal,
                )
            )
            ordinal += 1

        # Keep the complete VFP header region, not only its fixed 32-byte
        # prefix.  Bytes after the field terminator may contain reserved
        # padding and a database-container backlink.  They are irrelevant to
        # a logical export but must be restored for a byte-identical DBF.
        infile.seek(0)
        complete_header = infile.read(header_length)
        if len(complete_header) != header_length:
            raise ValueError(f"DBF header region is truncated in {dbf_path.name}.")

    return RawHeader(
        header_bytes=complete_header,
        dbversion_byte=dbversion_byte,
        language_driver=language_driver,
        year=unpacked[1],
        month=unpacked[2],
        day=unpacked[3],
        record_count=unpacked[4],
        header_length=header_length,
        record_length=record_length,
        incomplete_transaction=unpacked[8],
        encryption_flag=unpacked[9],
        structural_index_flag=unpacked[13],
        fields=fields,
    )


def _parse_field_descriptor(
    descriptor_data: bytes,
    encoding: str,
    config: ExportConfig,
    dbversion_byte: int,
    ordinal: int,
) -> FieldMetadata:
    raw_name = descriptor_data[:11]
    raw_type = descriptor_data[11:12]
    address = struct.unpack_from("<L", descriptor_data, 12)[0]
    length = descriptor_data[16]
    decimal_count = descriptor_data[17]
    set_fields_flag = descriptor_data[18]
    index_field_flag = descriptor_data[31]
    dbf_type = raw_type.decode("ascii")
    name = raw_name.split(b"\0", 1)[0].decode(encoding, errors=config.decode_errors)
    if dbf_type == "C":
        length |= decimal_count << 8
        decimal_count = 0

    return field_metadata(
        name=name,
        dbf_type=dbf_type,
        length=length,
        decimal_count=decimal_count,
        dbversion_byte=dbversion_byte,
        flags=set_fields_flag,
        ordinal=ordinal,
        address=address,
        index_field_flag=index_field_flag,
        descriptor_bytes=descriptor_data,
    )


def _guess_encoding(language_driver: int) -> str:
    try:
        return guess_encoding(language_driver)
    except LookupError:
        return "ascii"


def metadata_from_failed_header(
    dbf_path: Path, relative_path: Path, config: ExportConfig
) -> TableMetadata:
    raw = read_raw_header(dbf_path, config)
    encoding = config.encoding or _guess_encoding(raw.language_driver)
    return TableMetadata(
        table_name=dbf_path.stem,
        relative_path=relative_path,
        dbf_path=dbf_path,
        dbversion=get_dbversion_string(raw.dbversion_byte),
        dbversion_byte=raw.dbversion_byte,
        language_driver=raw.language_driver,
        language_driver_name=codepages.get(raw.language_driver, (None, None))[1],
        encoding=encoding,
        memo_path=None,
        memo_present=False,
        fields=raw.fields,
        source_size_bytes=dbf_path.stat().st_size,
        record_count=raw.record_count,
        header_length=raw.header_length,
        record_length=raw.record_length,
        last_update=_header_date(raw.year, raw.month, raw.day),
        incomplete_transaction=raw.incomplete_transaction,
        encryption_flag=raw.encryption_flag,
        structural_index_flag=raw.structural_index_flag,
        encoding_override=config.encoding,
        decode_errors=config.decode_errors,
        encoding_fallbacks=list(dict.fromkeys([encoding, *POLISH_FALLBACK_ENCODINGS])),
        memo_export_policy=config.memo,
        header_bytes=raw.header_bytes,
        source_sha256=sha256_file(dbf_path),
    )


def _header_date(year: int, month: int, day: int) -> str | None:
    try:
        full_year = 2000 + year if year < 80 else 1900 + year
        return date(full_year, month, day).isoformat()
    except ValueError:
        return None


def _memo_file_details(memo_path: Path | None) -> tuple[int | None, int | None, int | None]:
    if memo_path is None or not memo_path.is_file():
        return None, None, None
    size = memo_path.stat().st_size
    if memo_path.suffix.lower() != ".fpt":
        return size, None, None
    with memo_path.open("rb") as infile:
        header = infile.read(FPT_HEADER_PREFIX.size)
    if len(header) != FPT_HEADER_PREFIX.size:
        return size, None, None
    next_free_block, _reserved, block_size = FPT_HEADER_PREFIX.unpack(header)
    return size, next_free_block, block_size


def _read_prefix(path: Path | None, size: int) -> bytes | None:
    if path is None or not path.is_file():
        return None
    with path.open("rb") as infile:
        return infile.read(size)


__all__ = [
    "MissingMemoFile",
    "FieldParseError",
    "UnsupportedTableError",
    "metadata_from_failed_header",
    "open_table",
    "read_raw_header",
    "read_table_metadata",
]

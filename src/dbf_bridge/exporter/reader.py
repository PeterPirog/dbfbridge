from __future__ import annotations

import struct
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from dbfread import DBF, MissingMemoFile
from dbfread.codepages import codepages, guess_encoding
from dbfread.dbversions import get_dbversion_string
from dbfread.field_parser import FieldParser

from .models import DiscoveredTable, ExportConfig, FieldMetadata, TableMetadata
from .polish_codecs import POLISH_FALLBACK_ENCODINGS, register_polish_codecs
from .serialization import field_metadata

# Rejestrujemy polskie tabele kodowe (Mazovia/PIAST) przy imporcie modułu,
# aby były dostępne automatycznie — bez konieczności wywoływania przez
# użytkownika skryptu 05.
register_polish_codecs()

DBF_HEADER = struct.Struct("<BBBBLHHHBBLLLBBH")
FIELD_DESCRIPTOR = struct.Struct("<11scLBBHBBBB7sB")


class UnsupportedTableError(ValueError):
    """Raised when a table uses a field type that is not safe to export."""


class FieldParseError(ValueError):
    """Raised with field context when dbfread cannot parse a field."""


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
                    return text.decode(alt, errors="strict")
                except (UnicodeDecodeError, LookupError):
                    continue
            # Żadna strona kodowa nie pasuje — zwróć z replace, aby nie
            # przerywać eksportu całej tabeli.
            return text.decode(primary, errors="replace")

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
    dbversion_byte: int
    language_driver: int
    header_length: int
    record_length: int
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
    fields = [
        field_metadata(
            name=field.name,
            dbf_type=field.type,
            length=field.length,
            decimal_count=field.decimal_count,
            dbversion_byte=table.header.dbversion,
            flags=field.set_fields_flag,
        )
        for field in table.fields
    ]
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
    return TableMetadata(
        table_name=discovered.source_path.stem,
        relative_path=discovered.relative_path,
        dbf_path=discovered.source_path,
        dbversion=table.dbversion,
        dbversion_byte=table.header.dbversion,
        language_driver=table.header.language_driver,
        language_driver_name=language_driver_name,
        encoding=table.encoding,
        memo_path=Path(table.memofilename) if table.memofilename else discovered.memo_path,
        memo_present=table.memofilename is not None,
        fields=fields,
        warnings=warnings,
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
        while True:
            marker = infile.read(1)
            if marker in {b"\r", b"\n", b""}:
                break
            descriptor_data = marker + infile.read(FIELD_DESCRIPTOR.size - 1)
            if len(descriptor_data) != FIELD_DESCRIPTOR.size:
                raise ValueError(f"Field descriptor is truncated in {dbf_path.name}.")
            fields.append(
                _parse_field_descriptor(descriptor_data, encoding, config, dbversion_byte)
            )

    return RawHeader(
        dbversion_byte=dbversion_byte,
        language_driver=language_driver,
        header_length=header_length,
        record_length=record_length,
        fields=fields,
    )


def _parse_field_descriptor(
    descriptor_data: bytes,
    encoding: str,
    config: ExportConfig,
    dbversion_byte: int,
) -> FieldMetadata:
    (
        raw_name,
        raw_type,
        _address,
        length,
        decimal_count,
        _reserved1,
        _workarea_id,
        _reserved2,
        _reserved3,
        set_fields_flag,
        _reserved4,
        _index_field_flag,
    ) = FIELD_DESCRIPTOR.unpack(descriptor_data)
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
    )


__all__ = [
    "MissingMemoFile",
    "FieldParseError",
    "UnsupportedTableError",
    "metadata_from_failed_header",
    "open_table",
    "read_raw_header",
    "read_table_metadata",
]

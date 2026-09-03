from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from dbfread import DBF, MissingMemoFile
from dbfread.codepages import codepages
from dbfread.dbversions import get_dbversion_string
from dbfread.field_parser import FieldParser

from ..core import backend as core_backend
from ..core.codecs import (
    POLISH_FALLBACK_ENCODINGS,
    decode_with_polish_fallback,
)
from ..core.header import (
    fpt_header_details,
    last_update_date,
    parse_header,
)
from .models import DiscoveredTable, ExportConfig, FieldMetadata, TableMetadata
from .validation import sha256_file

# Polish OEM codecs (Mazovia/PIAST) are registered ON DEMAND at operation
# time (core.codecs._ensure_encoding_available / decode_with_polish_fallback /
# core.header.parse_header) — no module-import registration, so importing the
# exporter has no global codec-registry side effects.


class UnsupportedTableError(ValueError):
    """Raised when a table uses a field type that is not safe to export."""


class FieldParseError(ValueError):
    """Raised with field context when dbfread cannot parse a field."""


class LosslessText(str):
    """Decoded text retaining bytes when a fallback code page was required."""

    raw_bytes: bytes
    source_encoding: str | None

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
        decoded, source = decode_with_polish_fallback(text, primary, errors)
        if source is None or source != primary:
            # Fallback/replace: zatrzymaj surowe bajty i faktyczne kodowanie,
            # aby eksport mógł je odtworzyć bezstratnie.
            return LosslessText(decoded, text, source)
        return decoded

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
    encoding: str
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

    table = open_table(
        discovered.source_path,
        config,
        resolved_encoding=raw.encoding,
        # Only a table with true memo fields legitimately requires the FPT
        # companion (a VFP B double is inline data, not a memo pointer).
        require_memo_file=any(field.is_memo for field in raw.fields),
    )
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
        last_update=last_update_date(table.header.year, table.header.month, table.header.day),
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


def open_table(
    dbf_path: Path,
    config: ExportConfig,
    *,
    resolved_encoding: str | None = None,
    require_memo_file: bool = True,
) -> DBF:
    """Open a table for export (delegates to the shared core backend).

    An explicit user override (``config.encoding``) always wins; otherwise the
    encoding already resolved from the header (e.g. the Mazovia driver 0x69)
    is passed to ``dbfread`` explicitly so it does not fall back to ASCII for
    driver bytes it does not know.  The Polish fallback field parser keeps the
    historical export behaviour.

    ``require_memo_file=False`` suppresses dbfread's memo-companion check at
    table construction.  dbfread treats a ``B`` field as a memo pointer
    unconditionally, but in a Visual FoxPro table a ``B`` column is an inline
    8-byte double — a table without any real memo field must therefore open
    cleanly without an FPT companion (the core header classification already
    separates true memo fields from inline VFP doubles).
    """
    return core_backend.dbfread_backend.open_table(
        dbf_path,
        encoding=config.encoding or resolved_encoding,
        parserclass=LosslessFieldParser,
        char_decode_errors=config.decode_errors,
        ignore_missing_memofile=(
            config.missing_memo == "null-with-warning" or not require_memo_file
        ),
    )


def iter_physical_records(
    table: DBF, nullflags_layout: Any | None = None
) -> Iterator[tuple[object, bool, bytes]]:
    """Stream physical records through the shared core backend loop.

    Yields ``(record, is_deleted, raw_record)`` exactly as before — the
    physical/decoded iteration itself lives in ``dbf_bridge.core.backend``
    (one record loop in the codebase, dbfread as the reference backend).

    ``nullflags_layout`` carries the VFP ``_NullFlags`` bit layout so NULL
    values resolve to ``None`` and variable-length Varchar payloads keep
    their exact logical value (including significant trailing spaces).
    """
    for frame in core_backend.dbfread_backend.iter_physical_records(
        table,
        projection=None,
        keep_raw=True,
        use_memofile=True,
        nullflags_layout=nullflags_layout,
    ):
        yield table.recfactory(frame.items), frame.deleted, frame.raw  # type: ignore[arg-type]


def read_raw_header(dbf_path: Path, config: ExportConfig) -> RawHeader:
    parsed = parse_header(
        dbf_path,
        encoding=config.encoding,
        decode_errors=config.decode_errors,
    )
    fields = [
        FieldMetadata(
            name=field.name,
            dbf_type=field.dbf_type,
            length=field.length,
            decimal_count=field.decimal_count,
            target_representation=field.target_representation,
            is_memo=field.is_memo,
            is_binary=field.is_binary,
            supported=field.supported,
            unsupported_reason=field.unsupported_reason,
            flags=field.flags,
            ordinal=field.ordinal,
            address=field.address,
            index_field_flag=field.index_field_flag,
            descriptor_bytes=field.descriptor_bytes,
        )
        for field in parsed.fields
    ]
    return RawHeader(
        header_bytes=parsed.header_region,
        dbversion_byte=parsed.dbversion_byte,
        language_driver=parsed.language_driver,
        encoding=parsed.encoding,
        year=parsed.year,
        month=parsed.month,
        day=parsed.day,
        record_count=parsed.record_count,
        header_length=parsed.header_length,
        record_length=parsed.record_length,
        incomplete_transaction=parsed.incomplete_transaction,
        encryption_flag=parsed.encryption_flag,
        structural_index_flag=parsed.structural_index_flag,
        fields=fields,
    )


def metadata_from_failed_header(
    dbf_path: Path, relative_path: Path, config: ExportConfig
) -> TableMetadata:
    raw = read_raw_header(dbf_path, config)
    encoding = raw.encoding
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
        last_update=last_update_date(raw.year, raw.month, raw.day),
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


def _memo_file_details(memo_path: Path | None) -> tuple[int | None, int | None, int | None]:
    return fpt_header_details(memo_path)


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

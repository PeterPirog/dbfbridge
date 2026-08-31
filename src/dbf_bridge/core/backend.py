"""Backend boundary for the direct read core (DBF record streaming).

This module is the ONLY place inside ``dbf_bridge`` that talks to ``dbfread`` —
including package-private parts (``DBF._open_memofile``, the ``dbfread.memo``
submodule, ``FieldParser._parse_memo_index``).  Every other layer — the public
direct read API in :mod:`dbf_bridge.core.records` and the migration exporter —
goes through the backend capability protocols defined here.

The dbfread adapter (:class:`DbfreadBackend`) is the reference implementation.
Record reading is a single physical streaming loop shared by all consumers: it
seeks by physical record index, parses only the projected fields, and yields
the decoded field values together with the optional raw physical record image
in one pass.  There is exactly one record read loop and one header parser
(:func:`dbf_bridge.core.header.parse_header`) in the codebase; no second
VFP/FPT type parser exists.

The backend is strictly read-only: it never creates files, never touches CDX,
and converts filesystem failures into typed ``DBF_IO_ERROR``/``FPT_*`` errors
instead of leaking raw ``OSError``.
"""

from __future__ import annotations

import errno
import os
import struct
from collections.abc import Generator
from pathlib import Path
from typing import IO, Protocol

from dbfread import DBF, DBFNotFound, FieldParser, MissingMemoFile
from dbfread.memo import FakeMemoFile, MemoFile, open_memofile

from . import errors
from .header import ParsedHeader, parse_header

#: Visual FoxPro DBF version bytes (0x30 plain, 0x31 autoincrement, 0x32
#: Varchar) — they decide whether a ``B`` field is a double or a memo pointer.
VFP_DBVERSIONS = frozenset({0x30, 0x31, 0x32})


class PhysicalRecord:
    """One physical DBF frame produced by the shared streaming loop.

    ``items`` holds ``(field_name, decoded_value)`` pairs for every field the
    loop was asked to parse (a projection), in schema order.  ``raw`` is the
    exact physical record image (delete marker + field bytes) when requested,
    and ``memo_indices`` carries the ``(field, block)`` memo pointers for lazy
    memo access.  Frames whose parsing was skipped (deleted records under
    ``skip_deleted_parse``) still carry ``raw`` but have empty ``items``.
    """

    __slots__ = ("index", "deleted", "items", "raw", "memo_indices")

    def __init__(
        self,
        index: int,
        deleted: bool,
        items: list[tuple[str, object]],
        raw: bytes | None,
        memo_indices: tuple[tuple[str, int], ...] = (),
    ) -> None:
        self.index = index
        self.deleted = deleted
        self.items = items
        self.raw = raw
        self.memo_indices = memo_indices

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"PhysicalRecord(index={self.index}, deleted={self.deleted}, "
            f"fields={[name for name, _ in self.items]}, "
            f"raw={'-' if self.raw is None else len(self.raw)})"
        )


class HeaderInspectionBackend(Protocol):
    """Capability: validate and inspect one DBF header."""

    def inspect_header(
        self,
        path: str | os.PathLike[str],
        *,
        encoding: str | None = None,
        decode_errors: str = "strict",
    ) -> ParsedHeader:
        """Read, validate, and decode the header at *path* without touching
        records (delegates to the single shared header parser)."""
        ...


class RecordStreamBackend(Protocol):
    """Capability: stream physical records and their decoded values.

    Physical and decoded iteration share one implementation: each frame
    carries the decoded field values plus the optional raw record image read
    in the same pass, so no second read loop is ever needed.
    """

    def open_table(
        self,
        path: str | os.PathLike[str],
        *,
        encoding: str | None = None,
        parserclass: type[FieldParser] | None = None,
        char_decode_errors: str = "strict",
        ignore_missing_memofile: bool = True,
    ) -> object:
        """Open *path* for read-only record streaming (header only)."""
        ...

    def iter_physical_records(
        self,
        table: object,
        *,
        projection: frozenset[str] | None = None,
        memo_pointer_fields: frozenset[str] | None = None,
        keep_raw: bool = True,
        use_memofile: bool = True,
        start_index: int = 0,
        skip_deleted_parse: bool = False,
    ) -> Generator[PhysicalRecord, None, None]:
        """Yield :class:`PhysicalRecord` frames in physical order.

        The file handles live inside the generator: closing the iterator (or
        finishing, or raising) releases them on every platform, including
        Windows.
        """
        ...


class MemoPayloadBackend(Protocol):
    """Capability: read one memo payload through the reference backend."""

    def read_memo_payload(self, memo_path: Path, block: int, *, dbversion_byte: int) -> object:
        """Return the memo payload stored in *block* (bytes subclass or
        ``None`` for an empty block) with typed missing/invalid errors."""
        ...

    def decode_memo_payload(
        self, payload: object, *, encoding: str, decode_errors: str = "strict"
    ) -> object:
        """Turn a raw memo payload into its value: bytes for binary memos,
        decoded text for text memos, and ``None`` when empty."""
        ...


def is_memo_pointer_field(field_type: str, dbversion_byte: int) -> bool:
    """Whether the field's raw bytes hold a memo block pointer.

    M/G/P always point into the memo companion.  A non-Visual-FoxPro ``B``
    field is a memo pointer; VFP stores a double there instead.
    """
    if field_type in {"M", "G", "P"}:
        return True
    return field_type == "B" and dbversion_byte not in VFP_DBVERSIONS


class DirectRecordFieldParser(FieldParser):
    """Field parser of the direct record backend.

    dbfread's header check refuses tables with types it does not know.  The
    backend adds raw-bytes stubs for the VFP types ``dbfread`` cannot decode
    (Varbinary ``Q``, Blob ``W``) so tables carrying them remain streamable;
    the public record API still refuses to *decode* unsupported selected
    fields (``FIELD_TYPE_UNSUPPORTED``) before the parser is ever reached.
    """

    def parseQ(self, field, data):
        return data

    def parseW(self, field, data):
        return data


class DbfreadBackend:
    """dbfread-backed reference implementation of the backend capabilities.

    All third-party and private dbfread API usage is confined to this class
    and its module helpers.
    """

    # ------------------------------------------------------------ inspect ---

    def inspect_header(
        self,
        path: str | os.PathLike[str],
        *,
        encoding: str | None = None,
        decode_errors: str = "strict",
    ) -> ParsedHeader:
        return parse_header(path, encoding=encoding, decode_errors=decode_errors)

    # ------------------------------------------------------ record stream ---

    def open_table(
        self,
        path: str | os.PathLike[str],
        *,
        encoding: str | None = None,
        parserclass: type[FieldParser] | None = None,
        char_decode_errors: str = "strict",
        ignore_missing_memofile: bool = True,
    ) -> DBF:
        """Open *path* for read-only record streaming via dbfread."""
        dbf_path = Path(path)
        try:
            return DBF(
                dbf_path,
                load=False,
                encoding=encoding,
                parserclass=parserclass or DirectRecordFieldParser,
                char_decode_errors=char_decode_errors,
                ignore_missing_memofile=ignore_missing_memofile,
            )
        except MissingMemoFile as exc:
            raise errors.FptRequiredMissingError(
                f"Memo fields require the memo companion file '{dbf_path.stem}.fpt'.",
                path=dbf_path,
                context={"policy": "inline"},
            ) from exc
        except UnicodeDecodeError as exc:
            raise errors.TextDecodeError(
                f"The header cannot be decoded with {encoding!r}: {exc}",
                path=dbf_path,
                context={"encoding": encoding},
            ) from exc
        except DBFNotFound as exc:
            raise errors.DbfPathError(
                f"DBF source does not exist or is not a file: {dbf_path}",
                path=dbf_path,
            ) from exc
        except OSError as exc:
            if exc.errno in (errno.ENOENT, errno.ENOTDIR):
                raise errors.DbfPathError(
                    f"DBF source does not exist or is not a file: {dbf_path}",
                    path=dbf_path,
                ) from exc
            raise errors.DbfIoError(
                f"Cannot open the DBF source: {dbf_path}",
                path=dbf_path,
                context={"errno": exc.errno, "operation": "open"},
            ) from exc

    def iter_physical_records(
        self,
        table: DBF,
        *,
        projection: frozenset[str] | None = None,
        memo_pointer_fields: frozenset[str] | None = None,
        keep_raw: bool = True,
        use_memofile: bool = True,
        start_index: int = 0,
        skip_deleted_parse: bool = False,
    ) -> Generator[PhysicalRecord, None, None]:
        """Stream physical records (the single shared physical/decoded loop).

        The read is bounded by the declared record count and starts by
        seeking directly to *start_index* (records before it are not scanned
        or parsed).  The DBF and memo handles are opened lazily by this
        generator and released after the pass, on error, and on close.
        """
        path = Path(table.filename)
        with _open_input(path) as infile, _open_memofile(table, use_memofile) as memofile:
            try:
                infile.seek(table.header.headerlen + table.header.recordlen * start_index)
            except OSError as exc:
                raise errors.DbfIoError(
                    f"Cannot seek to record {start_index}: {path}",
                    path=path,
                    context={
                        "errno": exc.errno,
                        "operation": "seek",
                        "record_index": start_index,
                    },
                ) from exc
            parser = table.parserclass(table, memofile)
            parse = parser.parse
            read = infile.read
            fields = table.fields
            pointer_fields = memo_pointer_fields or frozenset()
            index = start_index
            for _ in range(max(0, table.header.numrecords - start_index)):
                marker = read(1)
                if marker not in (b" ", b"*"):
                    if marker in (b"\x1a", b""):
                        return
                    raise errors.DbfRecordInvalidError(
                        f"Unexpected DBF record marker {marker!r} at record {index} "
                        f"in {path.name}.",
                        path=path,
                        context={
                            "record_index": index,
                            "marker_hex": marker.hex() if marker else "",
                        },
                    )
                raw_fields = [read(field.length) for field in fields]
                if any(
                    len(raw) != field.length for raw, field in zip(raw_fields, fields, strict=True)
                ):
                    raise errors.DbfTruncatedError(
                        f"Truncated DBF record at record {index} in {path.name}.",
                        path=path,
                        context={"record_index": index},
                    )
                deleted = marker == b"*"
                items: list[tuple[str, object]] = []
                memo_indices: tuple[tuple[str, int], ...] = ()
                if not (skip_deleted_parse and deleted):
                    if projection is None:
                        wants = (field.name not in pointer_fields for field in fields)
                    else:
                        wants = (
                            field.name in projection and field.name not in pointer_fields
                            for field in fields
                        )
                    for field, field_raw, want in zip(fields, raw_fields, wants, strict=True):
                        if field.name in pointer_fields:
                            memo_indices = (
                                *memo_indices,
                                (field.name, parser._parse_memo_index(field_raw)),
                            )
                            continue
                        if not want:
                            continue
                        try:
                            value = parse(field, field_raw)
                        except UnicodeDecodeError as exc:
                            raise errors.TextDecodeError(
                                f"Field {field.name!r} cannot be decoded with "
                                f"{table.encoding!r}: {exc}",
                                path=path,
                                context={
                                    "field": field.name,
                                    "record_index": index,
                                    "encoding": table.encoding,
                                },
                            ) from exc
                        except OSError as exc:
                            if is_memo_pointer_field(field.type, table.header.dbversion):
                                raise errors.FptInvalidError(
                                    f"Memo payload for field {field.name!r} cannot be "
                                    f"read from {table.memofilename}: {exc}",
                                    path=Path(table.memofilename) if table.memofilename else path,
                                    context={"field": field.name, "record_index": index},
                                ) from exc
                            raise errors.DbfIoError(
                                f"Cannot read field {field.name!r} at record {index}: {path}",
                                path=path,
                                context={
                                    "errno": exc.errno,
                                    "field": field.name,
                                    "record_index": index,
                                },
                            ) from exc
                        except struct.error as exc:
                            if is_memo_pointer_field(field.type, table.header.dbversion):
                                raise errors.FptInvalidError(
                                    f"Memo payload for field {field.name!r} cannot be "
                                    f"read from {table.memofilename}: {exc}",
                                    path=Path(table.memofilename) if table.memofilename else path,
                                    context={"field": field.name, "record_index": index},
                                ) from exc
                            raise errors.DbfRecordInvalidError(
                                f"Field {field.name!r} at record {index} cannot be "
                                f"parsed: {exc}",
                                path=path,
                                context={"field": field.name, "record_index": index},
                            ) from exc
                        items.append((field.name, value))
                raw_image: bytes | None = None
                if keep_raw:
                    raw_image = marker + b"".join(raw_fields)
                yield PhysicalRecord(
                    index=index,
                    deleted=deleted,
                    items=items,
                    raw=raw_image,
                    memo_indices=memo_indices,
                )
                index += 1

    # ------------------------------------------------------------ memo -----

    def read_memo_payload(self, memo_path: Path, block: int, *, dbversion_byte: int) -> object:
        """Read one memo payload (raw bytes subclass or ``None``) via dbfread."""
        try:
            with open_memofile(os.fspath(memo_path), dbversion_byte) as memofile:
                return memofile[block]
        except FileNotFoundError as exc:
            raise errors.FptRequiredMissingError(
                f"The memo companion file disappeared: {memo_path}",
                path=memo_path,
                context={"operation": "memo_read", "block": block},
            ) from exc
        except struct.error as exc:
            raise errors.FptInvalidError(
                f"The memo companion header is invalid: {memo_path}",
                path=memo_path,
                context={"block": block},
            ) from exc
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EPERM):
                raise errors.DbfIoError(
                    f"Cannot read the memo companion: {memo_path}",
                    path=memo_path,
                    context={"errno": exc.errno, "operation": "memo_read"},
                ) from exc
            raise errors.FptInvalidError(
                f"The memo payload for block {block} cannot be read from {memo_path}: {exc}",
                path=memo_path,
                context={"block": block},
            ) from exc

    def decode_memo_payload(
        self, payload: object, *, encoding: str, decode_errors: str = "strict"
    ) -> object:
        """Turn a raw dbfread memo payload into its public value.

        Binary memos (``BinaryMemo`` bytes subclasses) stay bytes; text memos
        are decoded with the resolved encoding and the policy; an empty block
        is ``None``.  Strict decode failures raise the typed
        :class:`~dbf_bridge.core.errors.TextDecodeError`.
        """
        from dbfread.memo import BinaryMemo

        if payload is None:
            return None
        if not isinstance(payload, bytes):
            raise errors.FptInvalidError(
                f"Unexpected memo payload type {type(payload).__name__}.",
                path=None,
                context={},
            )
        if isinstance(payload, BinaryMemo):
            return payload
        try:
            return payload.decode(encoding, errors=decode_errors)
        except UnicodeDecodeError as exc:
            raise errors.TextDecodeError(
                f"Memo text cannot be decoded with {encoding!r}: {exc}",
                path=None,
                context={"encoding": encoding, "policy": decode_errors},
            ) from exc


def _open_input(path: Path) -> IO[bytes]:
    """Open *path* read-only, mapping OSError to a typed :class:`DbfIoError`."""
    try:
        return open(path, "rb")
    except OSError as exc:
        raise errors.DbfIoError(
            f"Cannot open the DBF for record streaming: {path}",
            path=path,
            context={"errno": exc.errno, "operation": "open"},
        ) from exc


def _open_memofile(table: DBF, use_memofile: bool) -> MemoFile:
    """Open the memo companion when the consumer needs parsed memo values.

    ``use_memofile=False`` returns a no-op fake never touching the FPT — the
    memo payloads stay unread (skip/null/lazy/raw paths).
    """
    try:
        if use_memofile:
            return table._open_memofile()
        return FakeMemoFile(None)
    except struct.error as exc:
        memo_path = Path(table.memofilename) if table.memofilename else Path(table.filename)
        raise errors.FptInvalidError(
            f"The memo companion header is invalid: {memo_path}",
            path=memo_path,
            context={"operation": "open"},
        ) from exc
    except OSError as exc:
        memo_path = Path(table.memofilename) if table.memofilename else Path(table.filename)
        raise errors.DbfIoError(
            f"Cannot open the memo companion: {memo_path}",
            path=memo_path,
            context={"errno": exc.errno, "operation": "open"},
        ) from exc


#: Module-level reference backend instance.
dbfread_backend = DbfreadBackend()

__all__ = [
    "DbfreadBackend",
    "HeaderInspectionBackend",
    "MemoPayloadBackend",
    "PhysicalRecord",
    "RecordStreamBackend",
    "VFP_DBVERSIONS",
    "dbfread_backend",
    "is_memo_pointer_field",
]

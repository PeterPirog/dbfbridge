"""Public Phase 1B streaming direct record API (``iter_records``/...).

This layer composes the Phase 1A header contracts with the dbfread reference
backend (:mod:`dbf_bridge.core.backend`) into a public, read-only, streaming
record access:

- :func:`iter_records` / :func:`read_records` — decoded records with an
  optional physical raw image, field projection, and memo policies;
- :func:`iter_raw_records` — every physical record (deleted included) with its
  exact raw image, without opening the memo companion;
- :class:`DirectRecord` / :class:`RecordPage` / :class:`LazyMemoValue` —
  immutable, typed, JSON-safe public models.

Streaming properties: iterators are O(1) in memory, ``read_records`` uses
O(limit) memory, ``offset`` is a physical record index resolved by a seek, and
a projection never parses unselected fields.  Every failure is a typed
:class:`~dbf_bridge.core.errors.DirectReadError` with a stable machine code.
"""

from __future__ import annotations

import base64
import os
from collections.abc import Callable, Generator, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from . import errors
from .backend import dbfread_backend, is_memo_pointer_field
from .header import (
    SUPPORTED_MEMO_FORMATS,
    ParsedHeader,
    fpt_header_details,
    memo_companion_extension,
    memo_companion_format,
    parse_header,
)

#: Supported memo read policies.
MEMO_POLICIES: tuple[str, ...] = ("lazy", "inline", "null", "skip")

#: Supported character decode error policies.
DECODE_ERROR_POLICIES: tuple[str, ...] = ("strict", "replace", "ignore")

__all__ = [
    "DECODE_ERROR_POLICIES",
    "MEMO_POLICIES",
    "DirectRecord",
    "LazyMemoValue",
    "RecordPage",
    "iter_raw_records",
    "iter_records",
    "read_records",
]


# ---------------------------------------------------------------------------
# public models
# ---------------------------------------------------------------------------


def _no_loader() -> Any:
    """Guard against a constructed-but-detached :class:`LazyMemoValue`."""
    raise errors.FptInvalidError("Memo value has no loader attached.", path=None, context={})


def _json_safe(value: Any) -> Any:
    """Convert one record value into a JSON-safe payload (no bytes, no Path)."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (bytes, bytearray)):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, LazyMemoValue):
        return value.to_dict()
    return repr(value)


@dataclass(frozen=True)
class DirectRecord:
    """One decoded DBF record from the direct read stream.

    ``physical_index`` is the zero-based physical record index (deleted
    records included in the numbering).  ``values`` maps field names to the
    decoded values in projection order; with ``raw=True`` only, ``raw_record``
    also carries the exact physical record image (delete marker + field
    bytes).  ``to_dict()`` is JSON-safe and never triggers a memo read.
    """

    physical_index: int
    deleted: bool
    values: Mapping[str, Any]
    raw_record: bytes | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe representation (no ``Path``, no raw ``bytes``)."""
        payload: dict[str, Any] = {
            "physical_index": self.physical_index,
            "deleted": self.deleted,
            "values": {name: _json_safe(value) for name, value in self.values.items()},
        }
        if self.raw_record is not None:
            payload["raw_record"] = base64.b64encode(self.raw_record).decode("ascii")
        return payload


@dataclass(frozen=True)
class RecordPage:
    """One bounded page of direct-read records (from ``read_records``).

    ``offset`` is the requested physical start index, ``records`` the decoded
    page, ``scanned`` the number of physical records consumed by the call
    (deleted skips included), and ``next_offset`` the physical index of the
    first record after the page (``None`` when exhausted).  Memory use is
    O(limit): records beyond the page are never materialized.
    """

    offset: int
    limit: int
    records: tuple[DirectRecord, ...]
    scanned: int
    next_offset: int | None
    exhausted: bool

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe representation (no ``Path``, no ``bytes``)."""
        return {
            "offset": self.offset,
            "limit": self.limit,
            "records": [record.to_dict() for record in self.records],
            "scanned": self.scanned,
            "next_offset": self.next_offset,
            "exhausted": self.exhausted,
        }


@dataclass(frozen=True)
class LazyMemoValue:
    """A reference to one memo payload that has not been read yet.

    Creating the value (and calling ``to_dict()``) performs no memo I/O:
    ``to_dict()`` describes the table, field, and physical memo block only.
    An explicit :meth:`load` (alias :meth:`read`) reads the payload through
    the dbfread reference backend and raises typed errors
    (``FPT_REQUIRED_MISSING``, ``FPT_INVALID``, ``TEXT_DECODE_ERROR``,
    ``DBF_IO_ERROR``).
    """

    dbf_path: Path
    field_name: str
    block: int
    memo_format: str | None
    _loader: Callable[[], Any] = field(default=_no_loader, compare=False, repr=False)

    def load(self) -> Any:
        """Read the memo payload explicitly (typed errors; not cached)."""
        return self._loader()

    read = load

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe pointer description — never touches the memo payload."""
        return {
            "dbf_path": self.dbf_path.as_posix(),
            "field": self.field_name,
            "block": self.block,
            "memo_format": self.memo_format,
        }


# ---------------------------------------------------------------------------
# request preparation (eager, typed validation) and streaming
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _RecordRequest:
    """Internally validated configuration of one direct record stream."""

    dbf_path: Path
    encoding: str
    decode_errors: str
    include_deleted: bool
    memo: str
    raw: bool
    record_count: int
    dbversion_byte: int
    #: Output key order (canonical schema names).
    order: tuple[str, ...]
    #: Fields the backend loop parses (always an explicit set here).
    projection: frozenset[str]
    #: Selected memo fields accessed lazily through block pointers.
    memo_pointer_fields: frozenset[str]
    #: Whether the backend loop opens the memo companion (inline only).
    use_memofile: bool
    #: Resolved memo companion path when lazy loading may need it.
    memo_path: Path | None
    #: Companion format implied by the DBF version (FPT/DBT/SMT/None).
    memo_format: str | None


def _as_dbf_path(path: str | os.PathLike[str]) -> Path:
    return Path(os.fspath(path)) if not isinstance(path, Path) else path


def _prepare(
    path: str | os.PathLike[str],
    *,
    fields: Sequence[str] | None,
    include_deleted: bool,
    memo: str,
    raw: bool,
    encoding: str,
    decode_errors: str,
) -> _RecordRequest:
    """Eagerly validate all call arguments and build the stream request."""
    dbf_path = _as_dbf_path(path)
    if memo not in MEMO_POLICIES:
        raise errors.ArgumentInvalidError(
            f"Unknown memo policy {memo!r}; expected one of {list(MEMO_POLICIES)}.",
            context={"memo": memo, "allowed": list(MEMO_POLICIES)},
        )
    if not isinstance(include_deleted, bool):
        raise errors.ArgumentInvalidError(
            "include_deleted must be a bool.",
            context={"include_deleted": include_deleted},
        )
    if not isinstance(raw, bool):
        raise errors.ArgumentInvalidError("raw must be a bool.", context={"raw": raw})
    if decode_errors not in DECODE_ERROR_POLICIES:
        raise errors.ArgumentInvalidError(
            f"Unknown decode_errors policy {decode_errors!r}; expected one of "
            f"{list(DECODE_ERROR_POLICIES)}.",
            context={"decode_errors": decode_errors, "allowed": list(DECODE_ERROR_POLICIES)},
        )
    if not isinstance(encoding, str):
        raise errors.ArgumentInvalidError(
            "encoding must be a codec name or 'auto'.", context={"encoding": encoding}
        )

    # "auto" keeps the Phase 1A language-driver resolution; an explicit
    # override always wins.
    header = parse_header(
        dbf_path,
        encoding=None if encoding == "auto" else encoding,
        decode_errors=decode_errors,
    )
    schema = [f.name for f in header.fields]
    lowered: dict[str, str] = {}
    for name in schema:
        lowered.setdefault(name.casefold(), name)

    if fields is None:
        order = tuple(schema)
        selected = frozenset(schema)
    else:
        if isinstance(fields, (str, bytes)):
            raise errors.ArgumentInvalidError(
                "fields must be a sequence of field names, not a single string.",
                context={"fields": fields},
            )
        try:
            requested = list(fields)
        except TypeError as exc:
            raise errors.ArgumentInvalidError(
                "fields must be None or an iterable of field names.",
                context={"fields_type": type(fields).__name__},
            ) from exc
        order_list: list[str] = []
        seen: set[str] = set()
        unknown: list[str] = []
        duplicates: list[str] = []
        for item in requested:
            if not isinstance(item, str):
                raise errors.ArgumentInvalidError(
                    f"Field names must be strings, got {type(item).__name__}.",
                    context={"field": item},
                )
            canonical = lowered.get(item.casefold())
            if canonical is None:
                unknown.append(item)
                continue
            if canonical.casefold() in seen:
                duplicates.append(canonical)
                continue
            seen.add(canonical.casefold())
            order_list.append(canonical)
        if unknown or duplicates:
            details = []
            if unknown:
                details.append(f"unknown fields: {unknown}")
            if duplicates:
                details.append(f"duplicate fields: {duplicates}")
            raise errors.FieldProjectionInvalidError(
                f"Invalid field projection: {'; '.join(details)}.",
                path=dbf_path,
                context={
                    "unknown": unknown,
                    "duplicates": duplicates,
                    "available": schema,
                },
            )
        order = tuple(order_list)
        selected = frozenset(order_list)

    memoish = frozenset(
        f.name for f in header.fields if is_memo_pointer_field(f.dbf_type, header.dbversion_byte)
    )
    unsupported = [f for f in header.fields if (f.name in selected) and not f.supported]
    if unsupported:
        details = [f"{f.name} ({f.dbf_type}): {f.unsupported_reason}" for f in unsupported]
        raise errors.FieldTypeUnsupportedError(
            "Selected field type(s) are not supported for decoding: " + "; ".join(details),
            path=dbf_path,
            context={"unsupported": details},
        )

    selected_memoish = selected & memoish
    memo_format = memo_companion_format(header.dbversion_byte)
    memo_path: Path | None = None
    pointer_fields: frozenset[str] = frozenset()
    use_memofile = False
    if memo == "inline":
        memo_path = _require_memo_companion(dbf_path, header, memo_format)
        use_memofile = bool(selected_memoish)
    elif memo == "lazy" and selected_memoish:
        # Only a typed companion lookup (no memo payload is opened or read);
        # a missing companion surfaces on lazy load, not here.
        memo_path = _discover_memo_path(dbf_path, header)
        pointer_fields = selected_memoish

    parse_set = selected - memoish if memo in {"skip", "lazy"} or not selected_memoish else selected

    return _RecordRequest(
        dbf_path=dbf_path,
        encoding=header.encoding,
        decode_errors=decode_errors,
        include_deleted=include_deleted,
        memo=memo,
        raw=raw,
        record_count=header.record_count,
        dbversion_byte=header.dbversion_byte,
        order=order,
        projection=parse_set,
        memo_pointer_fields=pointer_fields,
        use_memofile=use_memofile,
        memo_path=memo_path,
        memo_format=memo_format,
    )


def _discover_memo_path(dbf_path: Path, header: ParsedHeader) -> Path | None:
    """Case-insensitive companion discovery without opening the FPT."""
    from .inspect import _find_companions

    ext = memo_companion_extension(header.dbversion_byte)
    if ext is None or memo_companion_format(header.dbversion_byte) not in SUPPORTED_MEMO_FORMATS:
        return None
    found = _find_companions(dbf_path.parent, dbf_path.stem, (ext,))
    return found.get(ext)


def _require_memo_companion(dbf_path: Path, header: ParsedHeader, memo_format: str | None) -> Path:
    """Eagerly require a present, readable FPT companion for memo="inline"."""
    ext = memo_companion_extension(header.dbversion_byte)
    if ext is None:
        raise errors.FptRequiredMissingError(
            "Memo fields require a memo companion but DBF version "
            f"0x{header.dbversion_byte:02x} declares none.",
            path=dbf_path,
            context={"dbversion_byte": header.dbversion_byte},
        )
    if memo_format not in SUPPORTED_MEMO_FORMATS:
        raise errors.DbfFormatUnsupportedError(
            f"Memo companion format {memo_format} is not supported for reading "
            "in Direct Read; only FPT (VFP/FoxPro) is supported.",
            path=dbf_path,
            context={"memo_format": memo_format},
        )
    from .inspect import _find_companions

    found = _find_companions(dbf_path.parent, dbf_path.stem, (ext,))
    memo_path = found.get(ext)
    if memo_path is None:
        raise errors.FptRequiredMissingError(
            f"Memo fields require the memo companion file "
            f"'{dbf_path.stem}{ext}' that was not found.",
            path=dbf_path,
            context={"memo_format": memo_format, "expected_extension": ext},
        )
    size, _next_free, block_size = fpt_header_details(memo_path)
    if size is not None and size < 8:
        raise errors.FptInvalidError(
            f"The memo companion '{memo_path.name}' is only {size} bytes long; "
            "its header cannot be read.",
            path=memo_path,
            context={"size_bytes": size},
        )
    if block_size == 0:
        raise errors.FptInvalidError(
            f"The memo companion '{memo_path.name}' declares an invalid block size 0.",
            path=memo_path,
            context={"block_size": 0},
        )
    return memo_path


def _lazy_loader(request: _RecordRequest, field: str, block: int) -> Callable[[], Any]:
    """Build the explicit loader for one lazy memo value."""

    def _load() -> Any:
        if request.memo_format is None:
            raise errors.FptRequiredMissingError(
                "Memo fields require a memo companion but the DBF version declares none.",
                path=request.dbf_path,
                context={"field": field, "policy": "lazy"},
            )
        if request.memo_format not in SUPPORTED_MEMO_FORMATS:
            raise errors.DbfFormatUnsupportedError(
                f"Memo companion format {request.memo_format} is not supported "
                "for reading in Direct Read; only FPT (VFP/FoxPro) is supported.",
                path=request.dbf_path,
                context={"memo_format": request.memo_format, "field": field},
            )
        if request.memo_path is None:
            raise errors.FptRequiredMissingError(
                f"Memo value for field {field!r} requires the memo companion "
                f"'{request.dbf_path.stem}.fpt' that was not found.",
                path=request.dbf_path,
                context={"field": field, "policy": "lazy"},
            )
        payload = dbfread_backend.read_memo_payload(
            request.memo_path, block, dbversion_byte=request.dbversion_byte
        )
        return dbfread_backend.decode_memo_payload(
            payload, encoding=request.encoding, decode_errors=request.decode_errors
        )

    return _load


def _build_values(request: _RecordRequest, frame: object) -> dict[str, Any]:
    """Assemble ``values`` in projection order for one physical frame."""
    frame_record = frame
    items = frame_record.items  # type: ignore[attr-defined]
    parsed = dict(items)
    pointers = dict(frame_record.memo_indices)  # type: ignore[attr-defined]
    values: dict[str, Any] = {}
    for name in request.order:
        if name in parsed:
            values[name] = parsed[name]
        elif name in pointers:
            block = pointers[name]
            if block <= 0:
                # An empty memo block: the inline path also resolves to None.
                values[name] = None
            else:
                values[name] = LazyMemoValue(
                    dbf_path=request.dbf_path,
                    field_name=name,
                    block=block,
                    memo_format=request.memo_format,
                    _loader=_lazy_loader(request, name, block),
                )
        # memo == "skip": the field intentionally stays absent from values.
    return values


def _stream_records(
    request: _RecordRequest, *, start_index: int = 0, track: dict[str, Any]
) -> Generator[DirectRecord, None, None]:
    """Yield decoded records from the backend stream (handles live in the
    generator; they close on exhaustion, error, close(), and GC)."""
    table = dbfread_backend.open_table(
        request.dbf_path,
        encoding=request.encoding,
        char_decode_errors=request.decode_errors,
        ignore_missing_memofile=True,
    )
    frames = dbfread_backend.iter_physical_records(
        table,
        projection=request.projection,
        memo_pointer_fields=request.memo_pointer_fields or None,
        keep_raw=request.raw,
        use_memofile=request.use_memofile,
        start_index=start_index,
        skip_deleted_parse=not request.include_deleted,
    )
    ended = False
    try:
        for frame in frames:
            track["scanned"] += 1
            track["last_index"] = frame.index
            if frame.deleted and not request.include_deleted:
                continue
            yield DirectRecord(
                physical_index=frame.index,
                deleted=frame.deleted,
                values=_build_values(request, frame),
                raw_record=frame.raw,
            )
        ended = True
    finally:
        frames.close()
        track["ended"] = ended


# ---------------------------------------------------------------------------
# public entry points
# ---------------------------------------------------------------------------


def iter_records(
    path: str | os.PathLike[str],
    *,
    fields: Sequence[str] | None = None,
    include_deleted: bool = False,
    memo: str = "lazy",
    raw: bool = False,
    encoding: str = "auto",
    decode_errors: str = "strict",
) -> Iterator[DirectRecord]:
    """Stream decoded records of one DBF table (O(1) memory, read-only).

    - ``fields`` — projection: validated case-insensitively; ``values`` uses
      schema names in the given order; unselected fields are never parsed;
      unknown/duplicate names raise ``FIELD_PROJECTION_INVALID``; a selected
      unsupported field raises ``FIELD_TYPE_UNSUPPORTED``;
    - ``include_deleted=False`` skips deleted records in the same pass;
    - ``memo`` — ``skip`` (absent from values), ``null`` (``None``), ``lazy``
      (:class:`LazyMemoValue`, FPT not opened during iteration) or ``inline``
      (payload read through the dbfread backend);
    - ``raw=False`` stores no raw bytes; ``raw=True`` keeps the exact physical
      record in ``raw_record``;
    - ``encoding="auto"`` resolves from the language driver, an explicit
      override wins; strict decode failures raise ``TEXT_DECODE_ERROR``.

    Argument/header/companion validation runs eagerly; iterate the returned
    generator to stream.  Closing the iterator releases all file handles.
    """
    request = _prepare(
        path,
        fields=fields,
        include_deleted=include_deleted,
        memo=memo,
        raw=raw,
        encoding=encoding,
        decode_errors=decode_errors,
    )
    return _stream_records(request, track={"scanned": 0, "ended": False})


def read_records(
    path: str | os.PathLike[str],
    *,
    offset: int = 0,
    limit: int = 100,
    fields: Sequence[str] | None = None,
    include_deleted: bool = False,
    memo: str = "lazy",
    raw: bool = False,
    encoding: str = "auto",
    decode_errors: str = "strict",
) -> RecordPage:
    """Read one bounded physical page of records (O(limit) memory).

    ``offset`` is a zero-based physical record index (seeked, not scanned);
    ``limit`` must be positive and ``offset`` non-negative
    (``ARGUMENT_INVALID`` otherwise).  ``next_offset`` is the physical index
    of the first record after the page (``None`` when exhausted); ``scanned``
    counts the physical records consumed including skipped deleted ones.
    """
    if isinstance(offset, bool) or not isinstance(offset, int):
        raise errors.ArgumentInvalidError("offset must be an int.", context={"offset": offset})
    if offset < 0:
        raise errors.ArgumentInvalidError(
            "offset must be a non-negative physical record index.",
            context={"offset": offset},
        )
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise errors.ArgumentInvalidError("limit must be an int.", context={"limit": limit})
    if limit <= 0:
        raise errors.ArgumentInvalidError(
            "limit must be a positive number of records.",
            context={"limit": limit},
        )

    request = _prepare(
        path,
        fields=fields,
        include_deleted=include_deleted,
        memo=memo,
        raw=raw,
        encoding=encoding,
        decode_errors=decode_errors,
    )
    track: dict[str, Any] = {"scanned": 0, "ended": False}
    stream = _stream_records(request, start_index=offset, track=track)
    records: list[DirectRecord] = []
    try:
        for record in stream:
            records.append(record)
            if len(records) >= limit:
                break
    finally:
        stream.close()

    if len(records) < limit:
        exhausted = True
        next_offset: int | None = None
    else:
        candidate = records[-1].physical_index + 1
        exhausted = bool(track["ended"]) or candidate >= request.record_count
        next_offset = None if exhausted else candidate
    return RecordPage(
        offset=offset,
        limit=limit,
        records=tuple(records),
        scanned=int(track["scanned"]),
        next_offset=next_offset,
        exhausted=exhausted,
    )


def iter_raw_records(path: str | os.PathLike[str]) -> Iterator[DirectRecord]:
    """Stream every physical record (deleted included) with its raw image.

    Records come in physical zero-based order; the memo companion is never
    opened (memo fields are not decoded — their content lives in the raw
    physical image).  Text fields are decoded with the language-driver
    encoding; strict failures raise ``TEXT_DECODE_ERROR``.
    """
    dbf_path = _as_dbf_path(path)
    header = parse_header(dbf_path)
    memoish = {
        f.name for f in header.fields if is_memo_pointer_field(f.dbf_type, header.dbversion_byte)
    }
    parse_names = tuple(f.name for f in header.fields if f.supported and f.name not in memoish)
    request = _RecordRequest(
        dbf_path=dbf_path,
        encoding=header.encoding,
        decode_errors="strict",
        include_deleted=True,
        memo="skip",
        raw=True,
        record_count=header.record_count,
        dbversion_byte=header.dbversion_byte,
        order=parse_names,
        projection=frozenset(parse_names),
        memo_pointer_fields=frozenset(),
        use_memofile=False,
        memo_path=None,
        memo_format=None,
    )
    return _stream_records(request, track={"scanned": 0, "ended": False})

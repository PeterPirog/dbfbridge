"""Public Phase 1B streaming direct record API (``iter_records``/...).

This layer composes the Phase 1A header contracts with the dbfread reference
backend (:mod:`dbf_bridge.core.backend`) into a public, read-only, streaming
record access:

- :func:`iter_records` / :func:`read_records` — decoded records with an
  optional physical raw image, field projection, and memo policies;
- :func:`iter_raw_records` — a pure physical (forensic) stream: no field is
  parsed or decoded, the FPT is never opened, and ``values`` is an empty
  read-only mapping — damaged text bytes can never hide the raw record image;
  decoded values alongside the raw image are available through
  ``iter_records(..., raw=True)``;
- :class:`DirectRecord` / :class:`RecordPage` / :class:`LazyMemoValue` —
  immutable, typed, JSON-safe public models (``values`` is a defensive,
  read-only mapping in projection order).

Streaming properties: iterators are O(1) in memory, ``read_records`` uses
O(limit) memory, ``offset`` is a physical record index resolved by a seek, and
a projection never parses unselected fields.  Every failure is a typed
:class:`~dbf_bridge.core.errors.DirectReadError` with a stable machine code.
"""

from __future__ import annotations

import base64
import os
import types
from collections.abc import Callable, Generator, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..progress import CancellationCheck, ProgressCallback, ProgressEvent
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
from .nullflags import NullFlagsLayout, build_nullflags_layout

#: Supported memo read policies.
MEMO_POLICIES: tuple[str, ...] = ("lazy", "inline", "null", "skip")

#: Supported character decode error policies.
DECODE_ERROR_POLICIES: tuple[str, ...] = ("strict", "replace", "ignore")

#: Physical records processed per progress event (bounded internal cadence;
#: not a public knob — responsiveness is owned by the cancellation boundary,
#: which is checked at every physical record).
_PROGRESS_EVERY = 1000


def _new_track() -> dict[str, Any]:
    """One consistent live-counter mapping for every Direct Read stream."""
    return {"scanned": 0, "yielded": 0, "last_index": None, "ended": False}


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
    decoded values in projection order; the constructor takes a **defensive
    copy** wrapped in a read-only mapping, so later mutation of the caller's
    dict never leaks into the record and item assignment is rejected.  With
    ``raw=True`` only, ``raw_record`` also carries the exact physical record
    image (delete marker + field bytes).  ``to_dict()`` is JSON-safe, never
    triggers a memo read, and returns a fresh, independently mutable dict.
    """

    physical_index: int
    deleted: bool
    values: Mapping[str, Any]
    raw_record: bytes | None = None

    def __post_init__(self) -> None:
        # Defensive read-only snapshot: a private copy (projection order kept)
        # wrapped in a MappingProxyType, so neither the caller's dict nor any
        # field of this model can mutate the record afterwards.
        object.__setattr__(self, "values", _frozen_values_mapping(self.values))

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


def _frozen_values_mapping(values: Mapping[str, Any]) -> Mapping[str, Any]:
    """Snapshot *values* into an immutable, order-preserving mapping."""
    try:
        snapshot = dict(values)
    except TypeError as exc:
        raise TypeError("values must be a mapping of field names to values") from exc
    return types.MappingProxyType(snapshot)


@dataclass(frozen=True)
class RecordPage:
    """One bounded page of direct-read records (from ``read_records``).

    ``offset`` is the requested physical start index, ``records`` the decoded
    page (always a tuple), ``scanned`` the number of physical records consumed
    by the call (deleted skips included), and ``next_offset`` the physical
    index of the first record after the page (``None`` when exhausted).
    Memory use is O(limit): records beyond the page are never materialized.
    """

    offset: int
    limit: int
    records: tuple[DirectRecord, ...]
    scanned: int
    next_offset: int | None
    exhausted: bool

    def __post_init__(self) -> None:
        # Defensive snapshot: the page is an immutable tuple, whatever
        # sequence the caller passed in.
        object.__setattr__(self, "records", tuple(self.records))

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
    #: Selected memo fields that render as plain ``None`` (memo="null").
    null_memo_fields: frozenset[str]
    #: Whether the backend loop opens the memo companion (real inline only).
    use_memofile: bool
    #: Whether the memo companion must exist at open time (real inline).
    strict_memo_open: bool
    #: Resolved memo companion path when lazy loading may need it.
    memo_path: Path | None
    #: Companion format implied by the DBF version (FPT/DBT/SMT/None).
    memo_format: str | None
    #: VFP ``_NullFlags`` bit layout (computed once per request; ``None``
    #: when the table needs no bitmap).  Applied by the single physical loop.
    nullflags_layout: NullFlagsLayout | None = None


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
    # The effective projection is what really gets decoded: memo fields
    # removed by "skip" are excluded BEFORE the supported-type validation,
    # so a skipped unsupported memo field (e.g. a VFP Blob "W") never blocks
    # the read.  Every other selected field keeps the strict validation.
    effective = selected - memoish if memo == "skip" else selected
    unsupported = [f for f in header.fields if (f.name in effective) and not f.supported]
    if unsupported:
        details = [f"{f.name} ({f.dbf_type}): {f.unsupported_reason}" for f in unsupported]
        raise errors.FieldTypeUnsupportedError(
            "Selected field type(s) are not supported for decoding: " + "; ".join(details),
            path=dbf_path,
            context={"unsupported": details},
        )

    effective_memoish = effective & memoish
    memo_format = memo_companion_format(header.dbversion_byte)
    memo_path: Path | None = None
    pointer_fields: frozenset[str] = frozenset()
    use_memofile = False
    strict_memo_open = False
    if memo == "inline":
        if effective_memoish:
            # Only an effective projection that really decodes memo values
            # requires and opens the FPT; decoding only non-memo fields works
            # with a missing companion as well.
            memo_path = _require_memo_companion(dbf_path, header, memo_format)
            use_memofile = True
            strict_memo_open = True
    elif memo == "lazy" and effective_memoish:
        # Only a typed companion lookup (no memo payload is opened or read);
        # a missing companion surfaces on lazy load, not here.
        memo_path = _discover_memo_path(dbf_path, header)
        pointer_fields = effective_memoish

    if memo in {"skip", "lazy", "null"} or not effective_memoish:
        parse_set = selected - memoish
    else:  # real inline with memo fields: parse through the opened memo file
        parse_set = selected

    # VFP _NullFlags bitmap layout: computed once per request from the
    # canonical header (O(1) per record downstream; no extra source pass).
    # Raises a typed DbfHeaderInvalidError for structurally inconsistent
    # tables (flags needed but the bitmap column missing/too short).
    nullflags_layout = build_nullflags_layout(header.fields)

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
        null_memo_fields=effective_memoish if memo == "null" else frozenset(),
        use_memofile=use_memofile,
        strict_memo_open=strict_memo_open,
        memo_path=memo_path,
        memo_format=memo_format,
        nullflags_layout=nullflags_layout,
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


def _build_values(request: _RecordRequest, frame: object) -> Mapping[str, Any]:
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
        elif name in request.null_memo_fields:
            # memo="null": the field stays present with a None value and the
            # FPT payload is never read.
            values[name] = None
        # memo == "skip": the field intentionally stays absent from values.
    return values


def _read_cancelled(
    request: _RecordRequest, track: dict[str, Any], start_index: int
) -> errors.ReadCancelledError:
    """Build the typed, JSON-safe cancellation error from the live counters."""
    last = track.get("last_index")
    next_physical = start_index if last is None else int(last) + 1
    return errors.ReadCancelledError(
        "The Direct Read was cancelled by the caller before the next physical record.",
        path=request.dbf_path,
        context={
            "offset": start_index,
            "next_physical_index": next_physical,
            "scanned": int(track["scanned"]),
            "yielded": int(track["yielded"]),
            "record_count": request.record_count,
        },
    )


def _emit_read_progress(
    progress: ProgressCallback,
    request: _RecordRequest,
    track: dict[str, Any],
    *,
    current: int,
    message: str | None = None,
) -> None:
    """Emit one ``operation="read"`` progress event (never swallows errors)."""
    progress(
        ProgressEvent(
            operation="read",
            current=current,
            total=request.record_count,
            table=os.fspath(request.dbf_path),
            records=int(track["yielded"]),
            message=message,
        )
    )


def _stream_records(
    request: _RecordRequest,
    *,
    start_index: int = 0,
    track: dict[str, Any],
    progress: ProgressCallback | None = None,
    cancel_check: CancellationCheck | None = None,
    emit_final: bool = True,
) -> Generator[DirectRecord, None, None]:
    """Yield decoded records from the backend stream (handles live in the
    generator; they close on exhaustion, error, close(), and GC).

    ``track`` carries the live counters (``scanned``, ``yielded``,
    ``last_index``, ``ended``).  When both callbacks are ``None`` the loop is
    the historical fast path with zero per-record control overhead;
    otherwise the cooperative boundary checks cancellation **before** the
    next physical record is read/decoded and emits bounded-cadence progress.

    The final ``"completed"`` event on natural exhaustion is owned HERE
    unless ``emit_final=False`` (the internal pagination wrapper then owns
    exactly one ``"page completed"`` event instead, so a call never produces
    two final events).

    Cancellation probes follow an exact one-probe-per-boundary contract: the
    first probe runs before the physical stream starts (before the first
    prospective record), and after every consumed frame exactly one probe
    runs before the following prospective record is attempted — so consuming
    N frames costs N+1 probes, never N+2.
    """
    ended = False
    if cancel_check is not None and cancel_check():
        # The single boundary probe for the FIRST prospective physical
        # record: zero frames are consumed, nothing is decoded or yielded,
        # and the backend physical-record loop is never entered.  (Eager
        # argument/header/companion validation already ran in _prepare and
        # may have briefly read header/companion metadata — unchanged
        # contract.)
        raise _read_cancelled(request, track, start_index)
    table = dbfread_backend.open_table(
        request.dbf_path,
        encoding=request.encoding,
        char_decode_errors=request.decode_errors,
        # Real memo="inline" opens the companion strictly: a companion that
        # vanishes between the eager request validation and the first next()
        # raises FPT_REQUIRED_MISSING instead of silently reading nulls.
        ignore_missing_memofile=not request.strict_memo_open,
    )
    frames = dbfread_backend.iter_physical_records(
        table,
        projection=request.projection,
        memo_pointer_fields=request.memo_pointer_fields or None,
        keep_raw=request.raw,
        use_memofile=request.use_memofile,
        start_index=start_index,
        skip_deleted_parse=not request.include_deleted,
        nullflags_layout=request.nullflags_layout,
    )
    try:
        if progress is None and cancel_check is None:
            # Fast path — identical to the historical no-callback loop
            # (no yielded bookkeeping: the default path needs none).
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
        else:
            while True:
                try:
                    frame = next(frames)
                except StopIteration:
                    ended = True
                    break
                track["scanned"] += 1
                track["last_index"] = frame.index
                if progress is not None and track["scanned"] % _PROGRESS_EVERY == 0:
                    _emit_read_progress(progress, request, track, current=frame.index + 1)
                if frame.deleted and not request.include_deleted:
                    pass  # consumed, but not yielded
                else:
                    track["yielded"] += 1
                    yield DirectRecord(
                        physical_index=frame.index,
                        deleted=frame.deleted,
                        values=_build_values(request, frame),
                        raw_record=frame.raw,
                    )
                # Exactly ONE cooperative probe per boundary: before the next
                # prospective physical record is read/decoded.  Only a True
                # return cancels; an exception propagates unchanged.
                if cancel_check is not None and cancel_check():
                    raise _read_cancelled(request, track, start_index)
            if progress is not None and emit_final:
                # Final event for a normally completed call.
                current = (
                    int(track["last_index"]) + 1 if track["last_index"] is not None else start_index
                )
                _emit_read_progress(progress, request, track, current=current, message="completed")
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
    progress: ProgressCallback | None = None,
    cancel_check: CancellationCheck | None = None,
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
      override wins; strict decode failures raise ``TEXT_DECODE_ERROR``;
    - ``progress`` — optional callback receiving
      :class:`~dbf_bridge.progress.ProgressEvent` notifications
      (``operation="read"``) at a bounded internal cadence (every 1000
      scanned physical records) plus one final event on normal completion;
      ``current`` is the absolute physical position after the last processed
      record, ``records`` counts records yielded by this call;
    - ``cancel_check`` — cooperative cancellation: called at **every physical
      record boundary before the next record is read**; returning ``True``
      stops the read with :class:`ReadCancelledError` (records already
      yielded remain valid, all handles close, nothing is written).  An
      exception raised by the callable propagates unchanged.

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
    return _stream_records(
        request,
        track=_new_track(),
        progress=progress,
        cancel_check=cancel_check,
    )


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
    progress: ProgressCallback | None = None,
    cancel_check: CancellationCheck | None = None,
) -> RecordPage:
    """Read one bounded physical page of records (O(limit) memory).

    ``offset`` is a zero-based physical record index (seeked, not scanned);
    ``limit`` must be positive and ``offset`` non-negative
    (``ARGUMENT_INVALID`` otherwise).  ``next_offset`` is the physical index
    of the first record after the page (``None`` when exhausted); ``scanned``
    counts the physical records consumed including skipped deleted ones.

    ``progress`` receives ``operation="read"`` events at the internal cadence
    plus one final event when the page completes normally (``current`` stays
    a physical position: ``offset`` is a physical index, never an active
    record number).  ``cancel_check`` returning ``True`` raises
    :class:`ReadCancelledError` — never a partially filled page pretending to
    be a completed one; the progress accumulated so far is available in the
    error context.
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
    track: dict[str, Any] = _new_track()
    stream = _stream_records(
        request,
        start_index=offset,
        track=track,
        progress=progress,
        cancel_check=cancel_check,
        # The page wrapper owns exactly one final event ("page completed")
        # regardless of whether the limit or EOF ends the page — the stream's
        # own "completed" event is suppressed to avoid a duplicate final.
        emit_final=False,
    )
    records: list[DirectRecord] = []
    try:
        for record in stream:
            records.append(record)
            if len(records) >= limit:
                break
    finally:
        stream.close()

    if progress is not None:
        # Final event for a normally completed page (cancellation and callback
        # exceptions propagate above and never produce one).  `current` is the
        # PHYSICAL position after the last processed physical record — derived
        # from the scanned track, never from the returned records (skipped
        # deleted records after the last yielded record still advance it).
        if track["last_index"] is not None:
            current = int(track["last_index"]) + 1
        else:
            # Nothing was consumed: clamp to the physical table bounds.
            current = min(offset, request.record_count)
        _emit_read_progress(
            progress,
            request,
            track,
            current=current,
            message="page completed",
        )

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


def iter_raw_records(
    path: str | os.PathLike[str],
    *,
    progress: ProgressCallback | None = None,
    cancel_check: CancellationCheck | None = None,
) -> Iterator[DirectRecord]:
    """Stream every physical record as a pure forensic (raw) snapshot.

    This is clean physical streaming: **no field is parsed or decoded** (the
    FPT is never opened, and even damaged text bytes cannot hide the record
    image), while record-area truncation (`DBF_TRUNCATED`) and invalid
    record markers (`DBF_RECORD_INVALID`) are still detected.  Each record
    carries its zero-based ``physical_index``, the ``deleted`` flag, the
    exact ``raw_record`` bytes (delete marker + field bytes) and an **empty
    read-only** ``values`` mapping.  Decoded values together with the raw
    image are available through :func:`iter_records` with ``raw=True``.

    ``progress``/``cancel_check`` follow the shared Direct Read contract:
    cancellation is checked cooperatively before the next physical record
    (``READ_CANCELLED``); the forensic semantics of every record returned
    before the cancellation are unchanged.
    """
    dbf_path = _as_dbf_path(path)
    header = parse_header(dbf_path)
    request = _RecordRequest(
        dbf_path=dbf_path,
        encoding=header.encoding,
        decode_errors="strict",
        include_deleted=True,
        memo="skip",
        raw=True,
        record_count=header.record_count,
        dbversion_byte=header.dbversion_byte,
        order=(),
        projection=frozenset(),
        memo_pointer_fields=frozenset(),
        null_memo_fields=frozenset(),
        use_memofile=False,
        strict_memo_open=False,
        memo_path=None,
        memo_format=None,
    )
    return _stream_records(
        request,
        track=_new_track(),
        progress=progress,
        cancel_check=cancel_check,
    )

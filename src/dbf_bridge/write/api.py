"""Internal Direct Write entry point (v1.1 INTERNAL contract — Phase C).

``dbf_bridge.write.write_table`` is the internal user-facing function of the
shared physical writer.  It is deliberately NOT exported from the root public
facades yet — Phase D owns that promotion (DBFB-WRITE-001 is out of scope
here).

Design:

- it delegates to the SAME physical backend as the reconstruction pipeline
  (``dbf_bridge.write.backend.write_dbf`` — DBFB-WRITE-002/003);
- ``records`` is consumed EXACTLY once, streamed; flat tables stay
  O(1)/O(batch) memory, and the Varchar/``_NullFlags`` second logical pass is
  fed from the private bounded :mod:`~dbf_bridge.write.spool` instead of
  materializing the caller's input (DBFB-STREAM-001..004);
- ``overwrite`` defaults to **False** and reuses the stable
  ``OUTPUT_EXISTS`` machine code (``OperationOutputExistsError``) before any
  publication (DBFB-WAPI-002);
- ``progress`` receives the canonical :class:`~dbf_bridge.progress.
  ProgressEvent` model (``operation="write"``) — no second progress system
  (DBFB-WAPI-003);
- ``cancel_check`` is honoured at record boundaries and immediately before
  final publication; cancellation publishes nothing and cleans staging
  (DBFB-WAPI-004 / DBFB-ERR-006);
- failures are classified from the backend's structured ``ErrorCode``
  (never from English message text) and surface as the separate
  ``DirectWriteError`` family, JSON-safe, without record or memo values
  (DBFB-ERR-002..005).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..common import sha256_file
from ..core.errors import (
    DirectWriteError,
    ErrorCode,
    OperationArgumentError,
    OperationOutputExistsError,
    WriteCancelledError,
    WriteMemoFailedError,
    WritePublicationFailedError,
    WriteSchemaInvalidError,
    WriteValueInvalidError,
)
from ..core.models import TableSchema
from ..optional_deps import require_optional
from ..progress import ProgressEvent
from .spool import RecordSpool

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Callable, Iterable, Mapping

    from ..core.records import DirectRecord
    from ..progress import CancellationCheck, ProgressCallback

__all__ = ["WriteResult", "write_table"]

_OPERATION = "write_table"


@dataclass(frozen=True)
class WriteResult:
    """Immutable, JSON-safe publication summary of one ``write_table`` call.

    Counter names follow ONE convention (``records_written`` /
    ``deleted_records``); only sizes, paths, digests and warnings are
    reported — never record or memo payloads (DBFB-RESULT-001..004).
    """

    destination: Path
    fpt_path: Path | None
    fpt_published: bool
    records_written: int
    deleted_records: int
    structural_cdx: bool
    index_rebuild_required: bool
    dbc_bound: bool
    dbf_sha256: str
    fpt_sha256: str | None
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "destination": self.destination.as_posix(),
            "fpt_path": self.fpt_path.as_posix() if self.fpt_path is not None else None,
            "fpt_published": self.fpt_published,
            "records_written": self.records_written,
            "deleted_records": self.deleted_records,
            "structural_cdx": self.structural_cdx,
            "index_rebuild_required": self.index_rebuild_required,
            "dbc_bound": self.dbc_bound,
            "dbf_sha256": self.dbf_sha256,
            "fpt_sha256": self.fpt_sha256,
            "warnings": list(self.warnings),
        }


_WRITE_ERROR_TYPES: dict[ErrorCode, type] = {}


def _write_error_types() -> dict[ErrorCode, type]:
    """The stable structured-code -> typed-error mapping (one place)."""
    if not _WRITE_ERROR_TYPES:
        from ..core.errors import (
            DestinationIoError,
            WriteFieldUnsupportedError,
        )

        _WRITE_ERROR_TYPES.update(
            {
                ErrorCode.WRITE_VALUE_INVALID: WriteValueInvalidError,
                ErrorCode.WRITE_FIELD_UNSUPPORTED: WriteFieldUnsupportedError,
                ErrorCode.WRITE_MEMO_FAILED: WriteMemoFailedError,
                ErrorCode.WRITE_SCHEMA_INVALID: WriteSchemaInvalidError,
                ErrorCode.WRITE_PUBLICATION_FAILED: WritePublicationFailedError,
                # Backend argument failures (staging volume mismatch) reuse the
                # stable ARGUMENT_INVALID code via the neutral operation
                # boundary error, not a write-family class.
                ErrorCode.ARGUMENT_INVALID: OperationArgumentError,
                ErrorCode.DESTINATION_IO_ERROR: DestinationIoError,
            }
        )
    return _WRITE_ERROR_TYPES


def _typed_from_backend(exc: Any, destination_path: Path) -> Exception:
    """Map a backend failure to the typed write error via its machine code.

    The English message is NEVER parsed for classification (DBFB-ERR-004).
    For the two codes whose internal message may quote the offending record
    value (``WRITE_VALUE_INVALID`` / ``WRITE_MEMO_FAILED``) the public
    message is rebuilt from the structured context (field NAME + DBF type
    only); the backend's own text stays available as ``__cause__``.
    """
    code = getattr(exc, "code", None)
    context = getattr(exc, "context", None) or {}
    if code in (ErrorCode.WRITE_VALUE_INVALID, ErrorCode.WRITE_MEMO_FAILED):
        field = context.get("field")
        dbf_type = context.get("dbf_type")
        subject = f" for field {field!r}" if field else ""
        kind = f" (DBF type {dbf_type!r})" if dbf_type else ""
        message = (
            f"The record value{subject} cannot be written{kind}."
            if code == ErrorCode.WRITE_VALUE_INVALID
            else f"The memo payload{subject} could not be written{kind}."
        )
    else:
        message = exc.message
    cls = _write_error_types().get(code) if code is not None else None
    if cls is None:
        cls = WritePublicationFailedError
    public_context = {"operation": "write", **{str(k): v for k, v in context.items()}}
    if cls is OperationArgumentError:
        # The neutral operation-boundary classes take operation/path/table
        # kwargs; OperationArgumentError carries no path parameter.
        return cls(message, operation="write", context=public_context)
    return cls(message, path=destination_path, context=public_context)


def write_table(
    destination: Any,
    *,
    schema: TableSchema,
    records: Iterable[DirectRecord | Mapping[str, Any]],
    overwrite: bool = False,
    staging_directory: Any = None,
    progress: ProgressCallback | None = None,
    cancel_check: CancellationCheck | None = None,
) -> WriteResult:
    """Write a lazily consumed record stream as a fresh DBF/FPT pair.

    INTERNAL contract (``dbf_bridge.write.write_table``) — not part of the
    stable 1.x public surface.  See :mod:`dbf_bridge.write` for the full
    behavioural contract.
    """
    from . import backend as backend_module
    from .records import RecordAdapter
    from .schema_adapter import schema_to_mapping, validate_schema_for_write
    from .spool import RecordSpool

    destination_path = Path(destination)
    # Fail-before-output optional dependency gate (DBFB-DEP-002): before any
    # directory, staging, spool, DBF or FPT artifact exists.
    require_optional("dbf", extra="write", operation=_OPERATION, purpose="Direct DBF/FPT write")
    if not isinstance(schema, TableSchema):
        raise WriteSchemaInvalidError(
            "schema must be the public TableSchema produced by read_schema().",
            path=destination_path,
            context={"schema_type": type(schema).__name__},
        )
    validate_schema_for_write(schema)
    backend_schema = schema_to_mapping(schema)

    final_fpt = backend_module.memo_output_path(destination_path, backend_schema)
    if not overwrite and (destination_path.exists() or final_fpt.exists()):
        raise OperationOutputExistsError(
            "The output already exists; pass overwrite=True to replace it.",
            operation=_OPERATION,
            path=destination_path,
            context={"fpt_exists": final_fpt.exists()},
        )

    staging_root = Path(staging_directory) if staging_directory is not None else None
    if staging_root is not None and staging_root.exists() and not staging_root.is_dir():
        raise OperationArgumentError(
            "staging_directory must be a directory.",
            operation=_OPERATION,
            context={"staging_directory": staging_root.as_posix()},
        )

    adapter = RecordAdapter(backend_schema)
    needs_second_pass = any(field.dbf_type == "0" for field in schema.fields)
    spool: RecordSpool | None = None
    spool_dir = staging_root if staging_root is not None else destination_path.parent
    if needs_second_pass:
        spool_dir.mkdir(parents=True, exist_ok=True)
        spool = RecordSpool(spool_dir / f".{destination_path.stem}.direct-write.spool")

    state = {"count": 0}

    def _input_stream() -> Any:
        # Cancellation is checked BEFORE each record is pulled from the
        # caller's iterable (record-boundary semantics mirror Direct Read:
        # DBFB-WAPI-004); the iterable itself is consumed exactly once.
        iterator = iter(records)
        while True:
            if cancel_check is not None and cancel_check():
                raise WriteCancelledError(
                    "The write was cancelled by the caller before the next record.",
                    path=destination_path,
                    context={"records_mapped": state["count"]},
                )
            try:
                record = next(iterator)
            except StopIteration:
                return
            mapped = adapter.map(record)
            state["count"] += 1
            yield mapped
            if progress is not None and (state["count"] == 1 or state["count"] % 10_000 == 0):
                _emit(progress, destination_path, state["count"])

    def _before_publish() -> None:
        if cancel_check is not None and cancel_check():
            raise WriteCancelledError(
                "The write was cancelled by the caller before final publication.",
                path=destination_path,
                context={"records_mapped": state["count"]},
            )

    try:
        if spool is not None:
            stream = _spooling_stream(spool, _input_stream())
            records_factory: Callable[[], Any] | None = spool.replay
        else:
            stream = _input_stream()
            records_factory = None
        checksum, backend_warnings = backend_module.write_dbf(
            destination_path,
            stream,
            backend_schema,
            overwrite=overwrite,
            records_factory=records_factory,
            staging_directory=staging_root,
            before_publish=_before_publish,
        )
    except WriteCancelledError:
        raise
    except DirectWriteError:
        raise
    except Exception as exc:  # noqa: BLE001 - single typed classification boundary
        raise _classify_backend_failure(exc, destination_path) from exc
    finally:
        if spool is not None:
            spool.discard()

    warnings = list(backend_warnings)
    if schema.has_structural_cdx:
        warnings.append(
            "The schema references a structural CDX index; the DBF/FPT pair was written "
            "without the companion .cdx — index tags must be rebuilt externally before "
            "use."
        )
    if schema.dbc_bound:
        warnings.append(
            "The source table was bound to a database container; the written table is "
            "standalone — DBC binding, triggers, rules and stored procedures are not "
            "restored."
        )
    fpt_published = bool(final_fpt.is_file())
    if progress is not None:
        _emit(progress, destination_path, checksum.record_count, final=True)
    return WriteResult(
        destination=destination_path,
        fpt_path=final_fpt if fpt_published else None,
        fpt_published=fpt_published,
        records_written=checksum.record_count,
        deleted_records=checksum.deleted_records,
        structural_cdx=schema.has_structural_cdx,
        index_rebuild_required=schema.has_structural_cdx,
        dbc_bound=schema.dbc_bound,
        dbf_sha256=sha256_file(destination_path),
        fpt_sha256=sha256_file(final_fpt) if fpt_published else None,
        warnings=tuple(warnings),
    )


def _spooling_stream(spool: RecordSpool, stream: Any) -> Any:
    for mapped in stream:
        spool.append(mapped)
        yield mapped


def _emit(
    progress: ProgressCallback,
    destination_path: Path,
    current: int,
    *,
    final: bool = False,
) -> None:
    progress(
        ProgressEvent(
            operation="write",
            current=current,
            total=current if final else 0,
            table=destination_path.as_posix(),
            message=None if final else "writing records",
        )
    )


def _classify_backend_failure(exc: Exception, destination_path: Path) -> Exception:
    """One structured classification boundary for every backend failure."""
    from .backend import ReconstructionError

    if isinstance(exc, ReconstructionError):
        return _typed_from_backend(exc, destination_path)
    if isinstance(exc, FileExistsError):
        return OperationOutputExistsError(
            str(exc), operation=_OPERATION, path=destination_path
        )
    if isinstance(exc, OSError):
        from ..core.errors import DestinationIoError

        return DestinationIoError(
            "Cannot publish the DBF/FPT artifacts.",
            path=destination_path,
            context={"errno": exc.errno, "operation": "publication"},
        )
    return WritePublicationFailedError(
        f"The DBF/FPT write failed unexpectedly: {type(exc).__name__}",
        path=destination_path,
        context={"operation": "write"},
    )


class DirectRecordOrMapping:  # pragma: no cover - documentation type only
    """Typing alias placeholder — ``DirectRecord | Mapping[str, Any]]``."""

    def __init__(self) -> None:
        raise NotImplementedError("DirectRecordOrMapping is a typing-only marker")

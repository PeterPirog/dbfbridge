"""Typed direct-write public API (RESEARCH — next version) — ``write_table``.

Adapts the public ``TableSchema`` model and the direct read record stream to
the shared physical writer backend (:mod:`dbf_bridge.write.backend` — the
single DBF/FPT writing implementation, shared with the reconstruction
pipeline, which is the correctness authority).

Backend failures are classified at this boundary from the STRUCTURED machine
code the backend attaches to its error family — never from the English
message text.  The classification is therefore machine-readable end-to-end:

- ``ReconstructionError.code`` (a stable ``ErrorCode`` member) -> the typed
  ``DirectWriteError`` subclass with the same code;
- a pre-existing destination with ``overwrite=False`` -> the existing stable
  ``OperationOutputExistsError`` (code ``OUTPUT_EXISTS`` — reused, not
  re-invented);
- filesystem failures around staging/publication -> ``DestinationIoError``;
- any unexpected backend failure -> ``WritePublicationFailedError`` (the
  original failure stays available as ``__cause__``).

The ``dbf`` backend is imported lazily inside the shared writer only when the
write API actually moves bytes: ``import dbfbridge`` stays lazy and
side-effect-free.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core.errors import ErrorCode, OperationOutputExistsError, WriteSchemaInvalidError
from ..core.models import TableSchema
from ..optional_deps import OptionalDependencyMissingError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .backend import ReconstructionError

__all__ = ["WriteResult", "write_table"]

_WRITE_ERROR_TYPES: dict[ErrorCode, type] = {}


def _write_error_types() -> dict[ErrorCode, type]:
    """The stable code -> typed-error mapping (resolved lazily, one place)."""
    if not _WRITE_ERROR_TYPES:
        from ..core.errors import (
            WriteFieldUnsupportedError,
            WriteMemoFailedError,
            WritePublicationFailedError,
            WriteValueInvalidError,
        )

        _WRITE_ERROR_TYPES.update(
            {
                ErrorCode.WRITE_VALUE_INVALID: WriteValueInvalidError,
                ErrorCode.WRITE_FIELD_UNSUPPORTED: WriteFieldUnsupportedError,
                ErrorCode.WRITE_MEMO_FAILED: WriteMemoFailedError,
                ErrorCode.WRITE_SCHEMA_INVALID: WriteSchemaInvalidError,
                ErrorCode.WRITE_PUBLICATION_FAILED: WritePublicationFailedError,
            }
        )
    return _WRITE_ERROR_TYPES


def _typed_from_backend(exc: ReconstructionError, destination_path: Path) -> Exception:
    """Map a backend failure to the typed public error via its machine code.

    The backend carries a structured ``ErrorCode`` on every raise site; the
    English message is NEVER parsed for classification.  For the two codes
    whose internal message may quote the offending record value
    (``WRITE_VALUE_INVALID`` / ``WRITE_MEMO_FAILED``) the public message is
    rebuilt from the structured context (field NAME + DBF type only — never
    a record or memo value); the backend's own text stays available as the
    ``__cause__`` for maintainers.
    """
    code = getattr(exc, "code", None)
    if code in (ErrorCode.WRITE_VALUE_INVALID, ErrorCode.WRITE_MEMO_FAILED):
        context = getattr(exc, "context", {}) or {}
        field = context.get("field")
        dbf_type = context.get("dbf_type")
        subject = f" for field {field!r}" if field else ""
        kind = f" (DBF type {dbf_type!r})" if dbf_type else ""
        if code == ErrorCode.WRITE_VALUE_INVALID:
            message = f"The record value{subject} cannot be converted{kind}."
        else:
            message = f"The memo payload{subject} could not be written{kind}."
    else:
        message = exc.message
    cls = _write_error_types().get(code) if code is not None else None
    if cls is None:
        from ..core.errors import WritePublicationFailedError

        cls = WritePublicationFailedError
    backend_context = getattr(exc, "context", None) or {}
    public_context = {"operation": "write", **{str(k): v for k, v in backend_context.items()}}
    return cls(message, path=destination_path, context=public_context)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as infile:
        for chunk in iter(lambda: infile.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class WriteResult:
    """Immutable, JSON-safe publication summary of one ``write_table`` call.

    Field names follow ONE convention (``records_written`` /
    ``deleted_records`` — aligned with the reconstruction result naming; the
    unreleased prototype's draft spellings ``record_count`` /
    ``deleted_record_count`` are retired).  Only sizes, paths and SHA-256
    digests are reported — never record or memo payloads.
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


def write_table(
    destination: Any,
    *,
    schema: TableSchema,
    records: Any,
    overwrite: bool = True,
    staging_directory: Any = None,
    progress_callback: Any = None,
) -> WriteResult:
    """Write a lazily consumed record stream as a fresh DBF/FPT pair.

    RESEARCH API — not part of the stable 1.x contract.  ``records`` is
    consumed lazily (exactly once) unless the schema carries a VFP
    ``_NullFlags`` system column, whose canonical layout repair needs the
    logical values a second time; in that case the stream is materialized
    once internally (still consumed exactly once from the caller's side).
    """
    from .backend import ReconstructionError
    from .records import record_mapping as record_mapping
    from .schema_adapter import schema_to_mapping

    destination_path = Path(destination)
    if not isinstance(schema, TableSchema):
        raise WriteSchemaInvalidError(
            "schema must be the public TableSchema produced by read_schema(), got "
            f"{type(schema).__name__}",
            path=destination_path,
            context={"schema_type": type(schema).__name__},
        )
    if not schema.fields:
        raise WriteSchemaInvalidError(
            "The schema has no fields; nothing can be written.",
            path=destination_path,
            context={"field_count": 0},
        )

    backend_schema = schema_to_mapping(schema)

    # Varchar/_NullFlags tables need the canonical layout repair, which
    # re-streams the logical values; materialize once (honest O(N) exception,
    # documented in docs/architecture/direct-write-next.md).  Flat tables
    # stay fully streaming (O(1) memory, single pass).
    needs_layout_repair = any(field.dbf_type == "0" for field in schema.fields)
    if needs_layout_repair:
        materialized = [record_mapping(record, backend_schema) for record in records]
        records_iter: Any = iter(materialized)
        records_factory: Any = lambda: iter(materialized)  # noqa: E731
    else:
        records_iter = (record_mapping(record, backend_schema) for record in records)
        records_factory = None

    try:
        from . import backend as _backend

        checksum, warnings = _backend.write_dbf(
            destination_path,
            records_iter,
            backend_schema,
            overwrite=overwrite,
            records_factory=records_factory,
            progress_callback=progress_callback,
            staging_directory=Path(staging_directory) if staging_directory else None,
        )
        fpt_path = _backend.memo_output_path(destination_path, backend_schema)
    except ReconstructionError as exc:
        raise _typed_from_backend(exc, destination_path) from exc
    except FileExistsError as exc:
        raise OperationOutputExistsError(
            str(exc), operation="write_table", path=destination_path
        ) from exc
    except OptionalDependencyMissingError:
        # already fully typed and machine-readable (OPTIONAL_DEPENDENCY_MISSING)
        raise
    except OSError as exc:
        from ..core.errors import DestinationIoError

        raise DestinationIoError(
            f"Cannot publish the DBF/FPT artifacts: {exc}",
            path=destination_path,
            context={"errno": exc.errno, "operation": "publication"},
        ) from exc
    except Exception as exc:
        from ..core.errors import WritePublicationFailedError

        raise WritePublicationFailedError(
            f"The DBF/FPT write failed unexpectedly: {type(exc).__name__}",
            path=destination_path,
            context={"operation": "write"},
        ) from exc

    fpt_published = bool(fpt_path.is_file())
    return WriteResult(
        destination=destination_path,
        fpt_path=fpt_path if fpt_published else None,
        fpt_published=fpt_published,
        records_written=checksum.record_count,
        deleted_records=checksum.deleted_records,
        structural_cdx=schema.has_structural_cdx,
        index_rebuild_required=schema.has_structural_cdx,
        dbc_bound=schema.dbc_bound,
        dbf_sha256=_sha256_file(destination_path),
        fpt_sha256=_sha256_file(fpt_path) if fpt_published else None,
        warnings=tuple(warnings),
    )

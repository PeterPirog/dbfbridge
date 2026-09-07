"""Deterministic ``TableSchema`` -> shared-writer-backend adaptation.

One typed :class:`~dbf_bridge.core.models.TableSchema` (as produced by
``read_schema`` or assembled by the integrator) maps to exactly one backend
schema mapping — the reconstruction writer contract (``fields``/``dbf``/
``memo``/``text_encoding``).  Deliberately omitted: any raw Base64 header or
descriptor blobs (DBFB-SCHEMA-004) — Direct Write rebuilds the header and
field descriptors from the typed metadata and claims no raw byte identity
with any source (DBFB-WRITE-005).

``docs/compatibility-vfp.md`` stays the authority for supported types
(DBFB-SCHEMA-005): this adapter never "promotes" a type the compatibility
matrix marks unsupported merely because the physical backend could store it.
"""

from __future__ import annotations

from typing import Any

from ..core.errors import (
    DbfHeaderInvalidError,
    WriteFieldUnsupportedError,
    WriteSchemaInvalidError,
)
from ..core.models import FieldInfo, TableSchema
from ..core.nullflags import build_nullflags_layout

__all__ = ["validate_schema_for_write", "schema_to_mapping"]

#: Physical types the compatibility matrix marks writable.  ``Q`` (Varbinary)
#: and ``W`` (Blob) are typed refusals by design even though the physical
#: backend could store bytes for them.
_WRITE_UNSUPPORTED_TYPES = frozenset({"Q", "W"})

_NUMERIC_LENGTH_TYPES = frozenset({"N", "F"})


def validate_schema_for_write(schema: TableSchema) -> None:
    """Validate *schema* for Direct Write BEFORE any output is created.

    Raises the typed write-family errors (``WRITE_SCHEMA_INVALID`` /
    ``WRITE_FIELD_UNSUPPORTED``) with field-name/type context only.
    """
    if not schema.fields:
        raise WriteSchemaInvalidError(
            "The schema has no fields; nothing can be written.",
            context={"field_count": 0},
        )
    for field in schema.fields:
        _validate_field(field)
    # The canonical ``_NullFlags`` allocation must be derivable: Varchar/NULL
    # tables need exactly one trustworthy type-``0`` system column
    # (DBFB-SCHEMA-003 / DBFB-REC-006).
    try:
        build_nullflags_layout(schema.fields)
    except DbfHeaderInvalidError as exc:
        raise WriteSchemaInvalidError(
            "The schema's NULL/Varchar structure is not canonical: " + exc.message,
            context=exc.context,
        ) from exc
    if schema.memo_block_size is not None and schema.memo_block_size < 1:
        raise WriteSchemaInvalidError(
            "The memo block size must be a positive number of bytes.",
            context={"memo_block_size": schema.memo_block_size},
        )


def _validate_field(field: FieldInfo) -> None:
    if not field.supported:
        raise WriteFieldUnsupportedError(
            "Field is not supported for Direct Write according to the VFP "
            "compatibility matrix: " + (field.unsupported_reason or "unsupported"),
            context={"field": field.name, "dbf_type": field.dbf_type},
        )
    if field.dbf_type in _WRITE_UNSUPPORTED_TYPES:
        raise WriteFieldUnsupportedError(
            "Field type is not writable (typed refusal by design).",
            context={"field": field.name, "dbf_type": field.dbf_type},
        )
    if field.dbf_type == "V" and field.nocptrans:
        raise WriteFieldUnsupportedError(
            "Binary (NOCPTRANS) Varchar is not supported for Direct Write; "
            "text Varchar is.",
            context={"field": field.name, "dbf_type": "V"},
        )
    if field.dbf_type == "C" and field.nocptrans:
        raise WriteFieldUnsupportedError(
            "Binary (NOCPTRANS) Character is not supported for Direct Write.",
            context={"field": field.name, "dbf_type": "C"},
        )
    length = field.length
    if length < 1 or length > 255:
        raise WriteSchemaInvalidError(
            "Field length must be between 1 and 255 bytes.",
            context={"field": field.name, "dbf_type": field.dbf_type, "length": length},
        )
    if field.dbf_type in _NUMERIC_LENGTH_TYPES:
        if length > 20:
            raise WriteSchemaInvalidError(
                "Numeric/Float field length must be at most 20 (VFP limit).",
                context={"field": field.name, "length": length},
            )
        decimals = field.decimal_count
        if decimals < 0 or decimals > 15 or decimals > length - 1:
            raise WriteSchemaInvalidError(
                "Numeric/Float decimal count is inconsistent with the field length.",
                context={
                    "field": field.name,
                    "length": length,
                    "decimal_count": decimals,
                },
            )


def schema_to_mapping(schema: TableSchema) -> dict[str, Any]:
    """Build the writer-backend schema mapping from a public ``TableSchema``.

    The mapping mirrors the reconstruction writer contract (``fields``/``dbf``/
    ``memo``/``text_encoding``).  The memo companion name follows the
    DESTINATION (backend default ``<destination-stem>.fpt``): reusing the
    source companion name would publish the new FPT under the source table's
    name.  Dataset-level renaming stays a higher-layer decision.
    """
    fields: list[dict[str, Any]] = []
    for field in schema.fields:
        fields.append(
            {
                "ordinal": field.ordinal,
                "name": field.name,
                "dbf_type": field.dbf_type,
                "length": field.length,
                "decimal_count": field.decimal_count,
                "address": field.address,
                "flags": field.flags,
                "is_memo": field.is_memo,
                "is_binary": field.is_binary,
                "dbversion_byte": field.dbversion_byte,
            }
        )

    memo_block = schema.memo_block_size or 64
    # The generated table's layout is the shared writer's own: the ``dbf``
    # library pads VFP headers and normalizes memo-pointer widths, so SOURCE
    # header/record lengths are not expectations of the generated file —
    # Direct Write claims no source byte identity (DBFB-WRITE-005).  The ONE
    # meaningful expectation is the record length of a ``_NullFlags`` table:
    # the canonical Varchar/NULL bitmap repair validates the staged record
    # layout against it (deletion flag + field widths + bitmap bytes).
    dbf_info: dict[str, Any] = {
        "version_byte": schema.dbversion_byte,
        "language_driver": schema.language_driver,
        "last_update": schema.last_update,
        "structural_index_flag": 1 if schema.has_structural_cdx else 0,
    }
    if schema.record_length > 0 and build_nullflags_layout(schema.fields) is not None:
        dbf_info["record_length_bytes"] = schema.record_length
    return {
        "fields": fields,
        "dbf": dbf_info,
        "memo": {
            "block_size_bytes": int(memo_block),
            "path": None,
            "required": schema.has_memo,
        },
        "text_encoding": {
            "declared_or_detected_encoding": schema.encoding,
            "fallback_order": [],
        },
    }

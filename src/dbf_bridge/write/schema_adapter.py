"""Adaptation of the public ``TableSchema`` model to the shared writer backend.

The integrator never converts :class:`TableSchema` into the legacy export
schema mapping — :func:`schema_to_mapping` performs the typed-to-backend
adaptation in one deterministic place.
"""

from __future__ import annotations

from typing import Any

from ..core.models import TableSchema

__all__ = ["schema_to_mapping"]


def schema_to_mapping(schema: TableSchema) -> dict[str, Any]:
    """Build the writer-backend schema mapping from a public ``TableSchema``.

    The mapping mirrors the reconstruction writer contract (fields/dbf/memo/
    text_encoding).  Deliberately omitted: any raw/Base64 header or descriptor
    artifacts — the direct-write path rebuilds header and descriptors from the
    typed metadata, so raw byte identity with the source is not claimed.
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
    # The memo companion name follows the DESTINATION (backend default
    # ``<destination-stem>.fpt``): reusing the source companion name would
    # publish the new FPT under the source table's name and clobber/miss the
    # companion.  Dataset-level renaming stays a higher-layer decision.
    return {
        "fields": fields,
        "dbf": {
            "version_byte": schema.dbversion_byte,
            "language_driver": schema.language_driver,
            "last_update": schema.last_update,
            "structural_index_flag": 1 if schema.has_structural_cdx else 0,
            "header_length_bytes": schema.header_length,
            "record_length_bytes": schema.record_length,
        },
        "memo": {
            "block_size_bytes": memo_block_size(memo_block),
            "path": None,
            "required": schema.has_memo,
        },
        "text_encoding": {
            "declared_or_detected_encoding": schema.encoding,
            "fallback_order": [],
        },
    }


def memo_block_size(value: int) -> int:
    """The recorded memo block size (validated positive by the backend)."""
    return int(value)

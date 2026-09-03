"""VFP ``_NullFlags`` bitmap contract (pure, no I/O).

Visual FoxPro appends a hidden system column of physical type ``0``
(``_NullFlags``) to every table that needs NULL or variable-length metadata.
The column stores one bitmap per record; its meaning — verified against the
VFP physical format and cross-checked against the reference ``dbf`` writer's
own NULL round trips (bit set = NULL, bits allocated in field order):

- every ``V`` (Varchar) and ``Q`` (Varbinary) field consumes a **varlength**
  bit: when the bit is set, the last byte of that field's physical payload is
  the actual value length and the value is the preceding bytes; when the bit
  is clear, the logical value is the full declared field width;
- if a ``V``/``Q`` field is NULLable, the **next** allocated bit is its NULL
  bit (bit set = value is NULL);
- every other NULLable field consumes one bit (bit set = NULL);
- non-nullable, non-``V``/``Q`` fields consume no bits;
- bits are allocated in field (descriptor) order; this matches the layout the
  reference writer produces for NULLable tables without ``V``/``Q`` columns;
- bitmap bytes beyond the allocated bit count are template residue and are
  never interpreted;
- a table that needs flags (``V``/``Q`` or any NULLable field) but carries no
  type-``0`` system column is structurally inconsistent (typed
  ``DBF_HEADER_INVALID``), as is a ``_NullFlags`` column too short for the
  allocated bits.

This module owns the layout derivation for the whole codebase: the single
physical record loop (``core.backend``) and the exporter both consume
:class:`NullFlagsLayout` instead of re-implementing bit arithmetic.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from . import errors

#: Physical types whose per-record varlength bit decides the value length.
VARLENGTH_FIELD_TYPES = frozenset({"V", "Q"})

#: Physical type byte of the hidden system bitmap column.
NULLFLAGS_TYPE = "0"

_NULLABLE_FLAG = 0x02
_SYSTEM_FLAG = 0x01


def _descriptor(field: Any) -> tuple[str, str, int, int]:
    """Normalize one field descriptor to ``(name, dbf_type, flags, length)``.

    Accepts attribute-based descriptors (core ``ParsedField``, exporter
    ``FieldMetadata``) and Mapping-based descriptors (importer schema
    dicts), so every consumer shares ONE allocation engine instead of
    reimplementing bit arithmetic.
    """
    if isinstance(field, Mapping):
        name = str(field["name"])
        dbf_type = str(field["dbf_type"])
        flags = int(field.get("flags") or 0)
        length = field.get("length")
        return name, dbf_type, flags, int(length) if length is not None else 0
    return str(field.name), str(field.dbf_type), int(field.flags), int(field.length)


@dataclass(frozen=True)
class NullFlagsLayout:
    """Bit allocation of one table's ``_NullFlags`` bitmap.

    ``field_name`` is the bitmap column's name; the two mappings assign each
    interesting field its bit index.  Pure data — every lookup is a dict
    access, no I/O, safe to compute once per request (O(1) per record).
    """

    field_name: str
    byte_count: int
    varlength_bits: dict[str, int]
    null_bits: dict[str, int]

    @staticmethod
    def bit_is_set(bitmap: bytes | bytearray | None, bit: int) -> bool:
        """Whether one bitmap bit is set (out-of-range bits read as clear)."""
        if bitmap is None:
            return False
        byte, offset = divmod(bit, 8)
        if byte >= len(bitmap):
            return False
        return bool((bitmap[byte] >> offset) & 1)


def build_nullflags_layout(fields: Iterable[Any]) -> NullFlagsLayout | None:
    """Derive the ``_NullFlags`` bit layout from canonical field descriptors.

    *fields* are objects exposing ``name``, ``dbf_type``, and ``flags``
    (core ``ParsedField`` or exporter ``FieldMetadata``).  The iterable is
    materialized once, so one-shot generators are accepted.  Returns ``None``
    when the table needs no bitmap (no ``V``/``Q`` and no NULLable field).

    When a bitmap is needed, the table must carry exactly ONE type-``0``
    candidate and that candidate must carry the VFP system flag (bit 0x01) —
    anything else is a structurally untrustworthy control column.

    Raises:
        DbfHeaderInvalidError: when the table needs a bitmap (``V``/``Q``
            field or any NULLable field) but carries no type-``0`` system
            column, carries more than one, carries one without the system
            flag, or when the declared bitmap column is too short for the
            allocated bits.
    """
    field_list = list(fields)
    descriptors = [_descriptor(field) for field in field_list]
    bitmap_candidates = [field for field in field_list if _descriptor(field)[1] == NULLFLAGS_TYPE]
    varlength_bits: dict[str, int] = {}
    null_bits: dict[str, int] = {}
    bit = 0
    for name, dbf_type, flags, _length in descriptors:
        nullable = bool(flags & _NULLABLE_FLAG)
        if dbf_type == NULLFLAGS_TYPE:
            continue
        if dbf_type in VARLENGTH_FIELD_TYPES:
            varlength_bits[name] = bit
            bit += 1
            if nullable:
                null_bits[name] = bit
                bit += 1
        elif nullable:
            null_bits[name] = bit
            bit += 1
    if bit == 0:
        return None
    # A table that needs NULL/varlength metadata must carry exactly one
    # physically trustworthy bitmap column: a single type-0 field flagged as
    # a VFP system field.
    if len(bitmap_candidates) != 1:
        raise errors.DbfHeaderInvalidError(
            "The table carries Varchar/Varbinary or NULLable fields but has "
            f"{len(bitmap_candidates)} bitmap columns of physical type '0' "
            "(exactly one is required).",
            path=None,
            context={"bitmap_columns": len(bitmap_candidates), "allocated_bits": bit},
        )
    bitmap_name, _bitmap_type, bitmap_flags, declared_length = _descriptor(bitmap_candidates[0])
    if not bitmap_flags & _SYSTEM_FLAG:
        raise errors.DbfHeaderInvalidError(
            f"The bitmap column {bitmap_name!r} (physical type 0) "
            "lacks the VFP system flag (0x01); it is not trustworthy as the "
            "_NullFlags control structure.",
            path=None,
            context={"field": bitmap_name},
        )
    byte_count = (bit + 7) // 8
    if declared_length is not None and declared_length < byte_count:
        raise nullflags_capacity_error(declared_length, byte_count)
    return NullFlagsLayout(
        field_name=bitmap_name,
        byte_count=byte_count,
        varlength_bits=varlength_bits,
        null_bits=null_bits,
    )


def null_field_names(layout: NullFlagsLayout, bitmap: bytes | bytearray | None) -> set[str]:
    """The set of field names whose NULL bit is set in one record's bitmap.

    Pure interpretation helper: ``layout.null_bits`` + the bitmap bytes are
    the only inputs, so consumers (importer checksums, diagnostics) never
    reimplement ``divmod(bit, 8)`` against a parallel allocation model.
    """
    if layout is None or not layout.null_bits:
        return set()
    return {
        name for name, bit in layout.null_bits.items() if NullFlagsLayout.bit_is_set(bitmap, bit)
    }


def nullflags_capacity_error(
    declared_length: int, required_bytes: int
) -> errors.DbfHeaderInvalidError:
    """Typed error for a bitmap column shorter than the allocated bits."""
    return errors.DbfHeaderInvalidError(
        "The _NullFlags system column is too short for the allocated "
        f"varlength/NULL bits ({required_bytes} byte(s) required, "
        f"{declared_length} declared).",
        path=None,
        context={
            "declared_length": declared_length,
            "required_bytes": required_bytes,
        },
    )


__all__ = [
    "NULLFLAGS_TYPE",
    "NullFlagsLayout",
    "VARLENGTH_FIELD_TYPES",
    "build_nullflags_layout",
    "null_field_names",
    "nullflags_capacity_error",
]

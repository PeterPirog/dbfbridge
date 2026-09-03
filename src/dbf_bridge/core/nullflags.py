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

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from . import errors

#: Physical types whose per-record varlength bit decides the value length.
VARLENGTH_FIELD_TYPES = frozenset({"V", "Q"})

#: Physical type byte of the hidden system bitmap column.
NULLFLAGS_TYPE = "0"

_NULLABLE_FLAG = 0x02


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
    (core ``ParsedField`` or exporter ``FieldMetadata``).  Returns ``None``
    when the table needs no bitmap (no ``V``/``Q`` and no NULLable field).

    Raises:
        DbfHeaderInvalidError: when the table needs a bitmap (``V``/``Q``
            field or any NULLable field) but carries no type-``0`` system
            column, or when the declared bitmap column is too short for the
            allocated bits.
    """
    descriptors = [
        (field.name, field.dbf_type, bool(field.flags & _NULLABLE_FLAG)) for field in fields
    ]
    bitmap_name = next(
        (name for name, dbf_type, _nullable in descriptors if dbf_type == NULLFLAGS_TYPE),
        None,
    )
    varlength_bits: dict[str, int] = {}
    null_bits: dict[str, int] = {}
    bit = 0
    for name, dbf_type, nullable in descriptors:
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
    if bitmap_name is None:
        raise errors.DbfHeaderInvalidError(
            "The table carries Varchar/Varbinary or NULLable fields but has no "
            "_NullFlags system column (physical type 0) to hold their bits.",
            path=None,
            context={"allocated_bits": bit},
        )
    byte_count = (bit + 7) // 8
    declared_length = next(
        (
            field.length
            for field in fields
            if field.name == bitmap_name and field.dbf_type == NULLFLAGS_TYPE
        ),
        None,
    )
    if declared_length is not None and declared_length < byte_count:
        raise nullflags_capacity_error(declared_length, byte_count)
    return NullFlagsLayout(
        field_name=bitmap_name,
        byte_count=byte_count,
        varlength_bits=varlength_bits,
        null_bits=null_bits,
    )


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
    "nullflags_capacity_error",
]

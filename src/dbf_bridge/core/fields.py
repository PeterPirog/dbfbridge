"""Shared DBF field classification used by the exporter and the direct read core.

The classification is pure: it maps a physical field descriptor (type, length,
decimal count, flags, DBF version) to export semantics (memo/binary nature,
supported state, target representation) without touching any file.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Human-readable names for the DBF/VFP single-letter field types.
FIELD_TYPE_NAMES: dict[str, str] = {
    "0": "Null flags",
    "+": "Autoincrement",
    "@": "Timestamp",
    "B": "Double or binary memo",
    "C": "Character",
    "D": "Date",
    "F": "Float",
    "G": "General/OLE memo",
    "I": "Integer",
    "L": "Logical",
    "M": "Memo",
    "N": "Numeric",
    "O": "Double",
    "P": "Picture memo",
    "Q": "Varbinary",
    "T": "DateTime",
    "V": "Varchar",
    "W": "Blob",
    "Y": "Currency",
}

#: Field flags in the descriptor's ``set_fields_flag`` byte.
SYSTEM_FIELD_FLAG = 0x01
NULLABLE_FIELD_FLAG = 0x02
BINARY_FIELD_FLAG = 0x04

#: Visual FoxPro autoincrementing field-flags mask (both bits 0x04 | 0x08).
#: In VFP it marks an Integer (``I``) field whose next value/step live in
#: descriptor bytes 19-22 (little-endian) and 23.
VFP_AUTOINCREMENT_FIELD_MASK = 0x0C

#: Field types where the 0x04 descriptor bit means binary/NOCPTRANS.  VFP
#: documents it for Character and Memo only; bit 0x04 is also part of the
#: autoincrement mask 0x0C and must not classify Integer autoincrement
#: fields (or other numeric columns) as NOCPTRANS-binary.
NOCPTRANS_FIELD_TYPES = frozenset({"C", "V", "M", "G", "P", "W"})

#: VFP field types that dbfbridge does not export safely.
UNSUPPORTED_FIELD_TYPES = frozenset({"Q", "W"})

#: DBF version bytes where type ``B`` (length 8) is a double, not a binary memo.
VFP_DOUBLE_VERSIONS = frozenset({0x30, 0x31, 0x32})

#: Visual FoxPro DBF version bytes (0x30 plain, 0x31 autoincrement, 0x32 Varchar).
VFP_VERSIONS = frozenset({0x30, 0x31, 0x32})


def is_nocptrans_field(dbf_type: str, flags: int) -> bool:
    """Whether the descriptor's bit 0x04 means binary/NOCPTRANS.

    VFP documents bit 0x04 as a binary column flag "for CHAR and MEMO only";
    for other field types (notably autoincrement Integer, whose mask 0x0C
    contains bit 0x04, and nullable+binary 0x06 columns) it carries no
    NOCPTRANS semantics.
    """
    return bool(flags & BINARY_FIELD_FLAG) and dbf_type in NOCPTRANS_FIELD_TYPES


def is_autoincrement_field(*, dbf_type: str, flags: int, dbversion_byte: int) -> bool:
    """Whether a field descriptor is an autoincrement field.

    Visual FoxPro marks autoincrementing columns with the field-flags mask
    0x0C on an Integer (``I``) field; next value and step live in descriptor
    bytes 19-22 (little-endian) and 23.  dBASE Level 7 uses the physical type
    ``+`` instead: outside VFP that type alone remains the autoincrement
    marker, while inside VFP it is never evidence of autoincrement.
    """
    if dbversion_byte in VFP_VERSIONS:
        return (
            dbf_type == "I"
            and (flags & VFP_AUTOINCREMENT_FIELD_MASK) == VFP_AUTOINCREMENT_FIELD_MASK
        )
    return dbf_type == "+"


def type_name(dbf_type: str) -> str:
    """Return the readable name of a DBF field type (``Unknown`` if unmapped)."""
    return FIELD_TYPE_NAMES.get(dbf_type, "Unknown")


@dataclass(frozen=True)
class FieldClassification:
    """Export semantics derived from one physical field descriptor."""

    target_representation: str
    is_memo: bool
    is_binary: bool
    supported: bool
    unsupported_reason: str | None


def classify_field(
    *,
    dbf_type: str,
    length: int,
    decimal_count: int,
    dbversion_byte: int,
    flags: int,
) -> FieldClassification:
    """Classify a field descriptor for export purposes.

    Pure function of the descriptor: no I/O, no CLI, no side effects.
    """
    is_binary_flag = bool(flags & BINARY_FIELD_FLAG)
    representation = "unsupported"
    is_memo = False
    is_binary = False
    supported = True
    reason: str | None = None

    if dbf_type in UNSUPPORTED_FIELD_TYPES:
        supported = False
        reason = f"Unsupported Visual FoxPro field type {dbf_type!r}."
    elif dbf_type in {"C", "V"}:
        if is_binary_flag:
            supported = False
            reason = f"Binary {dbf_type!r} fields require a dedicated parser."
            is_binary = True
        else:
            representation = "string"
    elif dbf_type == "M":
        representation = "string-or-base64"
        is_memo = True
    elif dbf_type in {"G", "P"}:
        representation = "base64"
        is_memo = True
        is_binary = True
    elif dbf_type == "B":
        if dbversion_byte in VFP_DOUBLE_VERSIONS and length == 8:
            representation = "number"
        else:
            representation = "base64"
            is_memo = True
            is_binary = True
    elif dbf_type in {"N", "F", "I", "+", "O"}:
        representation = "number"
    elif dbf_type == "Y":
        representation = "decimal-string"
    elif dbf_type == "L":
        representation = "boolean-or-null"
    elif dbf_type == "D":
        representation = "date-iso8601"
    elif dbf_type in {"T", "@"}:
        representation = "datetime-iso8601"
    elif dbf_type == "0":
        representation = "base64"
        is_binary = True
    else:
        supported = False
        reason = f"Unknown DBF field type {dbf_type!r}."

    return FieldClassification(
        target_representation=representation,
        is_memo=is_memo,
        is_binary=is_binary,
        supported=supported,
        unsupported_reason=reason,
    )


__all__ = [
    "BINARY_FIELD_FLAG",
    "FIELD_TYPE_NAMES",
    "FieldClassification",
    "NOCPTRANS_FIELD_TYPES",
    "NULLABLE_FIELD_FLAG",
    "SYSTEM_FIELD_FLAG",
    "UNSUPPORTED_FIELD_TYPES",
    "VFP_AUTOINCREMENT_FIELD_MASK",
    "VFP_DOUBLE_VERSIONS",
    "VFP_VERSIONS",
    "classify_field",
    "is_autoincrement_field",
    "is_nocptrans_field",
    "type_name",
]

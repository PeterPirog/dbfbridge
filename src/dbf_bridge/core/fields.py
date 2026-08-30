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

#: VFP field types that dbfbridge does not export safely.
UNSUPPORTED_FIELD_TYPES = frozenset({"Q", "W"})

#: DBF version bytes where type ``B`` (length 8) is a double, not a binary memo.
VFP_DOUBLE_VERSIONS = frozenset({0x30, 0x31, 0x32})


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
    "NULLABLE_FIELD_FLAG",
    "SYSTEM_FIELD_FLAG",
    "UNSUPPORTED_FIELD_TYPES",
    "VFP_DOUBLE_VERSIONS",
    "classify_field",
    "type_name",
]

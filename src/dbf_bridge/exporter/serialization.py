from __future__ import annotations

import base64
import math
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .models import FieldMetadata

BINARY_FIELD_FLAG = 0x04
BINARY_MEMO_FIELDS_KEY = "__dbfbridge_binary_memo_fields__"
RAW_TEXT_FIELDS_KEY = "__dbfbridge_raw_text_fields__"
RAW_RECORD_KEY = "__dbfbridge_raw_record__"
UNSUPPORTED_FIELD_TYPES = {"Q", "W"}
VFP_DOUBLE_VERSIONS = {0x30, 0x31, 0x32}


class SerializationError(ValueError):
    """Raised when a value cannot be represented losslessly in JSON/CSV."""


def field_metadata(
    *,
    name: str,
    dbf_type: str,
    length: int,
    decimal_count: int,
    dbversion_byte: int,
    flags: int,
    ordinal: int | None = None,
    address: int | None = None,
    index_field_flag: int = 0,
    descriptor_bytes: bytes | None = None,
) -> FieldMetadata:
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

    return FieldMetadata(
        name=name,
        dbf_type=dbf_type,
        length=length,
        decimal_count=decimal_count,
        target_representation=representation,
        is_memo=is_memo,
        is_binary=is_binary,
        supported=supported,
        unsupported_reason=reason,
        flags=flags,
        ordinal=ordinal,
        address=address,
        index_field_flag=index_field_flag,
        descriptor_bytes=descriptor_bytes,
    )


def serialize_record(
    record: Mapping[str, Any],
    fields: list[FieldMetadata],
    *,
    deleted_marker: bool | None = None,
    memo_policy: str = "inline",
    strip_spaces: bool = False,
) -> dict[str, Any]:
    serialized: dict[str, Any] = {}
    binary_memo_fields: list[str] = []
    raw_text_fields: dict[str, str] = {}
    for field in fields:
        try:
            value = record[field.name]
        except KeyError as exc:
            raise SerializationError(f"Missing field {field.name!r}.") from exc

        if field.is_memo and memo_policy == "skip":
            serialized[field.name] = None
            continue

        if field.is_memo and memo_policy == "null" and value is not None:
            serialized[field.name] = None
            continue

        serialized_value = serialize_value(value, field_name=field.name)
        if field.dbf_type == "M" and isinstance(value, (bytes, bytearray)):
            # A Visual FoxPro M field may contain text or a binary memo on a
            # per-record basis.  A bare base64 string is ambiguous, so retain
            # a compact discriminator next to the otherwise portable value.
            binary_memo_fields.append(field.name)
        raw_bytes = getattr(value, "raw_bytes", None)
        if isinstance(raw_bytes, bytes):
            raw_text_fields[field.name] = base64.b64encode(raw_bytes).decode("ascii")

        if strip_spaces and isinstance(serialized_value, str) and field.dbf_type == "C":
            serialized_value = serialized_value.rstrip()

        serialized[field.name] = serialized_value

    if binary_memo_fields:
        serialized[BINARY_MEMO_FIELDS_KEY] = binary_memo_fields
    if raw_text_fields:
        serialized[RAW_TEXT_FIELDS_KEY] = raw_text_fields

    if deleted_marker is not None:
        serialized["__deleted__"] = deleted_marker
    return serialized


def serialize_value(value: Any, *, field_name: str = "<value>") -> Any:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SerializationError(f"Field {field_name!r} contains NaN or Infinity.")
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise SerializationError(
        f"Field {field_name!r} has unsupported Python value type {type(value).__name__}."
    )

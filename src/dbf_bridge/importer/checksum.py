from __future__ import annotations

import base64
import hashlib
import json
import struct
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any


class CanonicalChecksum:
    """Schema-aware checksum independent of JSON formatting and DBF headers."""

    def __init__(self, schema: Mapping[str, Any]) -> None:
        self.fields = list(schema.get("fields") or [])
        self.schema_signature = [
            [
                field.get("ordinal"),
                field.get("name"),
                field.get("dbf_type"),
                field.get("length"),
                field.get("decimal_count"),
                field.get("flags", 0),
            ]
            for field in self.fields
            if field.get("dbf_type") != "0"
        ]
        self._active = hashlib.sha256()
        self._deleted = hashlib.sha256()
        self.active_records = 0
        self.deleted_records = 0

    def update(self, record: Mapping[str, Any]) -> None:
        deleted = bool(record.get("__deleted__", False))
        values = list(canonical_record(record, self.fields).values())
        encoded = (
            json.dumps(values, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        if deleted:
            self._deleted.update(encoded)
            self.deleted_records += 1
        else:
            self._active.update(encoded)
            self.active_records += 1

    @property
    def record_count(self) -> int:
        return self.active_records + self.deleted_records

    def hexdigest(self) -> str:
        envelope = {
            "schema": self.schema_signature,
            "active_records": self.active_records,
            "deleted_records": self.deleted_records,
            "active_sha256": self._active.hexdigest(),
            "deleted_sha256": self._deleted.hexdigest(),
        }
        payload = json.dumps(
            envelope,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def nullable_null_fields(record: Mapping[str, Any], fields: list[Mapping[str, Any]]) -> set[str]:
    null_field = next((field for field in fields if field.get("dbf_type") == "0"), None)
    if null_field is None:
        return set()
    raw_value = record.get(str(null_field["name"]))
    if raw_value is None:
        return set()
    if isinstance(raw_value, (bytes, bytearray)):
        bitmap = bytes(raw_value)
    else:
        try:
            bitmap = base64.b64decode(str(raw_value), validate=True)
        except ValueError:
            return set()
    null_names: set[str] = set()
    nullable = [field for field in fields if int(field.get("flags") or 0) & 0x02]
    for bit, field in enumerate(nullable):
        byte_index, bit_index = divmod(bit, 8)
        if byte_index < len(bitmap) and bitmap[byte_index] & (1 << bit_index):
            null_names.add(str(field["name"]))
    return null_names


def canonical_record(record: Mapping[str, Any], fields: list[Mapping[str, Any]]) -> dict[str, Any]:
    null_names = nullable_null_fields(record, fields)
    return {
        str(field["name"]): canonical_value(
            None if field["name"] in null_names else record.get(field["name"]),
            field,
        )
        for field in fields
        if field.get("dbf_type") != "0"
    }


def canonical_value(value: Any, field: Mapping[str, Any]) -> Any:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        return base64.b64encode(bytes(value)).decode("ascii")
    dbf_type = str(field.get("dbf_type"))
    decimals = int(field.get("decimal_count") or 0)
    if dbf_type in {"N", "F", "I", "+", "Y"}:
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"Field {field.get('name')!r} is not numeric: {value!r}") from exc
        if dbf_type == "Y":
            return format(number.quantize(Decimal("0.0001")), "f")
        if dbf_type in {"I", "+"} or decimals == 0:
            return format(number.quantize(Decimal("1")), "f")
        quantum = Decimal(1).scaleb(-decimals)
        return format(number.quantize(quantum), "f")
    if dbf_type in {"B", "O"} and not field.get("is_memo"):
        return struct.pack("<d", float(value)).hex()
    if dbf_type == "D":
        if isinstance(value, datetime):
            value = value.date()
        return (
            value.isoformat()
            if isinstance(value, date)
            else date.fromisoformat(str(value)).isoformat()
        )
    if dbf_type in {"T", "@"}:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        return parsed.isoformat(timespec="milliseconds")
    if dbf_type == "L":
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"false", "f", "no", "n", "0"}:
                return False
            if lowered in {"true", "t", "yes", "y", "1"}:
                return True
        return bool(value)
    return str(value) if not isinstance(value, str) else value

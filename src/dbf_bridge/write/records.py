"""Record mapping for the internal direct-write stream.

``write_table`` accepts:

1. :class:`~dbf_bridge.core.records.DirectRecord` — the preferred record
   model of the Phase 1 direct read stream (``deleted`` state + ``values``
   mapping), and
2. plain ``Mapping[str, Any]`` objects using the reconstruction
   ``__deleted__`` marker convention (used by the JSONL oracle path).

Contract (DBFB-REC-001..009):

- the input physical order IS the output physical order — the adapter never
  reorders and the shared writer streams;
- unknown non-reserved keys are REJECTED (``WRITE_VALUE_INVALID``) instead of
  silently ignored (DBFB-REC-004); reserved internal transport keys
  (``__dbfbridge_*``) are recognized explicitly and passed through;
- every required (non-NULLable) logical field must be present — a missing
  required field is a typed error, never an accidental blank/zero
  (DBFB-REC-005); a missing NULLable field is an explicit NULL;
- the ``_NullFlags`` system column is WRITER-MANAGED: the caller never
  computes bitmaps (DBFB-REC-006); a caller-supplied bitmap column is
  rejected, and the canonical bitmap is derived from the logical ``None``
  values via ``dbf_bridge.core.nullflags`` (the single allocation engine);
- inline memo ``str``/``bytes`` pass through unchanged (DBFB-REC-007); a
  :class:`~dbf_bridge.core.records.LazyMemoValue` is resolved ONLY through
  its explicit ``load()`` — the writer never opens or guesses a source FPT
  (DBFB-REC-008); memo failures surface as typed ``WRITE_MEMO_FAILED``.

Error payloads carry field NAMES and DBF types only — never record or memo
values (DBFB-ERR-005).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..common import BINARY_MEMO_FIELDS_KEY
from ..core.errors import WriteMemoFailedError, WriteValueInvalidError
from ..core.nullflags import NullFlagsLayout, build_nullflags_layout, encode_nullflags_bitmap
from ..core.records import DirectRecord, LazyMemoValue

__all__ = ["RecordAdapter"]

_RESERVED_KEYS = frozenset({"__deleted__", "__NullFlags__"})


class RecordAdapter:
    """One deterministic mapper from input records to backend records.

    Built once per ``write_table`` call (O(1) construction); :meth:`map` is
    O(fields) per record with no per-record allocations beyond the output
    mapping, so the flat path stays O(1)/O(batch) memory.
    """

    def __init__(self, backend_schema: Mapping[str, Any]) -> None:
        self._fields = list(backend_schema["fields"])
        self._logical = [field for field in self._fields if str(field["dbf_type"]) != "0"]
        self._logical_names = {str(field["name"]) for field in self._logical}
        self._nullable_names = {
            str(field["name"])
            for field in self._logical
            if int(field.get("flags") or 0) & 0x02
        }
        # One canonical allocation for the whole table; a structurally
        # inconsistent NULL/Varchar schema raises the typed read-family error
        # here, which the API boundary maps to WRITE_SCHEMA_INVALID.
        self._layout: NullFlagsLayout | None = build_nullflags_layout(self._fields)
        self._reserved = set(_RESERVED_KEYS)
        if self._layout is not None:
            self._reserved.add(self._layout.field_name)
        self._memo_names = {
            str(field["name"]) for field in self._logical if str(field["dbf_type"]) == "M"
        }

    @property
    def layout(self) -> NullFlagsLayout | None:
        """The canonical ``_NullFlags`` layout (``None`` when not needed)."""
        return self._layout

    def map(self, record: Any) -> dict[str, Any]:
        """Convert one incoming record into the backend record mapping."""
        if isinstance(record, DirectRecord):
            source: dict[str, Any] = dict(record.values)
            deleted = bool(record.deleted)
        elif isinstance(record, Mapping):
            source = dict(record)
            deleted = bool(source.get("__deleted__", False))
        else:
            raise WriteValueInvalidError(
                "records must yield DirectRecord objects or plain mappings.",
                context={"record_type": type(record).__name__},
            )

        unknown = sorted(
            str(key)
            for key in source
            if str(key) not in self._logical_names
            and str(key) not in self._reserved
            and not str(key).startswith("__dbfbridge_")
        )
        if unknown:
            # Field NAME metadata only — never the stored value.
            raise WriteValueInvalidError(
                "Record contains keys that are not schema fields.",
                context={"unknown_fields": unknown[:16], "reason": "unknown_field"},
            )
        # The ``_NullFlags`` system column is WRITER-MANAGED (DBFB-REC-006):
        # a caller-supplied bitmap (e.g. a value carried over from a Direct
        # Read of the source table) is recognized as a reserved system key
        # and DROPPED — the canonical bitmap is re-derived from the logical
        # ``None`` values below via ``dbf_bridge.core.nullflags``, the single
        # allocation engine.
        bitmap_supplied = self._layout is not None and self._layout.field_name in source

        mapped: dict[str, Any] = {}
        null_names: set[str] = set()
        binary_memo_names: list[str] = []
        for field in self._logical:
            name = str(field["name"])
            if name in source:
                value = source[name]
            elif name in self._nullable_names:
                # Explicit NULL semantics for a missing NULLable field.
                value = None
                null_names.add(name)
            else:
                # A missing required field must never silently become a blank
                # or zero through backend behaviour (DBFB-REC-005).
                raise WriteValueInvalidError(
                    "Record is missing a required (non-NULLable) schema field.",
                    context={"field": name, "reason": "missing_required_field"},
                )
            if isinstance(value, LazyMemoValue):
                try:
                    value = value.load()
                except Exception as exc:
                    raise WriteMemoFailedError(
                        "The lazy memo payload could not be loaded through its "
                        "explicit load().",
                        context={"field": name, "reason": "lazy_load_failed"},
                    ) from exc
            if value is None and name in self._nullable_names:
                null_names.add(name)
            if name in self._memo_names and isinstance(value, (bytes, bytearray)):
                # Mirrors the exporter's per-record binary-memo discriminator.
                binary_memo_names.append(name)
            mapped[name] = value
        for key in source:
            key_text = str(key)
            if key_text.startswith("__dbfbridge_"):
                mapped[key_text] = source[key]
        mapped["__deleted__"] = deleted
        if binary_memo_names:
            mapped[BINARY_MEMO_FIELDS_KEY] = binary_memo_names
        if self._layout is not None:
            # Always the writer's canonical bitmap — never the caller's bytes.
            _ = bitmap_supplied  # recognized and dropped
            mapped[self._layout.field_name] = encode_nullflags_bitmap(self._layout, null_names)
        return mapped

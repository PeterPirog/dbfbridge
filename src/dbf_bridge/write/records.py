"""Record mapping for the direct-write stream.

``write_table`` accepts:

1. :class:`~dbf_bridge.core.records.DirectRecord` — the preferred record
   model of the Phase 1 direct read stream (``deleted`` state + values
   mapping), and
2. plain ``Mapping[str, Any]`` objects using the reconstruction
   ``__deleted__`` marker convention (used by the JSONL oracle path).

Memo contract: values produced by ``iter_records(..., memo="inline")`` (already
decoded ``str``/``bytes``) pass straight through.  A
:class:`~dbf_bridge.core.records.LazyMemoValue` left in the stream is resolved
through its **explicit** ``load()`` call — the loader closure was created by
the caller's read phase; the writer never opens a source FPT itself and memo
failures surface as typed ``WRITE_MEMO_FAILED`` errors.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..core.errors import DirectWriteError
from ..core.records import DirectRecord, LazyMemoValue

__all__ = ["record_mapping"]


def record_mapping(record: Any, backend_schema: Mapping[str, Any]) -> dict[str, Any]:
    """Convert one incoming record into the backend record mapping."""
    if isinstance(record, DirectRecord):
        return {
            **_values(record.values),
            "__deleted__": record.deleted,
        }
    if isinstance(record, Mapping):
        return dict(record)
    raise DirectWriteError(
        f"records must yield DirectRecord objects or plain mappings, got {type(record).__name__}",
        path=None,
        context={"record_type": type(record).__name__},
    )


def _values(values: Any) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for name, value in values.items():
        if isinstance(value, LazyMemoValue):
            # Explicit resolution through the value's own loader (created by
            # the caller's read phase; never a hidden source FPT handle).
            resolved[name] = value.load()
        else:
            resolved[name] = value
    return resolved

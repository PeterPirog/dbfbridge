"""Neutral, stdlib-only progress and cancellation boundary.

This module is the single source for the structured progress contract shared
by the Direct Read core (``operation="read"``) and the migration operations
(export/convert/reconstruct/verify/quality).  It is **stdlib-only** so
``core`` can depend on it without touching exporter/importer models.

Public compatibility is preserved: ``dbf_bridge.api_models`` re-exports
:class:`ProgressEvent` / :data:`Operation` from here (they are the very same
objects), so every existing import path keeps working.

The library never creates threads, event loops, background workers, or global
cancellation state: a caller supplies a plain callable
(:data:`CancellationCheck`) that returns ``True`` when the operation should
stop.  The library never installs, schedules, or polls anything on its own.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

#: Operations that can emit :class:`ProgressEvent` notifications.  ``read``
#: is the Direct Read operation (``iter_records`` / ``read_records`` /
#: ``iter_raw_records``).
Operation = Literal[
    "read",
    "export",
    "convert",
    "reconstruct",
    "verify",
    "quality",
]

#: A callable receiving one :class:`ProgressEvent` per bounded cadence step
#: plus one final event for a normally completed call/page.
ProgressCallback = Callable[["ProgressEvent"], None]

#: Cooperative cancellation probe: return ``True`` to stop before the next
#: physical record is read.  Plain callable supplied by the caller — the
#: library creates no threads, loops, workers, or global state.  Raising from
#: the callable propagates the original exception to the caller (after all
#: file handles are closed); only returning ``True`` is converted into
#: ``READ_CANCELLED``.
CancellationCheck = Callable[[], bool]


@dataclass(frozen=True)
class ProgressEvent:
    """A structured progress notification emitted by long-running API calls.

    Direct Read (``operation="read"``) semantics:

    - ``current``  — absolute **physical** position after the last processed
      record (``0 <= current <= total``); deleted records count toward it;
    - ``total``    — the declared physical record count of the table;
    - ``table``    — the DBF path as a string;
    - ``records``  — number of records yielded/returned by this call so far
      (deleted records do not count when ``include_deleted=False``).
    """

    operation: Operation
    current: int
    total: int
    table: str | None = None
    format: str | None = None
    records: int | None = None
    message: str | None = None


__all__ = ["CancellationCheck", "Operation", "ProgressCallback", "ProgressEvent"]

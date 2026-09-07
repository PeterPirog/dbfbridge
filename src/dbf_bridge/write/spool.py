"""Private bounded record spool for the shared writer's second logical pass.

The canonical Varchar/``_NullFlags`` layout pass (``dbf_bridge.write.backend``
``_repair_varchar_logical_layout``) re-streams the mapped records while
rewriting the staged DBF bytes.  A Direct Write caller supplies a one-shot
iterable (DBFB-STREAM-001), so the internal API feeds the repair from this
spool instead of materializing the caller's input in RAM (DBFB-STREAM-003):

- starts bounded (an in-memory batch of at most ``memory_threshold``
  mapped records) and spills to a private staging file after the
  deterministic threshold;
- the spool file lives ONLY inside the staging area, is NOT JSONL and is
  never part of any public contract (DBFB-STREAM-004): it is a private
  sequential pickle stream written and read back by the same process within
  one ``write_table`` call;
- it is closed and deleted after success, handled failure and cancellation;
  a hard crash may leave the staging file behind (documented staging
  residue, DBFB-PUB-006).
"""

from __future__ import annotations

import pickle
from collections.abc import Iterator
from pathlib import Path
from typing import Any

__all__ = ["RecordSpool"]

_DEFAULT_MEMORY_THRESHOLD = 10_000


class RecordSpool:
    """Bounded write-once/read-once spool of mapped backend records."""

    def __init__(
        self,
        path: Path,
        *,
        memory_threshold: int = _DEFAULT_MEMORY_THRESHOLD,
    ) -> None:
        self._path = path
        self._threshold = max(1, int(memory_threshold))
        self._buffer: list[dict[str, Any]] = []
        self._spilled = False
        self._handle = None
        self._count = 0

    def append(self, record: dict[str, Any]) -> None:
        """Buffer one mapped record, spilling to disk past the threshold."""
        if not self._spilled:
            if len(self._buffer) < self._threshold:
                self._buffer.append(record)
                self._count += 1
                return
            self._spill()
        pickle.dump(record, self._handle)  # type: ignore[union-attr]
        self._count += 1

    def _spill(self) -> None:
        self._handle = self._path.open("w+b")
        for record in self._buffer:
            pickle.dump(record, self._handle)
        self._buffer.clear()
        self._spilled = True

    @property
    def count(self) -> int:
        return self._count

    @property
    def spilled(self) -> bool:
        return self._spilled

    def replay(self) -> Iterator[dict[str, Any]]:
        """Re-stream the spooled records in insertion order (second pass)."""
        if not self._spilled:
            yield from self._buffer
            return
        handle = self._handle
        handle.seek(0)  # type: ignore[union-attr]
        while True:
            try:
                yield pickle.load(handle)  # type: ignore[union-attr]
            except EOFError:
                return

    def discard(self) -> None:
        """Close and delete the spool (success, failure and cancellation)."""
        if self._handle is not None:
            try:
                self._handle.close()
            finally:
                self._handle = None
        self._buffer.clear()
        self._path.unlink(missing_ok=True)

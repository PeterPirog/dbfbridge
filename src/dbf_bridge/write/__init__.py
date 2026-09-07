"""Internal shared physical DBF/FPT writer boundary (v1.1).

Phase B established the single physical writer
(:mod:`dbf_bridge.write.backend`); Phase C adds the INTERNAL Direct Write
contract on top of it:

- :func:`write_table` — the internal entry point (overwrite defaults to
  ``False``; canonical ``ProgressEvent`` progress; cooperative
  ``cancel_check``; exactly-once streaming with a private bounded spool for
  the Varchar/``_NullFlags`` second logical pass);
- :class:`WriteResult` — the immutable, JSON-safe publication summary;
- the separate :class:`~dbf_bridge.core.errors.DirectWriteError` family
  (never derived from :class:`~dbf_bridge.core.errors.DirectReadError`).

Everything in this package is INTERNAL until an explicit version decision
promotes it (Phase D): nothing here is exported from the root public
facades, and no symbol is part of the stable 1.0 contract.  The ``dbf``
dependency stays lazy and optional (``[write]``).
"""

from __future__ import annotations

from .api import WriteResult, write_table
from .schema_adapter import schema_to_mapping, validate_schema_for_write

__all__ = [
    "WriteResult",
    "schema_to_mapping",
    "validate_schema_for_write",
    "write_table",
]

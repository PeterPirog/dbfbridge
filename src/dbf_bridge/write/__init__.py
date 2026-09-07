"""Direct Write package (dbfbridge v1.1 public contract).

The Direct Write surface is the write-side counterpart of the Direct Read
core and has been part of the public API since the additive v1.1 contract
(``docs/api-1.1.md``); it reuses the single physical DBF/FPT writer
(:mod:`dbf_bridge.write.backend`) shared with the reconstruction pipeline:

- :func:`write_table` — the public entry point (``overwrite`` defaults to
  ``False``; canonical :class:`~dbf_bridge.progress.ProgressEvent` progress;
  cooperative ``cancel_check``; exactly-once streaming with a private
  bounded spool for the Varchar/``_NullFlags`` second logical pass);
- :class:`WriteResult` — the immutable, JSON-safe publication summary;
- the separate :class:`~dbf_bridge.core.errors.DirectWriteError` family
  (never derived from :class:`~dbf_bridge.core.errors.DirectReadError`).

Both public facades (``dbfbridge`` and ``dbf_bridge``) re-export these
symbols lazily.  The ``dbf`` dependency stays optional (``[write]`` extra)
and is imported lazily inside the physical writer only when bytes actually
move.
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

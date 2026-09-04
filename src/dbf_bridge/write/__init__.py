"""Typed Direct Write layer (RESEARCH — next version, not part of the stable
1.x contract) — the write-side counterpart of the Phase 1 Direct Read Core.

Modules:

- ``backend.py`` — the single physical DBF/FPT writer (derived from the
  proven reconstruction writer on current ``main``; ``importer.writer``
  delegates to it, so there is exactly one physical writer);
- ``schema_adapter.py`` — adapts the public ``TableSchema`` to the backend
  mapping (no legacy export-schema conversion required from integrators);
- ``records.py`` — record/record-value mapping for ``DirectRecord`` and
  plain mappings;
- ``api.py`` — the public ``write_table()`` entry point and :class:`WriteResult`.

The ``dbf`` backend import stays lazy inside the shared writer: importing
``dbfbridge`` never loads it, registers no codepage for writing, creates no
files and loads no CLI/reporting modules.  Backend failures carry structured
machine codes; the public boundary maps them onto typed errors WITHOUT
parsing the English message.
"""

from __future__ import annotations

from .api import WriteResult, write_table

__all__ = ["WriteResult", "write_table"]

"""Internal shared physical DBF/FPT writer boundary (Phase B).

This package hosts the SINGLE physical DBF/FPT writer implementation
(:mod:`dbf_bridge.write.backend`), used by the reconstruction pipeline
(``dbf_bridge.importer`` delegates to it) and reserved as the shared backend
for the future Direct Write contract.

It is internal implementation detail — no symbol here is part of the stable
1.0 public contract, and the package exposes no public names itself
(``dbf_bridge.write.backend`` is imported explicitly by the internal
compatibility layer ``dbf_bridge.importer.writer``).  The ``dbf`` dependency
stays lazy: importing this package never loads it and has no side effects.
"""

from __future__ import annotations

__all__: list[str] = []

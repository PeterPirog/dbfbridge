"""Compatibility re-exports for the canonical checksum primitives.

The implementation moved verbatim to the neutral shared layer
(:mod:`dbf_bridge.common`) so the physical writer boundary
(``dbf_bridge.write``) can share it without importing ``importer``
(DBFB-LAYER-005: no ``write -> importer`` coupling, no import cycle).
Every historical import path keeps working unchanged.
"""

from __future__ import annotations

from dbf_bridge.common import (  # noqa: F401 - compatibility re-exports
    CanonicalChecksum,
    canonical_record,
    canonical_value,
    nullable_null_fields,
)

__all__ = [
    "CanonicalChecksum",
    "canonical_record",
    "canonical_value",
    "nullable_null_fields",
]

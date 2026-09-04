"""Reconstruction import writer — compatibility delegation layer.

The physical DBF/FPT writing implementation lives in
:mod:`dbf_bridge.write.backend` (the single shared writer used by both the
reconstruction pipeline and the direct-write API).  This module re-exports
the reconstruction-facing surface unchanged so that existing imports keep
working:

- ``write_dbf`` / ``restore_raw_layout`` / ``memo_output_path`` / ``output_hashes``
- ``ReconstructionError`` (the physical writer's error family)

Raw-layout restoration (`restore_raw_layout`) stays part of the reconstruction
oracle path: it requires the raw Base64 record images of the JSONL transport.
"""

from __future__ import annotations

import os  # noqa: F401 (tests and callers historically access writer.os)
import shutil  # noqa: F401
import struct  # noqa: F401
from base64 import b64decode  # noqa: F401 (restoration helpers)

from dbf_bridge.write.backend import (  # noqa: F401 (re-exports)
    DBF_HEADER_SIZE,
    FIELD_DESCRIPTOR_SIZE,
    SUPPORTED_FIELD_TYPES,
    TYPE_ALIASES,
    ReconstructionError,
    memo_output_path,
    output_hashes,
    restore_raw_layout,
    write_dbf,
)

__all__ = [
    "DBF_HEADER_SIZE",
    "FIELD_DESCRIPTOR_SIZE",
    "ReconstructionError",
    "SUPPORTED_FIELD_TYPES",
    "TYPE_ALIASES",
    "memo_output_path",
    "output_hashes",
    "restore_raw_layout",
    "write_dbf",
]

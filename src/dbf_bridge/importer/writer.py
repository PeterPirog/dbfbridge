"""Reconstruction import writer — compatibility delegation layer.

The physical DBF/FPT writing implementation lives in
:mod:`dbf_bridge.write.backend` — the SINGLE shared physical writer used by
the reconstruction pipeline (and, in a later phase, the Direct Write
contract).  This module re-exports the reconstruction-facing surface
unchanged so that existing imports keep working; it retains no second copy
of the physical writer algorithms (DBFB-LAYER-003).

Re-exported surface:

- ``write_dbf`` / ``restore_raw_layout`` / ``memo_output_path`` / ``output_hashes``
- ``ReconstructionError`` (the physical writer's error family)
- layout constants (``DBF_HEADER_SIZE``, ``FIELD_DESCRIPTOR_SIZE``,
  ``SUPPORTED_FIELD_TYPES``, ``TYPE_ALIASES``) and the internal helper
  functions, for historical module-attribute access.

Raw-layout restoration (``restore_raw_layout``) stays part of the
reconstruction oracle path: it requires the raw Base64 record images of the
JSONL transport.
"""

from __future__ import annotations

import os  # noqa: F401 (tests and callers historically access writer.os)
import shutil  # noqa: F401
import struct  # noqa: F401
from base64 import b64decode  # noqa: F401 (restoration helpers)

from dbf_bridge.write.backend import (  # noqa: F401 (compatibility re-exports)
    DBF_HEADER_SIZE,
    FIELD_DESCRIPTOR_SIZE,
    SUPPORTED_FIELD_TYPES,
    TYPE_ALIASES,
    ReconstructionError,
    _binary_memo_fields,
    _coerce_value,
    _encode_text,
    _ensure_writer_text_codecs,
    _field_spec,
    _fsync_file,
    _hex_byte,
    _install_lossless_numeric_writer,
    _patch_dbf_metadata,
    _patch_fpt_block_types,
    _patch_fpt_metadata,
    _raw_text_fields,
    _record_bitmap,
    _relocate_memo_block,
    _repair_varchar_logical_layout,
    _set_bitmap_bit,
    _text_encodings,
    _update_numeric,
    _validate_layout,
    memo_output_path,
    output_hashes,
    restore_raw_layout,
    write_dbf,
)

__all__ = [
    "DBF_HEADER_SIZE",
    "FIELD_DESCRIPTOR_SIZE",
    "SUPPORTED_FIELD_TYPES",
    "TYPE_ALIASES",
    "ReconstructionError",
    "memo_output_path",
    "output_hashes",
    "restore_raw_layout",
    "write_dbf",
]

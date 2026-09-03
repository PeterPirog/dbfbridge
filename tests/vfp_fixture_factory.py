"""Deterministic VFP/FPT fixture factory for the correctness-matrix tests.

Small, portable builders for synthetic Visual FoxPro tables.  Everything is
deterministic (no randomness), runs on Windows/Linux, and needs no VFP
installation, no COM, and no network.

Two construction techniques are used, and every test documents which one:

- **legal generation** through the ``dbf`` package, which supports the VFP
  types C, N, F, I, Y, B (double), T, D, L, M, G, P natively and creates the
  hidden ``_NullFlags`` (type ``0``) system column for NULLable fields;
- **documented byte patching** of a legally generated table, only where the
  physical layout is known and stays structurally valid: type bytes with an
  identical physical width (``B``->``O``, ``I``->``+``, ``T``->``@``,
  ``C``->``Q``, ``M``->``W``, ``C``->``V`` with the VFP 0x32 version),
  descriptor flags (autoincrement 0x0C, NOCPTRANS 0x04), delete markers,
  memo pointers, and FPT block headers/layouts.

Never invent a layout: when a physical layout cannot be produced legally or
documented precisely, the compatibility matrix records ``NOT_YET_VERIFIED``
instead of faking a test.
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path
from typing import Any

import dbf

DBF_HEADER_SIZE = 32
FIELD_DESCRIPTOR_SIZE = 32

#: Descriptor byte offsets (relative to the descriptor start).
FLAG_SYSTEM = 0x01
FLAG_NULLABLE = 0x02
FLAG_BINARY = 0x04
FLAG_AUTOINCREMENT_MASK = 0x0C


def create_vfp_table(
    path: Path,
    field_specs: str,
    records: list[dict[str, Any]],
    codepage: int = 0xC8,
) -> Path:
    """Create a legal VFP table through ``dbf`` (deterministic content)."""
    path = Path(path)
    for suffix in (".dbf", ".fpt"):
        candidate = path.with_suffix(suffix)
        if candidate.exists():
            candidate.unlink()
    table = dbf.Table(str(path), field_specs=field_specs, dbf_type="vfp", codepage=codepage)
    table.open(mode=dbf.READ_WRITE)
    for record in records:
        table.append(record)
    table.close()
    return path


# ---------------------------------------------------------------------------
# DBF byte surgery (layout-preserving patches)
# ---------------------------------------------------------------------------


def dbf_layout(dbf_path: Path) -> tuple[int, int, int]:
    """Return ``(header_length, record_length, record_count)`` of a DBF."""
    data = Path(dbf_path).read_bytes()
    header_length = struct.unpack_from("<H", data, 8)[0]
    record_length = struct.unpack_from("<H", data, 10)[0]
    record_count = struct.unpack_from("<I", data, 4)[0]
    return header_length, record_length, record_count


def field_descriptor(dbf_path: Path, field_index: int) -> bytes:
    """Raw 32-byte field descriptor."""
    data = Path(dbf_path).read_bytes()
    start = DBF_HEADER_SIZE + field_index * FIELD_DESCRIPTOR_SIZE
    return data[start : start + FIELD_DESCRIPTOR_SIZE]


def patch_field_type(dbf_path: Path, field_index: int, type_code: str) -> None:
    """Rewrite one descriptor's physical type byte (byte 11).

    Only legal for same-width type substitutions; the caller documents the
    layout equivalence in the test.
    """
    path = Path(dbf_path)
    data = bytearray(path.read_bytes())
    data[DBF_HEADER_SIZE + field_index * FIELD_DESCRIPTOR_SIZE + 11] = ord(type_code)
    path.write_bytes(bytes(data))


def patch_field_flags(dbf_path: Path, field_index: int, flags: int) -> None:
    """Rewrite one descriptor's field-flags byte (offset 18)."""
    path = Path(dbf_path)
    data = bytearray(path.read_bytes())
    data[DBF_HEADER_SIZE + field_index * FIELD_DESCRIPTOR_SIZE + 18] = flags
    path.write_bytes(bytes(data))


def patch_dbversion(dbf_path: Path, version_byte: int) -> None:
    """Rewrite the DBF version byte (header offset 0)."""
    path = Path(dbf_path)
    data = bytearray(path.read_bytes())
    data[0] = version_byte
    path.write_bytes(bytes(data))


def patch_autoincrement_bookkeeping(
    dbf_path: Path, field_index: int, *, next_value: int, step: int
) -> None:
    """Write the VFP autoincrement next-value (bytes 19-22 LE) and step (23)."""
    path = Path(dbf_path)
    data = bytearray(path.read_bytes())
    base = DBF_HEADER_SIZE + field_index * FIELD_DESCRIPTOR_SIZE
    struct.pack_into("<L", data, base + 19, next_value)
    data[base + 23] = step
    path.write_bytes(bytes(data))


def _field_storage_length(descriptor: bytes) -> int:
    """Physical byte width of one field inside the record area."""
    length = descriptor[16]
    if descriptor[11:12] == b"C":
        # Long character fields store the high byte in the decimal position.
        length |= descriptor[17] << 8
    return length


def _field_record_offset(dbf_path: Path, record_index: int, field_index: int) -> int:
    path = Path(dbf_path)
    header_length, record_length, _count = dbf_layout(path)
    record_start = header_length + record_index * record_length
    field_offset = 1  # delete marker
    for index in range(field_index):
        field_offset += _field_storage_length(field_descriptor(path, index))
    return record_start + field_offset


def read_memo_pointer(dbf_path: Path, record_index: int, field_index: int) -> int:
    """Read one record's 4-byte memo block pointer."""
    data = Path(dbf_path).read_bytes()
    offset = _field_record_offset(dbf_path, record_index, field_index)
    return struct.unpack_from("<L", data, offset)[0]


def set_memo_pointer(dbf_path: Path, record_index: int, field_index: int, block: int) -> None:
    """Rewrite one record's 4-byte memo block pointer (little-endian)."""
    path = Path(dbf_path)
    data = bytearray(path.read_bytes())
    offset = _field_record_offset(path, record_index, field_index)
    struct.pack_into("<L", data, offset, block)
    path.write_bytes(bytes(data))


def mark_deleted(dbf_path: Path, record_index: int) -> None:
    """Flip one physical record's delete marker to 0x2A in place."""
    path = Path(dbf_path)
    data = bytearray(path.read_bytes())
    header_length, record_length, _count = dbf_layout(path)
    data[header_length + record_index * record_length] = 0x2A
    path.write_bytes(bytes(data))


# ---------------------------------------------------------------------------
# FPT surgery
# ---------------------------------------------------------------------------


def fpt_layout(fpt_path: Path) -> tuple[int, int]:
    """Return ``(next_free_block, block_size)`` from the 8-byte FPT prefix."""
    data = Path(fpt_path).read_bytes()
    next_free = struct.unpack_from(">L", data, 0)[0]
    block_size = struct.unpack_from(">H", data, 6)[0]
    return next_free, block_size


def read_memo_block_header(fpt_path: Path, block: int, block_size: int) -> tuple[int, int]:
    """Return ``(block_type, declared_payload_length)`` of one memo block."""
    data = Path(fpt_path).read_bytes()
    offset = block * block_size
    block_type = struct.unpack_from(">L", data, offset)[0]
    length = struct.unpack_from(">L", data, offset + 4)[0]
    return block_type, length


def patch_memo_block_header(
    fpt_path: Path,
    block: int,
    block_size: int,
    *,
    block_type: int | None = None,
    payload_length: int | None = None,
) -> None:
    """Patch one 8-byte memo block header (big-endian type and/or length)."""
    path = Path(fpt_path)
    data = bytearray(path.read_bytes())
    offset = block * block_size
    if block_type is not None:
        struct.pack_into(">L", data, offset, block_type)
    if payload_length is not None:
        struct.pack_into(">L", data, offset + 4, payload_length)
    path.write_bytes(bytes(data))


def rewrite_fpt(
    fpt_path: Path,
    *,
    block_size: int,
    blocks: list[tuple[int, int, bytes]],
) -> None:
    """Rebuild an FPT from scratch with a chosen block size.

    ``blocks`` lists ``(block_number, block_type, payload)`` tuples.  The
    layout follows the physical FPT contract: an 8-byte prefix (next-free
    block big-endian; block size big-endian at bytes 6-7), then each block at
    ``block_number * block_size`` with a big-endian ``(type, length)`` header
    followed by its payload.
    """
    next_free = max((number for number, _type, _payload in blocks), default=0) + 1
    first_block_offset = min(
        (number * block_size for number, _type, _payload in blocks),
        default=next_free * block_size,
    )
    data = bytearray()
    data += struct.pack(">L", next_free)
    data += b"\x00\x00"
    data += struct.pack(">H", block_size)
    data += b"\x00" * (max(first_block_offset, 8) - 8)
    for number, block_type, payload in blocks:
        offset = number * block_size
        if len(data) > offset:
            raise ValueError(f"overlapping FPT blocks at {number}")
        if len(data) < offset:
            data += b"\x00" * (offset - len(data))
        data += struct.pack(">L", block_type) + struct.pack(">L", len(payload)) + payload
    Path(fpt_path).write_bytes(bytes(data))


# ---------------------------------------------------------------------------
# safety fingerprints
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    """SHA-256 of one file."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def directory_fingerprint(directory: Path) -> list[tuple[str, str]]:
    """Sorted ``(name, sha256)`` list of every regular file under a directory.

    Proves a read touched nothing: no ``.partial``, lock, report, or
    temporary file may appear, and existing files keep their bytes.
    """
    return sorted(
        (item.name, sha256_file(item)) for item in Path(directory).rglob("*") if item.is_file()
    )

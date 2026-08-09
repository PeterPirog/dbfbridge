from __future__ import annotations

import base64
import os
import struct
from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from dbf_bridge.exporter.validation import sha256_file

from .checksum import CanonicalChecksum, nullable_null_fields

DBF_HEADER_SIZE = 32
FIELD_DESCRIPTOR_SIZE = 32
SUPPORTED_FIELD_TYPES = {
    "C",
    "V",
    "N",
    "F",
    "L",
    "D",
    "T",
    "@",
    "M",
    "G",
    "P",
    "B",
    "O",
    "I",
    "+",
    "Y",
    "0",
}
TYPE_ALIASES = {"@": "T", "O": "B", "+": "I", "V": "C"}


class ReconstructionError(ValueError):
    """Raised when exported data cannot recreate the declared DBF structure."""


def write_dbf(
    destination: Path,
    records: Iterable[Mapping[str, Any]],
    schema: Mapping[str, Any],
    *,
    overwrite: bool,
    progress_callback: Callable[[int], None] | None = None,
) -> tuple[CanonicalChecksum, list[str]]:
    try:
        import dbf
    except ImportError as exc:
        raise RuntimeError("DBF reconstruction requires dbf>=0.99.11.") from exc

    fields = [field for field in schema["fields"] if field.get("dbf_type") != "0"]
    unsupported = sorted(
        {
            str(field.get("dbf_type"))
            for field in fields
            if field.get("dbf_type") not in SUPPORTED_FIELD_TYPES
        }
    )
    if unsupported:
        raise ReconstructionError(f"Unsupported DBF field types for reconstruction: {unsupported}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    memo_required = any(field.get("is_memo") for field in fields)
    final_fpt = memo_output_path(destination, schema)
    if destination.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite existing output: {destination}")
    if memo_required and final_fpt.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite existing memo output: {final_fpt}")

    partial = destination.with_name(f".{destination.stem}.partial.dbf")
    partial_fpt = partial.with_suffix(".fpt")
    partial.unlink(missing_ok=True)
    partial_fpt.unlink(missing_ok=True)
    warnings: list[str] = []
    structural_index = int(schema.get("dbf", {}).get("structural_index_flag") or 0)
    if structural_index:
        warnings.append(
            "Source DBF references a structural CDX index, but index definitions are not present "
            "in the schema; reconstructed DBF has its structural-index flag cleared."
        )

    specs = "; ".join(_field_spec(field) for field in fields)
    codepage = _hex_byte(schema.get("dbf", {}).get("language_driver"), default=0x03)
    memo_size = int(schema.get("memo", {}).get("block_size_bytes") or 64)
    checksum = CanonicalChecksum(schema)
    table = None
    try:
        table = dbf.Table(
            str(partial),
            field_specs=specs,
            memo_size=memo_size,
            dbf_type="vfp",
            codepage=codepage,
        )
        table.open(mode=dbf.READ_WRITE)
        for index, source_record in enumerate(records, start=1):
            checksum.update(source_record)
            null_names = nullable_null_fields(source_record, list(schema["fields"]))
            values = {
                field["name"]: dbf.Null
                if field["name"] in null_names
                else _coerce_value(source_record.get(field["name"]), field)
                for field in fields
            }
            table.append(values)
            if source_record.get("__deleted__"):
                dbf.delete(table[-1])
            if progress_callback is not None and (index == 1 or index % 10_000 == 0):
                progress_callback(index)
        table.close()
        table = None

        if partial_fpt.exists():
            _patch_fpt_block_types(partial, partial_fpt, schema, fields)
        _patch_dbf_metadata(partial, schema, list(schema["fields"]))
        if partial_fpt.exists():
            _patch_fpt_metadata(partial_fpt, schema)
        _validate_layout(partial, schema)
        _fsync_file(partial)
        if partial_fpt.exists():
            _fsync_file(partial_fpt)
            os.replace(partial_fpt, final_fpt)
        elif memo_required:
            raise ReconstructionError("Memo fields are present but the FPT file was not created.")
        elif final_fpt.exists() and overwrite:
            final_fpt.unlink()
        os.replace(partial, destination)
    except Exception:
        if table is not None:
            with suppress(Exception):
                table.close()
        partial.unlink(missing_ok=True)
        partial_fpt.unlink(missing_ok=True)
        raise

    return checksum, warnings


def output_hashes(destination: Path, schema: Mapping[str, Any]) -> tuple[str, str | None]:
    fpt = memo_output_path(destination, schema)
    return sha256_file(destination), sha256_file(fpt) if fpt.is_file() else None


def memo_output_path(destination: Path, schema: Mapping[str, Any]) -> Path:
    memo_name = schema.get("memo", {}).get("path")
    return destination.with_name(str(memo_name)) if memo_name else destination.with_suffix(".fpt")


def _field_spec(field: Mapping[str, Any]) -> str:
    name = str(field["name"])
    original_type = str(field["dbf_type"])
    dbf_type = TYPE_ALIASES.get(original_type, original_type)
    length = int(field.get("length") or 0)
    decimals = int(field.get("decimal_count") or 0)
    flags = int(field.get("flags") or 0)
    if dbf_type == "C":
        spec = f"{name} C({length})"
    elif dbf_type in {"N", "F"}:
        spec = f"{name} {dbf_type}({length},{decimals})"
    elif dbf_type in {"L", "D", "T", "M", "G", "P", "B", "I", "Y"}:
        spec = f"{name} {dbf_type}"
    else:
        raise ReconstructionError(f"Cannot build field {name!r} of type {original_type!r}.")
    if flags & 0x02:
        spec += " NULL"
    if flags & 0x04 and dbf_type in {"C", "M"}:
        spec += " BINARY"
    return spec


def _coerce_value(value: Any, field: Mapping[str, Any]) -> Any:
    if value is None:
        return None
    name = str(field["name"])
    dbf_type = str(field["dbf_type"])
    try:
        if dbf_type in {"C", "V"}:
            return str(value)
        if dbf_type in {"N", "F", "Y"}:
            return Decimal(str(value))
        if dbf_type in {"I", "+"}:
            return int(Decimal(str(value)))
        if dbf_type in {"B", "O"} and not field.get("is_memo"):
            return float(value)
        if dbf_type == "L":
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in {"true", "t", "yes", "y", "1"}:
                    return True
                if lowered in {"false", "f", "no", "n", "0"}:
                    return False
                if lowered in {"", "?", "null", "none"}:
                    return None
                raise ValueError(f"invalid logical value {value!r}")
            return bool(value)
        if dbf_type == "D":
            return (
                value.date()
                if isinstance(value, datetime)
                else (value if isinstance(value, date) else date.fromisoformat(str(value)))
            )
        if dbf_type in {"T", "@"}:
            return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        if field.get("is_binary") or dbf_type in {"G", "P"}:
            return (
                value if isinstance(value, bytes) else base64.b64decode(str(value), validate=True)
            )
        if dbf_type == "M":
            return str(value)
    except (ValueError, TypeError) as exc:
        raise ReconstructionError(
            f"Cannot convert field {name!r} ({dbf_type}) value {value!r}: {exc}"
        ) from exc
    raise ReconstructionError(f"Unsupported field {name!r} of type {dbf_type!r}.")


def _patch_dbf_metadata(
    path: Path,
    schema: Mapping[str, Any],
    fields: list[Mapping[str, Any]],
) -> None:
    dbf_info = schema.get("dbf", {})
    version = _hex_byte(dbf_info.get("version_byte"), default=0x30)
    language_driver = _hex_byte(dbf_info.get("language_driver"), default=0x03)
    last_update = dbf_info.get("last_update")
    with path.open("r+b") as outfile:
        header = bytearray(outfile.read(DBF_HEADER_SIZE))
        if len(header) != DBF_HEADER_SIZE:
            raise ReconstructionError("Reconstructed DBF header is truncated.")
        raw_header = dbf_info.get("header_base64")
        if raw_header:
            original = base64.b64decode(str(raw_header), validate=True)
            if len(original) == DBF_HEADER_SIZE:
                header[0:4] = original[0:4]
                header[12:32] = original[12:32]
        else:
            header[0] = version
            if last_update:
                parsed = date.fromisoformat(str(last_update))
                header[1] = parsed.year - 1900
                header[2:4] = bytes((parsed.month, parsed.day))
        header[14] = 0
        header[15] = 0
        header[28] = 0
        header[29] = language_driver
        outfile.seek(0)
        outfile.write(header)

        for index, field in enumerate(fields):
            descriptor_offset = DBF_HEADER_SIZE + index * FIELD_DESCRIPTOR_SIZE
            outfile.seek(descriptor_offset)
            descriptor = bytearray(outfile.read(FIELD_DESCRIPTOR_SIZE))
            if len(descriptor) != FIELD_DESCRIPTOR_SIZE:
                raise ReconstructionError(f"Field descriptor {index + 1} is truncated.")
            raw_descriptor = field.get("descriptor_base64")
            if raw_descriptor:
                original = base64.b64decode(str(raw_descriptor), validate=True)
                if len(original) == FIELD_DESCRIPTOR_SIZE:
                    descriptor[:] = original
            else:
                original_type = str(field["dbf_type"])
                descriptor[11] = ord(original_type)
                descriptor[18] = int(field.get("flags") or 0)
                descriptor[31] = int(field.get("index_field_flag") or 0)
            outfile.seek(descriptor_offset)
            outfile.write(descriptor)
        outfile.flush()
        os.fsync(outfile.fileno())


def _patch_fpt_metadata(path: Path, schema: Mapping[str, Any]) -> None:
    raw_header = schema.get("memo", {}).get("header_base64")
    if not raw_header:
        return
    original = base64.b64decode(str(raw_header), validate=True)
    if len(original) < 512:
        return
    with path.open("r+b") as outfile:
        generated = bytearray(outfile.read(512))
        if len(generated) < 512:
            raise ReconstructionError("Reconstructed FPT header is truncated.")
        generated[4:6] = original[4:6]
        generated[8:512] = original[8:512]
        outfile.seek(0)
        outfile.write(generated)
        outfile.flush()
        os.fsync(outfile.fileno())


def _patch_fpt_block_types(
    dbf_path: Path,
    fpt_path: Path,
    schema: Mapping[str, Any],
    fields: list[Mapping[str, Any]],
) -> None:
    binary_memos = [
        field
        for field in fields
        if field.get("is_memo") and (field.get("is_binary") or field.get("dbf_type") in {"G", "P"})
    ]
    if not binary_memos:
        return
    block_size = int(schema.get("memo", {}).get("block_size_bytes") or 64)
    with dbf_path.open("rb") as dbf_file, fpt_path.open("r+b") as fpt_file:
        header = dbf_file.read(DBF_HEADER_SIZE)
        record_count = struct.unpack_from("<I", header, 4)[0]
        header_length, record_length = struct.unpack_from("<HH", header, 8)
        for record_index in range(record_count):
            record_offset = header_length + record_index * record_length
            for field in binary_memos:
                dbf_file.seek(record_offset + int(field["address"]))
                pointer_data = dbf_file.read(4)
                if len(pointer_data) != 4:
                    raise ReconstructionError("Memo pointer is truncated.")
                block = struct.unpack("<I", pointer_data)[0]
                if block == 0:
                    continue
                dbf_type = str(field.get("dbf_type"))
                block_type = 2 if dbf_type == "G" else 0
                fpt_file.seek(block * block_size)
                fpt_file.write(struct.pack(">I", block_type))
        fpt_file.flush()
        os.fsync(fpt_file.fileno())


def _validate_layout(path: Path, schema: Mapping[str, Any]) -> None:
    with path.open("rb") as infile:
        header = infile.read(DBF_HEADER_SIZE)
    header_length, record_length = struct.unpack_from("<HH", header, 8)
    expected_header = schema.get("dbf", {}).get("header_length_bytes")
    expected_record = schema.get("dbf", {}).get("record_length_bytes")
    if expected_header is not None and header_length != int(expected_header):
        raise ReconstructionError(
            f"Header length mismatch: reconstructed {header_length}, schema {expected_header}."
        )
    if expected_record is not None and record_length != int(expected_record):
        raise ReconstructionError(
            f"Record length mismatch: reconstructed {record_length}, schema {expected_record}."
        )


def _hex_byte(value: Any, *, default: int) -> int:
    if value is None:
        return default
    return int(str(value), 16) if isinstance(value, str) else int(value)


def _fsync_file(path: Path) -> None:
    with path.open("rb+") as handle:
        os.fsync(handle.fileno())

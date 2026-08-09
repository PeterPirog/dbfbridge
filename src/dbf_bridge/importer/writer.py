from __future__ import annotations

import base64
import os
import shutil
import struct
from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from dbf_bridge.exporter.serialization import (
    BINARY_MEMO_FIELDS_KEY,
    RAW_RECORD_KEY,
    RAW_TEXT_FIELDS_KEY,
)
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
            "in the schema. The DBF structural-index flag is preserved for binary identity, but "
            "the companion CDX file is not reconstructed."
        )

    specs = "; ".join(_field_spec(field) for field in fields)
    codepage = _hex_byte(schema.get("dbf", {}).get("language_driver"), default=0x03)
    memo_size = int(schema.get("memo", {}).get("block_size_bytes") or 64)
    text_encodings = _text_encodings(schema)
    checksum = CanonicalChecksum(schema)
    memo_block_overrides: list[tuple[int, str, int]] = []
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
        _install_lossless_numeric_writer(table)
        for index, source_record in enumerate(records, start=1):
            checksum.update(source_record)
            null_names = nullable_null_fields(source_record, list(schema["fields"]))
            binary_memo_names = _binary_memo_fields(source_record)
            raw_text_fields = _raw_text_fields(source_record)
            values = {
                field["name"]: dbf.Null
                if field["name"] in null_names
                else _coerce_value(
                    source_record.get(field["name"]),
                    field,
                    text_encodings=text_encodings,
                    binary_memo=field["name"] in binary_memo_names,
                    raw_text=raw_text_fields.get(str(field["name"])),
                )
                for field in fields
            }
            table.append(values)
            memo_block_overrides.extend((index - 1, name, 0) for name in binary_memo_names)
            if source_record.get("__deleted__"):
                dbf.delete(table[-1])
            if progress_callback is not None and (index == 1 or index % 10_000 == 0):
                progress_callback(index)
        table.close()
        table = None

        if partial_fpt.exists():
            _patch_fpt_block_types(
                partial,
                partial_fpt,
                schema,
                fields,
                memo_block_overrides,
            )
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


def restore_raw_layout(
    destination: Path,
    records: Iterable[Mapping[str, Any]],
    schema: Mapping[str, Any],
) -> bool:
    """Restore exact DBF records and relocate generated memo blocks to original pointers.

    Current JSONL exports carry the source record image.  Logical values still
    drive validation and FPT generation; the raw image is applied only after
    every record can be matched safely.
    """

    with destination.open("rb") as source_dbf:
        header = source_dbf.read(DBF_HEADER_SIZE)
    record_count = struct.unpack_from("<I", header, 4)[0]
    header_length, record_length = struct.unpack_from("<HH", header, 8)
    expected_count = schema.get("dbf", {}).get("record_count_from_header")
    if expected_count is not None and int(expected_count) != record_count:
        return False

    dbf_partial = destination.with_name(f".{destination.name}.raw-layout.partial")
    dbf_partial.unlink(missing_ok=True)
    shutil.copyfile(destination, dbf_partial)

    memo_fields = [field for field in schema.get("fields", []) if field.get("is_memo")]
    fpt = memo_output_path(destination, schema)
    fpt_partial = fpt.with_name(f".{fpt.name}.raw-layout.partial")
    generated_fpt = fpt.open("rb") if memo_fields and fpt.is_file() else None
    relocated_fpt = None
    try:
        if generated_fpt is not None:
            fpt_partial.unlink(missing_ok=True)
            relocated_fpt = fpt_partial.open("w+b")
            source_size = int(schema.get("memo", {}).get("size_bytes") or 0)
            if source_size:
                relocated_fpt.truncate(source_size)
            raw_header = schema.get("memo", {}).get("header_base64")
            if raw_header:
                memo_header = base64.b64decode(str(raw_header), validate=True)
            else:
                memo_header = generated_fpt.read(512)
            relocated_fpt.seek(0)
            relocated_fpt.write(memo_header[:512])

        block_size = int(schema.get("memo", {}).get("block_size_bytes") or 64)
        seen_records = 0
        with dbf_partial.open("r+b") as rebuilt_dbf:
            for record_index, record in enumerate(records):
                encoded = record.get(RAW_RECORD_KEY)
                if not isinstance(encoded, str):
                    return False
                try:
                    raw_record = base64.b64decode(encoded, validate=True)
                except ValueError as exc:
                    raise ReconstructionError(
                        f"Record {record_index + 1} has invalid raw DBF metadata."
                    ) from exc
                if len(raw_record) != record_length:
                    raise ReconstructionError(
                        f"Record {record_index + 1} raw length {len(raw_record)} does not match "
                        f"DBF record length {record_length}."
                    )
                offset = header_length + record_index * record_length
                rebuilt_dbf.seek(offset)
                generated_record = rebuilt_dbf.read(record_length)
                if len(generated_record) != record_length:
                    raise ReconstructionError(
                        f"Reconstructed DBF record {record_index + 1} is truncated."
                    )
                if generated_fpt is not None and relocated_fpt is not None:
                    for field in memo_fields:
                        address = int(field["address"])
                        original_pointer = struct.unpack_from("<I", raw_record, address)[0]
                        generated_pointer = struct.unpack_from("<I", generated_record, address)[0]
                        _relocate_memo_block(
                            generated_fpt,
                            relocated_fpt,
                            generated_pointer,
                            original_pointer,
                            block_size,
                            record_index + 1,
                            str(field["name"]),
                        )
                rebuilt_dbf.seek(offset)
                rebuilt_dbf.write(raw_record)
                seen_records += 1
            if seen_records != record_count:
                raise ReconstructionError(
                    f"Raw record metadata count {seen_records} does not match DBF record count "
                    f"{record_count}."
                )
            rebuilt_dbf.flush()
            os.fsync(rebuilt_dbf.fileno())

        if relocated_fpt is not None:
            relocated_fpt.flush()
            os.fsync(relocated_fpt.fileno())
            relocated_fpt.close()
            relocated_fpt = None
            generated_fpt.close()
            generated_fpt = None
            os.replace(fpt_partial, fpt)
        os.replace(dbf_partial, destination)
        return True
    finally:
        if generated_fpt is not None:
            generated_fpt.close()
        if relocated_fpt is not None:
            relocated_fpt.close()
        dbf_partial.unlink(missing_ok=True)
        fpt_partial.unlink(missing_ok=True)


def _relocate_memo_block(
    source: Any,
    destination: Any,
    generated_pointer: int,
    original_pointer: int,
    block_size: int,
    record_number: int,
    field_name: str,
) -> None:
    if generated_pointer == 0 and original_pointer == 0:
        return
    if generated_pointer == 0 or original_pointer == 0:
        raise ReconstructionError(
            f"Memo pointer presence differs at record {record_number}, field {field_name!r}."
        )
    source.seek(generated_pointer * block_size)
    header = source.read(8)
    if len(header) != 8:
        raise ReconstructionError(
            f"Generated memo block is truncated at record {record_number}, field {field_name!r}."
        )
    payload_length = struct.unpack_from(">I", header, 4)[0]
    source.seek(generated_pointer * block_size)
    block = source.read(8 + payload_length)
    if len(block) != 8 + payload_length:
        raise ReconstructionError(
            f"Generated memo payload is truncated at record {record_number}, "
            f"field {field_name!r}."
        )
    destination.seek(original_pointer * block_size)
    destination.write(block)


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
    # Build every M field as binary internally.  VFP permits text and binary
    # memo blocks in the same field; after writing, the original descriptor
    # flags and the per-block content types are restored.
    if (flags & 0x04 and dbf_type in {"C", "M"}) or dbf_type in {"C", "M"}:
        spec += " BINARY"
    return spec


def _coerce_value(
    value: Any,
    field: Mapping[str, Any],
    *,
    text_encodings: list[str],
    binary_memo: bool,
    raw_text: bytes | None,
) -> Any:
    if value is None:
        return None
    name = str(field["name"])
    dbf_type = str(field["dbf_type"])
    try:
        if dbf_type in {"C", "V"}:
            return raw_text if raw_text is not None else _encode_text(str(value), text_encodings)
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
        if field.get("is_binary") or dbf_type in {"G", "P"} or binary_memo:
            return (
                value if isinstance(value, bytes) else base64.b64decode(str(value), validate=True)
            )
        if dbf_type == "M":
            return raw_text if raw_text is not None else _encode_text(str(value), text_encodings)
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
            generated_record_count = bytes(header[4:8])
            generated_layout = bytes(header[8:12])
            # New schemas contain the complete header region.  If the input
            # record count and layout are unchanged, restore it verbatim,
            # including VFP reserved bytes/backlink and the CDX flag.
            if (
                len(original) > DBF_HEADER_SIZE
                and len(original) == struct.unpack_from("<H", header, 8)[0]
                and original[4:8] == generated_record_count
                and original[8:12] == generated_layout
            ):
                outfile.seek(0)
                outfile.write(original)
                header[:] = original[:DBF_HEADER_SIZE]
            elif len(original) >= DBF_HEADER_SIZE:
                header[0:4] = original[0:4]
                header[12:32] = original[12:32]
        else:
            header[0] = version
            if last_update:
                parsed = date.fromisoformat(str(last_update))
                header[1] = parsed.year - 1900
                header[2:4] = bytes((parsed.month, parsed.day))
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
    block_overrides: list[tuple[int, str, int]],
) -> None:
    binary_memos = [
        field
        for field in fields
        if field.get("is_memo") and (field.get("is_binary") or field.get("dbf_type") in {"G", "P"})
    ]
    if not binary_memos and not block_overrides:
        return
    fields_by_name = {str(field["name"]): field for field in fields}
    overrides_by_record: dict[int, list[tuple[Mapping[str, Any], int]]] = {}
    for record_index, field_name, block_type in block_overrides:
        field = fields_by_name.get(field_name)
        if field is not None:
            overrides_by_record.setdefault(record_index, []).append((field, block_type))
    block_size = int(schema.get("memo", {}).get("block_size_bytes") or 64)
    with dbf_path.open("rb") as dbf_file, fpt_path.open("r+b") as fpt_file:
        header = dbf_file.read(DBF_HEADER_SIZE)
        record_count = struct.unpack_from("<I", header, 4)[0]
        header_length, record_length = struct.unpack_from("<HH", header, 8)
        for record_index in range(record_count):
            record_offset = header_length + record_index * record_length
            patches = [(field, 2 if field.get("dbf_type") == "G" else 0) for field in binary_memos]
            patches.extend(overrides_by_record.get(record_index, []))
            for field, block_type in patches:
                dbf_file.seek(record_offset + int(field["address"]))
                pointer_data = dbf_file.read(4)
                if len(pointer_data) != 4:
                    raise ReconstructionError("Memo pointer is truncated.")
                block = struct.unpack("<I", pointer_data)[0]
                if block == 0:
                    continue
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


def _binary_memo_fields(record: Mapping[str, Any]) -> set[str]:
    value = record.get(BINARY_MEMO_FIELDS_KEY)
    if not isinstance(value, list):
        return set()
    return {str(name) for name in value}


def _raw_text_fields(record: Mapping[str, Any]) -> dict[str, bytes]:
    value = record.get(RAW_TEXT_FIELDS_KEY)
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, bytes] = {}
    for name, encoded in value.items():
        try:
            result[str(name)] = base64.b64decode(str(encoded), validate=True)
        except ValueError as exc:
            raise ReconstructionError(f"Invalid raw text metadata for field {name!r}.") from exc
    return result


def _text_encodings(schema: Mapping[str, Any]) -> list[str]:
    text = schema.get("text_encoding", {})
    candidates = [
        text.get("declared_or_detected_encoding"),
        *(text.get("fallback_order") or []),
    ]
    return list(dict.fromkeys(str(item) for item in candidates if item)) or ["cp1250"]


def _encode_text(value: str, encodings: list[str]) -> bytes:
    errors: list[str] = []
    for encoding in encodings:
        try:
            return value.encode(encoding, errors="strict")
        except (UnicodeEncodeError, LookupError) as exc:
            errors.append(f"{encoding}: {exc}")
    raise ReconstructionError(
        "Text memo cannot be encoded with any encoding declared by the schema: " + "; ".join(errors)
    )


def _install_lossless_numeric_writer(table: Any) -> None:
    """Accept FoxPro numeric forms such as ``-.25`` in narrow N/F fields."""

    for field_type, definition in table._meta.fieldtypes.items():
        raw_type = getattr(field_type, "value", field_type)
        if raw_type not in {ord("N"), ord("F"), b"N", b"F"}:
            continue
        definition["Update"] = _update_numeric


def _update_numeric(value: Any, fielddef: Any, *_ignore: Any) -> bytes:
    length = int(fielddef[2])
    decimals = int(fielddef[4])
    if value is None:
        return b" " * length
    try:
        number = Decimal(str(value))
        quantum = Decimal(1).scaleb(-decimals)
        rendered = format(number.quantize(quantum), f".{decimals}f")
    except (InvalidOperation, ValueError) as exc:
        raise ReconstructionError(f"Invalid numeric value {value!r}: {exc}") from exc
    if len(rendered) > length and rendered.startswith("0."):
        rendered = rendered[1:]
    elif len(rendered) > length and rendered.startswith("-0."):
        rendered = "-." + rendered[3:]
    if len(rendered) > length:
        # FoxPro accepts scientific notation in narrow N/F fields.  Real VFP
        # tables use this for values such as 9,000,000,000 in F(6,1).
        for precision in range(decimals, -1, -1):
            scientific = format(number, f".{precision}E")
            if len(scientific) <= length:
                rendered = scientific
                break
    if len(rendered) > length:
        raise ReconstructionError(f"Numeric value {value!r} does not fit N/F({length},{decimals}).")
    return rendered.rjust(length).encode("ascii")


def _fsync_file(path: Path) -> None:
    with path.open("rb+") as handle:
        os.fsync(handle.fileno())

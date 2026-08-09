from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Iterable, Mapping
from itertools import zip_longest
from pathlib import Path
from typing import Any

from dbfread import DBF

from dbf_bridge.exporter.reader import LosslessFieldParser
from dbf_bridge.exporter.validation import sha256_file

from .checksum import CanonicalChecksum, canonical_record
from .models import ImportConfig, ReconstructionResult
from .readers import discover_inputs, iter_records, load_schema, schema_path_for
from .reporting import write_reconstruction_report
from .writer import memo_output_path, output_hashes, restore_raw_layout, write_dbf


def reconstruct_tree(
    config: ImportConfig,
    *,
    progress_callback: Callable[[int, int, str, int | None], None] | None = None,
) -> list[ReconstructionResult]:
    inputs = discover_inputs(config.source, config.format)
    results: list[ReconstructionResult] = []
    source_root = config.source if config.source.is_dir() else config.source.parent
    for index, data_path in enumerate(inputs, start=1):
        relative = data_path.relative_to(source_root)
        destination = config.output / relative.with_suffix(".dbf")
        schema_path = schema_path_for(data_path)
        started = time.monotonic()
        result = ReconstructionResult(
            source=relative.as_posix(),
            schema=schema_path.relative_to(source_root).as_posix()
            if schema_path.is_relative_to(source_root)
            else str(schema_path),
            output=destination.relative_to(config.output).as_posix(),
            status="FAILED",
            format=config.format,
        )
        try:
            if not schema_path.is_file():
                raise FileNotFoundError(f"Schema file is missing: {schema_path}")
            schema = load_schema(schema_path)
            schema_relative = Path(str(schema.get("relative_path") or relative.with_suffix(".dbf")))
            if schema_relative.is_absolute() or ".." in schema_relative.parts:
                raise ValueError(f"Unsafe relative_path in schema: {schema_relative}")
            destination = config.output / schema_relative
            result.output = destination.relative_to(config.output).as_posix()
            result.schema_sha256 = sha256_file(schema_path)
            if (
                config.memo == "inline"
                and schema.get("memo", {}).get("required")
                and not schema.get("memo", {}).get("values_in_data_output")
            ):
                raise ValueError(
                    "Schema reports that memo values were not included in the source data."
                )

            def progress(
                record_count: int,
                table_index: int = index,
                table_relative: Path = relative,
            ) -> None:
                if config.progress:
                    print(
                        f"\r[dbf-bridge-import] {table_index}/{len(inputs)} {table_relative}: "
                        f"{record_count:,} rekordów",
                        end="",
                        flush=True,
                    )
                if progress_callback is not None:
                    progress_callback(
                        table_index,
                        len(inputs),
                        table_relative.as_posix(),
                        record_count,
                    )

            input_checksum, warnings = write_dbf(
                destination,
                _apply_memo_policy(
                    iter_records(data_path, config.format, schema),
                    schema,
                    config.memo,
                ),
                schema,
                overwrite=config.overwrite,
                progress_callback=progress,
            )
            if config.progress:
                print()
            if config.format in {"json", "jsonl"}:
                result.raw_layout_restored = restore_raw_layout(
                    destination,
                    _apply_memo_policy(
                        iter_records(data_path, config.format, schema),
                        schema,
                        config.memo,
                    ),
                    schema,
                )
            reconstructed_checksum = checksum_dbf(destination, schema)
            result.record_count = input_checksum.record_count
            result.active_records = input_checksum.active_records
            result.deleted_records = input_checksum.deleted_records
            result.input_canonical_sha256 = input_checksum.hexdigest()
            result.reconstructed_canonical_sha256 = reconstructed_checksum.hexdigest()
            result.canonical_match = (
                result.input_canonical_sha256 == result.reconstructed_canonical_sha256
            )
            result.dbf_sha256, result.fpt_sha256 = output_hashes(destination, schema)
            result.expected_source_dbf_sha256 = schema.get("source", {}).get("sha256")
            result.raw_dbf_match = (
                result.dbf_sha256 == result.expected_source_dbf_sha256
                if result.expected_source_dbf_sha256
                else None
            )
            result.expected_source_fpt_sha256 = schema.get("memo", {}).get("sha256")
            result.raw_fpt_match = (
                result.fpt_sha256 == result.expected_source_fpt_sha256
                if result.expected_source_fpt_sha256
                else None
            )
            fpt = memo_output_path(destination, schema)
            result.fpt_output = fpt.relative_to(config.output).as_posix() if fpt.is_file() else None
            result.warnings.extend(warnings)
            if config.memo == "null" and schema.get("memo", {}).get("required"):
                result.warnings.append(
                    "Memo policy is 'null'; source memo values were intentionally not reconstructed."
                )
            if result.raw_dbf_match is False:
                result.warnings.append(
                    "Raw DBF SHA-256 differs from the original; inspect structural metadata, "
                    "deleted-record order, and index flags in the quality report."
                )
            if result.raw_fpt_match is False:
                result.warnings.append(
                    "Raw FPT SHA-256 differs from the original although memo content may still "
                    "match canonically."
                )
            if result.expected_source_dbf_sha256 is None:
                result.warnings.append(
                    "The schema has no source DBF SHA-256. Exact binary identity cannot be "
                    "verified; regenerate JSONL and schema with the current exporter."
                )
            if not result.canonical_match:
                result.differences = diagnose_reconstruction(
                    data_path,
                    config.format,
                    schema,
                    config.memo,
                    destination,
                )
                result.errors.append(
                    "Canonical checksum mismatch after reading the reconstructed DBF/FPT."
                )
            result.status = "FAILED" if result.errors else "WARNING" if result.warnings else "OK"
        except Exception as exc:
            if config.progress:
                print()
            result.errors.append(str(exc))
            result.status = "FAILED"
        result.elapsed_seconds = time.monotonic() - started
        results.append(result)
        if progress_callback is not None:
            progress_callback(index, len(inputs), relative.as_posix(), result.record_count)
    write_reconstruction_report(config.output / "reconstruction_report.jsonl", results)
    return results


def _apply_memo_policy(
    records: Iterable[Mapping[str, Any]],
    schema: Mapping[str, Any],
    memo_policy: str,
) -> Iterable[Mapping[str, Any]]:
    if memo_policy == "inline":
        return records
    memo_fields = set(schema.get("memo", {}).get("field_names") or [])

    def with_null_memos() -> Iterable[Mapping[str, Any]]:
        for record in records:
            updated = dict(record)
            for name in memo_fields:
                updated[name] = None
            yield updated

    return with_null_memos()


def checksum_dbf(path: Path, schema: dict[str, Any]) -> CanonicalChecksum:
    encoding = schema.get("text_encoding", {}).get("declared_or_detected_encoding") or "cp1250"
    checksum = CanonicalChecksum(schema)
    table = DBF(
        path,
        load=False,
        encoding=encoding,
        parserclass=LosslessFieldParser,
        char_decode_errors="strict",
    )
    for record in table.records:
        checksum.update(record)
    for record in table.deleted:
        deleted = dict(record)
        deleted["__deleted__"] = True
        checksum.update(deleted)
    return checksum


def iter_dbf_records(path: Path, schema: Mapping[str, Any]) -> Iterable[dict[str, Any]]:
    encoding = schema.get("text_encoding", {}).get("declared_or_detected_encoding") or "cp1250"
    table = DBF(
        path,
        load=False,
        encoding=encoding,
        parserclass=LosslessFieldParser,
        char_decode_errors="strict",
    )
    for record in table.records:
        yield dict(record)
    for record in table.deleted:
        deleted = dict(record)
        deleted["__deleted__"] = True
        yield deleted


def diagnose_reconstruction(
    data_path: Path,
    input_format: str,
    schema: Mapping[str, Any],
    memo_policy: str,
    reconstructed: Path,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return bounded record/field diagnostics for a canonical mismatch."""

    expected_records = _apply_memo_policy(
        iter_records(data_path, input_format, schema), schema, memo_policy
    )
    fields = list(schema.get("fields") or [])
    differences: list[dict[str, Any]] = []
    for record_number, pair in enumerate(
        zip_longest(expected_records, iter_dbf_records(reconstructed, schema)), start=1
    ):
        expected, actual = pair
        if expected is None or actual is None:
            differences.append(
                {
                    "scope": "record_count",
                    "record": record_number,
                    "expected_present": expected is not None,
                    "actual_present": actual is not None,
                }
            )
            break
        expected_deleted = bool(expected.get("__deleted__", False))
        actual_deleted = bool(actual.get("__deleted__", False))
        if expected_deleted != actual_deleted:
            differences.append(
                {
                    "scope": "deleted_marker",
                    "record": record_number,
                    "expected": expected_deleted,
                    "actual": actual_deleted,
                }
            )
        expected_values = canonical_record(expected, fields)
        actual_values = canonical_record(actual, fields)
        for name in expected_values:
            if expected_values[name] == actual_values.get(name):
                continue
            differences.append(
                {
                    "scope": "field",
                    "record": record_number,
                    "field": name,
                    "expected": _diagnostic_value(expected_values[name]),
                    "actual": _diagnostic_value(actual_values.get(name)),
                }
            )
            if len(differences) >= limit:
                return differences
        if len(differences) >= limit:
            return differences
    return differences


def _diagnostic_value(value: Any) -> dict[str, Any]:
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    preview = str(value)
    if len(preview) > 160:
        preview = f"{preview[:157]}..."
    return {
        "type": type(value).__name__,
        "length": len(value) if isinstance(value, (str, list, dict)) else None,
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "preview": preview,
    }

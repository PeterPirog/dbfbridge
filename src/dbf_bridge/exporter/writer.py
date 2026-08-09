from __future__ import annotations

import base64
import csv
import json
import os
from pathlib import Path
from typing import Any, TextIO

from dbfread import MissingMemoFile

from .discovery import output_data_path, output_schema_path
from .models import DiscoveredTable, ExportConfig, FieldMetadata, StreamStats, TableResult
from .reader import UnsupportedTableError, iter_physical_records, open_table, read_table_metadata
from .serialization import RAW_RECORD_KEY, SerializationError, serialize_record
from .validation import StatsCollector, ValidationResult, sha256_file, validate_output


class OutputExistsError(FileExistsError):
    """Raised when an export would overwrite a final output."""


class AtomicTextWriter:
    def __init__(self, final_path: Path, *, overwrite: bool) -> None:
        self.final_path = final_path
        self.partial_path = partial_path(final_path)
        self.overwrite = overwrite
        self.handle: TextIO | None = None

    def __enter__(self) -> AtomicTextWriter:
        ensure_can_write_final(self.final_path, overwrite=self.overwrite)
        self.final_path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.partial_path.open("w", encoding="utf-8", newline="\n")
        return self

    def write(self, text: str) -> None:
        if self.handle is None:
            raise RuntimeError("AtomicTextWriter is not open.")
        self.handle.write(text)

    def flush_and_fsync(self) -> None:
        if self.handle is None:
            raise RuntimeError("AtomicTextWriter is not open.")
        self.handle.flush()
        os.fsync(self.handle.fileno())

    def finalize(self) -> None:
        if self.handle is not None and not self.handle.closed:
            self.flush_and_fsync()
            self.handle.close()
        os.replace(self.partial_path, self.final_path)

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        if self.handle is not None and not self.handle.closed:
            self.handle.close()
        return False


def partial_path(final_path: Path) -> Path:
    return final_path.with_name(f"{final_path.name}.partial")


def ensure_can_write_final(final_path: Path, *, overwrite: bool) -> None:
    if final_path.exists() and not overwrite:
        raise OutputExistsError(f"Refusing to overwrite existing output: {final_path}")


def export_table(discovered: DiscoveredTable, config: ExportConfig) -> TableResult:
    table_report_path = discovered.relative_path.as_posix()
    intended_data_path = output_data_path(config.output, discovered.relative_path, config.format)
    intended_output = intended_data_path.relative_to(config.output).as_posix()
    warnings: list[str] = []
    errors: list[str] = []
    metadata = None

    try:
        metadata = read_table_metadata(discovered, config)
        warnings.extend(metadata.warnings)
    except UnsupportedTableError as exc:
        return TableResult(
            table=table_report_path,
            output=intended_output,
            status="UNSUPPORTED",
            encoding=config.encoding,
            format=config.format,
            warnings=warnings,
            errors=[f"Unsupported table {table_report_path}: {exc}"],
        )
    except MissingMemoFile as exc:
        return TableResult(
            table=table_report_path,
            output=intended_output,
            status="FAILED",
            encoding=config.encoding,
            format=config.format,
            errors=[f"Table {table_report_path}: {exc}"],
        )
    except Exception as exc:
        return TableResult(
            table=table_report_path,
            output=intended_output,
            status="FAILED",
            encoding=config.encoding,
            format=config.format,
            errors=[f"Table {table_report_path}: preflight failed: {exc}"],
        )

    data_path = intended_data_path
    schema_path = output_schema_path(config.output, discovered.relative_path)
    deleted_path = deleted_output_path(data_path) if config.deleted == "separate" else None

    try:
        ensure_can_write_final(data_path, overwrite=config.overwrite)
        ensure_can_write_final(schema_path, overwrite=config.overwrite)
        if deleted_path is not None:
            ensure_can_write_final(deleted_path, overwrite=config.overwrite)
    except OutputExistsError as exc:
        return _failed_result(
            metadata,
            data_path,
            warnings,
            [str(exc)],
            output=intended_output,
        )

    schema_writer: AtomicTextWriter | None = None
    data_writer: AtomicTextWriter | None = None
    deleted_writer: AtomicTextWriter | None = None
    stats = StreamStats()
    deleted_stats = StreamStats()
    data_collector: StatsCollector | None = None
    deleted_collector: StatsCollector | None = None

    try:
        schema_writer = AtomicTextWriter(schema_path, overwrite=config.overwrite)
        with schema_writer:
            schema_writer.write(
                json.dumps(
                    metadata.to_schema(),
                    ensure_ascii=False,
                    allow_nan=False,
                    indent=2,
                )
            )
            schema_writer.write("\n")
            schema_writer.flush_and_fsync()

        table = open_table(discovered.source_path, config)
        data_collector = StatsCollector(metadata.fields)
        stats = data_collector.stats
        with AtomicTextWriter(data_path, overwrite=config.overwrite) as data_writer:
            if config.format == "csv":
                _write_csv_header(
                    data_writer, metadata.fields, include_deleted=config.deleted == "include"
                )
            elif config.format == "json":
                data_writer.write("[\n")
            json_first = True
            deleted_collector = StatsCollector(metadata.fields)
            deleted_stats = deleted_collector.stats
            if config.deleted == "include":
                for index, (record, is_deleted, raw_record) in enumerate(
                    iter_physical_records(table), start=1
                ):
                    serialized = _serialize_context_record(
                        record,
                        metadata.fields,
                        table_report_path,
                        index,
                        deleted_marker=is_deleted,
                        deleted=is_deleted,
                        memo_policy=config.memo,
                        strip_spaces=config.strip_spaces,
                    )
                    serialized[RAW_RECORD_KEY] = base64.b64encode(raw_record).decode("ascii")
                    json_first = not _write_record(
                        data_writer, serialized, metadata.fields, config, json_first=json_first
                    )
                    data_collector.add(serialized)
                    if is_deleted:
                        deleted_collector.add(serialized)
            else:
                active_index = 0
                for record, is_deleted, raw_record in iter_physical_records(table):
                    if is_deleted:
                        continue
                    active_index += 1
                    serialized = _serialize_context_record(
                        record,
                        metadata.fields,
                        table_report_path,
                        active_index,
                        deleted_marker=None,
                        memo_policy=config.memo,
                        strip_spaces=config.strip_spaces,
                    )
                    serialized[RAW_RECORD_KEY] = base64.b64encode(raw_record).decode("ascii")
                    json_first = not _write_record(
                        data_writer, serialized, metadata.fields, config, json_first=json_first
                    )
                    data_collector.add(serialized)

            if config.deleted == "separate" and deleted_path is not None:
                with AtomicTextWriter(deleted_path, overwrite=config.overwrite) as deleted_writer:
                    if config.format == "csv":
                        _write_csv_header(deleted_writer, metadata.fields, include_deleted=True)
                    elif config.format == "json":
                        deleted_writer.write("[\n")
                    deleted_json_first = True
                    for index, record in _iter_records_with_context(
                        table.deleted,
                        table_report_path,
                        deleted=True,
                    ):
                        serialized = _serialize_context_record(
                            record,
                            metadata.fields,
                            table_report_path,
                            index,
                            deleted_marker=True,
                            deleted=True,
                            memo_policy=config.memo,
                            strip_spaces=config.strip_spaces,
                        )
                        deleted_json_first = not _write_record(
                            deleted_writer,
                            serialized,
                            metadata.fields,
                            config,
                            json_first=deleted_json_first,
                        )
                        deleted_collector.add(serialized)
                    if config.format == "json":
                        deleted_writer.write("\n]\n")
                    deleted_stats = deleted_collector.finish()
                    deleted_writer.flush_and_fsync()
            elif config.deleted == "skip":
                deleted_collector.stats.record_count = len(table.deleted)

            if config.format == "json":
                data_writer.write("\n]\n")
            stats = data_collector.finish()
            if config.deleted != "separate":
                deleted_stats = deleted_collector.finish()
            data_writer.flush_and_fsync()

        validation_errors: list[str] = []
        if config.validate:
            validation = validate_output(
                partial_path(data_path), config.format, metadata.fields, stats
            )
            validation_errors.extend(validation.errors)
        else:
            validation = _file_result_without_reparse(partial_path(data_path), stats)
        if deleted_path is not None:
            if config.validate:
                deleted_validation = validate_output(
                    partial_path(deleted_path),
                    config.format,
                    metadata.fields,
                    deleted_stats,
                )
                validation_errors.extend(
                    f"Deleted output: {error}" for error in deleted_validation.errors
                )
            else:
                _file_result_without_reparse(partial_path(deleted_path), deleted_stats)

        if config.validate and validation_errors:
            return _failed_result(
                metadata,
                data_path,
                warnings,
                validation_errors,
                stats,
                deleted_stats,
                output=intended_output,
            )

        schema_writer.finalize()
        if deleted_writer is not None:
            deleted_writer.finalize()
        data_writer.finalize()

        memo_hashes = _memo_hash_report(stats.memo_hashes, validation.memo_hashes)
        result_warnings = warnings[:]
        status = "WARNING" if result_warnings else "OK"
        return TableResult(
            table=table_report_path,
            output=data_path.relative_to(config.output).as_posix(),
            schema=schema_path.relative_to(config.output).as_posix(),
            status=status,
            encoding=metadata.encoding,
            format=config.format,
            active_records=stats.record_count
            if config.deleted != "include"
            else stats.record_count - deleted_stats.record_count,
            deleted_records=deleted_stats.record_count,
            memo_fields=metadata.memo_fields,
            null_counts=stats.null_counts,
            empty_string_counts=stats.empty_string_counts,
            memo_hashes=memo_hashes,
            sha256=validation.sha256,
            size_bytes=validation.size_bytes,
            schema_sha256=sha256_file(schema_path),
            deleted_output=deleted_path.relative_to(config.output).as_posix()
            if deleted_path is not None
            else None,
            deleted_sha256=sha256_file(deleted_path)
            if deleted_path is not None and deleted_path.is_file()
            else None,
            engine="dbfread-streaming",
            warnings=result_warnings,
            errors=[],
        )
    except Exception as exc:
        if data_collector is not None:
            stats = data_collector.finish()
        if deleted_collector is not None:
            deleted_stats = deleted_collector.finish()
        error = f"Table {table_report_path}: export failed: {exc}"
        return _failed_result(
            metadata,
            data_path,
            warnings,
            errors + [error],
            stats,
            deleted_stats,
            output=intended_output,
        )


def deleted_output_path(data_path: Path) -> Path:
    return data_path.with_name(f"{data_path.stem}.deleted{data_path.suffix}")


def _iter_records_with_context(records: object, table: str, *, deleted: bool) -> object:
    iterator = iter(records)
    index = 1
    kind = "deleted record" if deleted else "record"
    while True:
        try:
            record = next(iterator)
        except StopIteration:
            return
        except Exception as exc:
            raise RuntimeError(f"{table} {kind} {index}: {exc}") from exc
        yield index, record
        index += 1


def _serialize_context_record(
    record: dict[str, Any],
    fields: list[FieldMetadata],
    table: str,
    index: int,
    *,
    deleted_marker: bool | None,
    deleted: bool = False,
    memo_policy: str = "inline",
    strip_spaces: bool = False,
) -> dict[str, Any]:
    try:
        return serialize_record(
            record,
            fields,
            deleted_marker=deleted_marker,
            memo_policy=memo_policy,
            strip_spaces=strip_spaces,
        )
    except SerializationError as exc:
        kind = "deleted record" if deleted else "record"
        raise SerializationError(f"{table} {kind} {index}: {exc}") from exc


def _write_record(
    writer: AtomicTextWriter,
    record: dict[str, Any],
    fields: list[FieldMetadata],
    config: ExportConfig,
    *,
    json_first: bool = True,
) -> bool:
    """Zapisuje rekord. Zwraca True jeśli był to pierwszy rekord (json).

    Dla json: pisze przecinek (jeśli nie pierwszy) + rekord.
    Dla jsonl: rekord + nowa linia.
    Dla csv: wiersz csv.
    """
    if config.format == "jsonl":
        writer.write(json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
        writer.write("\n")
        return False
    if config.format == "json":
        text = json.dumps(
            record,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        if not json_first:
            writer.write(",\n")
        writer.write(text)
        return True
    # csv
    names = [field.name for field in fields]
    if "__deleted__" in record:
        names.append("__deleted__")
    row = [json.dumps(record.get(name), ensure_ascii=False, allow_nan=False) for name in names]
    csv_writer = csv.writer(_WriterAdapter(writer), lineterminator="\n")
    csv_writer.writerow(row)
    return False


def _write_csv_header(
    writer: AtomicTextWriter,
    fields: list[FieldMetadata],
    *,
    include_deleted: bool,
) -> None:
    names = [field.name for field in fields]
    if include_deleted:
        names.append("__deleted__")
    csv_writer = csv.writer(_WriterAdapter(writer), lineterminator="\n")
    csv_writer.writerow(names)


class _WriterAdapter:
    def __init__(self, writer: AtomicTextWriter) -> None:
        self.writer = writer

    def write(self, text: str) -> int:
        self.writer.write(text)
        return len(text)


def _failed_result(
    metadata: object,
    data_path: Path,
    warnings: list[str],
    errors: list[str],
    stats: StreamStats | None = None,
    deleted_stats: StreamStats | None = None,
    output: str | None = None,
) -> TableResult:
    stats = stats or StreamStats()
    deleted_stats = deleted_stats or StreamStats()
    if hasattr(metadata, "relative_path"):
        table = metadata.relative_path.as_posix()
        encoding = metadata.encoding
        memo_fields = metadata.memo_fields
    else:
        table = data_path.name
        encoding = None
        memo_fields = []
    return TableResult(
        table=table,
        output=output or data_path.name,
        status="FAILED",
        encoding=encoding,
        active_records=stats.record_count,
        deleted_records=deleted_stats.record_count,
        memo_fields=memo_fields,
        null_counts=stats.null_counts,
        empty_string_counts=stats.empty_string_counts,
        memo_hashes={},
        warnings=warnings,
        errors=errors,
    )


def _memo_hash_report(
    source_hashes: dict[str, str],
    output_hashes: dict[str, str],
) -> dict[str, dict[str, Any]]:
    return {
        name: {
            "source": source_hash,
            "output": output_hashes.get(name),
            "match": source_hash == output_hashes.get(name),
        }
        for name, source_hash in source_hashes.items()
    }


def _file_result_without_reparse(path: Path, stats: StreamStats) -> ValidationResult:
    return ValidationResult(
        record_count=stats.record_count,
        null_counts=stats.null_counts.copy(),
        empty_string_counts=stats.empty_string_counts.copy(),
        memo_hashes=stats.memo_hashes.copy(),
        sha256=sha256_file(path),
        size_bytes=path.stat().st_size,
    )

from __future__ import annotations

import csv
import json
import os
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, TextIO

try:
    import orjson
except ImportError:
    orjson = None

IO_BUFFER_SIZE = 16 * 1024 * 1024
PROGRESS_INTERVAL_BYTES = 8 * 1024 * 1024
CANCEL_CHECK_RECORDS = 4096
EXCEL_MAX_ROWS = 1_048_576
EXCEL_MAX_COLUMNS = 16_384
EXCEL_MAX_STRING_LENGTH = 32_767


class JsonlConversionError(ValueError):
    """Raised when a JSONL input cannot be converted safely."""


class ConversionCancelled(RuntimeError):
    """Raised when a conversion is cancelled by its callback."""


class MissingConversionDependency(RuntimeError):
    """Raised when an output dependency is unavailable."""


@dataclass(frozen=True)
class ConversionProgress:
    processed_bytes: int
    total_bytes: int
    records: int
    elapsed_seconds: float

    @property
    def percent(self) -> float:
        if self.total_bytes == 0:
            return 100.0
        return min(100.0, self.processed_bytes / self.total_bytes * 100.0)

    @property
    def bytes_per_second(self) -> float:
        if self.elapsed_seconds == 0:
            return 0.0
        return self.processed_bytes / self.elapsed_seconds


@dataclass(frozen=True)
class ConversionStats:
    source: Path
    destination: Path
    input_size: int
    output_size: int
    record_count: int
    skipped_invalid: int
    elapsed_seconds: float
    engine: str
    sheet_count: int = 0
    overflow_value_count: int = 0
    overflow_chunk_count: int = 0
    overflow_sheet_count: int = 0

    @property
    def megabytes_per_second(self) -> float:
        if self.elapsed_seconds == 0:
            return 0.0
        return self.input_size / (1024 * 1024) / self.elapsed_seconds


@dataclass
class _ReadState:
    records: int = 0
    skipped_invalid: int = 0
    processed_bytes: int = 0


@dataclass
class _Inspection:
    columns: list[str] = field(default_factory=list)
    kinds: dict[str, set[str]] = field(default_factory=dict)
    nested: bool = False
    unsupported_integer: bool = False
    records: int = 0
    skipped_invalid: int = 0


@dataclass
class _XlsxOverflowWriter:
    """Stream values that do not fit in an Excel cell to companion sheets."""

    workbook: Any
    max_rows_per_sheet: int = EXCEL_MAX_ROWS
    worksheet: Any = None
    row_index: int = EXCEL_MAX_ROWS
    sheet_count: int = 0
    value_count: int = 0
    chunk_count: int = 0

    def write(
        self,
        text: str,
        *,
        data_sheet: str,
        data_row: int,
        source_line: int,
        column: str,
        value_type: str,
    ) -> str:
        self.value_count += 1
        overflow_id = self.value_count
        part_count = _excel_chunk_count(text)
        for part, chunk in enumerate(_iter_excel_chunks(text), start=1):
            self._ensure_row()
            values = (
                overflow_id,
                data_sheet,
                data_row,
                source_line,
                column,
                value_type,
                part,
                part_count,
                chunk,
            )
            for column_index, value in enumerate(values):
                if isinstance(value, int):
                    self.worksheet.write_number(self.row_index, column_index, value)
                else:
                    self.worksheet.write_string(self.row_index, column_index, value)
            self.row_index += 1
            self.chunk_count += 1
        return f"[[DBFBRIDGE_OVERFLOW:{overflow_id}]]"

    def _ensure_row(self) -> None:
        if self.row_index < self.max_rows_per_sheet:
            return
        self.sheet_count += 1
        self.worksheet = self.workbook.add_worksheet(f"Dlugie_teksty_{self.sheet_count}")
        headers = (
            "overflow_id",
            "data_sheet",
            "data_row",
            "source_line",
            "column",
            "value_type",
            "part",
            "parts",
            "text",
        )
        for column_index, name in enumerate(headers):
            self.worksheet.write_string(0, column_index, name)
        self.row_index = 1


class _ProgressReporter:
    def __init__(
        self,
        callback: Callable[[ConversionProgress], None] | None,
        total_bytes: int,
    ) -> None:
        self.callback = callback
        self.total_bytes = total_bytes
        self.processed_bytes = 0
        self.records = 0
        self.started = time.monotonic()
        self.last_reported_bytes = 0

    def add(self, byte_count: int, *, records: int = 0, force: bool = False) -> None:
        self.processed_bytes += byte_count
        self.records += records
        if self.callback is None:
            return
        if not force and self.processed_bytes - self.last_reported_bytes < PROGRESS_INTERVAL_BYTES:
            return
        self.last_reported_bytes = self.processed_bytes
        self.callback(
            ConversionProgress(
                processed_bytes=min(self.processed_bytes, self.total_bytes),
                total_bytes=self.total_bytes,
                records=self.records,
                elapsed_seconds=time.monotonic() - self.started,
            )
        )


def jsonl_to_json(
    source: str | Path,
    destination: str | Path,
    *,
    strict: bool = True,
    overwrite: bool = True,
    buffer_size: int = IO_BUFFER_SIZE,
    progress_callback: Callable[[ConversionProgress], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
) -> ConversionStats:
    """Convert JSONL to a JSON array without materializing or reserializing records."""
    source_path, destination_path, partial = _prepare_paths(source, destination, overwrite)
    input_size = source_path.stat().st_size
    reporter = _ProgressReporter(progress_callback, input_size)
    state = _ReadState()
    started = time.monotonic()

    try:
        with source_path.open("rb", buffering=buffer_size) as infile, partial.open(
            "wb", buffering=buffer_size
        ) as outfile:
            outfile.write(b"[")
            first = True
            for _, raw, _ in _iter_jsonl_records(
                infile,
                strict=strict,
                state=state,
                reporter=reporter,
                cancel_callback=cancel_callback,
            ):
                outfile.write(b"\n" if first else b",\n")
                outfile.write(raw)
                first = False
            outfile.write(b"]\n" if first else b"\n]\n")
            _flush_and_fsync(outfile)
        _commit_partial(partial, destination_path)
    except Exception:
        partial.unlink(missing_ok=True)
        raise

    reporter.add(0, force=True)
    return _stats(
        source_path,
        destination_path,
        input_size,
        state,
        started,
        engine="binary-stream",
    )


def jsonl_to_csv(
    source: str | Path,
    destination: str | Path,
    *,
    columns: Sequence[str] | None = None,
    schema_types: Mapping[str, str] | None = None,
    expected_record_count: int | None = None,
    source_is_validated: bool = False,
    null_columns: Sequence[str] = (),
    separator: str = ",",
    include_bom: bool = False,
    infer_schema_length: int = 10_000,
    strict: bool = True,
    flatten: bool = False,
    overwrite: bool = True,
    progress_callback: Callable[[ConversionProgress], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
) -> ConversionStats:
    """Convert JSONL to CSV with bounded memory and a Polars streaming fast path."""
    if len(separator) != 1:
        raise ValueError("CSV separator must be exactly one character.")
    if infer_schema_length < 1:
        raise ValueError("infer_schema_length must be positive.")
    if source_is_validated and (schema_types is None or expected_record_count is None):
        raise ValueError(
            "source_is_validated requires schema_types and expected_record_count."
        )

    source_path, destination_path, partial = _prepare_paths(source, destination, overwrite)
    input_size = source_path.stat().st_size
    known_schema = source_is_validated and schema_types is not None
    reporter = _ProgressReporter(progress_callback, input_size * (1 if known_schema else 2))
    started = time.monotonic()
    if known_schema:
        inspection = _Inspection(
            columns=list(schema_types),
            kinds=_schema_kinds(schema_types),
            records=expected_record_count or 0,
        )
    else:
        inspection = _inspect_jsonl(
            source_path,
            strict=strict,
            flatten=flatten,
            reporter=reporter,
            cancel_callback=cancel_callback,
        )
    selected_columns = _merge_columns(columns, inspection.columns)
    null_names = set(null_columns)
    state = _ReadState(records=inspection.records, skipped_invalid=inspection.skipped_invalid)
    engine = "python-stream"

    use_polars = (
        not flatten
        and not inspection.nested
        and not inspection.unsupported_integer
        and inspection.skipped_invalid == 0
        and cancel_callback is None
        and progress_callback is None
        and _polars_schema_compatible(inspection.kinds)
    )

    try:
        if use_polars and inspection.records:
            try:
                _write_csv_polars(
                    source_path,
                    partial,
                    selected_columns,
                    null_names,
                    separator=separator,
                    include_bom=include_bom,
                    infer_schema_length=infer_schema_length,
                    kinds=inspection.kinds,
                )
                engine = "polars-streaming"
                reporter.add(input_size, force=True)
            except ImportError:
                state = _write_csv_python(
                    source_path,
                    partial,
                    selected_columns,
                    null_names,
                    separator=separator,
                    include_bom=include_bom,
                    strict=strict,
                    flatten=flatten,
                    reporter=reporter,
                    cancel_callback=cancel_callback,
                )
        else:
            state = _write_csv_python(
                source_path,
                partial,
                selected_columns,
                null_names,
                separator=separator,
                include_bom=include_bom,
                strict=strict,
                flatten=flatten,
                reporter=reporter,
                cancel_callback=cancel_callback,
            )
        _commit_partial(partial, destination_path)
    except Exception:
        partial.unlink(missing_ok=True)
        raise

    return _stats(
        source_path,
        destination_path,
        input_size,
        state,
        started,
        engine=engine,
    )


def jsonl_to_xlsx(
    source: str | Path,
    destination: str | Path,
    *,
    columns: Sequence[str] | None = None,
    strict: bool = True,
    flatten: bool = False,
    overwrite: bool = True,
    max_rows_per_sheet: int = EXCEL_MAX_ROWS,
    long_text_policy: str = "overflow",
    progress_callback: Callable[[ConversionProgress], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
) -> ConversionStats:
    """Convert JSONL to XLSX row by row using XlsxWriter constant-memory mode.

    Values exceeding Excel's cell limit are losslessly split across companion
    ``Dlugie_teksty_*`` sheets by default. The data cell contains a stable
    overflow marker and the companion rows contain reconstruction metadata.
    """
    if not 2 <= max_rows_per_sheet <= EXCEL_MAX_ROWS:
        raise ValueError(f"max_rows_per_sheet must be between 2 and {EXCEL_MAX_ROWS}.")
    if long_text_policy not in {"overflow", "error"}:
        raise ValueError("long_text_policy must be 'overflow' or 'error'.")

    try:
        import xlsxwriter
    except ImportError as exc:
        raise MissingConversionDependency(
            "XLSX conversion requires XlsxWriter. Reinstall dbfbridge or run: "
            'pip install "xlsxwriter>=3.2"'
        ) from exc

    source_path, destination_path, partial = _prepare_paths(source, destination, overwrite)
    input_size = source_path.stat().st_size
    unknown_columns = columns is None
    reporter = _ProgressReporter(progress_callback, input_size * (2 if unknown_columns else 1))
    started = time.monotonic()
    inspection = None
    if unknown_columns:
        inspection = _inspect_jsonl(
            source_path,
            strict=strict,
            flatten=flatten,
            reporter=reporter,
            cancel_callback=cancel_callback,
        )
        selected_columns = inspection.columns
    else:
        selected_columns = list(dict.fromkeys(columns or ()))

    if len(selected_columns) > EXCEL_MAX_COLUMNS:
        raise JsonlConversionError(
            f"XLSX supports at most {EXCEL_MAX_COLUMNS} columns; got {len(selected_columns)}."
        )

    workbook = None
    state = _ReadState()
    sheet_count = 1
    try:
        workbook = xlsxwriter.Workbook(
            partial,
            {
                "constant_memory": True,
                "strings_to_formulas": False,
                "strings_to_urls": False,
            },
        )
        blank_row_format = workbook.add_format()
        overflow = _XlsxOverflowWriter(workbook)
        worksheet = _new_worksheet(workbook, sheet_count, selected_columns)
        row_index = 1
        with source_path.open("rb", buffering=IO_BUFFER_SIZE) as infile:
            for line_number, _, record in _iter_jsonl_records(
                infile,
                strict=strict,
                state=state,
                reporter=reporter,
                cancel_callback=cancel_callback,
            ):
                normalized = _flatten_record(record) if flatten else record
                if row_index >= max_rows_per_sheet:
                    sheet_count += 1
                    worksheet = _new_worksheet(workbook, sheet_count, selected_columns)
                    row_index = 1
                _write_xlsx_row(
                    worksheet,
                    row_index,
                    line_number,
                    normalized,
                    selected_columns,
                    blank_row_format,
                    overflow,
                    long_text_policy,
                )
                row_index += 1
        workbook.close()
        workbook = None
        _commit_partial(partial, destination_path)
    except Exception:
        if workbook is not None:
            with suppress(Exception):
                workbook.close()
        partial.unlink(missing_ok=True)
        raise

    reporter.add(0, force=True)
    skipped = inspection.skipped_invalid if inspection is not None else state.skipped_invalid
    final_state = _ReadState(records=state.records, skipped_invalid=skipped)
    return _stats(
        source_path,
        destination_path,
        input_size,
        final_state,
        started,
        engine="xlsxwriter-constant-memory",
        sheet_count=sheet_count,
        overflow_value_count=overflow.value_count,
        overflow_chunk_count=overflow.chunk_count,
        overflow_sheet_count=overflow.sheet_count,
    )


def _prepare_paths(
    source: str | Path,
    destination: str | Path,
    overwrite: bool,
) -> tuple[Path, Path, Path]:
    source_path = Path(source)
    destination_path = Path(destination)
    if not source_path.is_file():
        raise FileNotFoundError(f"JSONL source does not exist: {source_path}")
    if source_path.resolve() == destination_path.resolve():
        raise ValueError("Source and destination must be different files.")
    if destination_path.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite existing output: {destination_path}")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    partial = destination_path.with_name(f"{destination_path.name}.partial")
    partial.unlink(missing_ok=True)
    return source_path, destination_path, partial


def _iter_jsonl_records(
    infile: BinaryIO,
    *,
    strict: bool,
    state: _ReadState,
    reporter: _ProgressReporter,
    cancel_callback: Callable[[], bool] | None,
) -> Iterator[tuple[int, bytes, dict[str, Any]]]:
    bytes_since_cancel_check = 0
    for line_number, line in enumerate(infile, start=1):
        state.processed_bytes += len(line)
        bytes_since_cancel_check += len(line)
        raw = line.strip()
        if not raw:
            reporter.add(len(line))
            continue
        try:
            record = _loads(raw)
        except Exception as exc:
            state.skipped_invalid += 1
            reporter.add(len(line))
            if strict:
                raise JsonlConversionError(
                    f"Invalid JSONL at line {line_number}: malformed JSON."
                ) from exc
            continue
        if not isinstance(record, dict):
            state.skipped_invalid += 1
            reporter.add(len(line))
            if strict:
                raise JsonlConversionError(
                    f"Invalid JSONL at line {line_number}: expected a JSON object."
                )
            continue
        state.records += 1
        reporter.add(len(line), records=1)
        if cancel_callback is not None and (
            state.records % CANCEL_CHECK_RECORDS == 0
            or bytes_since_cancel_check >= PROGRESS_INTERVAL_BYTES
        ):
            bytes_since_cancel_check = 0
            if cancel_callback():
                raise ConversionCancelled(f"Conversion cancelled after line {line_number}.")
        yield line_number, raw, record
    if cancel_callback is not None and cancel_callback():
        raise ConversionCancelled("Conversion cancelled after reading the input.")


def _loads(raw: bytes) -> Any:
    if orjson is not None:
        return orjson.loads(raw)
    return json.loads(raw)


def _inspect_jsonl(
    source: Path,
    *,
    strict: bool,
    flatten: bool,
    reporter: _ProgressReporter,
    cancel_callback: Callable[[], bool] | None,
) -> _Inspection:
    inspection = _Inspection()
    state = _ReadState()
    known: set[str] = set()
    with source.open("rb", buffering=IO_BUFFER_SIZE) as infile:
        for _, _, record in _iter_jsonl_records(
            infile,
            strict=strict,
            state=state,
            reporter=reporter,
            cancel_callback=cancel_callback,
        ):
            normalized = _flatten_record(record) if flatten else record
            for name, value in normalized.items():
                if name not in known:
                    known.add(name)
                    inspection.columns.append(name)
                    inspection.kinds[name] = set()
                inspection.kinds[name].add(_value_kind(value))
                if isinstance(value, (dict, list)):
                    inspection.nested = True
                if (
                    isinstance(value, int)
                    and not isinstance(value, bool)
                    and (value < -(2**63) or value > 2**63 - 1)
                ):
                    inspection.unsupported_integer = True
    inspection.records = state.records
    inspection.skipped_invalid = state.skipped_invalid
    return inspection


def _merge_columns(configured: Sequence[str] | None, discovered: Sequence[str]) -> list[str]:
    if configured is None:
        return list(discovered)
    merged = list(dict.fromkeys(configured))
    known = set(merged)
    for name in discovered:
        if name not in known:
            merged.append(name)
            known.add(name)
    return merged


def _value_kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, dict):
        return "dict"
    if isinstance(value, list):
        return "list"
    return "other"


def _polars_schema_compatible(kinds: Mapping[str, set[str]]) -> bool:
    for column_kinds in kinds.values():
        non_null = column_kinds - {"null"}
        if not non_null:
            continue
        if non_null <= {"int", "float"}:
            continue
        if len(non_null) == 1 and non_null <= {"bool", "str"}:
            continue
        return False
    return True


def _schema_kinds(schema_types: Mapping[str, str]) -> dict[str, set[str]]:
    aliases = {
        "boolean": "bool",
        "bool": "bool",
        "float": "float",
        "integer": "int",
        "int": "int",
        "null": "null",
        "number": "float",
        "str": "str",
        "string": "str",
        "text": "str",
    }
    kinds: dict[str, set[str]] = {}
    for name, declared_type in schema_types.items():
        try:
            kinds[name] = {aliases[declared_type.lower()]}
        except KeyError as exc:
            raise ValueError(
                f"Unsupported schema type {declared_type!r} for column {name!r}."
            ) from exc
    return kinds


def _write_csv_polars(
    source: Path,
    partial: Path,
    columns: list[str],
    null_columns: set[str],
    *,
    separator: str,
    include_bom: bool,
    infer_schema_length: int,
    kinds: Mapping[str, set[str]],
) -> None:
    import polars as pl

    schema: dict[str, Any] = {}
    for name in columns:
        column_kinds = kinds.get(name, {"null"}) - {"null"}
        if not column_kinds or column_kinds == {"str"}:
            schema[name] = pl.String
        elif column_kinds == {"bool"}:
            schema[name] = pl.Boolean
        elif column_kinds == {"int"}:
            schema[name] = pl.Int64
        else:
            schema[name] = pl.Float64

    lazy = pl.scan_ndjson(
        source,
        schema=schema,
        infer_schema_length=infer_schema_length,
        batch_size=1024,
        low_memory=True,
    )
    expressions = [
        pl.lit(None, dtype=pl.String).alias(name) if name in null_columns else pl.col(name)
        for name in columns
    ]
    with pl.Config() as config:
        config.set_streaming_chunk_size(1024)
        lazy.select(expressions).sink_csv(
            partial,
            include_bom=include_bom,
            separator=separator,
            batch_size=1024,
            maintain_order=True,
            sync_on_close="data",
            engine="streaming",
        )


def _write_csv_python(
    source: Path,
    partial: Path,
    columns: list[str],
    null_columns: set[str],
    *,
    separator: str,
    include_bom: bool,
    strict: bool,
    flatten: bool,
    reporter: _ProgressReporter,
    cancel_callback: Callable[[], bool] | None,
) -> _ReadState:
    encoding = "utf-8-sig" if include_bom else "utf-8"
    state = _ReadState()
    with source.open("rb", buffering=IO_BUFFER_SIZE) as infile, partial.open(
        "w", encoding=encoding, newline="", buffering=IO_BUFFER_SIZE
    ) as outfile:
        writer = csv.writer(outfile, delimiter=separator, lineterminator="\n")
        writer.writerow(columns)
        for _, _, record in _iter_jsonl_records(
            infile,
            strict=strict,
            state=state,
            reporter=reporter,
            cancel_callback=cancel_callback,
        ):
            normalized = _flatten_record(record) if flatten else record
            writer.writerow(
                [
                    "" if name in null_columns else _csv_cell(normalized.get(name))
                    for name in columns
                ]
            )
        _flush_and_fsync(outfile)
    return state


def _csv_cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return _dumps_text(value)
    return value


def _flatten_record(record: Mapping[str, Any]) -> dict[str, Any]:
    flattened: dict[str, Any] = {}

    def visit(value: Any, prefix: str) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                name = f"{prefix}.{key}" if prefix else str(key)
                visit(nested, name)
        else:
            flattened[prefix] = value

    for key, value in record.items():
        visit(value, str(key))
    return flattened


def _new_worksheet(workbook: Any, index: int, columns: Sequence[str]) -> Any:
    worksheet = workbook.add_worksheet(f"Dane_{index}")
    for column_index, name in enumerate(columns):
        worksheet.write_string(0, column_index, name)
    return worksheet


def _write_xlsx_row(
    worksheet: Any,
    row_index: int,
    line_number: int,
    record: Mapping[str, Any],
    columns: Sequence[str],
    blank_row_format: Any,
    overflow: _XlsxOverflowWriter,
    long_text_policy: str,
) -> None:
    wrote_value = False
    for column_index, name in enumerate(columns):
        value = record.get(name)
        if value is None:
            continue
        if isinstance(value, bool):
            worksheet.write_boolean(row_index, column_index, value)
            wrote_value = True
            continue
        if isinstance(value, (int, float)):
            worksheet.write_number(row_index, column_index, value)
            wrote_value = True
            continue
        is_string = isinstance(value, str)
        text = value if is_string else _dumps_text(value)
        if _excel_string_length(text) > EXCEL_MAX_STRING_LENGTH:
            if long_text_policy == "error":
                raise JsonlConversionError(
                    f"XLSX value at line {line_number}, column {name!r} exceeds "
                    f"Excel's {EXCEL_MAX_STRING_LENGTH}-character cell limit."
                )
            text = overflow.write(
                text,
                data_sheet=worksheet.get_name(),
                data_row=row_index + 1,
                source_line=line_number,
                column=name,
                value_type="string" if is_string else "json",
            )
        worksheet.write_string(row_index, column_index, text)
        wrote_value = True
    if not wrote_value and columns:
        worksheet.write_blank(row_index, 0, None, blank_row_format)


def _dumps_text(value: Any) -> str:
    if orjson is not None:
        return orjson.dumps(value).decode("utf-8")
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _excel_string_length(text: str) -> int:
    """Return the length Excel applies to a string (UTF-16 code units)."""
    return len(text.encode("utf-16-le")) // 2


def _excel_chunk_count(text: str) -> int:
    units = 0
    chunks = 1
    for character in text:
        character_units = 2 if ord(character) > 0xFFFF else 1
        if units and units + character_units > EXCEL_MAX_STRING_LENGTH:
            chunks += 1
            units = 0
        units += character_units
    return chunks


def _iter_excel_chunks(text: str) -> Iterator[str]:
    start = 0
    units = 0
    for index, character in enumerate(text):
        character_units = 2 if ord(character) > 0xFFFF else 1
        if units and units + character_units > EXCEL_MAX_STRING_LENGTH:
            yield text[start:index]
            start = index
            units = 0
        units += character_units
    yield text[start:]


def _flush_and_fsync(handle: BinaryIO | TextIO) -> None:
    handle.flush()
    os.fsync(handle.fileno())


def _commit_partial(partial: Path, destination: Path) -> None:
    # Windows requires a writable file descriptor for fsync().  Opening the
    # completed partial file read-only causes OSError(9, "Bad file descriptor").
    with partial.open("rb+") as infile:
        os.fsync(infile.fileno())
    os.replace(partial, destination)


def _stats(
    source: Path,
    destination: Path,
    input_size: int,
    state: _ReadState,
    started: float,
    *,
    engine: str,
    sheet_count: int = 0,
    overflow_value_count: int = 0,
    overflow_chunk_count: int = 0,
    overflow_sheet_count: int = 0,
) -> ConversionStats:
    return ConversionStats(
        source=source,
        destination=destination,
        input_size=input_size,
        output_size=destination.stat().st_size,
        record_count=state.records,
        skipped_invalid=state.skipped_invalid,
        elapsed_seconds=time.monotonic() - started,
        engine=engine,
        sheet_count=sheet_count,
        overflow_value_count=overflow_value_count,
        overflow_chunk_count=overflow_chunk_count,
        overflow_sheet_count=overflow_sheet_count,
    )

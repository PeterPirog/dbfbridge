"""Stable, typed Python API for dbfbridge operations."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, cast

from .api_models import (
    ExportOptions,
    ExportRunResult,
    ProgressEvent,
    QualityRunResult,
    ReconstructionOptions,
    ReconstructionRunResult,
    VerificationRunResult,
)
from .exporter.models import (
    DecodeErrors,
    DeletedPolicy,
    MemoPolicy,
    MissingMemoPolicy,
    OutputFormat,
)
from .importer.models import InputFormat

PathLike = str | os.PathLike[str]
ProgressCallback = Callable[[ProgressEvent], None]
SUPPORTED_FORMATS = ("csv", "json", "jsonl", "xlsx")
SUPPORTED_INPUT_FORMATS = ("jsonl", "json", "csv", "xlsx")


def export_dbf(
    source: PathLike,
    output: PathLike,
    *,
    formats: str | Iterable[OutputFormat] | None = None,
    memo: MemoPolicy | None = None,
    strip_spaces: bool = False,
    encoding: str = "auto",
    decode_errors: DecodeErrors = "strict",
    deleted: DeletedPolicy = "skip",
    missing_memo: MissingMemoPolicy = "fail",
    overwrite: bool = True,
    validate: bool = True,
    xlsx_long_text: str = "overflow",
    incremental: bool = False,
    progress: ProgressCallback | None = None,
    options: ExportOptions | None = None,
) -> ExportRunResult:
    """Export one DBF file or a directory tree to one or more formats.

    The function is silent by default. Use ``progress`` to receive structured events.
    Pass either keyword settings or an ``ExportOptions`` object; when ``options`` is
    supplied, its values are used and the other option keywords must stay at defaults.
    Per-table failures are returned in ``ExportRunResult`` and can be converted to an
    exception with ``result.raise_for_errors()``.
    """
    if options is not None:
        if formats is not None or any(
            value != default
            for value, default in (
                (memo, None),
                (strip_spaces, False),
                (encoding, "auto"),
                (decode_errors, "strict"),
                (deleted, "skip"),
                (missing_memo, "fail"),
                (overwrite, True),
                (validate, True),
                (xlsx_long_text, "overflow"),
                (incremental, False),
            )
        ):
            raise ValueError("Use either options=ExportOptions(...) or individual option keywords.")
        formats = options.formats
        memo = options.memo
        strip_spaces = options.strip_spaces
        encoding = options.encoding
        decode_errors = options.decode_errors
        deleted = options.deleted
        missing_memo = options.missing_memo
        overwrite = options.overwrite
        validate = options.validate
        xlsx_long_text = options.xlsx_long_text
        incremental = options.incremental

    source_path = Path(source)
    output_path = Path(output)
    resolved_formats = _normalize_formats(
        ("jsonl",) if formats is None else formats,
        SUPPORTED_FORMATS,
    )
    if not source_path.is_dir() and not (
        source_path.is_file() and source_path.suffix.lower() == ".dbf"
    ):
        raise FileNotFoundError(f"DBF source does not exist: {source_path}")
    if memo not in {None, "skip", "inline", "null"}:
        raise ValueError("memo must be one of: skip, inline, null")
    if decode_errors not in {"strict", "ignore", "replace"}:
        raise ValueError("decode_errors must be one of: strict, ignore, replace")
    if deleted not in {"skip", "separate", "include"}:
        raise ValueError("deleted must be one of: skip, separate, include")
    if missing_memo not in {"fail", "null-with-warning"}:
        raise ValueError("missing_memo must be one of: fail, null-with-warning")
    if xlsx_long_text not in {"overflow", "error"}:
        raise ValueError("xlsx_long_text must be one of: overflow, error")

    from .cli import run_export

    return run_export(
        source=source_path,
        output=output_path,
        formats=cast(tuple[OutputFormat, ...], resolved_formats),
        memo=memo,
        strip_spaces=strip_spaces,
        encoding=encoding,
        decode_errors=decode_errors,
        deleted=deleted,
        missing_memo=missing_memo,
        overwrite=overwrite,
        validate=validate,
        xlsx_long_text=xlsx_long_text,
        incremental=incremental,
        console=False,
        progress_callback=progress,
    )


def reconstruct_dbf(
    source: PathLike,
    output: PathLike,
    *,
    input_format: InputFormat = "jsonl",
    memo: str = "inline",
    overwrite: bool = False,
    progress: ProgressCallback | None = None,
    options: ReconstructionOptions | None = None,
) -> ReconstructionRunResult:
    """Reconstruct a DBF/FPT directory tree from exactly one exported format."""
    if options is not None:
        if input_format != "jsonl" or memo != "inline" or overwrite:
            raise ValueError(
                "Use either options=ReconstructionOptions(...) or individual option keywords."
            )
        input_format = options.input_format
        memo = options.memo
        overwrite = options.overwrite
    normalized_format = cast(
        InputFormat,
        _normalize_formats((input_format,), SUPPORTED_INPUT_FORMATS)[0],
    )
    if memo not in {"inline", "null"}:
        raise ValueError("memo must be one of: inline, null")
    source_path = Path(source)
    output_path = Path(output)
    if not source_path.exists():
        raise FileNotFoundError(f"Export source does not exist: {source_path}")

    from .importer import ImportConfig, reconstruct_tree

    output_path.mkdir(parents=True, exist_ok=True)
    table_results = reconstruct_tree(
        ImportConfig(
            source=source_path,
            output=output_path,
            format=normalized_format,  # type: ignore[arg-type]
            memo=memo,
            overwrite=overwrite,
            progress=False,
        ),
        progress_callback=(
            lambda current, total, table, records: progress(
                ProgressEvent(
                    operation="reconstruct",
                    current=current,
                    total=total,
                    table=table,
                    format=normalized_format,
                    records=records,
                )
            )
            if progress is not None
            else None
        ),
    )
    if not table_results:
        raise FileNotFoundError(f"No *.{normalized_format} data files found in {source_path}")
    failed = sum(result.status == "FAILED" for result in table_results)
    warning = sum(result.status == "WARNING" for result in table_results)
    return ReconstructionRunResult(
        source=source_path.resolve(),
        output=output_path.resolve(),
        input_format=normalized_format,
        results=tuple(table_results),
        exit_code=1 if failed else 2 if warning else 0,
        report_path=output_path / "reconstruction_report.jsonl",
    )


def verify_conversion(
    source: PathLike,
    output: PathLike,
    *,
    formats: str | Iterable[OutputFormat] = SUPPORTED_FORMATS,
    strict: bool = True,
    report: PathLike | None = None,
    write_report: bool = True,
    verbose: bool = False,
) -> VerificationRunResult:
    """Verify exported data, schemas, counts, syntax, and recorded checksums."""
    source_path = Path(source)
    output_path = Path(output)
    if not source_path.is_dir():
        raise FileNotFoundError(f"DBF source directory does not exist: {source_path}")
    if not output_path.is_dir():
        raise FileNotFoundError(f"Export output directory does not exist: {output_path}")
    resolved_formats = _normalize_formats(formats, SUPPORTED_FORMATS)

    from .verifier import summarize, verify_all

    checks, global_errors = verify_all(
        source_path,
        output_path,
        list(resolved_formats),
        verbose,
    )
    summary = summarize(checks, global_errors)
    report_path = Path(report) if report is not None else output_path / "verification_report.json"
    if write_report:
        _atomic_json(report_path, summary)
    failed = int(summary["failed"])
    warning = int(summary["warning"])
    result = VerificationRunResult(
        source=source_path.resolve(),
        output=output_path.resolve(),
        formats=cast(tuple[OutputFormat, ...], resolved_formats),
        checks=tuple(checks),
        summary=summary,
        global_errors=tuple(global_errors),
        exit_code=1 if failed or global_errors else 2 if strict and warning else 0,
        report_path=report_path if write_report else None,
    )
    return result


def check_conversion_quality(
    source: PathLike,
    output: PathLike,
    *,
    overwrite: bool = False,
    max_differences: int = 20,
    progress: ProgressCallback | None = None,
) -> QualityRunResult:
    """Run and retain a diagnostic DBF -> JSONL -> DBF round trip."""
    if max_differences < 1:
        raise ValueError("max_differences must be positive")
    source_path = Path(source)
    output_path = Path(output)
    if not source_path.exists():
        raise FileNotFoundError(f"DBF source does not exist: {source_path}")

    from .quality import run_quality_check

    reports, summary = run_quality_check(
        source_path,
        output_path,
        overwrite=overwrite,
        progress=False,
        max_differences=max_differences,
        progress_callback=(
            lambda stage, current, total, table, records: progress(
                ProgressEvent(
                    operation="quality",
                    current=current,
                    total=total,
                    table=table,
                    records=records,
                    message=stage,
                )
            )
            if progress is not None
            else None
        ),
    )
    failed = int(summary["failed"])
    warning = int(summary["warning"])
    return QualityRunResult(
        source=source_path.resolve(),
        output=output_path.resolve(),
        reports=tuple(reports),
        summary=summary,
        exit_code=1 if failed else 2 if warning else 0,
        report_path=output_path / "conversion_quality_report.jsonl",
    )


def _normalize_formats(
    formats: str | Iterable[str],
    supported: tuple[str, ...],
) -> tuple[str, ...]:
    values = formats.split(",") if isinstance(formats, str) else list(formats)
    normalized = tuple(dict.fromkeys(str(value).strip().lower() for value in values if str(value).strip()))
    if not normalized:
        raise ValueError("At least one format is required.")
    invalid = [value for value in normalized if value not in supported]
    if invalid:
        raise ValueError(f"Unsupported format(s): {invalid}. Available: {list(supported)}")
    return normalized


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.partial")
    with partial.open("w", encoding="utf-8", newline="\n") as outfile:
        json.dump(value, outfile, ensure_ascii=False, indent=2)
        outfile.write("\n")
        outfile.flush()
        os.fsync(outfile.fileno())
    os.replace(partial, path)

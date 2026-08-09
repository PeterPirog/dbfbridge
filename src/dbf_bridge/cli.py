"""
dbf_bridge.cli
==============

Fasada CLI dla pakietu dbf_bridge — rekurencyjnie eksportuje pliki DBF
(wraz z powiązanymi FPT/CDX) do CSV, JSON, JSONL i XLSX, zachowując strukturę
katalogów. Wykorzystuje streamingowy, atomowy eksporter z walidacją
SHA-256 i automatycznym fallback polskich stron kodowych (cp1250/cp852/Mazovia).

Punkt wejścia instalowany przez pip: ``dbf-bridge``. Parametry ``--source`` i
``--output`` są wymagane. Domyślny format to JSONL; CSV domyślnie pomija memo,
a JSON/JSONL/XLSX zapisują memo inline.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from dbf_bridge import __version__
from dbf_bridge.api_models import ExportRunResult, ProgressEvent
from dbf_bridge.converters import (
    ConversionStats,
    jsonl_to_csv,
    jsonl_to_json,
    jsonl_to_xlsx,
)
from dbf_bridge.exporter.config import ConfigError, make_config
from dbf_bridge.exporter.discovery import discover_tables
from dbf_bridge.exporter.incremental import (
    CHECKSUM_MANIFEST_NAME,
    OUTPUT_COMPATIBILITY_VERSION,
    cached_results_for_table,
    load_checksum_manifest,
    source_fingerprint,
    write_checksum_manifest,
)
from dbf_bridge.exporter.models import DiscoveredTable, TableResult
from dbf_bridge.exporter.reporting import exit_code, write_reports
from dbf_bridge.exporter.validation import sha256_file
from dbf_bridge.exporter.writer import export_table

DEFAULTS = {
    "formats": "jsonl",
    "memo": None,
    "strip_spaces": False,
    "encoding": "auto",
    "decode_errors": "strict",
    "deleted": "skip",
    "missing_memo": "fail",
    "overwrite": True,
    "validate": True,
    "progress": True,
    "xlsx_long_text": "overflow",
    "incremental": False,
}

DEFAULT_MEMO_POLICY: dict[str, str] = {
    "csv": "skip",
    "json": "inline",
    "jsonl": "inline",
    "xlsx": "inline",
}

ALL_FORMATS: tuple[str, ...] = ("csv", "json", "jsonl", "xlsx")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dbf-bridge",
        description=(
            "Rekurencyjnie eksportuje pliki DBF do CSV, JSON, JSONL i XLSX z zachowaniem "
            "struktury katalogów. Fasada nad pakietem dbf_bridge.exporter."
        ),
    )
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Katalog źródłowy DBF (wymagany).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Katalog wyjściowy (wymagany).",
    )
    parser.add_argument(
        "--formats",
        default=DEFAULTS["formats"],
        help=f"Lista formatów rozdzielona przecinkami (domyślnie: {DEFAULTS['formats']}). Dostępne: {', '.join(ALL_FORMATS)}",
    )
    parser.add_argument(
        "--memo",
        choices=["skip", "inline", "null"],
        default=DEFAULTS["memo"],
        help="Polityka pól memo. Domyślnie: skip dla CSV, inline dla JSON/JSONL/XLSX.",
    )
    parser.add_argument(
        "--strip-spaces",
        action=argparse.BooleanOptionalAction,
        default=DEFAULTS["strip_spaces"],
        help="Usuń końcowe spacje z pól Character (C).",
    )
    parser.add_argument(
        "--encoding",
        default=DEFAULTS["encoding"],
        help="Strona kodowa DBF lub 'auto' (wykrywanie z nagłówka).",
    )
    parser.add_argument(
        "--decode-errors",
        choices=["strict", "ignore", "replace"],
        default=DEFAULTS["decode_errors"],
        help="Polityka błędów dekodowania znaków.",
    )
    parser.add_argument(
        "--deleted",
        choices=["skip", "separate", "include"],
        default=DEFAULTS["deleted"],
        help="Polityka usuniętych rekordów DBF.",
    )
    parser.add_argument(
        "--missing-memo",
        choices=["fail", "null-with-warning"],
        default=DEFAULTS["missing_memo"],
        help="Polityka dla tabel DBF bez pliku memo FPT.",
    )
    parser.add_argument(
        "--overwrite",
        action=argparse.BooleanOptionalAction,
        default=DEFAULTS["overwrite"],
        help="Nadpisz istniejące pliki wyjściowe (domyślnie: włączone).",
    )
    parser.add_argument(
        "--no-validate",
        dest="validate",
        action="store_false",
        default=DEFAULTS["validate"],
        help="Pomiń walidację SHA-256 i round-trip wyjścia.",
    )
    parser.add_argument(
        "--progress",
        action=argparse.BooleanOptionalAction,
        default=DEFAULTS["progress"],
        help="Pokazuj postęp konwersji per tabela (domyślnie: włączone).",
    )
    parser.add_argument(
        "--xlsx-long-text",
        choices=["overflow", "error"],
        default=DEFAULTS["xlsx_long_text"],
        help=(
            "Obsługa tekstu dłuższego niż limit komórki Excela: "
            "'overflow' zapisuje go bezstratnie w arkuszach Dlugie_teksty_* "
            "(domyślnie), 'error' przerywa konwersję tabeli."
        ),
    )
    parser.add_argument(
        "--incremental",
        action=argparse.BooleanOptionalAction,
        default=DEFAULTS["incremental"],
        help=(
            "Pomiń niezmienione tabele, jeśli źródła, konfiguracja, schemat i wszystkie "
            "żądane wyniki są zgodne z conversion_checksums.json."
        ),
    )
    return parser


def _resolve_formats(formats_arg: str) -> list[str]:
    requested = [f.strip().lower() for f in formats_arg.split(",") if f.strip()]
    if not requested:
        return list(ALL_FORMATS)
    invalid = [f for f in requested if f not in ALL_FORMATS]
    if invalid:
        raise ValueError(f"Nieobsługiwany format(y): {invalid}. Dostępne: {list(ALL_FORMATS)}")
    seen: set[str] = set()
    ordered: list[str] = []
    for f in requested:
        if f not in seen:
            ordered.append(f)
            seen.add(f)
    return ordered


def _resolve_memo_policy(fmt: str, memo_arg: str | None) -> str:
    if memo_arg is not None:
        return memo_arg
    return DEFAULT_MEMO_POLICY.get(fmt, "inline")


def _format_count(n: int) -> str:
    return f"{n:,}".replace(",", "\u202f")


def _print_progress(
    label: str,
    index: int,
    total: int,
    *,
    elapsed_s: float,
    rate: float | None = None,
    width: int = 40,
) -> None:
    """Rysuje jednowierszowy pasek postępu w konsoli (karrubka, bez tqdm)."""
    import sys as _sys

    fraction = index / total if total else 1.0
    filled = int(width * fraction)
    bar = "#" * filled + "-" * (width - filled)
    pct = fraction * 100.0
    line = f"\r{label} [{bar}] {index}/{total} ({pct:5.1f}%) {elapsed_s:6.1f}s"
    if rate is not None and rate > 0:
        line += f" {rate:5.1f} tbl/s"
    line += "   "
    _sys.stderr.write(line)
    _sys.stderr.flush()


def _export_one(
    source: Path,
    output: Path,
    fmt: str,
    memo: str,
    *,
    strip_spaces: bool,
    encoding: str,
    decode_errors: str,
    deleted: str,
    missing_memo: str,
    validate: bool,
    overwrite: bool,
    progress: bool,
    tables: list[DiscoveredTable] | None = None,
    console: bool = True,
    progress_callback: Callable[[ProgressEvent], None] | None = None,
) -> tuple[int, list[TableResult]]:
    try:
        config = make_config(
            source=source,
            output=output,
            export_format=fmt,  # type: ignore[arg-type]
            encoding=encoding,
            decode_errors=decode_errors,  # type: ignore[arg-type]
            deleted=deleted,  # type: ignore[arg-type]
            missing_memo=missing_memo,  # type: ignore[arg-type]
            memo=memo,  # type: ignore[arg-type]
            strip_spaces=strip_spaces,
            validate=validate,
            overwrite=overwrite,
        )
    except ConfigError as exc:
        if not console:
            raise
        print(f"[dbf-bridge] Błąd konfiguracji: {exc}", file=sys.stderr)
        return 1, []

    if console:
        print(f"[dbf-bridge] Eksport {fmt.upper()} (memo={memo}) -> {config.output}")
    tables = discover_tables(config.source) if tables is None else tables
    if not tables:
        if console:
            print("[dbf-bridge] Brak tabel wymagających konwersji.")
        return 0, []

    total = len(tables)
    if console:
        print(f"[dbf-bridge] Znaleziono {_format_count(total)} plik(ów) DBF.")

    label = f"[dbf-bridge] {fmt.upper():5}"
    start = time.monotonic()
    results: list[TableResult] = []
    for i, table in enumerate(tables, start=1):
        rel = table.relative_path.as_posix()
        try:
            results.append(export_table(table, config))
        except Exception as exc:
            results.append(
                TableResult(
                    table=rel,
                    output=None,
                    status="FAILED",
                    encoding=config.encoding,
                    format=config.format,
                    errors=[f"{rel}: nieoczekiwany błąd: {exc}"],
                )
            )
        elapsed = time.monotonic() - start
        rate = i / elapsed if elapsed > 0 else None
        if progress and console:
            _print_progress(label, i, total, elapsed_s=elapsed, rate=rate)
        if progress_callback is not None:
            progress_callback(
                ProgressEvent(
                    operation="export",
                    current=i,
                    total=total,
                    table=table.relative_path.as_posix(),
                    format=fmt,
                    records=results[-1].active_records + results[-1].deleted_records,
                )
            )
    if progress and console:
        sys.stderr.write("\n")
        sys.stderr.flush()
    return 0, results


def _print_summary(results: list[TableResult]) -> None:
    if not results:
        return
    ok = sum(1 for r in results if r.status == "OK")
    warning = sum(1 for r in results if r.status == "WARNING")
    skipped = sum(1 for r in results if r.status == "SKIPPED")
    failed = sum(1 for r in results if r.status in {"FAILED", "UNSUPPORTED"})
    print(f"  -> OK: {ok}  Ostrzeżenia: {warning}  Pominięte: {skipped}  Błędy: {failed}")
    for r in results:
        if r.status in {"FAILED", "UNSUPPORTED"} or r.errors:
            print(f"      - {r.table}: {r.status} | {'; '.join(r.errors) if r.errors else ''}")


def _schema_details(
    output: Path,
    result: TableResult,
) -> tuple[list[str], list[str], dict[str, str]]:
    if result.schema is None:
        return [], [], {}
    schema_path = output / result.schema
    with schema_path.open("r", encoding="utf-8") as infile:
        schema = json.load(infile)
    fields = schema.get("fields", [])
    columns = [field["name"] for field in fields]
    memo_fields = [field["name"] for field in fields if field.get("is_memo")]
    schema_types: dict[str, str] = {}
    for field in fields:
        representation = field.get("target_representation")
        if representation == "boolean-or-null":
            schema_types[field["name"]] = "boolean"
        elif representation == "number":
            is_integer = field.get("decimal_count") == 0 and field.get("dbf_type") not in {
                "B",
                "F",
                "O",
            }
            schema_types[field["name"]] = "integer" if is_integer else "number"
        else:
            schema_types[field["name"]] = "string"
    return columns, memo_fields, schema_types


def _convert_jsonl_outputs(
    output: Path,
    results: list[TableResult],
    formats: list[str],
    *,
    memo_arg: str | None,
    deleted: str,
    overwrite: bool,
    xlsx_long_text: str = "overflow",
    console: bool = True,
    progress_callback: Callable[[ProgressEvent], None] | None = None,
) -> list[TableResult]:
    conversion_results: list[TableResult] = []
    target_formats = [fmt for fmt in formats if fmt != "jsonl"]
    if not target_formats:
        return conversion_results

    total = len(results) * len(target_formats)
    current = 0
    for result in results:
        for fmt in target_formats:
            current += 1
            intended_output = str(Path(result.table).with_suffix(f".{fmt}")).replace("\\", "/")
            if result.status in {"FAILED", "UNSUPPORTED"} or result.output is None:
                conversion_results.append(
                    TableResult(
                        table=result.table,
                        output=intended_output,
                        status="FAILED",
                        encoding=result.encoding,
                        format=fmt,  # type: ignore[arg-type]
                        active_records=result.active_records,
                        deleted_records=result.deleted_records,
                        memo_fields=result.memo_fields,
                        schema=result.schema,
                        schema_sha256=result.schema_sha256,
                        errors=[
                            f"Conversion to {fmt.upper()} was not attempted because the JSONL "
                            f"export status is {result.status}."
                        ],
                    )
                )
                if progress_callback is not None:
                    progress_callback(
                        ProgressEvent(
                            operation="convert",
                            current=current,
                            total=total,
                            table=result.table,
                            format=fmt,
                        )
                    )
                continue

            primary_source = output / result.output
            columns, memo_fields, schema_types = _schema_details(output, result)
            if deleted == "include":
                columns.append("__deleted__")
                schema_types["__deleted__"] = "boolean"

            sources: list[tuple[str, Path, list[str], dict[str, str], int]] = [
                (
                    "primary",
                    primary_source,
                    columns,
                    schema_types,
                    result.active_records + (result.deleted_records if deleted == "include" else 0),
                )
            ]
            if deleted == "separate":
                deleted_source = primary_source.with_name(
                    f"{primary_source.stem}.deleted{primary_source.suffix}"
                )
                sources.append(
                    (
                        "deleted",
                        deleted_source,
                        [*columns, "__deleted__"],
                        {**schema_types, "__deleted__": "boolean"},
                        result.deleted_records,
                    )
                )

            errors: list[str] = []
            converted: dict[str, tuple[Path, ConversionStats, str]] = {}
            for variant, source_path, source_columns, source_schema, record_count in sources:
                destination_path = source_path.with_suffix(f".{fmt}")
                try:
                    stats = _convert_jsonl_file(
                        fmt,
                        source_path,
                        destination_path,
                        columns=source_columns,
                        schema_types=source_schema,
                        expected_record_count=record_count,
                        memo_fields=memo_fields,
                        memo_arg=memo_arg,
                        overwrite=overwrite,
                        xlsx_long_text=xlsx_long_text,
                    )
                    output_hash = sha256_file(destination_path)
                    converted[variant] = (destination_path, stats, output_hash)
                    sheet_info = f", {stats.sheet_count} arkusz(e)" if stats.sheet_count else ""
                    if stats.overflow_value_count:
                        sheet_info += (
                            f", długie wartości: {stats.overflow_value_count} "
                            f"({stats.overflow_chunk_count} części, "
                            f"{stats.overflow_sheet_count} arkusz(e) przepełnień)"
                        )
                    if console:
                        print(
                            f"  -> {source_path.name} -> {fmt.upper()}: "
                            f"{_format_count(stats.record_count)} rekordów, "
                            f"{stats.megabytes_per_second:.1f} MB/s, {stats.engine}{sheet_info}"
                        )
                except Exception as exc:
                    message = f"{variant}: {exc}"
                    errors.append(message)
                    if console:
                        print(
                            f"[dbf-bridge] Błąd konwersji {source_path} -> {fmt}: {exc}",
                            file=sys.stderr,
                        )

            primary = converted.get("primary")
            deleted_result = converted.get("deleted")
            conversion_results.append(
                TableResult(
                    table=result.table,
                    output=intended_output,
                    status="FAILED" if errors else "OK",
                    encoding=result.encoding,
                    format=fmt,  # type: ignore[arg-type]
                    active_records=result.active_records,
                    deleted_records=result.deleted_records,
                    memo_fields=result.memo_fields,
                    sha256=primary[2] if primary is not None else None,
                    size_bytes=primary[1].output_size if primary is not None else None,
                    schema=result.schema,
                    schema_sha256=result.schema_sha256,
                    deleted_output=(
                        deleted_result[0].relative_to(output).as_posix()
                        if deleted_result is not None
                        else None
                    ),
                    deleted_sha256=deleted_result[2] if deleted_result is not None else None,
                    engine=primary[1].engine if primary is not None else None,
                    sheet_count=primary[1].sheet_count if primary is not None else 0,
                    overflow_value_count=(
                        primary[1].overflow_value_count if primary is not None else 0
                    ),
                    overflow_chunk_count=(
                        primary[1].overflow_chunk_count if primary is not None else 0
                    ),
                    overflow_sheet_count=(
                        primary[1].overflow_sheet_count if primary is not None else 0
                    ),
                    elapsed_seconds=primary[1].elapsed_seconds if primary is not None else None,
                    errors=errors,
                )
            )
            if progress_callback is not None:
                progress_callback(
                    ProgressEvent(
                        operation="convert",
                        current=current,
                        total=total,
                        table=result.table,
                        format=fmt,
                        records=result.active_records + result.deleted_records,
                    )
                )
    return conversion_results


def _convert_jsonl_file(
    fmt: str,
    source_path: Path,
    destination_path: Path,
    *,
    columns: list[str],
    schema_types: dict[str, str],
    expected_record_count: int,
    memo_fields: list[str],
    memo_arg: str | None,
    overwrite: bool,
    xlsx_long_text: str = "overflow",
) -> ConversionStats:
    if fmt == "csv":
        null_columns = memo_fields if _resolve_memo_policy("csv", memo_arg) != "inline" else []
        return jsonl_to_csv(
            source_path,
            destination_path,
            columns=columns,
            schema_types=schema_types,
            expected_record_count=expected_record_count,
            source_is_validated=True,
            null_columns=null_columns,
            overwrite=overwrite,
        )
    if fmt == "json":
        return jsonl_to_json(source_path, destination_path, overwrite=overwrite)
    return jsonl_to_xlsx(
        source_path,
        destination_path,
        columns=columns,
        overwrite=overwrite,
        long_text_policy=xlsx_long_text,
    )


def _print_format_summary(results: list[TableResult]) -> None:
    print("\n[dbf-bridge] Podsumowanie formatów:")
    for fmt in sorted({result.format for result in results}):
        format_results = [result for result in results if result.format == fmt]
        ok = sum(result.status == "OK" for result in format_results)
        warnings = sum(result.status == "WARNING" for result in format_results)
        skipped = sum(result.status == "SKIPPED" for result in format_results)
        failed = sum(result.status in {"FAILED", "UNSUPPORTED"} for result in format_results)
        print(
            f"  -> {fmt.upper():5} OK: {ok}  Ostrzeżenia: {warnings}  "
            f"Pominięte: {skipped}  Błędy: {failed}"
        )


def _incremental_signature(
    *,
    source: Path,
    formats: list[str],
    memo: str | None,
    strip_spaces: bool,
    encoding: str,
    decode_errors: str,
    deleted: str,
    missing_memo: str,
    validate: bool,
    xlsx_long_text: str,
) -> dict[str, object]:
    return {
        "output_compatibility_version": OUTPUT_COMPATIBILITY_VERSION,
        "source": str(source.resolve()),
        "formats": sorted(formats),
        "memo_policy_by_format": {
            fmt: _resolve_memo_policy(fmt, memo) for fmt in sorted(formats)
        },
        "strip_spaces": strip_spaces,
        "encoding": encoding,
        "decode_errors": decode_errors,
        "deleted_policy": deleted,
        "missing_memo_policy": missing_memo,
        "validation_enabled": validate,
        "xlsx_long_text_policy": xlsx_long_text,
    }


def _source_root(source: Path) -> Path:
    resolved = source.resolve()
    return resolved if resolved.is_dir() else resolved.parent


def _known_source_hashes(output: Path, result: TableResult) -> tuple[str | None, str | None]:
    if result.schema is None:
        return None, None
    schema_path = output / result.schema
    if not schema_path.is_file():
        return None, None
    with schema_path.open("r", encoding="utf-8") as infile:
        schema = json.load(infile)
    return schema.get("source", {}).get("sha256"), schema.get("memo", {}).get("sha256")


def run_export(
    *,
    source: Path,
    output: Path,
    formats: tuple[str, ...],
    memo: str | None,
    strip_spaces: bool,
    encoding: str,
    decode_errors: str,
    deleted: str,
    missing_memo: str,
    overwrite: bool,
    validate: bool,
    xlsx_long_text: str,
    incremental: bool,
    console: bool,
    show_progress: bool = False,
    progress_callback: Callable[[ProgressEvent], None] | None = None,
) -> ExportRunResult:
    started_at = datetime.now(timezone.utc)
    started_monotonic = time.monotonic()
    if not source.is_dir() and not (source.is_file() and source.suffix.lower() == ".dbf"):
        raise FileNotFoundError(f"DBF source does not exist: {source}")
    make_config(
        source=source,
        output=output,
        export_format="jsonl",
        encoding=encoding,
        decode_errors=decode_errors,  # type: ignore[arg-type]
        deleted=deleted,  # type: ignore[arg-type]
        missing_memo=missing_memo,  # type: ignore[arg-type]
        memo=_resolve_memo_policy("jsonl", memo),  # type: ignore[arg-type]
        strip_spaces=strip_spaces,
        validate=validate,
        overwrite=overwrite,
    )
    output.mkdir(parents=True, exist_ok=True)
    requested_formats = list(formats)
    if console:
        print(f"[dbf-bridge] Source:   {source}")
        print(f"[dbf-bridge] Output:   {output}")
        print(f"[dbf-bridge] Formats:  {', '.join(requested_formats)}")
        print(f"[dbf-bridge] Overwrite: {overwrite}")
        print(f"[dbf-bridge] Incremental: {incremental}")
        print()

    overall_errors = 0
    warnings: list[str] = []
    discovered_tables = discover_tables(source)
    signature = _incremental_signature(
        source=source,
        formats=requested_formats,
        memo=memo,
        strip_spaces=strip_spaces,
        encoding=encoding,
        decode_errors=decode_errors,
        deleted=deleted,
        missing_memo=missing_memo,
        validate=validate,
        xlsx_long_text=xlsx_long_text,
    )
    result_formats = ["jsonl", *(fmt for fmt in requested_formats if fmt != "jsonl")]
    source_root = _source_root(source)
    fingerprints: dict[str, dict[str, object]] = {}
    cached_results: list[TableResult] = []
    tables_to_convert = discovered_tables
    if incremental:
        manifest, manifest_warning = load_checksum_manifest(output)
        if manifest_warning:
            warnings.append(manifest_warning)
            if console:
                print(f"[dbf-bridge] Ostrzeżenie: {manifest_warning}", file=sys.stderr)
        if manifest is not None and manifest.get("signature") == signature:
            tables_to_convert = []
            if console:
                print(f"[dbf-bridge] Weryfikacja {CHECKSUM_MANIFEST_NAME}...")
            for table in discovered_tables:
                table_name = table.relative_path.as_posix()
                fingerprint = source_fingerprint(table, source_root)
                fingerprints[table_name] = fingerprint
                cached = cached_results_for_table(
                    manifest,
                    table_name,
                    fingerprint,
                    signature,
                    result_formats,
                    output,
                )
                if cached is None:
                    tables_to_convert.append(table)
                else:
                    cached_results.extend(cached)
            if console:
                print(
                    f"[dbf-bridge] Przyrostowo: do konwersji {len(tables_to_convert)}, "
                    f"pominięto {len(cached_results) // len(result_formats)}."
                )
        elif manifest is not None and console:
            print("[dbf-bridge] Konfiguracja uległa zmianie; wszystkie tabele będą przeliczone.")
    jsonl_memo = _resolve_memo_policy("jsonl", memo)
    code, all_results = _export_one(
        source=source,
        output=output,
        fmt="jsonl",
        memo=jsonl_memo,
        strip_spaces=strip_spaces,
        encoding=encoding,
        decode_errors=decode_errors,
        deleted=deleted,
        missing_memo=missing_memo,
        validate=validate,
        overwrite=overwrite,
        progress=show_progress,
        tables=tables_to_convert,
        console=console,
        progress_callback=progress_callback,
    )
    overall_errors = max(overall_errors, code)
    if console:
        _print_summary(all_results)
    conversion_results = _convert_jsonl_outputs(
        output,
        all_results,
        requested_formats,
        memo_arg=memo,
        deleted=deleted,
        overwrite=overwrite,
        xlsx_long_text=xlsx_long_text,
        console=console,
        progress_callback=progress_callback,
    )
    report_results = [*all_results, *conversion_results, *cached_results]
    format_order = {fmt: index for index, fmt in enumerate(requested_formats)}
    report_results.sort(
        key=lambda result: (result.table.lower(), format_order.get(result.format, len(formats)))
    )
    if console:
        _print_format_summary(report_results)
    overall_errors = max(overall_errors, exit_code(report_results))

    if report_results:
        finished_at = datetime.now(timezone.utc)
        write_reports(
            output,
            report_results,
            run_metadata={
                "dbfbridge_version": __version__,
                "started_at": started_at.isoformat(),
                "finished_at": finished_at.isoformat(),
                "elapsed_seconds": time.monotonic() - started_monotonic,
                "source": str(source.resolve()),
                "output": str(output.resolve()),
                "requested_formats": requested_formats,
                "memo_policy": memo or "per-format-default",
                "memo_policy_by_format": {
                    fmt: _resolve_memo_policy(fmt, memo) for fmt in requested_formats
                },
                "deleted_policy": deleted,
                "encoding": encoding,
                "decode_errors": decode_errors,
                "missing_memo_policy": missing_memo,
                "overwrite": overwrite,
                "validation_enabled": validate,
                "xlsx_long_text_policy": xlsx_long_text,
                "incremental_enabled": incremental,
                "converted_tables": len(tables_to_convert),
                "skipped_tables": len(cached_results) // len(result_formats),
                "checksum_manifest": CHECKSUM_MANIFEST_NAME,
                "exit_code": overall_errors,
            },
        )
        jsonl_results = {
            result.table: result for result in report_results if result.format == "jsonl"
        }
        for table in discovered_tables:
            table_name = table.relative_path.as_posix()
            if table_name in fingerprints:
                continue
            result = jsonl_results.get(table_name)
            if result is None or result.status not in {"OK", "WARNING", "SKIPPED"}:
                continue
            dbf_hash, fpt_hash = _known_source_hashes(output, result)
            fingerprints[table_name] = source_fingerprint(
                table,
                source_root,
                known_dbf_sha256=dbf_hash,
                known_fpt_sha256=fpt_hash,
            )
        write_checksum_manifest(
            output,
            source_root=source_root,
            signature=signature,
            fingerprints=fingerprints,
            results=report_results,
            requested_formats=result_formats,
            dbfbridge_version=__version__,
        )

    elapsed = time.monotonic() - started_monotonic
    result = ExportRunResult(
        source=source.resolve(),
        output=output.resolve(),
        formats=tuple(requested_formats),
        results=tuple(report_results),
        exit_code=overall_errors,
        elapsed_seconds=elapsed,
        migration_report_jsonl=output / "migration_report.jsonl" if report_results else None,
        migration_report_csv=output / "migration_report.csv" if report_results else None,
        checksum_manifest=output / CHECKSUM_MANIFEST_NAME if report_results else None,
        warnings=tuple(warnings),
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        formats = tuple(_resolve_formats(args.formats))
        result = run_export(
            source=args.source,
            output=args.output,
            formats=formats,
            memo=args.memo,
            strip_spaces=args.strip_spaces,
            encoding=args.encoding,
            decode_errors=args.decode_errors,
            deleted=args.deleted,
            missing_memo=args.missing_memo,
            overwrite=args.overwrite,
            validate=args.validate,
            xlsx_long_text=args.xlsx_long_text,
            incremental=args.incremental,
            console=True,
            show_progress=args.progress,
        )
    except (ConfigError, FileNotFoundError, ValueError) as exc:
        print(f"[dbf-bridge] Błąd: {exc}", file=sys.stderr)
        return 1
    print(f"\n[dbf-bridge] Zakończono. Kod wyjścia: {result.exit_code}")
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())

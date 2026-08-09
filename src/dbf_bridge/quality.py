"""Diagnostic DBF -> JSONL -> DBF round-trip quality verification."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
from collections.abc import Callable
from itertools import zip_longest
from pathlib import Path
from typing import Any

from dbf_bridge.exporter.config import make_config
from dbf_bridge.exporter.discovery import discover_tables, find_related_file
from dbf_bridge.exporter.reporting import write_reports
from dbf_bridge.exporter.validation import sha256_file
from dbf_bridge.exporter.writer import export_table
from dbf_bridge.importer import ImportConfig, reconstruct_tree


def run_quality_check(
    source: Path,
    output: Path,
    *,
    overwrite: bool,
    progress: bool,
    max_differences: int = 20,
    progress_callback: Callable[[str, int, int, str, int | None], None] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_resolved = source.resolve()
    output_resolved = output.resolve()
    if output_resolved == source_resolved or source_resolved in output_resolved.parents:
        raise ValueError("Quality output must not be inside the source DBF directory.")

    forward = output / "01_forward_jsonl"
    reconstructed = output / "02_reconstructed_dbf"
    verification = output / "03_reexported_jsonl"
    forward_results = _export_tree(
        source,
        forward,
        overwrite=overwrite,
        progress=progress,
        stage="forward-export",
        progress_callback=progress_callback,
    )
    reconstruction_progress: Callable[[int, int, str, int | None], None] | None = None
    if progress_callback is not None:
        def reconstruction_progress(current: int, total: int, table: str, records: int | None) -> None:
            progress_callback("reconstruction", current, total, table, records)
    import_results = reconstruct_tree(
        ImportConfig(
            source=forward,
            output=reconstructed,
            format="jsonl",
            memo="inline",
            overwrite=overwrite,
            progress=progress,
        ),
        progress_callback=reconstruction_progress,
    )
    verification_results = _export_tree(
        reconstructed,
        verification,
        overwrite=overwrite,
        progress=progress,
        stage="verification-export",
        progress_callback=progress_callback,
    )

    import_by_output = {result.output: result for result in import_results}
    verify_by_table = {result.table: result for result in verification_results}
    source_root = source if source.is_dir() else source.parent
    reports: list[dict[str, Any]] = []
    for source_result in forward_results:
        relative_dbf = Path(source_result.table)
        original_dbf = source_root / relative_dbf
        reconstructed_dbf = reconstructed / relative_dbf
        forward_jsonl = forward / relative_dbf.with_suffix(".jsonl")
        verification_jsonl = verification / relative_dbf.with_suffix(".jsonl")
        reconstructed_result = import_by_output.get(relative_dbf.as_posix())
        reexport_result = verify_by_table.get(relative_dbf.as_posix())
        errors: list[str] = []
        warnings: list[str] = []
        differences: list[dict[str, Any]] = []

        if source_result.status in {"FAILED", "UNSUPPORTED"}:
            errors.extend(source_result.errors or ["Forward JSONL export failed."])
        if reconstructed_result is None:
            errors.append("No reconstruction result was produced.")
        elif reconstructed_result.status == "FAILED":
            errors.extend(reconstructed_result.errors)
        else:
            warnings.extend(reconstructed_result.warnings)
        if reexport_result is None:
            errors.append("No verification re-export result was produced.")
        elif reexport_result.status in {"FAILED", "UNSUPPORTED"}:
            errors.extend(reexport_result.errors)

        raw_dbf = _file_comparison(original_dbf, reconstructed_dbf, max_differences, kind="dbf")
        original_fpt = find_related_file(original_dbf, ".fpt")
        reconstructed_fpt = find_related_file(reconstructed_dbf, ".fpt")
        raw_fpt = _file_comparison(
            original_fpt,
            reconstructed_fpt,
            max_differences,
            kind="fpt",
        )
        if not raw_dbf["match"]:
            differences.append(
                {
                    "scope": "raw_dbf",
                    "message": "DBF files are not byte-identical.",
                    "probable_causes": _raw_dbf_causes(
                        raw_dbf,
                        source_result.deleted_records,
                    ),
                    **raw_dbf,
                }
            )
        if raw_fpt["applicable"] and not raw_fpt["match"]:
            differences.append(
                {
                    "scope": "raw_fpt",
                    "message": "FPT files are not byte-identical.",
                    **raw_fpt,
                }
            )

        record_differences = _compare_jsonl(
            forward_jsonl,
            verification_jsonl,
            max_differences=max_differences,
        )
        differences.extend(record_differences)
        source_canonical = (
            reconstructed_result.input_canonical_sha256 if reconstructed_result else None
        )
        reconstructed_canonical = (
            reconstructed_result.reconstructed_canonical_sha256 if reconstructed_result else None
        )
        canonical_match = (
            source_canonical == reconstructed_canonical
            if source_canonical and reconstructed_canonical
            else False
        )
        source_cdx = find_related_file(original_dbf, ".cdx")
        if source_cdx is not None:
            warnings.append(
                "Source CDX exists, but index expressions and tags are not available in the "
                "schema; CDX reconstruction is outside this round-trip."
            )
            differences.append(
                {
                    "scope": "cdx",
                    "message": "Source CDX was not reconstructed.",
                    "source": source_cdx.relative_to(source_root).as_posix(),
                }
            )

        if errors or not canonical_match or record_differences:
            status = "FAILED"
        elif (
            not warnings
            and raw_dbf["match"]
            and (not raw_fpt["applicable"] or raw_fpt["match"])
        ):
            status = "OK"
        else:
            status = "WARNING"
        reports.append(
            {
                "type": "table",
                "table": relative_dbf.as_posix(),
                "status": status,
                "source_dbf": original_dbf.relative_to(source_root).as_posix(),
                "reconstructed_dbf": reconstructed_dbf.relative_to(output).as_posix(),
                "forward_jsonl": forward_jsonl.relative_to(output).as_posix(),
                "verification_jsonl": verification_jsonl.relative_to(output).as_posix(),
                "raw_dbf": raw_dbf,
                "raw_fpt": raw_fpt,
                "canonical": {
                    "source_sha256": source_canonical,
                    "reconstructed_sha256": reconstructed_canonical,
                    "match": canonical_match,
                },
                "record_count": reconstructed_result.record_count if reconstructed_result else None,
                "differences": differences,
                "warnings": list(dict.fromkeys(warnings)),
                "errors": errors,
            }
        )

    summary = {
        "type": "summary",
        "report_version": 1,
        "source": str(source.resolve()),
        "output": str(output.resolve()),
        "tables": len(reports),
        "ok": sum(report["status"] == "OK" for report in reports),
        "warning": sum(report["status"] == "WARNING" for report in reports),
        "failed": sum(report["status"] == "FAILED" for report in reports),
        "raw_dbf_matches": sum(report["raw_dbf"]["match"] is True for report in reports),
        "raw_fpt_matches": sum(report["raw_fpt"]["match"] is True for report in reports),
        "canonical_matches": sum(report["canonical"]["match"] is True for report in reports),
        "artifact_directories": {
            "forward_jsonl": forward.relative_to(output).as_posix(),
            "reconstructed_dbf": reconstructed.relative_to(output).as_posix(),
            "reexported_jsonl": verification.relative_to(output).as_posix(),
        },
    }
    _write_quality_report(output / "conversion_quality_report.jsonl", summary, reports)
    return reports, summary


def _export_tree(
    source: Path,
    output: Path,
    *,
    overwrite: bool,
    progress: bool,
    stage: str,
    progress_callback: Callable[[str, int, int, str, int | None], None] | None,
) -> list[Any]:
    config = make_config(
        source=source,
        output=output,
        export_format="jsonl",
        memo="inline",
        deleted="include",
        overwrite=overwrite,
        validate=True,
    )
    tables = discover_tables(source)
    results = []
    for index, table in enumerate(tables, start=1):
        if progress:
            print(f"[quality] JSONL {index}/{len(tables)}: {table.relative_path}")
        results.append(export_table(table, config))
        if progress_callback is not None:
            progress_callback(stage, index, len(tables), table.relative_path.as_posix(), None)
    write_reports(output, results, run_metadata={"requested_formats": ["jsonl"]})
    return results


def _file_comparison(
    source: Path | None,
    reconstructed: Path | None,
    max_differences: int,
    *,
    kind: str,
) -> dict[str, Any]:
    if source is None:
        return {
            "applicable": False,
            "source_sha256": None,
            "reconstructed_sha256": None,
            "match": None,
            "source_size": None,
            "reconstructed_size": None,
            "first_different_offsets": [],
        }
    if reconstructed is None or not reconstructed.is_file():
        return {
            "applicable": True,
            "source_sha256": sha256_file(source),
            "reconstructed_sha256": None,
            "match": False,
            "source_size": source.stat().st_size,
            "reconstructed_size": None,
            "first_different_offsets": [],
        }
    source_hash = sha256_file(source)
    reconstructed_hash = sha256_file(reconstructed)
    offsets = _different_offsets(source, reconstructed, max_differences, kind=kind)
    return {
        "applicable": True,
        "source_sha256": source_hash,
        "reconstructed_sha256": reconstructed_hash,
        "match": source_hash == reconstructed_hash,
        "source_size": source.stat().st_size,
        "reconstructed_size": reconstructed.stat().st_size,
        "first_different_offsets": offsets,
    }


def _different_offsets(
    source: Path,
    reconstructed: Path,
    limit: int,
    *,
    kind: str,
) -> list[dict[str, Any]]:
    differences: list[dict[str, Any]] = []
    offset = 0
    header_length = record_length = None
    if kind == "dbf":
        with source.open("rb") as infile:
            header = infile.read(32)
        if len(header) == 32:
            header_length, record_length = struct.unpack_from("<HH", header, 8)
    with source.open("rb") as left, reconstructed.open("rb") as right:
        while len(differences) < limit:
            left_chunk = left.read(1024 * 1024)
            right_chunk = right.read(1024 * 1024)
            if not left_chunk and not right_chunk:
                break
            common = min(len(left_chunk), len(right_chunk))
            for index in range(common):
                if left_chunk[index] != right_chunk[index]:
                    absolute = offset + index
                    differences.append(
                        {
                            "offset": absolute,
                            "source_byte": left_chunk[index],
                            "reconstructed_byte": right_chunk[index],
                            "area": _binary_area(
                                absolute,
                                kind,
                                header_length=header_length,
                                record_length=record_length,
                            ),
                        }
                    )
                    if len(differences) >= limit:
                        break
            if len(differences) >= limit:
                break
            if len(left_chunk) != len(right_chunk):
                absolute = offset + common
                differences.append(
                    {
                        "offset": absolute,
                        "source_byte": left_chunk[common] if common < len(left_chunk) else None,
                        "reconstructed_byte": right_chunk[common]
                        if common < len(right_chunk)
                        else None,
                        "area": "file_length_or_trailing_data",
                    }
                )
                break
            offset += common
    return differences


def _binary_area(
    offset: int,
    kind: str,
    *,
    header_length: int | None = None,
    record_length: int | None = None,
) -> str:
    if kind == "fpt":
        if 0 <= offset <= 3:
            return "fpt_header.next_free_block"
        if 4 <= offset <= 5:
            return "fpt_header.reserved"
        if 6 <= offset <= 7:
            return "fpt_header.block_size"
        if offset < 512:
            return "fpt_header.reserved"
        return "fpt_memo_block_data_or_padding"
    if offset == 0:
        return "header.version"
    if 1 <= offset <= 3:
        return "header.last_update"
    if 4 <= offset <= 7:
        return "header.record_count"
    if 8 <= offset <= 11:
        return "header.layout_lengths"
    if offset == 14:
        return "header.incomplete_transaction"
    if offset == 15:
        return "header.encryption"
    if offset == 28:
        return "header.structural_index_flag"
    if offset == 29:
        return "header.language_driver"
    if header_length is not None and 32 <= offset < header_length:
        return "field_descriptors_or_header_padding"
    if header_length is not None and record_length:
        relative = offset - header_length
        if relative >= 0:
            record_index, record_offset = divmod(relative, record_length)
            return f"record_data[record={record_index + 1},offset={record_offset}]"
    return "record_data_or_file_tail"


def _raw_dbf_causes(comparison: dict[str, Any], deleted_records: int) -> list[str]:
    areas = {item.get("area") for item in comparison.get("first_different_offsets", [])}
    causes: list[str] = []
    if "header.structural_index_flag" in areas:
        causes.append(
            "The source references CDX metadata that cannot be recreated from field schema."
        )
    if "header.last_update" in areas:
        causes.append("The DBF last-update header bytes differ.")
    if "field_descriptors_or_header_padding" in areas:
        causes.append("A field descriptor, reserved header byte, or VFP backlink area differs.")
    record_areas = {area for area in areas if str(area).startswith("record_data")}
    if deleted_records and record_areas:
        causes.append(
            "The JSONL may predate physical-order/raw-record metadata, so deleted record "
            "positions may not be reproducible."
        )
    elif record_areas:
        causes.append(
            "Record bytes differ (for example memo pointers, numeric formatting, or blank "
            "logical markers); compare canonical data and raw-layout metadata."
        )
    if not causes:
        causes.append("Inspect first_different_offsets and the retained JSONL artifacts.")
    return causes


def _compare_jsonl(
    expected: Path,
    actual: Path,
    *,
    max_differences: int,
) -> list[dict[str, Any]]:
    if not expected.is_file() or not actual.is_file():
        return [
            {
                "scope": "records",
                "message": "Cannot compare records because one JSONL artifact is missing.",
            }
        ]
    differences: list[dict[str, Any]] = []
    with expected.open("r", encoding="utf-8") as left, actual.open("r", encoding="utf-8") as right:
        for record_number, pair in enumerate(zip_longest(left, right), start=1):
            left_line, right_line = pair
            if left_line == right_line:
                continue
            if left_line is None or right_line is None:
                differences.append(
                    {
                        "scope": "record_count",
                        "record": record_number,
                        "expected_present": left_line is not None,
                        "actual_present": right_line is not None,
                    }
                )
            else:
                expected_record = json.loads(left_line)
                actual_record = json.loads(right_line)
                for field in sorted(set(expected_record) | set(actual_record)):
                    if expected_record.get(field) != actual_record.get(field):
                        differences.append(
                            {
                                "scope": "field",
                                "record": record_number,
                                "field": field,
                                "expected": _value_diagnostic(expected_record.get(field)),
                                "actual": _value_diagnostic(actual_record.get(field)),
                            }
                        )
                        if len(differences) >= max_differences:
                            return differences
            if len(differences) >= max_differences:
                return differences
    return differences


def _value_diagnostic(value: Any) -> dict[str, Any]:
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True).encode("utf-8")
    preview = str(value)
    if len(preview) > 160:
        preview = f"{preview[:157]}..."
    return {
        "type": type(value).__name__,
        "length": len(value) if isinstance(value, (str, list, dict)) else None,
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "preview": preview,
    }


def _write_quality_report(
    path: Path,
    summary: dict[str, Any],
    reports: list[dict[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.partial")
    with partial.open("w", encoding="utf-8", newline="\n") as outfile:
        for entry in [summary, *reports]:
            outfile.write(
                json.dumps(entry, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"
            )
        outfile.flush()
        os.fsync(outfile.fileno())
    os.replace(partial, path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dbf-bridge-quality",
        description="Runs DBF -> JSONL -> DBF and produces a diagnostic quality report.",
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--progress", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--max-differences", type=int, default=20)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        from dbf_bridge import check_conversion_quality

        run = check_conversion_quality(
            args.source,
            args.output,
            overwrite=args.overwrite,
            max_differences=args.max_differences,
            progress=(
                lambda event: print(
                    f"[quality] {event.message} {event.current}/{event.total}: {event.table}"
                )
            )
            if args.progress
            else None,
        )
        reports = run.reports
        summary = run.summary
    except Exception as exc:
        print(f"[quality] Failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"[quality] Tables: {summary['tables']}  OK: {summary['ok']}  "
        f"Warnings: {summary['warning']}  Errors: {summary['failed']}"
    )
    for report in reports:
        if report["status"] != "OK":
            print(
                f"  - {report['table']}: {report['status']} | "
                f"differences={len(report['differences'])}"
            )
    print(f"[quality] Report: {args.output / 'conversion_quality_report.jsonl'}")
    return run.exit_code


if __name__ == "__main__":
    sys.exit(main())

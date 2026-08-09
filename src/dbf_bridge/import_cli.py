"""Command-line reconstruction of DBF/FPT tables from one exported format."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dbf_bridge import reconstruct_dbf

FORMATS = ("jsonl", "json", "csv", "xlsx")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dbf-bridge-import",
        description=(
            "Reconstructs Visual FoxPro DBF/FPT files from one selected export format "
            "and companion *_schema.json files."
        ),
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--formats",
        required=True,
        help="Exactly one input format: jsonl, json, csv, or xlsx.",
    )
    parser.add_argument("--memo", choices=["inline", "null"], default="inline")
    parser.add_argument(
        "--overwrite",
        action=argparse.BooleanOptionalAction,
        default=False,
    )
    parser.add_argument(
        "--progress",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    return parser


def _one_format(value: str) -> str:
    values = [part.strip().lower() for part in value.split(",") if part.strip()]
    if len(values) != 1 or values[0] not in FORMATS:
        raise ValueError("--formats must select exactly one of: jsonl, json, csv, xlsx")
    return values[0]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.source.exists():
        print(f"[dbf-bridge-import] Source does not exist: {args.source}", file=sys.stderr)
        return 1
    try:
        input_format = _one_format(args.formats)
    except ValueError as exc:
        print(f"[dbf-bridge-import] {exc}", file=sys.stderr)
        return 1
    try:
        run = reconstruct_dbf(
            args.source,
            args.output,
            input_format=input_format,
            memo=args.memo,
            overwrite=args.overwrite,
            progress=(
                lambda event: print(
                    f"\r[dbf-bridge-import] {event.current}/{event.total} "
                    f"{event.table}: {event.records or 0:,} rekordów",
                    end="\n" if event.records is not None else "",
                    flush=True,
                )
            )
            if args.progress
            else None,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"[dbf-bridge-import] {exc}", file=sys.stderr)
        return 1
    results = run.results
    ok = sum(result.status == "OK" for result in results)
    warning = sum(result.status == "WARNING" for result in results)
    failed = sum(result.status == "FAILED" for result in results)
    print(
        f"[dbf-bridge-import] Tables: {len(results)}  OK: {ok}  "
        f"Warnings: {warning}  Errors: {failed}"
    )
    for result in results:
        if result.status != "OK":
            details = "; ".join([*result.warnings, *result.errors])
            print(f"  - {result.source}: {result.status} | {details}")
    print(f"[dbf-bridge-import] Report: {args.output / 'reconstruction_report.jsonl'}")
    return run.exit_code


if __name__ == "__main__":
    sys.exit(main())

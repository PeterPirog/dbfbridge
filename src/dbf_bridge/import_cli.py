"""Command-line reconstruction of DBF/FPT tables from one exported format."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dbf_bridge.importer import ImportConfig, reconstruct_tree

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
    args.output.mkdir(parents=True, exist_ok=True)
    results = reconstruct_tree(
        ImportConfig(
            source=args.source,
            output=args.output,
            format=input_format,  # type: ignore[arg-type]
            memo=args.memo,
            overwrite=args.overwrite,
            progress=args.progress,
        )
    )
    if not results:
        print(f"[dbf-bridge-import] No *.{input_format} data files found.", file=sys.stderr)
        return 1
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
    return 1 if failed else 2 if warning else 0


if __name__ == "__main__":
    sys.exit(main())

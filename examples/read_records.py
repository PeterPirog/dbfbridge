"""Streaming direct record read (Phase 1B) example.

Read-only, streaming record access over one DBF table:

    python examples/read_records.py --dbf path\\to\\table.dbf
    python examples/read_records.py --dbf path\\to\\table.dbf --offset 100 --limit 50
    python examples/read_records.py --dbf path\\to\\table.dbf --json
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from dbfbridge import (
    DirectRecord,
    LazyMemoValue,
    RecordPage,
    iter_raw_records,
    iter_records,
    read_records,
    read_schema,
)


def build_parser() -> Any:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dbf", required=True, help="path to one DBF table")
    parser.add_argument("--offset", type=int, default=0, help="zero-based physical record index")
    parser.add_argument("--limit", type=int, default=10, help="page size (positive)")
    parser.add_argument("--memo", default="lazy", choices=["lazy", "inline", "null", "skip"])
    parser.add_argument("--fields", default="", help="comma-separated field projection")
    parser.add_argument("--json", action="store_true", help="print JSON-safe payloads")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    fields = [name.strip() for name in args.fields.split(",") if name.strip()] or None

    schema = read_schema(args.dbf)
    print(f"{schema.dbversion_name} encoding={schema.encoding} records={schema.record_count}")

    page: RecordPage = read_records(
        args.dbf,
        offset=args.offset,
        limit=args.limit,
        fields=fields,
        memo=args.memo,
    )
    for record in page.records:
        _print_record(record, json_mode=args.json)
    print(
        f"page offset={page.offset} limit={page.limit} scanned={page.scanned} "
        f"next_offset={page.next_offset} exhausted={page.exhausted}"
    )

    print("lazy streaming (O(1) memory; the FPT payload is read on demand only):")
    for record in iter_records(args.dbf, fields=fields, memo="lazy"):
        for value in record.values.values():
            if isinstance(value, LazyMemoValue):
                print(
                    f"  lazy memo {value.field_name} block={value.block} "
                    f"format={value.memo_format} -> {value.load()!r}"
                )

    print("raw streaming (pure forensic: no field is decoded, the FPT is never")
    print("opened, and even damaged text bytes cannot hide the record image):")
    for record in iter_raw_records(args.dbf):
        print(record.physical_index, record.deleted, len(record.raw_record or b""))
    return 0


def _print_record(record: DirectRecord, *, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(record.to_dict(), ensure_ascii=False))
        return
    print(record.physical_index, record.deleted, record.values)


if __name__ == "__main__":
    raise SystemExit(main())

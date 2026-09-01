"""Inspect a single DBF table without creating any files (Phase 1A direct read).

This is a read-only operation: the record area is never read, no output or
temporary files are created, and the source stays byte-identical.

Running from a repository checkout (no install required):

    python examples/inspect_table.py --dbf path\\to\\table.dbf
    python examples/inspect_table.py --dbf path\\to\\table.dbf --json

Requirements:

    pip install dbfbridge

The example prints a compact ``TableInfo`` summary, the physical field layout,
and (with ``--json``) the full JSON-safe ``TableSchema`` payload.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = _REPO_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from dbfbridge import (  # noqa: E402
    DirectReadError,
    inspect_table,
    read_schema,
)


def summarize(dbf_path: Path, as_json: bool) -> int:
    info = inspect_table(dbf_path)
    if not as_json:
        print(f"table:        {info.path.name}")
        print(f"records:      {info.record_count}")
        print(f"header/record: {info.header_length} / {info.record_length} bytes")
        print(f"language:     0x{info.language_driver:02x} -> {info.encoding}")
        print(f"memo fields:  {info.has_memo}")
        print(f"structural CDX flag: {info.has_structural_cdx}")
        print(f"dbc-bound:    {info.dbc_bound}")
        print("fields:")
        for field in info.fields:
            suffix = " (memo)" if field.is_memo else ""
            note = "" if field.supported else f"  [unsupported: {field.unsupported_reason}]"
            print(
                f"  {field.ordinal:>3} {field.name:<12} "
                f"{field.dbf_type} {field.dbf_type_name}{suffix}{note}"
            )
        for warning in info.warnings:
            print(f"warning: {warning}")
    if as_json:
        schema = read_schema(dbf_path)
        print(json.dumps(schema.to_dict(), ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only inspection of one DBF table (no files created)."
    )
    parser.add_argument("--dbf", required=True, type=Path, help="Path to the .dbf file.")
    parser.add_argument("--json", action="store_true", help="Emit the full JSON schema.")
    args = parser.parse_args(argv)

    try:
        return summarize(args.dbf, args.json)
    except DirectReadError as error:
        print(f"error [{error.code.value}]: {error.message}", file=sys.stderr)
        if error.context:
            print(json.dumps(error.to_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

"""Direct copy example (RESEARCH API): read_schema + iter_records + write_table.

The direct pair never materializes an intermediate JSONL file.  ``write_table``
is a RESEARCH (next-version) API — it is intentionally NOT exported from the
package root while the capability is unreleased.

python examples/direct_copy.py --dbf path\\to\\source.dbf --dest path\\to\\output.dbf
"""

from __future__ import annotations

import argparse

from dbf_bridge.write import write_table
from dbfbridge import iter_records, read_schema


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dbf", required=True, help="source DBF table")
    parser.add_argument("--dest", required=True, help="output DBF path")
    parser.add_argument("--overwrite", action="store_true", help="overwrite the output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    schema = read_schema(args.dbf)
    records = iter_records(args.dbf, include_deleted=True, memo="inline")
    result = write_table(args.dest, schema=schema, records=records, overwrite=args.overwrite)
    print(
        f"{result.records_written} records, deleted={result.deleted_records}, "
        f"structural_cdx={result.structural_cdx}, "
        f"index_rebuild_required={result.index_rebuild_required}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

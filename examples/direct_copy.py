"""Direct-copy example: DBF -> typed schema + records -> fresh DBF/FPT pair.

Requires the ``[write]`` extra (``python -m pip install "dbfbridge[write]"``).
Uses ONLY the public API (dbfbridge v1.1 contract, docs/api-1.1.md):

    python examples/direct_copy.py <source.dbf> <destination.dbf>

The source table is never modified: ``write_table`` creates a fresh output
pair from the typed schema and the streamed records. The caller iterable is
consumed exactly once, and the DBF+FPT pair is published atomically.
"""

from __future__ import annotations

import sys
from pathlib import Path

from dbfbridge import iter_records, read_schema, write_table


def main() -> int:
    if len(sys.argv) != 3:
        print(f"usage: {Path(__file__).name} <source.dbf> <destination.dbf>")
        return 2
    source = Path(sys.argv[1])
    destination = Path(sys.argv[2])

    schema = read_schema(source)
    records = iter_records(source, memo="inline", include_deleted=True)

    result = write_table(
        destination,
        schema=schema,
        records=records,
        overwrite=False,  # default: existing output is refused (OUTPUT_EXISTS)
        cancel_check=None,  # supply a callable() -> bool for cooperative cancel
    )

    payload = result.to_dict()
    print(f"records_written : {payload['records_written']}")
    print(f"deleted_records : {payload['deleted_records']}")
    print(f"destination     : {payload['destination']}")
    print(f"dbf_sha256      : {payload['dbf_sha256']}")
    for warning in payload["warnings"]:
        print(f"warning         : {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

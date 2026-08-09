"""Use dbfbridge as a Python library instead of spawning its CLI commands."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = _REPO_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from dbfbridge import ProgressEvent, export_dbf, reconstruct_dbf, verify_conversion


def print_progress(event: ProgressEvent) -> None:
    """Example callback suitable for replacement with a GUI or job-queue update."""
    records = f", records={event.records:,}" if event.records is not None else ""
    print(
        f"[{event.operation}] {event.current}/{event.total}: "
        f"{event.table or '-'} ({event.format or '-'}){records}"
    )


def migrate(source: Path, output: Path, reconstructed: Path, *, incremental: bool) -> None:
    export = export_dbf(
        source,
        output,
        formats=("csv", "json", "jsonl", "xlsx"),
        memo="inline",
        overwrite=True,
        incremental=incremental,
        progress=print_progress,
    )
    print(f"Export: OK={export.ok}, skipped={export.skipped}, failed={export.failed}")
    export.raise_for_errors()

    verification = verify_conversion(
        source,
        output,
        formats=("csv", "json", "jsonl", "xlsx"),
    )
    print(f"Verification exit code: {verification.exit_code}")
    verification.raise_for_errors()

    reconstruction = reconstruct_dbf(
        output,
        reconstructed,
        input_format="jsonl",
        memo="inline",
        overwrite=True,
        progress=print_progress,
    )
    print(
        f"Reconstruction: OK={reconstruction.ok}, "
        f"warnings={reconstruction.warning}, failed={reconstruction.failed}"
    )
    reconstruction.raise_for_errors()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reconstructed", type=Path, required=True)
    parser.add_argument("--incremental", action="store_true")
    args = parser.parse_args()
    migrate(args.source, args.output, args.reconstructed, incremental=args.incremental)


if __name__ == "__main__":
    main()

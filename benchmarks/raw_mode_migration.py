"""Migration raw-mode cost comparison (none vs full-record).

Standalone evidence script for the RawMode split (architecture §7, closure
BLK-02).  It does NOT touch the canonical Phase 3 baseline, the regression
policy, or any committed benchmark contract: it builds a throwaway fixture,
runs the public migration export twice (``raw_mode="none"`` vs
``raw_mode="full-record"``) and reports the measured cost difference.

Note: the existing benchmark scenarios named ``raw_mode_none`` and
``raw_record_metadata_default`` measure the *Direct Read* ``raw=`` flag cost
(``iter_records``), not the migration export raw-retention cost — this script
fills that gap with the smallest possible migration-level measurement.

Run:  python -m benchmarks.raw_mode_migration
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import dbf

from dbf_bridge import export_dbf

RECORDS = 50_000
NAME = "rawmode_bench.dbf"


def _build_fixture(root: Path) -> Path:
    path = root / NAME
    table = dbf.Table(str(path), "KOD N(8,0); NAZWA C(60); KWOTA N(12,2)", dbf_type="vfp", codepage=0xC8)
    table.open(dbf.READ_WRITE)
    for index in range(RECORDS):
        table.append(
            {
                "KOD": index,
                "NAZWA": f"pozycja-{index:07d}-" + "x" * 40,
                "KWOTA": round(index + 0.25, 2),
            }
        )
    table.close()
    return path


def _output_bytes(output: Path) -> int:
    return sum(file.stat().st_size for file in output.rglob("*") if file.is_file())


def _measure(raw_mode: str, source: Path, root: Path) -> dict[str, float | int | str]:
    output = root / f"out-{raw_mode}"
    started = time.perf_counter()
    run = export_dbf(source, output, formats=("jsonl",), raw_mode=raw_mode, overwrite=True)
    elapsed = time.perf_counter() - started
    records = int(run.results[0].active_records)
    return {
        "raw_mode": raw_mode,
        "wall_seconds": round(elapsed, 3),
        "output_bytes": _output_bytes(output),
        "records": records,
        "records_per_second": int(records / elapsed) if elapsed else 0,
    }


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="dbfbridge-rawmode-") as tmp:
        root = Path(tmp)
        source = _build_fixture(root)
        source_bytes = source.stat().st_size
        results = [_measure(mode, source, root) for mode in ("none", "full-record")]
        for result in results:
            result["output_to_source"] = round(
                float(result["output_bytes"]) / source_bytes, 3
            )

        print(f"fixture: {NAME} ({RECORDS:,} records, {source_bytes:,} bytes)")
        header = f"{'metric':<24}{'none':>16}{'full-record':>16}"
        print(header)
        print("-" * len(header))
        for label, key in (
            ("wall time [s]", "wall_seconds"),
            ("output bytes", "output_bytes"),
            ("records", "records"),
            ("records/s", "records_per_second"),
            ("output/source ratio", "output_to_source"),
        ):
            values = [str(result[key]) for result in results]
            print(f"{label:<24}{values[0]:>16}{values[1]:>16}")

        evidence = Path(os.environ.get("RAWMODE_EVIDENCE_DIR", ".")) / "raw-mode-comparison.json"
        payload = {
            "fixture": {
                "records": RECORDS,
                "columns": ["KOD N(8,0)", "NAZWA C(60)", "KWOTA N(12,2)"],
            },
            "measured": results,
        }
        evidence.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"\nevidence written to: {evidence}")


if __name__ == "__main__":
    main()

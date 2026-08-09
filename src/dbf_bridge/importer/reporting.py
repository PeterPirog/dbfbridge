from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .models import ReconstructionResult


def write_reconstruction_report(path: Path, results: list[ReconstructionResult]) -> None:
    summary = {
        "type": "summary",
        "report_version": 2,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tables": len(results),
        "ok": sum(result.status == "OK" for result in results),
        "warning": sum(result.status == "WARNING" for result in results),
        "failed": sum(result.status == "FAILED" for result in results),
        "canonical_matches": sum(result.canonical_match is True for result in results),
        "canonical_mismatches": sum(result.canonical_match is False for result in results),
        "raw_dbf_matches": sum(result.raw_dbf_match is True for result in results),
        "raw_dbf_mismatches": sum(result.raw_dbf_match is False for result in results),
        "raw_dbf_unverifiable": sum(result.raw_dbf_match is None for result in results),
        "raw_layout_restored": sum(result.raw_layout_restored for result in results),
        "raw_fpt_matches": sum(result.raw_fpt_match is True for result in results),
        "raw_fpt_mismatches": sum(result.raw_fpt_match is False for result in results),
        "raw_fpt_unverifiable": sum(
            result.fpt_output is not None and result.raw_fpt_match is None for result in results
        ),
    }
    lines = [summary, *({"type": "table", **result.to_dict()} for result in results)]
    text = "".join(
        json.dumps(line, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"
        for line in lines
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.partial")
    with partial.open("w", encoding="utf-8", newline="\n") as outfile:
        outfile.write(text)
        outfile.flush()
        os.fsync(outfile.fileno())
    os.replace(partial, path)

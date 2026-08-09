from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import orjson

from dbf_bridge.converters import jsonl_to_csv, jsonl_to_json, jsonl_to_xlsx


def generate_jsonl(path: Path, size_mb: int) -> int:
    target_size = size_mb * 1024 * 1024
    record_count = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb", buffering=16 * 1024 * 1024) as outfile:
        while outfile.tell() < target_size:
            record = {
                "id": record_count,
                "name": f"Klient {record_count}",
                "city": "Zażółć gęślą jaźń",
                "active": record_count % 2 == 0,
                "amount": record_count / 100,
                "note": "syntetyczny rekord benchmarkowy " * 2,
            }
            outfile.write(orjson.dumps(record))
            outfile.write(b"\n")
            record_count += 1
        outfile.flush()
        os.fsync(outfile.fileno())
    path.with_suffix(".benchmark.json").write_text(
        json.dumps({"records": record_count}), encoding="utf-8"
    )
    return record_count


def legacy_json(source: Path, destination: Path) -> int:
    with source.open("rb") as infile:
        records = [orjson.loads(line) for line in infile if line.strip()]
    with destination.open("w", encoding="utf-8") as outfile:
        json.dump(records, outfile, ensure_ascii=False, separators=(",", ":"))
    return len(records)


def legacy_csv(source: Path, destination: Path) -> int:
    import polars as pl

    frame = pl.read_ndjson(source)
    frame.write_csv(destination)
    return frame.height


def peak_rss_mb() -> float | None:
    try:
        import resource
    except ImportError:
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return peak / (1024 * 1024)
    return peak / 1024


def run(mode: str, source: Path, destination: Path) -> dict[str, Any]:
    metadata_path = source.with_suffix(".benchmark.json")
    if metadata_path.exists():
        expected_records = json.loads(metadata_path.read_text(encoding="utf-8"))["records"]
    else:
        with source.open("rb") as infile:
            expected_records = sum(1 for line in infile if line.strip())
    started = time.perf_counter()
    if mode == "json":
        stats = jsonl_to_json(source, destination)
        records = stats.record_count
        engine = stats.engine
    elif mode == "csv":
        stats = jsonl_to_csv(
            source,
            destination,
            columns=["id", "name", "city", "active", "amount", "note"],
            schema_types={
                "id": "integer",
                "name": "string",
                "city": "string",
                "active": "boolean",
                "amount": "number",
                "note": "string",
            },
            expected_record_count=expected_records,
            source_is_validated=True,
        )
        records = stats.record_count
        engine = stats.engine
    elif mode == "xlsx":
        stats = jsonl_to_xlsx(
            source,
            destination,
            columns=["id", "name", "city", "active", "amount", "note"],
        )
        records = stats.record_count
        engine = stats.engine
    elif mode == "legacy-json":
        records = legacy_json(source, destination)
        engine = "materialized-list"
    else:
        records = legacy_csv(source, destination)
        engine = "materialized-polars"
    elapsed = time.perf_counter() - started
    input_size = source.stat().st_size
    return {
        "mode": mode,
        "engine": engine,
        "records": records,
        "input_mb": round(input_size / (1024 * 1024), 2),
        "output_mb": round(destination.stat().st_size / (1024 * 1024), 2),
        "elapsed_seconds": round(elapsed, 3),
        "mb_per_second": round(input_size / (1024 * 1024) / elapsed, 2),
        "peak_rss_mb": round(peak, 2) if (peak := peak_rss_mb()) is not None else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode", choices=["generate", "json", "csv", "xlsx", "legacy-json", "legacy-csv"]
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path, nargs="?")
    parser.add_argument("--size-mb", type=int, default=100)
    args = parser.parse_args()

    if args.mode == "generate":
        records = generate_jsonl(args.source, args.size_mb)
        print(
            json.dumps(
                {
                    "mode": "generate",
                    "records": records,
                    "size_mb": round(args.source.stat().st_size / (1024 * 1024), 2),
                }
            )
        )
        return 0
    if args.destination is None:
        parser.error("destination is required for conversion modes")
    print(json.dumps(run(args.mode, args.source, args.destination), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

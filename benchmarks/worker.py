"""Worker: run one dbfbridge Phase 0 scenario (or a full profile) in this process.

Invoked by the controller (``run_benchmark.py``) inside a dedicated
subprocess so that a crash in one scenario cannot take down the report or
the controller.  The worker prints a JSON payload to stdout:

    {"ok": true, "payload": {...}}
    {"ok": false, "error": "..."}

Usage:
    python -m benchmarks.worker --profile fast --work-dir <dir>
    python -m benchmarks.worker --profile fast --work-dir <dir> --scenario <name>
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from benchmarks import fixtures as fixture_factory  # noqa: E402
from benchmarks import metrics as bench_metrics  # noqa: E402

STATUS_FAILED = "FAILED"
STATUS_MEASURED = "MEASURED"
STATUS_NOT_AVAILABLE = "NOT_AVAILABLE"
STATUS_NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


def not_implemented(name: str, reason: str) -> dict[str, object]:
    return {
        "scenario": name,
        "description": reason,
        "status": STATUS_NOT_IMPLEMENTED,
        "parameters": {},
        "metrics": {},
    }


class Runner:
    """Executes scenarios in-process; the controller isolates it per scenario."""

    @classmethod
    def scenario_names(cls, profile: str) -> tuple[str, ...]:
        """Ordered scenario names for a profile (no instance state required)."""

        fast = (
            "jsonl_conversion_existing",
            "raw_record_metadata_default",
            "export_jsonl_validate_on",
            "export_jsonl_validate_off",
            "memo_skip",
            "memo_null",
            "memo_inline",
            "deleted_skip",
            "deleted_include",
            "encoding_cp1250",
            "encoding_cp852",
            "encoding_mazovia",
            "reconstruction_jsonl_to_dbf",
            "roundtrip_quality",
        )
        if profile != "full":
            return fast
        return fast + (
            "export_1m_records",
            "memo_heavy_190k",
            "reconstruction_190k",
        )

    def __init__(self, root: Path, profile: str, work_dir: Path, repetitions: int) -> None:
        self.root = root
        self.profile = profile
        self.work_dir = work_dir
        self.repetitions = max(1, repetitions)
        self.fixture_dir = work_dir / "fixtures"
        self.results: list[dict[str, object]] = []

    # ------------------------------------------------------------------ fixtures

    def small(self) -> Path:
        path = self.fixture_dir / "small" / "small.dbf"
        if not path.exists():
            fixture_factory.generate_flat(path, 300)
        return path

    def medium(self) -> Path:
        path = self.fixture_dir / "medium" / "medium.dbf"
        if not path.exists():
            fixture_factory.generate_flat(path, 190_000)
        return path

    def large(self) -> Path:
        path = self.fixture_dir / "large" / "large.dbf"
        if not path.exists():
            fixture_factory.generate_flat(path, 1_000_000)
        return path

    def memo_heavy(self, records: int) -> Path:
        path = self.fixture_dir / "memo" / f"memo{records}.dbf"
        if not path.exists():
            fixture_factory.generate_memo_heavy(path, records)
        return path

    def with_deleted(self) -> Path:
        path = self.fixture_dir / "deleted" / "deleted.dbf"
        if not path.exists():
            fixture_factory.generate_flat(path, 1_000, deleted_fraction=0.1)
        return path

    def fixture_manifest(self) -> dict[str, object]:
        return fixture_factory.fixture_manifest(self.fixture_dir)

    # ------------------------------------------------------------------ helpers

    def _record(self, name: str, description: str, function, **context: object) -> None:
        input_bytes_raw = context.pop("input_bytes", None)
        input_records_raw = context.pop("input_records", None)
        input_bytes = int(input_bytes_raw) if isinstance(input_bytes_raw, int) else None
        input_records = int(input_records_raw) if isinstance(input_records_raw, int) else None
        result = bench_metrics.run(
            function,
            input_bytes=input_bytes,
            input_records=input_records,
            output_dir=self.work_dir / "out",
        )
        self.results.append(
            {
                "scenario": name,
                "description": description,
                "status": result.pop("status"),
                "parameters": context,
                "metrics": result,
            }
        )

    def export(
        self,
        name: str,
        description: str,
        source: Path,
        *,
        memo: str | None = None,
        deleted: str = "skip",
        encoding: str = "auto",
        validate: bool = True,
        input_records: int | None = None,
        **extra: object,
    ) -> Path:
        from dbfbridge import export_dbf

        output_dir = self.work_dir / "out" / name / "export"
        output_dir.mkdir(parents=True, exist_ok=True)
        input_bytes = sum(p.stat().st_size for p in source.parent.glob("*") if p.is_file())

        def run() -> None:
            result = export_dbf(
                str(source.parent),
                str(output_dir),
                formats=("jsonl",),
                memo=memo,  # type: ignore[arg-type]
                deleted=deleted,  # type: ignore[arg-type]
                encoding=encoding,
                overwrite=True,
                validate=validate,
            )
            result.raise_for_errors()

        self._record(
            name,
            description,
            run,
            input_bytes=input_bytes,
            input_records=input_records,
            memo=memo,
            deleted=deleted,
            encoding=encoding,
            validate=validate,
            **extra,
        )
        return output_dir

    # ------------------------------------------------------------------ scenarios

    def scenario_jsonl_conversion(self) -> None:
        name = "jsonl_conversion_existing"
        source = self.work_dir / "jsonl" / "input.jsonl"
        destination = self.work_dir / "out" / name
        destination.mkdir(parents=True, exist_ok=True)
        if not source.exists():
            script = self.root / "benchmarks" / "benchmark_jsonl_conversion.py"
            subprocess.run(
                [sys.executable, str(script), "generate", str(source), "--size-mb", "20"],
                check=True,
                capture_output=True,
            )

        def run() -> None:
            script = self.root / "benchmarks" / "benchmark_jsonl_conversion.py"
            subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "json",
                    str(source),
                    str(destination / "output.json"),
                ],
                check=True,
                capture_output=True,
            )

        self._record(
            name,
            "Existing JSONL->JSON benchmark (benchmarks/benchmark_jsonl_conversion.py)",
            run,
            input_bytes=source.stat().st_size,
            engine="jsonl_to_json",
        )

    def scenario_reconstruction(self, source_dbf: Path, input_records: int | None) -> None:
        name = "reconstruction_jsonl_to_dbf"
        export_dir = self.work_dir / "out" / name / "export"
        if not export_dir.exists():
            from dbfbridge import export_dbf

            export_dir.mkdir(parents=True, exist_ok=True)
            result = export_dbf(
                str(source_dbf.parent),
                str(export_dir),
                formats=("jsonl",),
                deleted="include",
                overwrite=True,
            )
            result.raise_for_errors()
        output_dir = self.work_dir / "out" / name / "rebuilt"
        output_dir.mkdir(parents=True, exist_ok=True)
        from dbfbridge import reconstruct_dbf

        def run() -> None:
            result = reconstruct_dbf(
                str(export_dir), str(output_dir), input_format="jsonl", overwrite=True
            )
            result.raise_for_errors()

        self._record(
            name,
            "JSONL -> DBF/FPT reconstruction (reconstruct_dbf)",
            run,
            input_bytes=sum(p.stat().st_size for p in export_dir.rglob("*") if p.is_file()),
            input_records=input_records,
        )

    def scenario_roundtrip(self, source_dbf: Path) -> None:
        name = "roundtrip_quality"
        from dbfbridge import check_conversion_quality

        output_dir = self.work_dir / "out" / name
        output_dir.mkdir(parents=True, exist_ok=True)

        def run() -> None:
            result = check_conversion_quality(
                str(source_dbf.parent), str(output_dir), overwrite=True
            )
            result.raise_for_errors()

        self._record(
            name,
            "Full DBF -> JSONL -> DBF round trip with verification (check_conversion_quality)",
            run,
            input_bytes=sum(p.stat().st_size for p in source_dbf.parent.iterdir() if p.is_file()),
        )

    def scenario_raw_metadata_baseline(self, source_dbf: Path) -> None:
        name = "raw_record_metadata_default"
        export_dir = self.export(
            name,
            "Default JSONL export with raw-record metadata (raw_mode baseline)",
            source_dbf,
            deleted="include",
        )
        jsonl = next(iter(export_dir.glob("*.jsonl")))
        total = jsonl.stat().st_size
        raw = 0
        with jsonl.open("r", encoding="utf-8") as infile:
            for line in infile:
                if "__dbfbridge_raw_record__" not in line:
                    continue
                payload = line.split("__dbfbridge_raw_record__", 1)[1]
                raw += len(payload)
        self.results.append(
            {
                "scenario": name,
                "description": (
                    "Raw-record Base64 share of JSONL output; measured by parsing the "
                    "reserved __dbfbridge_raw_record__ property written by "
                    "exporter/writer.py. raw_mode option does not exist in 0.1.0."
                ),
                "status": STATUS_MEASURED,
                "parameters": {"jsonl_bytes": total, "raw_bytes_included": raw},
                "metrics": {
                    "raw_share": round(raw / total, 4) if total else None,
                    "jsonl_bytes": total,
                    "raw_bytes_included": raw,
                },
            }
        )

    # ------------------------------------------------------------------ registry

    def _memo_records(self) -> int:
        return 2_000 if self.profile == "fast" else 4_000

    def registry(self) -> dict[str, tuple[str, int | None]]:  # noqa: F811  (kept for --list)
        """Ordered mapping of scenario name -> (source description, record count)."""

        medium = 190_000
        memo = self._memo_records()
        entries: dict[str, tuple[str, int | None]] = {
            "jsonl_conversion_existing": ("Existing JSONL->JSON conversion", None),
            "raw_record_metadata_default": ("Default raw-record metadata export", 300),
            "export_jsonl_validate_on": ("DBF->JSONL validation enabled", medium),
            "export_jsonl_validate_off": ("DBF->JSONL validation disabled", medium),
            "memo_skip": ("DBF->JSONL memo=skip", memo),
            "memo_null": ("DBF->JSONL memo=null", memo),
            "memo_inline": ("DBF->JSONL memo=inline", memo),
            "deleted_skip": ("DBF->JSONL deleted=skip", 1_000),
            "deleted_include": ("DBF->JSONL deleted=include", 1_000),
            "encoding_cp1250": ("DBF->JSONL encoding=cp1250", 300),
            "encoding_cp852": ("DBF->JSONL encoding=cp852", 300),
            "encoding_mazovia": ("DBF->JSONL encoding=mazovia", 300),
            "reconstruction_jsonl_to_dbf": ("JSONL->DBF reconstruction", 300),
            "roundtrip_quality": ("DBF->JSONL->DBF round trip + verification", 300),
        }
        if self.profile == "full":
            entries["export_1m_records"] = ("DBF->JSONL 1,000,000 records", 1_000_000)
            entries["memo_heavy_190k"] = ("DBF/FPT memo-heavy 190,000 records", 190_000)
            entries["reconstruction_190k"] = ("JSONL->DBF reconstruction 190,000 records", medium)
        return entries

    def run_scenario(self, name: str) -> None:
        if name == "jsonl_conversion_existing":
            self.scenario_jsonl_conversion()
        elif name == "raw_record_metadata_default":
            self.scenario_raw_metadata_baseline(self.small())
        elif name == "export_jsonl_validate_on":
            self.export(
                name,
                "DBF -> JSONL with output validation enabled (default)",
                self.medium(),
                validate=True,
                input_records=190_000,
            )
        elif name == "export_jsonl_validate_off":
            self.export(
                name,
                "DBF -> JSONL with output validation disabled (--no-validate)",
                self.medium(),
                validate=False,
                input_records=190_000,
            )
        elif name in {"memo_skip", "memo_null", "memo_inline"}:
            memo = name.removeprefix("memo_")
            self.export(
                name,
                f"DBF -> JSONL memo={memo}",
                self.memo_heavy(self._memo_records()),
                memo=memo,  # type: ignore[arg-type]
                input_records=self._memo_records(),
            )
        elif name in {"deleted_skip", "deleted_include"}:
            policy = name.removeprefix("deleted_")
            self.export(
                name,
                f"DBF -> JSONL deleted={policy} (10% deleted rows)",
                self.with_deleted(),
                deleted=policy,  # type: ignore[arg-type]
                input_records=1_000,
            )
        elif name in {"encoding_cp1250", "encoding_cp852", "encoding_mazovia"}:
            codec = name.removeprefix("encoding_")
            self.export(
                name,
                f"DBF -> JSONL with encoding forced to {codec}",
                self.small(),
                encoding=codec,
            )
        elif name == "reconstruction_jsonl_to_dbf":
            self.scenario_reconstruction(self.small(), 300)
        elif name == "roundtrip_quality":
            self.scenario_roundtrip(self.small())
        elif name == "export_1m_records":
            self.export(
                name,
                "DBF -> JSONL, 1,000,000 records (full profile only)",
                self.large(),
                validate=True,
                input_records=1_000_000,
            )
        elif name == "memo_heavy_190k":
            self.export(
                name,
                "DBF/FPT memo-heavy, 190,000 records (full profile only)",
                self.memo_heavy(190_000),
                memo="inline",
                input_records=190_000,
            )
        elif name == "reconstruction_190k":
            self.scenario_reconstruction(self.medium(), 190_000)
        else:
            raise SystemExit(f"unknown scenario {name!r}")

    def run_profile(self) -> list[dict[str, object]]:
        for name in self.registry():
            self.run_scenario(name)
        self.results.extend(
            [
                not_implemented(
                    "direct_read_bounded",
                    "read_records()/iter_records() do not exist in dbfbridge 0.1.0; "
                    "the planned Direct Read Core is a Phase 1 feature.",
                ),
                not_implemented(
                    "field_projection",
                    "No fields= projection option exists in dbfbridge 0.1.0.",
                ),
                not_implemented(
                    "memo_lazy",
                    'memo="lazy" does not exist in dbfbridge 0.1.0 (skip/inline/null only).',
                ),
                not_implemented(
                    "raw_mode_none",
                    'raw_mode="none" does not exist in dbfbridge 0.1.0; the raw-record '
                    "property is always written to JSON/JSONL.",
                ),
            ]
        )
        return self.results

    def payload(self) -> dict[str, object]:
        return {
            "fixtures": self.fixture_manifest(),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "scenarios": self.results,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=["fast", "full"], default="fast")
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--scenario", help="Run a single scenario by name")
    args = parser.parse_args(argv)

    runner = Runner(REPO_ROOT, args.profile, args.work_dir, args.repetitions)
    try:
        if args.scenario:
            runner.run_scenario(args.scenario)
        else:
            runner.run_profile()
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    print(json.dumps({"ok": True, "payload": runner.payload()}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

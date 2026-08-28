"""Worker: run dbfbridge Phase 0 scenarios (or a full profile) in this process.

Invoked by the controller (``run_benchmark.py``) inside a dedicated subprocess
so that a crash in one scenario cannot take down the report or the controller.

Measurement model
-----------------
- A configurable number of **warm-up** runs (``--warmup``) execute first and are
  *excluded* from the reported results.
- A configurable number of **measured** runs (``--repetitions``) each write into
  its own fresh ``out/<scenario>/rep-<n>/`` directory (no inherited output).
- For each measured run the wall/CPU time, records/s, source MiB/s, authoritative
  ``output_bytes`` and a **sampled peak RSS** (``psutil``, joined in ``finally``)
  are captured.
- The per-run samples are aggregated with the **median** of wall/CPU/records/s
  (clearly documented method); the raw samples are all preserved in the payload.

The worker prints a single JSON payload to stdout:

    {"ok": true, "scenarios": [ {single result per scenario}, ... ], "payload": {...}}
    {"ok": false, "error": "..."}
"""

from __future__ import annotations

import argparse
import contextlib
import json
import statistics
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
STATUS_NOT_IMPLEMENTED = "NOT_IMPLEMENTED"

AGGREGATION = "median-of-measured-repetitions"


def _median(values: list[float | None]) -> float | None:
    usable = [v for v in values if v is not None]
    if not usable:
        return None
    return round(statistics.median(usable), 6)


def aggregate(samples: list[dict[str, object]]) -> dict[str, object]:
    """Aggregate the SUCCESSFUL measured-run samples into a documented median summary.

    Failed samples never participate in the medians.  When nothing succeeded
    the aggregate reports ``None`` medians together with ``repetitions_succeeded``
    ``0``, so a FAILED scenario is never presented as a comparable baseline.
    """

    success = [s for s in samples if s.get("status") == "MEASURED"]
    failed = len(samples) - len(success)

    def col(key: str) -> list[float | None]:
        out: list[float | None] = []
        for s in success:
            value = s.get(key)
            out.append(value if isinstance(value, (int, float)) else None)
        return out

    def maxcol(key: str) -> int | None:
        values: list[int] = []
        for s in success:
            value = s.get(key)
            if isinstance(value, int):
                values.append(value)
        return max(values) if values else None

    wall = _median(col("wall_seconds"))
    cpu = _median(col("cpu_seconds"))
    return {
        "aggregation": AGGREGATION,
        "repetitions": len(samples),
        "repetitions_succeeded": len(success),
        "repetitions_failed": failed,
        "valid_baseline": failed == 0 and len(success) > 0,
        "median_wall_seconds": wall,
        "median_cpu_seconds": cpu,
        "median_records_per_second": _median(col("records_per_second")),
        "median_source_mib_per_second": _median(col("source_mib_per_second")),
        "max_peak_rss_bytes": maxcol("peak_rss_bytes"),
        "max_output_bytes": maxcol("output_bytes"),
    }


def _scenario_names(profile: str) -> tuple[str, ...]:
    fast = (
        "jsonl_conversion_json",
        "jsonl_conversion_csv",
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
        "jsonl_conversion_xlsx",
    )


class Runner:
    """Executes scenarios in-process; the controller isolates it per scenario."""

    def __init__(
        self,
        root: Path,
        profile: str,
        work_dir: Path,
        repetitions: int,
        warmup: int,
    ) -> None:
        self.root = root
        self.profile = profile
        self.work_dir = work_dir
        # The controller validates these values (repetitions >= 1, warmup >= 0);
        # the worker must not silently clamp them so the report matches reality.
        self.repetitions = repetitions
        self.warmup = warmup
        self.fixture_dir = work_dir / "fixtures"
        self.results: list[dict[str, object]] = []

    # ------------------------------------------------------------------ fixtures

    def _flat(self, name: str, records: int, *, deleted_fraction: float = 0.0) -> Path:
        path = self.fixture_dir / "flat" / f"{name}.dbf"
        return fixture_factory.generate_flat(path, records, deleted_fraction=deleted_fraction)

    def small(self) -> Path:
        return self._flat("small", 300)

    def medium(self) -> Path:
        return self._flat("medium", 190_000)

    def large(self) -> Path:
        return self._flat("large", 1_000_000)

    def deleted(self) -> Path:
        return self._flat("deleted", 1_000, deleted_fraction=0.1)

    def memo_heavy(self, records: int) -> Path:
        path = self.fixture_dir / "memo" / f"memo{records}.dbf"
        return fixture_factory.generate_memo_heavy(path, records)

    def encoding_fixture(self, codec: str) -> Path:
        path = self.fixture_dir / "enc" / f"enc_{codec}.dbf"
        return fixture_factory.generate_encoding(path, codec)

    def fixture_manifest(self) -> dict[str, object]:
        return fixture_factory.fixture_manifest(self.fixture_dir)

    # ------------------------------------------------------------------ helpers

    def _fresh_out(self, scenario: str, rep: str) -> Path:
        # Every execution (warm-up or measured) must start from a brand-new,
        # empty output directory: remove any stale directory left by an earlier
        # run of the same work-dir before the measured window begins.
        import shutil

        out = self.work_dir / "out" / scenario / rep
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True, exist_ok=True)
        return out

    def _source_bytes(self, source_dbf: Path) -> int:
        total = 0
        for p in source_dbf.parent.iterdir() if source_dbf.parent.is_dir() else []:
            if p.is_file() and (p.suffix in {".dbf", ".fpt"}):
                total += p.stat().st_size
        return total

    def _measure(
        self,
        name: str,
        description: str,
        function_factory,
        *,
        input_bytes: int | None,
        input_records: int | None,
        **parameters: object,
    ) -> dict[str, object]:
        """Run warm-ups then measured reps, each into its own fresh output dir."""

        warmup_samples: list[dict[str, object]] = []
        for _ in range(self.warmup):
            out = self._fresh_out(name, "warmup")
            warmup_samples.append(
                bench_metrics.run(
                    function_factory(out),
                    input_bytes=input_bytes,
                    input_records=input_records,
                    output_dir=out,
                    warmup=True,
                )
            )

        measured: list[dict[str, object]] = []
        for i in range(1, self.repetitions + 1):
            out = self._fresh_out(name, f"rep-{i}")
            measured.append(
                bench_metrics.run(
                    function_factory(out),
                    input_bytes=input_bytes,
                    input_records=input_records,
                    output_dir=out,
                    warmup=False,
                )
            )

        # ANY failed warm-up OR measured repetition fails the whole scenario;
        # raw samples and errors are preserved, and the aggregate is flagged
        # as not a valid baseline.
        all_samples = warmup_samples + measured
        errors: list[str] = []
        for s in all_samples:
            if s.get("error"):
                errors.append(str(s.get("error")))
        status = "MEASURED" if all(s["status"] == "MEASURED" for s in all_samples) else "FAILED"
        return {
            "scenario": name,
            "description": description,
            "status": status,
            "warmup": self.warmup,
            "repetitions": self.repetitions,
            "parameters": parameters,
            "warmup_samples": warmup_samples,
            "samples": measured,
            "aggregated": aggregate(measured),
            **({"errors": errors} if errors else {}),
        }

    # ------------------------------------------------------------------ scenarios

    def _export_function(self, source_dbf: Path, **export_kwargs: object):
        from dbfbridge import export_dbf

        def make(out: Path):
            def run() -> None:
                result = export_dbf(
                    str(source_dbf.parent),
                    str(out),
                    formats=("jsonl",),
                    overwrite=True,
                    **export_kwargs,  # type: ignore[arg-type]
                )
                result.raise_for_errors()

            return run

        return make

    def scenario_export(
        self,
        name: str,
        description: str,
        source_dbf: Path,
        *,
        input_records: int | None,
        **export_kwargs: object,
    ) -> None:
        self.results.append(
            self._measure(
                name,
                description,
                self._export_function(source_dbf, **export_kwargs),
                input_bytes=self._source_bytes(source_dbf),
                input_records=input_records,
                **export_kwargs,
            )
        )

    def scenario_jsonl_conversion(self, mode: str) -> None:
        """In-process call into the legacy benchmark's conversion functions."""

        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "dbfbridge_bench_jsonl",
            self.root / "benchmarks" / "benchmark_jsonl_conversion.py",
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("could not load benchmarks/benchmark_jsonl_conversion.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        name = f"jsonl_conversion_{mode}"
        size_mb = 20
        source = self.work_dir / "jsonl" / f"input_{mode}.jsonl"
        meta = source.with_suffix(".benchmark.json")
        records: int | None = None
        if meta.is_file():
            with contextlib.suppress(
                OSError, json.JSONDecodeError, KeyError, TypeError, ValueError
            ):
                records = int(json.loads(meta.read_text(encoding="utf-8"))["records"])
        # Reuse an existing input only when it is complete AND its sidecar
        # confirms the record count; otherwise (re)generate it before measuring.
        if not source.is_file() or records is None:
            for stale in (source, meta):
                if stale.exists():
                    stale.unlink()
            module.generate_jsonl(source, size_mb)
        if not source.is_file():
            raise RuntimeError(f"JSONL input could not be prepared at {source}")
        if meta.is_file():
            records = int(json.loads(meta.read_text(encoding="utf-8"))["records"])
        if records is None or records <= 0:
            raise RuntimeError(f"JSONL input metadata is missing a valid record count: {meta}")
        input_bytes = source.stat().st_size

        def make(out: Path):
            def run() -> None:
                destination = out / f"output.{mode}"
                if mode == "json":
                    module.jsonl_to_json(source, destination)
                elif mode == "csv":
                    module.jsonl_to_csv(
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
                        expected_record_count=records,
                        source_is_validated=True,
                    )
                elif mode == "xlsx":
                    module.jsonl_to_xlsx(
                        source,
                        destination,
                        columns=["id", "name", "city", "active", "amount", "note"],
                    )
                else:
                    raise SystemExit(f"unsupported jsonl mode {mode}")

            return run

        self.results.append(
            self._measure(
                name,
                f"Legacy JSONL -> {mode.upper()} conversion (benchmark_jsonl_conversion.py)",
                make,
                input_bytes=input_bytes,
                input_records=records or None,
                mode=mode,
            )
        )

    def scenario_raw_metadata_baseline(self) -> None:
        """One result: export metrics + raw-record share computed after measurement."""

        name = "raw_record_metadata_default"
        source_dbf = self.small()

        def make(out: Path):
            from dbfbridge import export_dbf

            def run() -> None:
                result = export_dbf(
                    str(source_dbf.parent),
                    str(out),
                    formats=("jsonl",),
                    deleted="include",
                    overwrite=True,
                )
                result.raise_for_errors()

            return run

        result = self._measure(
            name,
            "Default JSONL export with raw-record metadata (raw_mode baseline).",
            make,
            input_bytes=self._source_bytes(source_dbf),
            input_records=300,
        )

        # Raw-share analysis is computed *after* measurement, on the last
        # measured repetition's JSONL, so it never inflates wall time or
        # output_bytes.
        import base64

        jsonl_paths = sorted(
            (self.work_dir / "out" / name).glob("rep-*/*.jsonl"),
            key=lambda p: (
                int(p.parent.name.removeprefix("rep-"))
                if p.parent.name.removeprefix("rep-").isdigit()
                else -1
            ),
            reverse=True,
        )
        if jsonl_paths:
            # Only consider well-formed rep-N directories (ignore legacy/stale ones).
            jsonl_paths = [
                p
                for p in jsonl_paths
                if p.parent.name.startswith("rep-") and p.parent.name.removeprefix("rep-").isdigit()
            ]
            # rep-* dirs also contain migration_report.jsonl; the data file is
            # the one that carries the raw-record metadata key.
            from dbf_bridge.exporter.serialization import RAW_RECORD_KEY

            jsonl: Path | None = None
            for candidate in jsonl_paths:
                with candidate.open("r", encoding="utf-8") as infile:
                    first = infile.readline()
                if first.strip() and RAW_RECORD_KEY in first:
                    jsonl = candidate
                    break
            if jsonl is None:
                jsonl = max(jsonl_paths, key=lambda p: p.stat().st_size)

            total = jsonl.stat().st_size
            raw_chars = 0
            raw_decoded = 0
            with jsonl.open("r", encoding="utf-8") as infile:
                for line in infile:
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    encoded = record.get(RAW_RECORD_KEY)
                    if isinstance(encoded, str):
                        raw_chars += len(encoded)
                        raw_decoded += len(base64.b64decode(encoded, validate=True))
            result["raw_share"] = {
                "jsonl_bytes": total,
                "raw_base64_chars": raw_chars,
                "raw_decoded_bytes": raw_decoded,
                "raw_base64_share_of_jsonl": round(raw_chars / total, 4) if total else None,
            }
        self.results.append(result)

    def scenario_reconstruction(self, name: str, source_dbf: Path, records: int) -> None:
        """Distinct export/rebuilt directories per reconstruction scenario."""

        from dbfbridge import export_dbf, reconstruct_dbf

        export_dir = self.work_dir / "out" / name / "export"
        export_dir.mkdir(parents=True, exist_ok=True)
        # Prepare the JSONL input once (not measured) using the same dir name
        # space so reconstruction_* never collides across scenarios.
        export_dbf(
            str(source_dbf.parent),
            str(export_dir),
            formats=("jsonl",),
            deleted="include",
            overwrite=True,
        ).raise_for_errors()
        input_bytes = sum(p.stat().st_size for p in export_dir.rglob("*") if p.is_file())

        def make(out: Path):
            def run() -> None:
                target = out / "rebuilt"
                result = reconstruct_dbf(
                    str(export_dir),
                    str(target),
                    input_format="jsonl",
                    overwrite=True,
                )
                result.raise_for_errors()
                # Flatten so the scenario's own output dir holds the artefacts
                # (output_bytes is measured on this dir).
                if target.exists():
                    for child in target.iterdir():
                        child.rename(out / child.name)
                    target.rmdir()

            return run

        self.results.append(
            self._measure(
                name,
                f"JSONL -> DBF/FPT reconstruction ({records} records)",
                make,
                input_bytes=input_bytes,
                input_records=records,
            )
        )

    def scenario_roundtrip(self) -> None:
        name = "roundtrip_quality"
        from dbfbridge import check_conversion_quality

        source_dbf = self.small()

        def make(out: Path):
            def run() -> None:
                result = check_conversion_quality(
                    str(source_dbf.parent),
                    str(out),
                    overwrite=True,
                )
                result.raise_for_errors()

            return run

        self.results.append(
            self._measure(
                name,
                "Full DBF -> JSONL -> DBF round trip with verification (check_conversion_quality)",
                make,
                input_bytes=self._source_bytes(source_dbf),
                input_records=300,
            )
        )

    # ------------------------------------------------------------------ dispatch

    def run_scenario(self, name: str) -> None:
        # Shared scenarios must use IDENTICAL parameters in fast and full; only
        # scenario *names* differ between profiles, never their parameters.
        if name == "jsonl_conversion_json":
            self.scenario_jsonl_conversion("json")
        elif name == "jsonl_conversion_csv":
            self.scenario_jsonl_conversion("csv")
        elif name == "jsonl_conversion_xlsx":
            self.scenario_jsonl_conversion("xlsx")
        elif name == "raw_record_metadata_default":
            self.scenario_raw_metadata_baseline()
        elif name == "export_jsonl_validate_on":
            self.scenario_export(
                name,
                "DBF -> JSONL with output validation enabled (default)",
                self.medium(),
                input_records=190_000,
                validate=True,
            )
        elif name == "export_jsonl_validate_off":
            self.scenario_export(
                name,
                "DBF -> JSONL with output validation disabled (--no-validate)",
                self.medium(),
                input_records=190_000,
                validate=False,
            )
        elif name in {"memo_skip", "memo_null", "memo_inline"}:
            memo = name.removeprefix("memo_")
            self.scenario_export(
                name,
                f"DBF -> JSONL memo={memo}",
                self.memo_heavy(2_000),
                input_records=2_000,
                memo=memo,  # type: ignore[arg-type]
            )
        elif name in {"deleted_skip", "deleted_include"}:
            policy = name.removeprefix("deleted_")
            self.scenario_export(
                name,
                f"DBF -> JSONL deleted={policy} (10% deleted rows)",
                self.deleted(),
                input_records=1_000,
                deleted=policy,  # type: ignore[arg-type]
            )
        elif name in {"encoding_cp1250", "encoding_cp852", "encoding_mazovia"}:
            codec = name.removeprefix("encoding_")
            self.scenario_export(
                name,
                f"DBF -> JSONL with encoding forced to {codec} (Polish diacritics)",
                self.encoding_fixture(codec),
                input_records=1,
                encoding=codec,
                decode_errors="strict",
            )
        elif name == "reconstruction_jsonl_to_dbf":
            self.scenario_reconstruction("reconstruction_jsonl_to_dbf", self.small(), 300)
        elif name == "reconstruction_190k":
            self.scenario_reconstruction("reconstruction_190k", self.medium(), 190_000)
        elif name == "roundtrip_quality":
            self.scenario_roundtrip()
        elif name == "export_1m_records":
            self.scenario_export(
                name,
                "DBF -> JSONL, 1,000,000 records (full profile only)",
                self.large(),
                input_records=1_000_000,
                validate=True,
            )
        elif name == "memo_heavy_190k":
            self.scenario_export(
                name,
                "DBF/FPT memo-heavy, 190,000 records (full profile only)",
                self.memo_heavy(190_000),
                input_records=190_000,
                memo="inline",
            )
        else:
            raise SystemExit(f"unknown scenario {name!r}")

    def run_profile(self) -> list[dict[str, object]]:
        for name in _scenario_names(self.profile):
            self.run_scenario(name)
        self.results.extend(
            [
                {
                    "scenario": "direct_read_bounded",
                    "description": (
                        "read_records()/iter_records() do not exist in dbfbridge 0.1.0; "
                        "the planned Direct Read Core is a Phase 1 feature."
                    ),
                    "status": STATUS_NOT_IMPLEMENTED,
                    "parameters": {},
                    "metrics": {},
                },
                {
                    "scenario": "field_projection",
                    "description": "No fields= projection option exists in dbfbridge 0.1.0.",
                    "status": STATUS_NOT_IMPLEMENTED,
                    "parameters": {},
                    "metrics": {},
                },
                {
                    "scenario": "memo_lazy",
                    "description": (
                        'memo="lazy" does not exist in dbfbridge 0.1.0 (skip/inline/null only).'
                    ),
                    "status": STATUS_NOT_IMPLEMENTED,
                    "parameters": {},
                    "metrics": {},
                },
                {
                    "scenario": "raw_mode_none",
                    "description": (
                        'raw_mode="none" does not exist in dbfbridge 0.1.0; the raw-record '
                        "property is always written to JSON/JSONL."
                    ),
                    "status": STATUS_NOT_IMPLEMENTED,
                    "parameters": {},
                    "metrics": {},
                },
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
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--scenario", help="Run a single scenario by name")
    args = parser.parse_args(argv)

    runner = Runner(REPO_ROOT, args.profile, args.work_dir, args.repetitions, args.warmup)
    try:
        if args.scenario:
            runner.run_scenario(args.scenario)
        else:
            runner.run_profile()
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    print(
        json.dumps(
            {"ok": True, "scenarios": runner.results, "payload": runner.payload()},
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

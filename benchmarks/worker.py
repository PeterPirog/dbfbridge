"""Worker: run dbfbridge Phase 0 scenarios (or a full profile) in this process.

Invoked by the controller (``run_benchmark.py``) inside a dedicated subprocess
so that a crash in one scenario cannot take down the report or the controller.

Measurement model
-----------------
- A configurable number of **warm-up** runs (``--warmup``) execute first and are
  *excluded* from the reported results.  Warm-ups and measured repetitions use
  the same post-validation path; an invalid warm-up invalidates the scenario.
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
import hashlib
import json
import statistics
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from benchmarks import fixtures as fixture_factory  # noqa: E402
from benchmarks import metrics as bench_metrics  # noqa: E402

STATUS_FAILED = "FAILED"
STATUS_MEASURED = "MEASURED"
STATUS_NOT_IMPLEMENTED = "NOT_IMPLEMENTED"

AGGREGATION = "median-of-measured-repetitions"

# Bump when the JSONL input recipe changes; triggers safe regeneration.
JSONL_INPUT_VERSION = "1"

#: Versioned contract of the Phase 1 benchmark report (direct record read).
#: A future Phase 1 AFTER baseline must carry exactly this value.
BENCHMARK_CONTRACT = "phase-1-direct-read-v1"


def _logical_value_text(value: object) -> str:
    """Deterministic text of one decoded value for the projection digest."""
    if isinstance(value, Decimal):
        return f"decimal:{format(value, 'f')}"
    if isinstance(value, (datetime, date)):
        return f"dt:{value.isoformat()}"
    return repr(value)


def _install_memo_read_guard() -> tuple[dict[str, int], Callable[[], None]]:
    """Instrument the REAL backend memo boundary (benchmark harness only).

    Wraps the actual implementation-level entry points, not ``Path.open``:

    - ``backend._open_memofile`` with ``use_memofile=True`` (a real memo-file
      open through the adapter);
    - ``backend.dbfread_backend.read_memo_payload`` (an explicit lazy payload
      read);
    - the ``dbfread.memo.open_memofile`` call the adapter uses inside
      ``read_memo_payload`` (any adapter-level FPT read).

    Returns ``(counters, restore)``; *restore* is idempotent and always safe
    to call in a ``finally``.
    """
    import dbfread.memo as dbfread_memo_module

    from dbf_bridge.core import backend as core_backend

    counters: dict[str, int] = {
        "memofile_opens_use_memofile_true": 0,
        "read_memo_payload_calls": 0,
        "adapter_fpt_opens": 0,
    }
    real_memo_payload = core_backend.dbfread_backend.read_memo_payload
    real_backend_open = core_backend._open_memofile
    real_dbfread_open = dbfread_memo_module.open_memofile
    installed: dict[str, bool] = {"active": True}

    def counting_memo_payload(memo_path, block, *, dbversion_byte):
        # NOTE: read_memo_payload is a BOUND method of the backend instance;
        # assigning a plain function to the instance attribute never binds a
        # self parameter.
        counters["read_memo_payload_calls"] += 1
        return real_memo_payload(memo_path, block, dbversion_byte=dbversion_byte)

    def counting_backend_open(table, use_memofile):
        if use_memofile:
            counters["memofile_opens_use_memofile_true"] += 1
        return real_backend_open(table, use_memofile)

    def counting_dbfread_open(filename, dbversion):
        counters["adapter_fpt_opens"] += 1
        return real_dbfread_open(filename, dbversion)

    # Patch the three real boundary points.  The adapter binds dbfread's
    # open_memofile into its own module namespace, so the adapter binding (and
    # the dbfread.memo original, defensively) are both patched.
    core_backend.dbfread_backend.read_memo_payload = counting_memo_payload  # type: ignore[method-assign]
    core_backend._open_memofile = counting_backend_open  # type: ignore[assignment]
    core_backend.open_memofile = counting_dbfread_open  # type: ignore[assignment]

    def restore() -> None:
        if not installed["active"]:  # pragma: no cover - idempotence guard
            return
        installed["active"] = False
        core_backend.dbfread_backend.read_memo_payload = real_memo_payload  # type: ignore[method-assign]
        core_backend._open_memofile = real_backend_open  # type: ignore[assignment]
        core_backend.open_memofile = real_dbfread_open  # type: ignore[assignment]
        dbfread_memo_module.open_memofile = real_dbfread_open  # type: ignore[assignment]

    return counters, restore


def _validate_memo_lazy_state(state: dict[str, object], sample: dict[str, object]) -> None:
    """Shared post-validation of the memo_lazy evidence (also unit-tested)."""
    records = state["records"]  # type: ignore[assignment]
    lazy_values = state["lazy_values"]  # type: ignore[assignment]
    empty_values = state["empty_values"]  # type: ignore[assignment]
    if not isinstance(records, int) or records != 2_000:
        raise RuntimeError(f"memo_lazy must stream exactly 2,000 records, got {records}")
    if not isinstance(lazy_values, int) or not isinstance(empty_values, int):
        raise RuntimeError("memo_lazy scenario did not collect its counters")
    if lazy_values + empty_values != records:
        raise RuntimeError(
            f"every record must expose a lazy or empty memo, got "
            f"{lazy_values} lazy + {empty_values} empty"
        )
    counters = state.get("memo_guard")
    if not isinstance(counters, dict) or any(counters.values()):
        raise RuntimeError(
            f'memo="lazy" must not touch the real backend memo boundary once '
            f"(memofile open use_memofile=True / read_memo_payload / adapter FPT "
            f"open): {counters}"
        )
    if sample.get("output_bytes") != 0:
        raise RuntimeError("Direct Read must not write any output bytes")
    if sample.get("temporary_bytes_written") != 0:
        raise RuntimeError("Direct Read must write zero temporary bytes")


def _median(values: list[float | None]) -> float | None:
    usable = [v for v in values if v is not None]
    if not usable:
        return None
    return round(statistics.median(usable), 6)


def aggregate(
    samples: list[dict[str, object]],
    *,
    warmup_samples: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Aggregate the SUCCESSFUL measured-run samples into a documented median summary.

    - Failed measured samples never participate in the medians.
    - If **any warm-up** FAILED, ``valid_baseline`` is forced to ``False`` even
      when every measured repetition succeeded — a broken warm-up means the
      scenario must not be treated as a comparable baseline.
    - ``warmups_succeeded`` / ``warmups_failed`` are always reported.
    """

    warmup_samples = warmup_samples or []
    warmups_succeeded = sum(1 for s in warmup_samples if s.get("status") == "MEASURED")
    warmups_failed = len(warmup_samples) - warmups_succeeded

    success = [s for s in samples if s.get("status") == "MEASURED"]
    failed = len(samples) - len(success)
    valid = failed == 0 and warmups_failed == 0 and len(success) > 0

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
        "warmups_succeeded": warmups_succeeded,
        "warmups_failed": warmups_failed,
        "valid_baseline": valid,
        "median_wall_seconds": wall,
        "median_cpu_seconds": cpu,
        "median_records_per_second": _median(col("records_per_second")),
        "median_source_mib_per_second": _median(col("source_mib_per_second")),
        "median_read_amplification": _median(col("read_amplification")),
        "median_write_amplification": _median(col("write_amplification")),
        "max_temporary_bytes_written": maxcol("temporary_bytes_written"),
        "max_peak_rss_bytes": maxcol("peak_rss_bytes"),
        "max_output_bytes": maxcol("output_bytes"),
        # Memo-reconstruction extras (present only on scenarios that rebuild a
        # memo table; None elsewhere so the Markdown renders NOT_AVAILABLE).
        "max_output_dbf_bytes": maxcol("output_dbf_bytes"),
        "max_output_fpt_bytes": maxcol("output_fpt_bytes"),
        "median_fpt_mib_per_second": _median(col("fpt_mib_per_second")),
    }


def _validate_reconstruction_output(
    output_dir: Path,
    *,
    expected_records: int,
    require_fpt: bool,
) -> tuple[Path, Path | None]:
    """Validate one isolated reconstruction output outside its timed window.

    A reconstruction scenario has exactly one DBF.  Memo-free scenarios must
    not accidentally create an FPT; the dedicated memo scenario must create
    exactly one non-empty FPT.  Physical record counting is deliberately part
    of post-validation, never part of the measured writer call.
    """

    dbfs = sorted(path for path in output_dir.rglob("*.dbf") if path.is_file())
    fpts = sorted(path for path in output_dir.rglob("*.fpt") if path.is_file())
    if len(dbfs) != 1:
        raise RuntimeError(f"expected exactly one reconstructed DBF, found {len(dbfs)}")
    dbf_path = dbfs[0]
    if dbf_path.stat().st_size == 0:
        raise RuntimeError("reconstructed DBF is empty")

    if require_fpt:
        if len(fpts) != 1:
            raise RuntimeError(f"expected exactly one reconstructed FPT, found {len(fpts)}")
        fpt_path: Path | None = fpts[0]
        if fpt_path.stat().st_size == 0:
            raise RuntimeError("reconstructed FPT is empty")
    else:
        if fpts:
            raise RuntimeError(f"memo-free reconstruction produced {len(fpts)} unexpected FPT")
        fpt_path = None

    counts = fixture_factory._measured_counts(dbf_path)
    if counts["total_records"] != expected_records:
        raise RuntimeError(
            f"reconstructed DBF has {counts['total_records']} records, expected {expected_records}"
        )

    partials = [
        path
        for path in output_dir.rglob("*")
        if path.is_file() and "partial" in path.name.split(".")
    ]
    if partials:
        names = ", ".join(path.name for path in partials[:3])
        raise RuntimeError(f"reconstruction left temporary partial files: {names}")
    return dbf_path, fpt_path


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
        "direct_read_bounded",
        "field_projection",
        "memo_lazy",
        "raw_mode_none",
    )
    if profile == "phase3":
        return (
            "inspect_schema_1",
            "inspect_schema_100",
            "inspect_schema_1000",
            "direct_read_190k",
            "direct_read_1m",
            "direct_read_memo_heavy",
            "direct_read_deleted_include",
            "direct_read_deleted_skip",
            "direct_read_cp1250",
            "direct_read_cp852",
            "direct_read_mazovia",
            "migration_dbf_to_jsonl",
            "migration_jsonl_to_dbf_fpt",
            "migration_validate_off",
            "migration_validate_on",
            "direct_read_raw_none",
            "direct_read_raw_full",
            "direct_read_projection_selected",
            "direct_read_projection_all",
            "direct_read_memo_skip",
            "direct_read_memo_lazy",
            "direct_read_memo_inline",
            "cold_import",
        )
    if profile != "full":
        return fast
    return fast + (
        "export_1m_records",
        "memo_heavy_190k",
        "reconstruction_190k",
        "reconstruction_memo_190k",
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
        """Logical input size of one table: its DBF plus same-stem FPT."""

        wanted_stem = source_dbf.stem.casefold()
        return sum(
            path.stat().st_size
            for path in source_dbf.parent.iterdir()
            if path.is_file()
            and path.stem.casefold() == wanted_stem
            and path.suffix.casefold() in {".dbf", ".fpt"}
        )

    def _measure(
        self,
        name: str,
        description: str,
        function_factory,
        *,
        input_bytes: int | None,
        input_records: int | None,
        post_validate: Callable[[Path, dict[str, object]], None] | None = None,
        extra_parameters: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Run warm-ups then measured reps, each into its own fresh output dir.

        ``post_validate`` (optional) runs **after every successful warm-up and
        measured repetition** has returned from ``metrics.run`` — i.e. outside
        the wall/CPU measurement window.  It may inspect the artefacts and
        attach per-sample extras.  Raising marks that sample ``FAILED`` (with
        the error and measured times preserved) without inflating the measured
        wall/CPU values.
        """

        def run_sample(rep: str, *, warmup: bool) -> dict[str, object]:
            out = self._fresh_out(name, rep)
            sample = bench_metrics.run(
                function_factory(out),
                input_bytes=input_bytes,
                input_records=input_records,
                output_dir=out,
                warmup=warmup,
            )
            if post_validate is not None and sample.get("status") == STATUS_MEASURED:
                validation_started = time.perf_counter()
                try:
                    post_validate(out, sample)
                except Exception as exc:  # post-validation failure fails the SAMPLE
                    sample["status"] = STATUS_FAILED
                    sample["error"] = f"post-validation failed: {type(exc).__name__}: {exc}"
                finally:
                    # Diagnostic only.  This timer starts after metrics.run has
                    # already closed the measured wall/CPU window.
                    sample["post_validation_seconds"] = round(
                        time.perf_counter() - validation_started,
                        6,
                    )
            return sample

        warmup_samples = [run_sample(f"warmup-{i}", warmup=True) for i in range(1, self.warmup + 1)]

        measured = [run_sample(f"rep-{i}", warmup=False) for i in range(1, self.repetitions + 1)]

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
            "parameters": dict(extra_parameters or {}),
            "warmup_samples": warmup_samples,
            "samples": measured,
            "aggregated": aggregate(measured, warmup_samples=warmup_samples),
            **({"errors": errors} if errors else {}),
        }

    # ------------------------------------------------------------------ scenarios

    def _export_function(self, source_dbf: Path, **export_kwargs: object):
        from dbfbridge import export_dbf

        def make(out: Path):
            def run() -> None:
                result = export_dbf(
                    str(source_dbf),
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
                extra_parameters=dict(export_kwargs),
            )
        )

    def _prepare_jsonl_input(self, module, name: str, size_mb: int) -> tuple[Path, int, int]:
        """Prepare (or strictly re-validate) the JSONL conversion input.

        The Phase 0 sidecar ``<name>.meta.json`` records:

        - ``generator`` / ``version`` (recipe identity);
        - ``records``;
        - ``bytes``;
        - ``sha256``;
        - ``complete_line_count``.

        After (re)generation the generator's own legacy ``.benchmark.json``
        sidecar is read and its ``records`` treated as the *expected* count.
        The file is then strictly validated: expected records > 0, every
        non-empty line is complete valid JSON (``invalid_line_count == 0``),
        ``actual complete_line_count == expected records``, the Phase 0 sidecar
        ``records == expected records``, and bytes/SHA-256 match the file.
        A generator that does not honour its declared record count is rejected
        (``ValueError``) — the invalid file is kept for diagnosis and no Phase 0
        sidecar is written for it, so no blind regeneration loop can occur.
        Reuse of an already-valid input never regenerates.
        """

        source = self.work_dir / "jsonl" / name
        sidecar = source.with_name(name + ".meta.json")
        legacy = source.with_suffix(".benchmark.json")

        def analyze() -> dict[str, object]:
            sha = hashlib.sha256()
            size = 0
            complete = 0
            invalid = 0
            last_complete = False
            with source.open("rb") as infile:
                while chunk := infile.read(1 << 20):
                    sha.update(chunk)
                    size += len(chunk)
            with source.open("rb") as infile:
                for line in infile:
                    if not line.strip():
                        continue
                    try:
                        json.loads(line)
                        complete += 1
                        last_complete = True
                    except json.JSONDecodeError:
                        invalid += 1
                        last_complete = False
            return {
                "bytes": size,
                "sha256": sha.hexdigest(),
                "complete_line_count": complete,
                "invalid_line_count": invalid,
                "ends_with_complete_line": last_complete,
            }

        def expected_records() -> int | None:
            if not legacy.is_file():
                return None
            try:
                value = json.loads(legacy.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
            rec = value.get("records") if isinstance(value, dict) else None
            return rec if isinstance(rec, int) and not isinstance(rec, bool) else None

        def build_sidecar(expected: int) -> dict[str, object]:
            actual = analyze()
            data: dict[str, object] = {
                "generator": "benchmark_jsonl_conversion.generate_jsonl",
                "version": JSONL_INPUT_VERSION,
                "expected_records": expected,
                "records": int(actual["complete_line_count"]),  # type: ignore[arg-type]
                "bytes": int(actual["bytes"]),  # type: ignore[arg-type]
                "sha256": actual["sha256"],
                "complete_line_count": int(actual["complete_line_count"]),  # type: ignore[arg-type]
                "invalid_line_count": int(actual["invalid_line_count"]),  # type: ignore[arg-type]
            }
            return data

        def strict_ok(stored: dict[str, object]) -> bool:
            expected = expected_records()
            if expected is None or expected <= 0:
                return False
            if not stored.get("generator") or stored.get("version") != JSONL_INPUT_VERSION:
                return False
            if stored.get("expected_records") != expected or stored.get("records") != expected:
                return False
            if stored.get("complete_line_count") != expected:
                return False
            if stored.get("invalid_line_count") != 0:
                return False
            actual = analyze()
            return (
                stored.get("bytes") == actual["bytes"]
                and stored.get("sha256") == actual["sha256"]
                and int(stored["records"]) == int(actual["complete_line_count"])  # type: ignore[arg-type]
            )

        if sidecar.is_file():
            try:
                stored = json.loads(sidecar.read_text(encoding="utf-8"))
                if isinstance(stored, dict) and strict_ok(stored):
                    return source, int(stored["records"]), int(stored["bytes"])  # type: ignore[arg-type]
            except (OSError, json.JSONDecodeError):
                pass

        # Missing / stale / invalid / inconsistent -> (re)generate.  Reuse of a
        # valid input is handled above, so this branch only ever runs for inputs
        # that are genuinely not ready.
        for stale in (source, sidecar, legacy):
            if stale.exists():
                stale.unlink()
        module.generate_jsonl(source, size_mb)

        expected = expected_records()
        actual = analyze()
        if expected is None or expected <= 0:
            raise ValueError(f"JSONL generator did not declare a positive record count: {legacy}")
        if int(actual["invalid_line_count"]) != 0:  # type: ignore[arg-type]
            raise ValueError(
                f"JSONL generator produced {int(actual['invalid_line_count'])} invalid line(s); "  # type: ignore[arg-type]
                f"refusing to use {source}"
            )
        if int(actual["complete_line_count"]) != expected:  # type: ignore[arg-type]
            raise ValueError(
                f"JSONL generator produced {int(actual['complete_line_count'])} complete "  # type: ignore[arg-type]
                f"line(s) but declared {expected}; refusing to use {source}"
            )
        sidecar_data = build_sidecar(expected)
        sidecar.write_text(json.dumps(sidecar_data, indent=2), encoding="utf-8")
        return source, expected, int(sidecar_data["bytes"])  # type: ignore[arg-type]

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
        source, records, input_bytes = self._prepare_jsonl_input(
            module, f"input_{mode}.jsonl", size_mb
        )

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
                extra_parameters={"mode": mode},
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
                    str(source_dbf),
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
        """Memo-free reconstruction with validation outside the timed call."""

        from dbfbridge import export_dbf, reconstruct_dbf

        # Preparation is outside the measured window but must still be fresh:
        # stale JSONL/schema files from an earlier run would reconstruct extra
        # tables and invalidate both correctness and the input-size denominator.
        export_dir = self._fresh_out(name, "export")
        # Prepare the JSONL input once (not measured) using the same dir name
        # space so reconstruction_* never collides across scenarios.
        export_dbf(
            str(source_dbf),
            str(export_dir),
            formats=("jsonl",),
            deleted="include",
            overwrite=True,
        ).raise_for_errors()
        input_bytes = sum(p.stat().st_size for p in export_dir.rglob("*") if p.is_file())

        def make(out: Path):
            def run() -> None:
                result = reconstruct_dbf(
                    str(export_dir),
                    str(out / "rebuilt"),
                    input_format="jsonl",
                    overwrite=True,
                )
                result.raise_for_errors()

            return run

        def post_validate(out: Path, sample: dict[str, object]) -> None:
            dbf_path, _ = _validate_reconstruction_output(
                out,
                expected_records=records,
                require_fpt=False,
            )
            # Keep the reconstructed tree intact.  metrics.run and this
            # authoritative re-check both count output files recursively, so
            # flattening/renaming is unnecessary and never enters the timed call.
            sample["output_bytes"] = bench_metrics.directory_size_bytes(out)
            if sample["output_bytes"] < dbf_path.stat().st_size:
                raise RuntimeError("reconstruction output size is smaller than its DBF")

        self.results.append(
            self._measure(
                name,
                f"JSONL -> DBF reconstruction ({records} records, memo-free)",
                make,
                input_bytes=input_bytes,
                input_records=records,
                post_validate=post_validate,
            )
        )

    def scenario_reconstruction_memo(self, name: str, source_dbf: Path, records: int) -> None:
        """Memo-heavy reconstruction: a real JSONL -> DBF + FPT rebuild.

        The JSONL input is prepared **outside** the measured window.  The
        **measured callable is ONLY the public ``reconstruct_dbf``** — nothing
        else.  All post-validation (verifying the DBF and FPT artefacts are
        present and non-empty, counting records, and
        attaching ``output_dbf_bytes`` / ``output_fpt_bytes`` /
        ``fpt_mib_per_second``) runs in ``post_validate`` **after** the
        wall/CPU measurement window has closed, so it can never inflate the
        measured times.  A missing/empty FPT (or a record-count mismatch)
        fails the sample via post-validation.
        """

        from dbfbridge import export_dbf, reconstruct_dbf

        export_dir = self._fresh_out(name, "export")
        export_dbf(
            str(source_dbf),
            str(export_dir),
            formats=("jsonl",),
            deleted="include",
            memo="inline",
            overwrite=True,
        ).raise_for_errors()
        input_bytes = sum(p.stat().st_size for p in export_dir.rglob("*") if p.is_file())

        def make(out: Path):
            def run() -> None:
                # ONLY the public reconstruct_dbf call is inside the measured
                # window.  Stat and artifact validation are in
                # post_validate (outside the wall/CPU measurement).
                result = reconstruct_dbf(
                    str(export_dir),
                    str(out / "rebuilt"),
                    input_format="jsonl",
                    memo="inline",
                    overwrite=True,
                )
                result.raise_for_errors()

            return run

        def post_validate(out: Path, sample: dict[str, object]) -> None:
            dbf_path, fpt_path = _validate_reconstruction_output(
                out,
                expected_records=records,
                require_fpt=True,
            )
            assert fpt_path is not None
            dbf_bytes = dbf_path.stat().st_size
            fpt_bytes = fpt_path.stat().st_size
            sample["output_dbf_bytes"] = dbf_bytes
            sample["output_fpt_bytes"] = fpt_bytes
            sample["output_bytes"] = bench_metrics.directory_size_bytes(out)
            wall = sample.get("wall_seconds")
            if isinstance(wall, (int, float)) and not isinstance(wall, bool) and wall > 0:
                sample["fpt_mib_per_second"] = round((fpt_bytes / (1024 * 1024)) / wall, 6)
            else:
                sample["fpt_mib_per_second"] = None

        self.results.append(
            self._measure(
                name,
                f"JSONL -> DBF+FPT memo reconstruction ({records} records, memo=inline)",
                make,
                input_bytes=input_bytes,
                input_records=records,
                post_validate=post_validate,
            )
        )

    def scenario_roundtrip(self) -> None:
        name = "roundtrip_quality"
        from dbfbridge import check_conversion_quality

        source_dbf = self.small()

        def make(out: Path):
            def run() -> None:
                result = check_conversion_quality(
                    str(source_dbf),
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

    # -------------------------------------------------------- phase 1B ----

    def scenario_direct_read_bounded(self) -> None:
        """read_records(limit=100) on the 190k fixture: bounded, zero-output."""
        from dbfbridge import read_records

        source_dbf = self.medium()
        input_bytes = self._source_bytes(source_dbf)
        state: dict[str, object] = {}

        def make(out: Path):
            def run() -> None:
                state["page"] = read_records(str(source_dbf), offset=0, limit=100)

            return run

        def post_validate(out: Path, sample: dict[str, object]) -> None:
            page: Any = state["page"]
            if page.offset != 0 or page.limit != 100:
                raise RuntimeError("unexpected read_records parameters")
            if len(page.records) != 100:
                raise RuntimeError(f"expected 100 records, got {len(page.records)}")
            if [record.physical_index for record in page.records] != list(range(100)):
                raise RuntimeError("physical order violated")
            if page.next_offset != 100 or page.exhausted:
                raise RuntimeError("page must continue at physical offset 100")
            if sample.get("output_bytes") != 0:
                raise RuntimeError("Direct Read must not write any output bytes")
            if sample.get("temporary_bytes_written") != 0:
                raise RuntimeError("Direct Read must write zero temporary bytes")
            amplification = sample.get("read_amplification")
            if (
                isinstance(amplification, (int, float))
                and not isinstance(amplification, bool)
                and amplification >= 1.0
            ):
                raise RuntimeError(
                    "read_records(limit=100) must not scan the whole table "
                    f"(read amplification {amplification})"
                )

        self.results.append(
            self._measure(
                "direct_read_bounded",
                "Direct Read: read_records(limit=100) over the 190k table (bounded, zero output)",
                make,
                input_bytes=input_bytes,
                input_records=100,
                post_validate=post_validate,
            )
        )

    def scenario_field_projection(self) -> None:
        """iter_records(fields=...) matches the unprojected logical result.

        The reference digest is computed exactly once (outside the measured
        window); every measured call only accumulates the projected digest in
        O(1) extra memory — nothing proportional to the 190k record count is
        ever materialized, so peak RSS reflects the streaming behaviour.
        """

        from dbfbridge import iter_records

        source_dbf = self.medium()
        input_bytes = self._source_bytes(source_dbf)
        fields = ("ID", "NAZWA", "KWOTA")

        # The reference full-read digest is computed ONCE, before all
        # warm-ups and repetitions, outside every measured window.  It walks
        # the unprojected stream and digests only the projected columns, so
        # each measured repetition can verify "same logical result" without
        # a second full scan and without materializing records.
        def _digest_of(values: dict[str, object]) -> str:
            digest = hashlib.sha256()
            for name in fields:
                digest.update(name.encode("utf-8"))
                digest.update(b"\x1f")
                digest.update(_logical_value_text(values[name]).encode("utf-8"))
                digest.update(b"\x1e")
            return digest.hexdigest()

        reference = hashlib.sha256()
        reference_records = 0
        for record in iter_records(str(source_dbf), memo="null"):
            reference.update(_digest_of(dict(record.values)).encode("ascii"))
            reference_records += 1
        reference_digest = reference.hexdigest()
        if reference_records != 190_000:  # pragma: no cover - fixture contract
            raise RuntimeError(
                f"the reference full read covered {reference_records} records, expected 190000"
            )

        state: dict[str, object] = {
            "reference_digest": reference_digest,
            "reference_records": reference_records,
            "digests": [],
            "records_read": 0,
        }

        def make(out: Path):
            def run() -> None:
                digest = hashlib.sha256()
                records_read = 0
                for record in iter_records(str(source_dbf), fields=fields, memo="null"):
                    values = dict(record.values)
                    digest.update(_digest_of(values).encode("ascii"))
                    records_read += 1
                state["digests"] = [*state.get("digests", []), digest.hexdigest()]  # type: ignore[assignment]
                state["records_read"] = records_read

            return run

        def post_validate(out: Path, sample: dict[str, object]) -> None:
            records_read = state["records_read"]  # type: ignore[assignment]
            digests = state["digests"]  # type: ignore[assignment]
            if not isinstance(records_read, int) or not isinstance(digests, list):
                raise RuntimeError("field_projection scenario did not collect its counters")
            if records_read != state["reference_records"]:  # type: ignore[comparison-overlap]
                raise RuntimeError(
                    f"projection must cover {state['reference_records']} records, "  # type: ignore[attr-defined]
                    f"got {records_read}"
                )
            if len(set(digests)) != 1 or digests[-1] != state["reference_digest"]:  # type: ignore[comparison-overlap]
                raise RuntimeError("field projection returned a different logical result")
            if sample.get("output_bytes") != 0:
                raise RuntimeError("Direct Read must not write any output bytes")
            if sample.get("temporary_bytes_written") != 0:
                raise RuntimeError("Direct Read must write zero temporary bytes")

        result = self._measure(
            "field_projection",
            f"Direct Read: iter_records(fields={list(fields)}) over the 190k table "
            "(O(1) memory, digest equal to the once-precomputed full-read reference)",
            make,
            input_bytes=input_bytes,
            input_records=190_000,
            post_validate=post_validate,
        )
        # Diagnostics only (never part of the metrics): the per-repetition
        # digests prove every repetition produced the same logical result.
        result["projection_digests"] = list(state.get("digests", []))  # type: ignore[arg-type]
        result["projection_records"] = state.get("records_read")  # type: ignore[union-attr]
        self.results.append(result)

    def scenario_memo_lazy(self) -> None:
        """iter_records(memo="lazy"): the REAL backend memo boundary stays silent.

        The instrumentation wraps the actual adapter entry points
        (``backend._open_memofile(use_memofile=True)``,
        ``backend.dbfread_backend.read_memo_payload``, and the adapter's
        ``dbfread.memo.open_memofile``), not ``Path.open`` — every warm-up and
        measured repetition must see all three counters at zero.  The
        instrumentation is always restored in ``finally``.
        """
        from dbfbridge import LazyMemoValue, iter_records

        source_dbf = self.memo_heavy(2_000)
        input_bytes = self._source_bytes(source_dbf)
        state: dict[str, object] = {"lazy_values": 0, "empty_values": 0, "records": 0}

        def make(out: Path):
            def run() -> None:
                lazy_values = 0
                empty_values = 0
                records = 0
                for record in iter_records(str(source_dbf), memo="lazy"):
                    records += 1
                    value = record.values["NOTATKA"]
                    if isinstance(value, LazyMemoValue):
                        metadata = value.to_dict()  # pure metadata, no payload read
                        if metadata["field"] != "NOTATKA":
                            raise RuntimeError("lazy memo value for the wrong field")
                        lazy_values += 1
                    elif value is not None:
                        raise RuntimeError(f"unexpected non-lazy memo value {value!r}")
                    else:
                        empty_values += 1
                state["lazy_values"] = lazy_values
                state["empty_values"] = empty_values
                state["records"] = records

            return run

        def post_validate(out: Path, sample: dict[str, object]) -> None:
            _validate_memo_lazy_state(state, sample)

        # Instrument the real backend memo boundary for warm-ups AND measured
        # repetitions; the guard only counts (it never changes behaviour) and
        # the instrumentation is always restored in finally.
        counters, restore_memo_guard = _install_memo_read_guard()
        state["memo_guard"] = counters
        try:
            self.results.append(
                self._measure(
                    "memo_lazy",
                    'Direct Read: iter_records(memo="lazy") over the 2,000-record memo table '
                    "(zero real backend memo operations, zero output)",
                    make,
                    input_bytes=input_bytes,
                    input_records=2_000,
                    post_validate=post_validate,
                )
            )
        finally:
            restore_memo_guard()

    def scenario_raw_mode_none(self) -> None:
        """iter_records(raw=False): no raw record images are kept."""
        from dbfbridge import iter_records

        source_dbf = self.medium()
        input_bytes = self._source_bytes(source_dbf)
        state: dict[str, object] = {"records": 0, "raw_bytes": 0}

        def make(out: Path):
            def run() -> None:
                records = 0
                raw_count = 0
                total_values = 0
                for record in iter_records(str(source_dbf), memo="null", raw=False):
                    records += 1
                    total_values += len(record.values)
                    if record.raw_record is not None:
                        raw_count += 1
                state["records"] = records
                state["raw_count"] = raw_count
                state["total_values"] = total_values

            return run

        def post_validate(out: Path, sample: dict[str, object]) -> None:
            records = state["records"]  # type: ignore[assignment]
            raw_count = state["raw_count"]  # type: ignore[assignment]
            if not isinstance(records, int) or not isinstance(raw_count, int):
                raise RuntimeError("raw_mode_none scenario did not collect its counters")
            if records != 190_000:
                raise RuntimeError(f"expected 190,000 records, got {records}")
            if raw_count != 0:
                raise RuntimeError(f"raw=False kept {raw_count} raw record images")
            if state["total_values"] == 0:  # type: ignore[comparison-overlap]
                raise RuntimeError("no field values were decoded")
            if sample.get("output_bytes") != 0:
                raise RuntimeError("Direct Read must not write any output bytes")
            if sample.get("temporary_bytes_written") != 0:
                raise RuntimeError("Direct Read must write zero temporary bytes")

        self.results.append(
            self._measure(
                "raw_mode_none",
                "Direct Read: iter_records(raw=False) over the 190k table (no raw bytes kept)",
                make,
                input_bytes=input_bytes,
                input_records=190_000,
                post_validate=post_validate,
            )
        )

    # ------------------------------------------------------------------ phase 3 ----

    def scenario_inspect_schema(self, name: str, iterations: int) -> None:
        """inspect_table + read_schema call-count scaling (1 / 100 / 1000).

        Every iteration performs exactly one ``inspect_table`` and one
        ``read_schema`` call on the same 300-record table.  The measured
        window covers the full loop; the last results are re-checked in
        post-validation (correct record/field counts, zero output).
        """
        from dbfbridge import inspect_table, read_schema

        path = self.small()  # 300-record fixture, cheap metadata
        state: dict[str, object] = {"inspect_calls": 0, "schema_calls": 0}

        def make(out: Path):
            def run() -> None:
                # Counters reflect THIS repetition only (warm-ups reset them).
                state["inspect_calls"] = 0
                state["schema_calls"] = 0
                for _ in range(iterations):
                    info = inspect_table(str(path))
                    state["inspect_calls"] = int(state["inspect_calls"]) + 1  # type: ignore[arg-type]
                    schema = read_schema(str(path))
                    state["schema_calls"] = int(state["schema_calls"]) + 1  # type: ignore[arg-type]
                    state["record_count"] = info.record_count
                    state["field_count"] = len(schema.fields)

            return run

        def post_validate(out: Path, sample: dict[str, object]) -> None:
            if state["inspect_calls"] != iterations or state["schema_calls"] != iterations:  # type: ignore[comparison-overlap]
                raise RuntimeError(
                    f"expected exactly {iterations} inspect_table and {iterations} "
                    f"read_schema calls, got {state['inspect_calls']}/{state['schema_calls']}"
                )
            if state["record_count"] != 300 or state["field_count"] != 8:  # type: ignore[comparison-overlap]
                raise RuntimeError(
                    "inspect/read_schema returned unexpected metadata "
                    f"(record_count={state['record_count']}, field_count={state['field_count']})"
                )
            if sample.get("output_bytes") != 0:
                raise RuntimeError("Direct Read must not write output bytes")
            if sample.get("temporary_bytes_written") != 0:
                raise RuntimeError("Direct Read must write zero temporary bytes")

        self.results.append(
            self._measure(
                name,
                f"inspect_table + read_schema {iterations}× over the 300-record table "
                "(backend open/pass audit)",
                make,
                input_bytes=self._source_bytes(path),
                input_records=iterations,
                post_validate=post_validate,
            )
        )

    def _direct_read_stream(
        self,
        name: str,
        description: str,
        source_dbf: Path,
        input_records: int,
        iterate: Callable[[Any, dict[str, object]], None],
        post_validate: Callable[[dict[str, object], dict[str, object]], None],
        memo: str = "null",
    ) -> None:
        """Shared zero-output Direct Read measurement over one fixture.

        ``iterate(record, state)`` accumulates O(1) per-record evidence into
        *state*; ``post_validate(state, sample)`` runs outside the measured
        window and receives the same *state*.
        """
        from dbfbridge import iter_records

        input_bytes = self._source_bytes(source_dbf)
        state: dict[str, object] = {"records": 0}

        def make(out: Path):
            def run() -> None:
                # O(1) evidence for THIS repetition only (warm-ups reset it).
                state["records"] = 0
                state["id_sum"] = 0
                records = 0
                for record in iter_records(str(source_dbf), memo=memo):
                    iterate(record, state)
                    records += 1
                state["records"] = records

            return run

        def validate(out: Path, sample: dict[str, object]) -> None:
            records = state["records"]  # type: ignore[assignment]
            if not isinstance(records, int) or records != input_records:
                raise RuntimeError(f"expected {input_records} records, got {records}")
            if sample.get("output_bytes") != 0:
                raise RuntimeError("Direct Read must not write any output bytes")
            if sample.get("temporary_bytes_written") != 0:
                raise RuntimeError("Direct Read must write zero temporary bytes")
            post_validate(state, sample)

        self.results.append(
            self._measure(
                name,
                description,
                make,
                input_bytes=input_bytes,
                input_records=input_records,
                post_validate=validate,
            )
        )

    def scenario_direct_read(self, name: str, size: int) -> None:
        """Full iter_records stream over the flat table (190k / 1M scaling).

        Every repetition verifies the exact record count and the arithmetic
        ID sum (``N*(N+1)/2``) with O(1) extra memory — the decoded values
        are digested, never materialized.
        """

        def iterate(record: Any, state: dict[str, object]) -> None:
            state["id_sum"] = int(state.get("id_sum", 0)) + int(record.values["ID"])  # type: ignore[arg-type]

        def post_validate(state: dict[str, object], sample: dict[str, object]) -> None:
            expected_sum = size * (size + 1) // 2
            if state.get("id_sum") != expected_sum:
                raise RuntimeError(
                    f"streamed ID values do not sum to the fixture recipe "
                    f"({state.get('id_sum')} != {expected_sum})"
                )

        self._direct_read_stream(
            name,
            f"Direct Read: iter_records over the {size:,}-record table "
            "(O(1) memory, record count + ID-sum verified)",
            self._flat_by_size(size),
            size,
            iterate,
            post_validate,
        )

    def _flat_by_size(self, size: int) -> Path:
        return {300: self.small(), 190_000: self.medium(), 1_000_000: self.large()}[size]

    def scenario_direct_read_memo_heavy(self) -> None:
        """iter_records(memo="inline") over the 190k memo table (FPT at scale)."""
        from dbfbridge import iter_records

        source_dbf = self.memo_heavy(190_000)
        input_bytes = self._source_bytes(source_dbf)
        state: dict[str, object] = {"records": 0, "memo_values": 0, "empty_values": 0}

        def make(out: Path):
            def run() -> None:
                records = 0
                memo_values = 0
                empty_values = 0
                for record in iter_records(str(source_dbf), memo="inline"):
                    records += 1
                    if record.values["NOTATKA"] is None:
                        empty_values += 1
                    else:
                        memo_values += 1
                state["records"] = records
                state["memo_values"] = memo_values
                state["empty_values"] = empty_values

            return run

        def post_validate(out: Path, sample: dict[str, object]) -> None:
            if state["memo_values"] != 171_000 or state["empty_values"] != 19_000:  # type: ignore[comparison-overlap]
                raise RuntimeError(
                    "expected 171,000 decoded memo values and 19,000 empty memo "
                    f"fields, got {state['memo_values']}/{state['empty_values']}"
                )
            if sample.get("output_bytes") != 0:
                raise RuntimeError("Direct Read must not write any output bytes")
            if sample.get("temporary_bytes_written") != 0:
                raise RuntimeError("Direct Read must write zero temporary bytes")

        self.results.append(
            self._measure(
                "direct_read_memo_heavy",
                'Direct Read: iter_records(memo="inline") over the 190,000-record memo table '
                "(per-record FPT block reads, zero output)",
                make,
                input_bytes=input_bytes,
                input_records=190_000,
                post_validate=post_validate,
            )
        )

    def scenario_direct_read_deleted(self, policy: str) -> None:
        """iter_records over the 1,000-record fixture with 10% deleted rows."""
        from dbfbridge import iter_records

        source_dbf = self.deleted()
        input_bytes = self._source_bytes(source_dbf)
        include = policy == "include"
        state: dict[str, object] = {"records": 0, "deleted": 0}

        def make(out: Path):
            def run() -> None:
                records = 0
                deleted = 0
                for record in iter_records(str(source_dbf), include_deleted=include):
                    records += 1
                    if record.deleted:
                        deleted += 1
                state["records"] = records
                state["deleted"] = deleted

            return run

        def post_validate(out: Path, sample: dict[str, object]) -> None:
            records = state["records"]  # type: ignore[assignment]
            deleted = state["deleted"]  # type: ignore[assignment]
            if include:
                if records != 1_000 or deleted != 100:
                    raise RuntimeError(
                        f"expected 1,000 records with 100 deleted, got {records}/{deleted}"
                    )
            else:
                if records != 900 or deleted != 0:
                    raise RuntimeError(
                        f"expected 900 active records and no deleted rows, got {records}/{deleted}"
                    )
            if sample.get("output_bytes") != 0:
                raise RuntimeError("Direct Read must not write any output bytes")
            if sample.get("temporary_bytes_written") != 0:
                raise RuntimeError("Direct Read must write zero temporary bytes")

        self.results.append(
            self._measure(
                f"direct_read_deleted_{policy}",
                f"Direct Read: iter_records(include_deleted={include}) over the "
                "1,000-record table with 100 deleted rows (zero output)",
                make,
                input_bytes=input_bytes,
                input_records=1_000 if include else 900,
                post_validate=post_validate,
            )
        )

    def scenario_direct_read_encoding(self, codec: str) -> None:
        """iter_records with a forced Polish codepage over the encoding fixture.

        The Polish Mazovia/PIAST codecs are registered explicitly OUTSIDE the
        measured window (registration is a documented, on-demand side effect
        of the code paths that need them; Direct Read with an explicit
        non-auto encoding does not register them by itself).
        """
        import json as json_module

        from dbf_bridge.exporter.polish_codecs import register_polish_codecs
        from dbfbridge import iter_records

        register_polish_codecs()
        source_dbf = self.encoding_fixture(codec)
        expected_text = json_module.loads(
            source_dbf.with_suffix(".meta.json").read_text(encoding="utf-8")
        )["text"]
        input_bytes = self._source_bytes(source_dbf)
        state: dict[str, object] = {"records": 0, "text": None}

        def make(out: Path):
            def run() -> None:
                records = 0
                text = None
                for record in iter_records(str(source_dbf), encoding=codec, decode_errors="strict"):
                    records += 1
                    text = record.values["TEKST"]
                state["records"] = records
                state["text"] = text

            return run

        def post_validate(out: Path, sample: dict[str, object]) -> None:
            records = state["records"]  # type: ignore[assignment]
            text = state["text"]  # type: ignore[assignment]
            if records != 1:
                raise RuntimeError(f"expected exactly 1 record, got {records}")
            if text != expected_text:
                raise RuntimeError(f"forced {codec} decoding produced wrong logical text: {text!r}")
            if sample.get("output_bytes") != 0:
                raise RuntimeError("Direct Read must not write any output bytes")
            if sample.get("temporary_bytes_written") != 0:
                raise RuntimeError("Direct Read must write zero temporary bytes")

        self.results.append(
            self._measure(
                f"direct_read_{codec}",
                f"Direct Read: iter_records(encoding={codec!r}) over the Polish-diacritics "
                "table (strict decode, logical text verified, zero output)",
                make,
                input_bytes=input_bytes,
                input_records=1,
                post_validate=post_validate,
            )
        )

    def scenario_direct_read_raw(self, keep_raw: bool) -> None:
        """iter_records(raw=False/True) over the 190k table."""
        from dbfbridge import iter_records

        source_dbf = self.medium()
        input_bytes = self._source_bytes(source_dbf)
        state: dict[str, object] = {"records": 0, "raw_count": 0}

        def make(out: Path):
            def run() -> None:
                records = 0
                raw_count = 0
                for record in iter_records(str(source_dbf), memo="null", raw=keep_raw):
                    records += 1
                    if record.raw_record is not None:
                        raw_count += 1
                state["records"] = records
                state["raw_count"] = raw_count

            return run

        def post_validate(out: Path, sample: dict[str, object]) -> None:
            records = state["records"]  # type: ignore[assignment]
            raw_count = state["raw_count"]  # type: ignore[assignment]
            if records != 190_000:
                raise RuntimeError(f"expected 190,000 records, got {records}")
            if keep_raw and raw_count != records:
                raise RuntimeError(f"raw=True kept only {raw_count} raw record images")
            if not keep_raw and raw_count != 0:
                raise RuntimeError(f"raw=False kept {raw_count} raw record images")
            if sample.get("output_bytes") != 0:
                raise RuntimeError("Direct Read must not write any output bytes")
            if sample.get("temporary_bytes_written") != 0:
                raise RuntimeError("Direct Read must write zero temporary bytes")

        label = "kept" if keep_raw else "dropped"
        self.results.append(
            self._measure(
                "direct_read_raw_full" if keep_raw else "direct_read_raw_none",
                f"Direct Read: iter_records(raw={keep_raw}) over the 190k table "
                f"(physical record image {label}, zero output)",
                make,
                input_bytes=input_bytes,
                input_records=190_000,
                post_validate=post_validate,
            )
        )

    def scenario_direct_read_projection(self, mode: str) -> None:
        """iter_records field projection: selected columns vs every schema field."""
        from dbfbridge import iter_records, read_schema

        source_dbf = self.medium()
        input_bytes = self._source_bytes(source_dbf)
        if mode == "selected":
            fields: tuple[str, ...] | None = ("ID", "NAZWA", "KWOTA")
        else:
            # "all": explicitly select every schema field by name (resolved
            # once, outside the measured window).
            fields = tuple(field.name for field in read_schema(str(source_dbf)).fields)
        selected = tuple(sorted(fields)) if fields else ()
        state: dict[str, object] = {"records": 0, "keys": None}

        def make(out: Path):
            def run() -> None:
                records = 0
                keys: frozenset[str] | None = None
                for record in iter_records(str(source_dbf), fields=fields, memo="null"):
                    records += 1
                    if keys is None:
                        keys = frozenset(record.values)
                state["records"] = records
                state["keys"] = keys

            return run

        def post_validate(out: Path, sample: dict[str, object]) -> None:
            records = state["records"]  # type: ignore[assignment]
            keys = state["keys"]  # type: ignore[assignment]
            if records != 190_000:
                raise RuntimeError(f"expected 190,000 records, got {records}")
            if keys != frozenset(selected):
                raise RuntimeError(f"projection returned unexpected value keys: {keys!r}")
            if sample.get("output_bytes") != 0:
                raise RuntimeError("Direct Read must not write any output bytes")
            if sample.get("temporary_bytes_written") != 0:
                raise RuntimeError("Direct Read must write zero temporary bytes")

        self.results.append(
            self._measure(
                "direct_read_projection_selected"
                if mode == "selected"
                else "direct_read_projection_all",
                f"Direct Read: iter_records(fields={'3 selected columns' if mode == 'selected' else 'all schema fields'}) "
                "over the 190k table (unselected fields never parsed, zero output)",
                make,
                input_bytes=input_bytes,
                input_records=190_000,
                post_validate=post_validate,
            )
        )

    def scenario_direct_read_memo_policy(self, policy: str) -> None:
        """iter_records memo-policy triplet over the 2,000-record memo table."""
        from dbfbridge import LazyMemoValue, iter_records

        source_dbf = self.memo_heavy(2_000)
        input_bytes = self._source_bytes(source_dbf)
        state: dict[str, object] = {"records": 0, "memo": 0, "empty": 0, "absent": 0}

        def make(out: Path):
            def run() -> None:
                records = 0
                memo = 0
                empty = 0
                absent = 0
                for record in iter_records(str(source_dbf), memo=policy):
                    records += 1
                    if "NOTATKA" not in record.values:
                        absent += 1
                    elif isinstance(record.values["NOTATKA"], LazyMemoValue):
                        memo += 1
                    elif record.values["NOTATKA"] is None:
                        empty += 1
                    else:
                        memo += 1
                state["records"] = records
                state["memo"] = memo
                state["empty"] = empty
                state["absent"] = absent

            return run

        def post_validate(out: Path, sample: dict[str, object]) -> None:
            records = state["records"]  # type: ignore[assignment]
            if records != 2_000:
                raise RuntimeError(f"expected 2,000 records, got {records}")
            if policy == "skip":
                # skip removes the memo field from values entirely.
                if state["absent"] != 2_000:  # type: ignore[comparison-overlap]
                    raise RuntimeError(
                        f'memo="skip" must exclude the memo field from every record, '
                        f"absent in {state['absent']}"  # type: ignore[str-bytes-safe]
                    )
            elif policy == "lazy":
                # lazy exposes LazyMemoValue metadata (no FPT I/O) or None.
                if state["memo"] != 1_800 or state["empty"] != 200:  # type: ignore[comparison-overlap]
                    raise RuntimeError(
                        f"expected 1,800 lazy + 200 empty memo values, got "
                        f"{state['memo']}/{state['empty']}"  # type: ignore[str-bytes-safe]
                    )
            else:  # inline
                if state["memo"] != 1_800 or state["empty"] != 200:  # type: ignore[comparison-overlap]
                    raise RuntimeError(
                        f"expected 1,800 decoded + 200 empty memo values, got "
                        f"{state['memo']}/{state['empty']}"  # type: ignore[str-bytes-safe]
                    )
            if sample.get("output_bytes") != 0:
                raise RuntimeError("Direct Read must not write any output bytes")
            if sample.get("temporary_bytes_written") != 0:
                raise RuntimeError("Direct Read must write zero temporary bytes")

        self.results.append(
            self._measure(
                f"direct_read_memo_{policy}",
                f'Direct Read: iter_records(memo="{policy}") over the 2,000-record memo table '
                "(zero output)",
                make,
                input_bytes=input_bytes,
                input_records=2_000,
                post_validate=post_validate,
            )
        )

    def scenario_cold_import(self) -> None:
        """Subprocess timing of `import dbfbridge` (cold import cost)."""
        state: dict[str, object] = {}

        def make(out: Path):
            def run() -> None:
                result = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "import sys, time; t0=time.perf_counter(); import dbfbridge;"
                        " t1=time.perf_counter();"
                        " heavy=[m for m in ('dbf','polars','orjson','openpyxl','xlsxwriter')"
                        " if m in sys.modules]; assert not heavy, heavy;"
                        " print(f'{t1-t0:.4f}')",
                    ],
                    capture_output=True,
                    text=True,
                    cwd=str(self.root),
                )
                if result.returncode != 0:
                    raise RuntimeError(result.stderr[:500])
                elapsed = float(result.stdout.strip().splitlines()[-1])
                state["import_seconds"] = elapsed

            return run

        def post_validate(out: Path, sample: dict[str, object]) -> None:
            if sample.get("output_bytes") != 0:
                raise RuntimeError("cold import must not write output bytes")

        self.results.append(
            self._measure(
                "cold_import",
                "Cold import of dbfbridge in a fresh subprocess (no heavy deps loaded)",
                make,
                input_bytes=0,
                input_records=None,
                post_validate=post_validate,
            )
        )

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
        elif name == "reconstruction_memo_190k":
            self.scenario_reconstruction_memo(
                "reconstruction_memo_190k", self.memo_heavy(190_000), 190_000
            )
        elif name == "roundtrip_quality":
            self.scenario_roundtrip()
        elif name == "direct_read_bounded":
            self.scenario_direct_read_bounded()
        elif name == "field_projection":
            self.scenario_field_projection()
        elif name == "memo_lazy":
            self.scenario_memo_lazy()
        elif name == "raw_mode_none":
            self.scenario_raw_mode_none()
        elif name == "inspect_schema_1":
            self.scenario_inspect_schema(name, 1)
        elif name == "inspect_schema_100":
            self.scenario_inspect_schema(name, 100)
        elif name == "inspect_schema_1000":
            self.scenario_inspect_schema(name, 1000)
        elif name in {"direct_read_190k", "direct_read_1m"}:
            size = 190_000 if name == "direct_read_190k" else 1_000_000
            self.scenario_direct_read(name, size)
        elif name == "direct_read_memo_heavy":
            self.scenario_direct_read_memo_heavy()
        elif name in {"direct_read_deleted_include", "direct_read_deleted_skip"}:
            self.scenario_direct_read_deleted(name.removeprefix("direct_read_deleted_"))
        elif name in {"direct_read_cp1250", "direct_read_cp852", "direct_read_mazovia"}:
            self.scenario_direct_read_encoding(name.removeprefix("direct_read_"))
        elif name == "migration_dbf_to_jsonl":
            self.scenario_export(
                name,
                "Migration: DBF -> JSONL export over the 190k table (validate on)",
                self.medium(),
                input_records=190_000,
                validate=True,
            )
        elif name == "migration_jsonl_to_dbf_fpt":
            self.scenario_reconstruction_memo(
                "migration_jsonl_to_dbf_fpt", self.memo_heavy(190_000), 190_000
            )
        elif name in {"migration_validate_off", "migration_validate_on"}:
            self.scenario_export(
                name,
                f"Migration: DBF -> JSONL with output validation "
                f"{'disabled' if name.endswith('off') else 'enabled'}",
                self.medium(),
                input_records=190_000,
                validate=name.endswith("on"),
            )
        elif name in {"direct_read_raw_none", "direct_read_raw_full"}:
            self.scenario_direct_read_raw(name == "direct_read_raw_full")
        elif name in {"direct_read_projection_selected", "direct_read_projection_all"}:
            self.scenario_direct_read_projection(name.removeprefix("direct_read_projection_"))
        elif name in {"direct_read_memo_skip", "direct_read_memo_lazy", "direct_read_memo_inline"}:
            self.scenario_direct_read_memo_policy(name.removeprefix("direct_read_memo_"))
        elif name == "cold_import":
            self.scenario_cold_import()
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
        return self.results

    def payload(self) -> dict[str, object]:
        return {
            "fixtures": self.fixture_manifest(),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "scenarios": self.results,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=["fast", "full", "phase3"], default="fast")
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

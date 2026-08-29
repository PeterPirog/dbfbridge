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
import hashlib
import json
import statistics
import sys
import time
from collections.abc import Callable
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

# Bump when the JSONL input recipe changes; triggers safe regeneration.
JSONL_INPUT_VERSION = "1"


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
        post_validate: Callable[[Path, dict[str, object]], None] | None = None,
        extra_parameters: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Run warm-ups then measured reps, each into its own fresh output dir.

        ``post_validate`` (optional) runs **after** each measured repetition's
        ``metrics.run`` has returned — i.e. outside the wall/CPU measurement
        window — and may inspect/flatten the artefacts and attach per-sample
        extras.  Raising marks that sample ``FAILED`` (with the error preserved)
        without inflating the measured times.
        """

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
            sample = bench_metrics.run(
                function_factory(out),
                input_bytes=input_bytes,
                input_records=input_records,
                output_dir=out,
                warmup=False,
            )
            if post_validate is not None and sample.get("status") == STATUS_MEASURED:
                try:
                    post_validate(out, sample)
                except Exception as exc:  # post-validation failure fails the SAMPLE
                    sample["status"] = STATUS_FAILED
                    sample["error"] = f"post-validation failed: {type(exc).__name__}: {exc}"
            measured.append(sample)

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

    def scenario_reconstruction_memo(self, name: str, source_dbf: Path, records: int) -> None:
        """Memo-heavy reconstruction: a real JSONL -> DBF + FPT rebuild.

        The JSONL input is prepared **outside** the measured window.  The
        **measured callable is ONLY the public ``reconstruct_dbf``** — nothing
        else.  All post-validation (flattening the rebuilt tree, verifying the
        DBF and FPT artefacts are present and non-empty, counting records, and
        attaching ``output_dbf_bytes`` / ``output_fpt_bytes`` /
        ``fpt_mib_per_second``) runs in ``post_validate`` **after** the
        wall/CPU measurement window has closed, so it can never inflate the
        measured times.  A missing/empty FPT (or a record-count mismatch)
        fails the sample via post-validation.
        """

        from dbfbridge import export_dbf, reconstruct_dbf

        export_dir = self.work_dir / "out" / name / "export"
        export_dir.mkdir(parents=True, exist_ok=True)
        export_dbf(
            str(source_dbf.parent),
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
                # window.  Flattening, stat and artifact validation are in
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
            target = out / "rebuilt"
            if target.exists():
                for child in target.iterdir():
                    child.rename(out / child.name)
                target.rmdir()
            dbfs = [p for p in out.rglob("*.dbf") if p.is_file()]
            fpts = [p for p in out.rglob("*.fpt") if p.is_file()]
            if not dbfs or dbfs[0].stat().st_size == 0:
                raise RuntimeError("reconstructed DBF is missing or empty")
            if not fpts or fpts[0].stat().st_size == 0:
                raise RuntimeError("reconstructed FPT is missing or empty")
            counts = fixture_factory._measured_counts(dbfs[0])
            if counts["total_records"] != records:
                raise RuntimeError(
                    f"reconstructed DBF has {counts['total_records']} records, expected {records}"
                )
            dbf_bytes = dbfs[0].stat().st_size
            fpt_bytes = fpts[0].stat().st_size
            sample["output_dbf_bytes"] = dbf_bytes
            sample["output_fpt_bytes"] = fpt_bytes
            # Authoritative output size now that the artefacts are flattened in.
            sample["output_bytes"] = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
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
        elif name == "reconstruction_memo_190k":
            self.scenario_reconstruction_memo(
                "reconstruction_memo_190k", self.memo_heavy(190_000), 190_000
            )
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

"""Repeatable Phase 0 benchmark controller for dbfbridge.

Each scenario runs in a **dedicated worker subprocess** (``benchmarks/worker.py``)
so that a crash or timeout in one scenario cannot take down the controller or the
rest of the report.  The controller records the environment (commit, worktree
state, Python, OS, CPU, physical memory, dependency versions, fixture sizes) and
**always** writes JSON + Markdown results, even when scenarios fail.

Scenario statuses are never invented:

- ``MEASURED``          - the code path exists and every measured repetition ran
                          successfully; warm-up runs are excluded from results;
- ``FAILED``            - the scenario raised, a repetition failed, the worker
                          crashed, produced malformed output, or timed out
                          (exit code + diagnostic log included; no metrics invented);
- ``NOT_IMPLEMENTED``   - a scenario in the profile contract is not implemented
                          (the list is empty since Phase 1 implements the direct
                          record paths; any unexpected NOT_IMPLEMENTED entry is
                          reported verbatim, never simulated);
- ``NOT_AVAILABLE``     - a specific metric could not be provided (e.g. RSS without
                          ``psutil``); recorded as ``null`` / ``NOT_AVAILABLE``.

Controller exit code:
- ``0``  when every executable scenario is ``MEASURED``;
- non-zero when any executable scenario is ``FAILED`` (reports are still written).

Usage:
    python -m benchmarks.run_benchmark --profile fast
    python -m benchmarks.run_benchmark --profile fast --warmup 1 --repetitions 3
    python -m benchmarks.run_benchmark --profile fast --scenario export_jsonl_validate_on
    python -m benchmarks.run_benchmark --profile full --baseline --timeout 600
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

#: Versioned identity of the Phase 1 benchmark report contract (direct record
#: read).  A future Phase 1 AFTER baseline is only accepted when the payload
#: carries exactly this value, which visibly separates it from the preserved
#: Phase 0 BEFORE baseline.  Single source: ``benchmarks.artifacts``.
from .artifacts import BENCHMARK_CONTRACT as BENCHMARK_CONTRACT
from .artifacts import CONTRACT_PHASE_1 as CONTRACT_PHASE_1
from .artifacts import BaselinePublishError, publish_baseline_pair, report_stem
from .contract import derive_run_id, validate_saved_phase1_after

REPO_ROOT = Path(__file__).resolve().parent.parent

STATUS_FAILED = "FAILED"
STATUS_MEASURED = "MEASURED"
STATUS_NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


def git_state(root: Path) -> dict[str, str | bool]:
    def run(*args: str) -> str:
        completed = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=False
        )
        return completed.stdout.strip()

    return {
        "commit": run("rev-parse", "HEAD"),
        "origin_main": run("rev-parse", "origin/main"),
        "branch": run("branch", "--show-current"),
        "worktree_dirty": bool(run("status", "--porcelain")),
        "worktree_status": run("status", "--short"),
    }


def package_versions() -> dict[str, str]:
    names = [
        "dbfbridge",
        "dbf",
        "dbfread",
        "orjson",
        "polars",
        "openpyxl",
        "xlsxwriter",
        "psutil",
    ]
    versions: dict[str, str] = {}
    for name in names:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "NOT_AVAILABLE"
    return versions


def physical_memory_bytes() -> int | None:
    """Total physical memory via ``psutil``; ``None`` (NOT_AVAILABLE) otherwise.

    No direct WinAPI (ctypes) is used anywhere in the benchmark infrastructure.
    """
    try:
        import psutil

        return int(psutil.virtual_memory().total)
    except (ImportError, Exception):
        return None


def psutil_available() -> bool:
    """True when the optional ``psutil`` benchmark dependency is importable."""

    try:
        import psutil  # noqa: F401

        return True
    except ImportError:
        return False


def system_info() -> dict[str, object]:
    return {
        "python": platform.python_version(),
        "os": f"{platform.system()} {platform.release()} {platform.version()}",
        "arch": platform.machine(),
        "processor": platform.processor() or "NOT_AVAILABLE",
        "cpu_count": os.cpu_count(),
        "physical_memory_bytes": physical_memory_bytes(),
    }


def _scenario_names(profile: str) -> list[str]:
    from benchmarks.worker import _scenario_names as names

    return list(names(profile))


def _failed(
    scenario: str,
    reason: str,
    *,
    exit_code: int | None = None,
    diagnostic_log: str | None = None,
    error_tail: str | None = None,
    timed_out: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "scenario": scenario,
        "status": STATUS_FAILED,
        "reason": reason,
        "parameters": {},
        "samples": [],
        "aggregated": {},
    }
    if exit_code is not None:
        result["worker_exit_code"] = exit_code
        result["worker_exit_code_hex"] = f"0x{exit_code & 0xFFFFFFFF:X}"
    if diagnostic_log:
        result["diagnostic_log"] = diagnostic_log
    if error_tail:
        result["error_tail"] = error_tail
    if timed_out:
        result["timed_out"] = True
    return result


def run_scenario(
    profile: str,
    work_dir: Path,
    repetitions: int,
    warmup: int,
    scenario_name: str,
    logs_dir: Path,
    timeout: float,
) -> dict[str, Any]:
    """Run one scenario in a fresh worker subprocess; always returns a result."""

    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"{profile}_{scenario_name}.log"
    env = dict(os.environ, PYTHONFAULTHANDLER="1", PYTHONPATH=str(REPO_ROOT / "src"))
    worker_args = [
        "--profile",
        profile,
        "--work-dir",
        str(work_dir),
        "--repetitions",
        str(repetitions),
        "--warmup",
        str(warmup),
        "--scenario",
        scenario_name,
    ]
    # ``BENCHMARK_WORKER`` (a .py path) replaces ``-m benchmarks.worker``; it
    # exists so tests can inject a faulty worker without touching the package.
    worker_override = os.environ.get("BENCHMARK_WORKER")
    if worker_override:
        command = [sys.executable, "-X", "faulthandler", worker_override, *worker_args]
    else:
        command = [
            sys.executable,
            "-X",
            "faulthandler",
            "-m",
            "benchmarks.worker",
            *worker_args,
        ]

    try:
        with log_path.open("wb") as log_file:
            proc = subprocess.run(
                command,
                cwd=str(REPO_ROOT),
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
    except subprocess.TimeoutExpired as exc:
        tail = exc.output[-2000:].decode("utf-8", "replace") if exc.output else ""
        return _failed(
            scenario_name,
            "worker exceeded the per-scenario timeout",
            exit_code=-1,
            diagnostic_log=str(log_path),
            error_tail=tail,
            timed_out=True,
        )

    exit_code = proc.returncode
    tail = ""
    if exit_code != 0:
        with contextlib.suppress(OSError):
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
        return _failed(
            scenario_name,
            f"worker process exited with code {exit_code}",
            exit_code=exit_code,
            diagnostic_log=str(log_path),
            error_tail=tail,
        )

    stdout = ""
    with contextlib.suppress(OSError):
        stdout = log_path.read_text(encoding="utf-8", errors="replace").strip()
    try:
        last_line = stdout.splitlines()[-1]
        parsed = json.loads(last_line)
    except (IndexError, json.JSONDecodeError) as exc:
        with contextlib.suppress(OSError):
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
        return _failed(
            scenario_name,
            f"worker output could not be parsed: {exc}",
            exit_code=0,
            diagnostic_log=str(log_path),
            error_tail=tail,
        )

    if not parsed.get("ok"):
        return _failed(
            scenario_name,
            str(parsed.get("error", "worker reported failure")),
            exit_code=0,
            diagnostic_log=str(log_path),
        )

    scenarios = parsed.get("scenarios", [])
    if len(scenarios) != 1:
        return _failed(
            scenario_name,
            f"expected exactly 1 worker result, got {len(scenarios)}",
            exit_code=0,
            diagnostic_log=str(log_path),
        )
    result = dict(scenarios[0])
    if result.get("scenario") != scenario_name:
        return _failed(
            scenario_name,
            f"worker returned scenario {result.get('scenario')!r}, expected {scenario_name!r}",
            exit_code=0,
            diagnostic_log=str(log_path),
        )
    result.setdefault("worker_exit_code", 0)
    result["diagnostic_log"] = str(log_path)
    return result


NOT_IMPLEMENTED: tuple[dict[str, str], ...] = ()


def _not_implemented() -> list[dict[str, Any]]:
    return [
        {
            **entry,
            "status": STATUS_NOT_IMPLEMENTED,
            "parameters": {},
            "samples": [],
            "aggregated": {},
        }
        for entry in NOT_IMPLEMENTED
    ]


AGG_METRIC_COLUMNS = [
    ("median_wall_seconds", "median wall (s)"),
    ("median_cpu_seconds", "median cpu (s)"),
    ("median_records_per_second", "median rec/s"),
    ("median_source_mib_per_second", "median MiB/s"),
    ("max_peak_rss_bytes", "peak RSS (MiB)"),
    ("max_output_bytes", "output (MiB)"),
    ("max_output_dbf_bytes", "max DBF (MiB)"),
    ("max_output_fpt_bytes", "max FPT (MiB)"),
    ("median_fpt_mib_per_second", "median FPT MiB/s"),
    ("median_read_amplification", "median read amp"),
    ("median_write_amplification", "median write amp"),
    ("max_temporary_bytes_written", "max temporary written (MiB)"),
]

# Aggregate columns rendered in MiB units.
_MIB_COLUMNS = {
    "max_peak_rss_bytes",
    "max_output_bytes",
    "max_output_dbf_bytes",
    "max_output_fpt_bytes",
    "max_temporary_bytes_written",
}


def _sample_missing_metrics(sample: dict[str, Any]) -> list[str]:
    """Return the names of required metrics missing from a single measured sample.

    The requirement is per-sample and conditional on the sample's own
    ``input_bytes`` / ``output_bytes`` (both are present in every sample).
    """

    missing: list[str] = []
    for key in (
        "wall_seconds",
        "cpu_seconds",
        "records_per_second",
        "output_bytes",
        "peak_rss_bytes",
    ):
        if sample.get(key) is None:
            missing.append(key)
    input_bytes = sample.get("input_bytes")
    if input_bytes is not None and input_bytes > 0:
        for key in ("source_mib_per_second", "read_amplification"):
            if sample.get(key) is None:
                missing.append(key)
    output_bytes = sample.get("output_bytes")
    if output_bytes is not None and output_bytes > 0 and sample.get("write_amplification") is None:
        missing.append("write_amplification")
    temp = sample.get("temporary_bytes_written")
    if not isinstance(temp, int) or isinstance(temp, bool) or temp < 0:
        missing.append("temporary_bytes_written")
    for key in ("temporary_files_left", "temporary_bytes_left"):
        value = sample.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value != 0:
            missing.append(f"{key}=0")
    return missing


def _memo_sample_missing_metrics(sample: dict[str, Any]) -> list[str]:
    """Extra requirements for a ``reconstruction_memo_190k`` sample.

    Only this scenario reconstructs a memo table, so only it must show real
    DBF **and** FPT outputs: both non-empty, a positive FPT throughput, at
    least two temporary publishes (DBF + FPT), and temporary bytes covering
    the final DBF+FPT sizes.  Scenarios without an FPT are never asked for a
    separate FPT throughput.
    """

    missing: list[str] = []
    dbf = sample.get("output_dbf_bytes")
    fpt = sample.get("output_fpt_bytes")
    if not (isinstance(dbf, int) and not isinstance(dbf, bool) and dbf > 0):
        missing.append("output_dbf_bytes")
    if not (isinstance(fpt, int) and not isinstance(fpt, bool) and fpt > 0):
        missing.append("output_fpt_bytes")
    rate = sample.get("fpt_mib_per_second")
    if not isinstance(rate, (int, float)) or isinstance(rate, bool) or rate <= 0:
        missing.append("fpt_mib_per_second")
    count = sample.get("temporary_publish_count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 2:
        missing.append("temporary_publish_count")
    # Only when both outputs are present can the sum be checked; otherwise the
    # generic temporary_bytes_written check (int >= 0) still applies.
    if isinstance(dbf, int) and isinstance(fpt, int) and dbf > 0 and fpt > 0:
        temp = sample.get("temporary_bytes_written")
        if not isinstance(temp, int) or isinstance(temp, bool) or temp < dbf + fpt:
            missing.append("temporary_bytes_written")
    return missing


def _is_positive_int(value: Any, minimum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def check_baseline_gate(payload: dict[str, Any]) -> list[str]:
    """Return the list of reasons a versioned baseline must be REJECTED.

    An empty list means the run is eligible to be copied into
    ``benchmarks/baselines/``.  The shared saved-artifact contract (frozen
    scenario names, exact sample/warm-up counts, per-sample statuses and
    metrics, valid_baseline, full commit and clean worktree, system/package
    metadata, run_id) is delegated to the host-independent validator
    :func:`benchmarks.contract.validate_saved_phase1_after`, which works purely
    on the payload content.

    The gate keeps the one check that genuinely belongs to the ACTIVE machine:
    the current availability of ``psutil`` (RSS/IO metrics must have been
    collectable while the scenarios ran).
    """

    reasons: list[str] = []
    if not isinstance(payload, dict):
        return ["payload is not a dict (malformed payload)"]
    # Live check: the current process must have had psutil available, so the
    # recorded samples were actually able to carry RSS/IO metrics.
    if not psutil_available():
        reasons.append("psutil is not available; a baseline requires RSS/IO metrics")
    # Everything else is the pure saved-artifact contract.
    reasons.extend(validate_saved_phase1_after(payload))
    _ = _is_positive_int  # kept for helper parity
    return reasons


def _fmt(value: object, unit: str = "") -> str:
    if value is None:
        return "NOT_AVAILABLE"
    if not isinstance(value, (int, float)):
        return str(value)
    if unit == "MiB":
        return f"{value / (1024 * 1024):,.2f}"
    if isinstance(value, float):
        return f"{value:,.3f}"
    return f"{value:,}"


def render_markdown(payload: dict[str, Any]) -> str:
    env: dict[str, Any] = payload["environment"]
    git: dict[str, Any] = env["git"]
    scenarios: list[dict[str, Any]] = list(payload["scenarios"])
    lines = [
        "# dbfbridge benchmark report",
        "",
        f"- run_id: `{env.get('run_id', 'n/a')}` (identical in JSON, Markdown and the manifest)",
        f"- benchmark_contract: `{env.get('benchmark_contract', 'legacy-phase-0')}`",
        f"- Profile: `{env['profile']}`",
        f"- Commit: `{git['commit']}` (origin/main: `{git['origin_main']}`)",
        f"- Worktree: {'dirty' if git['worktree_dirty'] else 'clean'} on branch `{git['branch']}`",
        f"- Warm-up: {env['warmup']} (excluded from results); Repetitions: {env['repetitions']} (measured)",
        "- Aggregation: median of measured repetitions (all samples preserved in JSON)",
        f"- Python: {env['system']['python']}",
        f"- OS: {env['system']['os']}",
        f"- CPU: {env['system']['processor']} ({env['system']['cpu_count']} logical CPUs)"
        + (
            f", {int(env['system']['physical_memory_bytes']) / (1 << 30):.0f} GiB RAM"
            if env["system"]["physical_memory_bytes"]
            else ", physical RAM NOT_AVAILABLE"
        ),
        "- Packages: " + ", ".join(f"{k} {v}" for k, v in env["packages"].items()),
        "",
        "Statuses: `MEASURED` = all measured repetitions succeeded; `FAILED` = a repetition "
        "failed, the worker crashed/timed out, or output was invalid (no metrics invented); "
        "`NOT_IMPLEMENTED` = the feature does not exist in dbfbridge 0.1.0 (not simulated); "
        "`NOT_AVAILABLE` = a metric could not be provided (e.g. RSS without psutil).",
        "",
        "| Scenario | Status | " + " | ".join(label for _, label in AGG_METRIC_COLUMNS) + " |",
        "|---|---|" + "---:|" * len(AGG_METRIC_COLUMNS),
    ]
    for scenario in scenarios:
        if scenario.get("status") == STATUS_NOT_IMPLEMENTED:
            lines.append(
                f"| `{scenario['scenario']}` | NOT_IMPLEMENTED | "
                + " | ".join(["NOT_AVAILABLE"] * len(AGG_METRIC_COLUMNS))
                + " |"
            )
            continue
        agg = dict(scenario.get("aggregated") or {})
        failed_note = (
            f" — {scenario.get('reason', '')}" if scenario.get("status") == STATUS_FAILED else ""
        )
        valid_baseline = (scenario.get("status") != STATUS_FAILED) and bool(
            agg.get("valid_baseline", True)
        )
        cells = [
            _fmt(agg.get(key), "MiB" if key in _MIB_COLUMNS else "")
            for key, _label in AGG_METRIC_COLUMNS
        ]
        # A FAILED scenario must never present its (partial) medians as a
        # comparable baseline: when the aggregate is present but not valid,
        # label every cell so the row cannot be misread as a success.
        if not valid_baseline:
            cells = [f"NOT A VALID BASELINE ({c})" for c in cells]
        elif scenario.get("status") == STATUS_FAILED:
            cells = ["NOT_AVAILABLE" for _ in cells]
        lines.append(
            f"| `{scenario['scenario']}` | {scenario['status']}{failed_note} | "
            + " | ".join(cells)
            + " |"
        )
    lines += [
        "",
        "## Notes",
        "",
        "- Each scenario runs in its own worker subprocess with a configurable timeout; one "
        "failed/timed-out scenario is `FAILED` and does not affect the others.",
        "- Warm-up runs are excluded from aggregates; every warm-up and repetition writes into "
        "a fresh isolated directory and uses the same post-validation after the timed call.",
        "- `output_bytes` is the authoritative final size of the scenario's own output directory, "
        "so re-running an overwritten scenario still reports the real size (never a zero diff).",
        "- Peak RSS is the maximum of `psutil` samples taken on a background thread during the "
        "measured call (the sampler is always stopped/joined in `finally`). Without `psutil` it "
        "is `NOT_AVAILABLE`.",
        "- `temporary_bytes_written` is the logical size of the atomic `.partial` files "
        "published by the measured call (the worker intercepts `os.replace` for that call "
        "only); it is **0** when the operation created no temporary file and "
        "`NOT_AVAILABLE` (with a reason) only if the platform forbids reading the "
        "temporary file.",
        "- `temporary_files_left` and `temporary_bytes_left` are checked after timing and must "
        "both be zero; atomic-write residue fails the sample and baseline gate.",
        "- `NOT_IMPLEMENTED` scenarios are listed verbatim and are not estimated.",
    ]
    return "\n".join(lines) + "\n"


def _fixture_manifest(work_dir: Path) -> dict[str, object]:
    fixture_dir = work_dir / "fixtures"
    manifest: dict[str, object] = {"directory": str(fixture_dir)}
    if fixture_dir.is_dir():
        for path in sorted(fixture_dir.rglob("*")):
            if path.is_file() and path.suffix != ".meta.json":
                manifest[path.as_posix()] = path.stat().st_size
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=["fast", "full"], default="fast")
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=REPO_ROOT / "benchmark-data",
        help="Working directory for fixtures and outputs (not committed).",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=REPO_ROOT / "benchmarks" / "results",
        help="Where the JSON and Markdown reports are written (git-ignored).",
    )
    parser.add_argument("--repetitions", type=int, default=3, help="Measured repetitions (>=1)")
    parser.add_argument(
        "--warmup",
        "--warmups",
        dest="warmup",
        type=int,
        default=1,
        help="Warm-up runs (excluded, >=0)",
    )
    parser.add_argument(
        "--scenario",
        help="Run one or more scenarios by comma-separated name (see --list)",
    )
    parser.add_argument("--list", action="store_true", help="List scenario names and exit")
    parser.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        help="Per-scenario worker timeout in seconds (exceeded => FAILED).",
    )
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="Also copy the JSON + Markdown report into benchmarks/baselines/ for versioning.",
    )
    args = parser.parse_args(argv)

    if args.repetitions < 1:
        parser.error("--repetitions must be >= 1")
    if args.warmup < 0:
        parser.error("--warmup must be >= 0")
    if args.timeout <= 0:
        parser.error("--timeout must be > 0")

    scenario_names = _scenario_names(args.profile)
    if args.list:
        for name in scenario_names:
            print(name)
        return 0
    requested = [name.strip() for name in (args.scenario or "").split(",") if name.strip()]
    for name in requested:
        if name not in scenario_names:
            parser.error(f"unknown scenario {name!r}; use --list")

    work_dir: Path = args.work_dir
    logs_dir = work_dir / "logs"
    names = requested or scenario_names

    results: list[dict[str, Any]] = []
    for name in names:
        print(f"[{name}] running ...", file=sys.stderr)
        results.append(
            run_scenario(
                args.profile,
                work_dir,
                args.repetitions,
                args.warmup,
                name,
                logs_dir,
                args.timeout,
            )
        )

    results.extend(_not_implemented())

    # ------------------------------------------------------------------ payload shape
    payload: dict[str, Any] = {
        "environment": {
            "git": git_state(REPO_ROOT),
            "system": system_info(),
            "packages": package_versions(),
            "benchmark_contract": BENCHMARK_CONTRACT,
            "profile": args.profile,
            "repetitions": args.repetitions,
            "warmup": args.warmup,
            "aggregation": "median-of-measured-repetitions",
            # The run_id is derived deterministically from the run content and
            # embedded in the JSON, the Markdown and (on publication) the
            # manifest — one identifier for all artifacts.
            "run_id": derive_run_id(
                {
                    "environment": {
                        "benchmark_contract": BENCHMARK_CONTRACT,
                        "git": git_state(REPO_ROOT),
                        "profile": args.profile,
                        "repetitions": args.repetitions,
                        "warmup": args.warmup,
                    },
                    "scenarios": results,
                }
            ),
        },
        "fixtures": _fixture_manifest(work_dir),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "scenarios": results,
    }
    run_id = payload["environment"]["run_id"]

    # ALWAYS write reports, even if scenarios failed.  The report names are
    # derived from the versioned benchmark contract (never the legacy
    # phase-0 prefix), including --scenario suffixes.
    args.results_dir.mkdir(parents=True, exist_ok=True)
    scenario_suffix = "_".join(requested) if requested else ""
    stem = report_stem(BENCHMARK_CONTRACT, args.profile, scenario_suffix)
    json_path = args.results_dir / f"{stem}.json"
    md_path = args.results_dir / f"{stem}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(payload), encoding="utf-8")

    # A versioned baseline is created ONLY when the full gate passes, and the
    # publication is an exception-safe transaction: contract-derived names
    # from the actually-read JSON, full AFTER validation of the published
    # bytes, no overwrite, a manifest published last, rollback on failure and
    # post-write verification.
    baseline_note = ""
    if args.baseline:
        gate_reasons = check_baseline_gate(payload)
        if gate_reasons:
            for reason in gate_reasons:
                print(f"BASELINE REFUSED: {reason}", file=sys.stderr)
            print(
                "baseline NOT created; no files were copied into benchmarks/baselines/",
                file=sys.stderr,
            )
            return 2
        baseline_dir = REPO_ROOT / "benchmarks" / "baselines"
        try:
            published = publish_baseline_pair(json_path, md_path, baseline_dir)
        except BaselinePublishError as exc:
            print(f"BASELINE REFUSED: {exc}", file=sys.stderr)
            print(
                "baseline NOT created; benchmarks/baselines/ is unchanged.",
                file=sys.stderr,
            )
            return 2
        baseline_note = (
            f" baseline={published['json']}"
            f" manifest={published['manifest']}"
            f" run_id={published['run_id']}"
            f" json_sha256={published['json_sha256']}"
            f" markdown_sha256={published['markdown_sha256']}"
        )

    print(
        json.dumps(
            {
                "profile": args.profile,
                "run_id": run_id,
                "json": str(json_path),
                "markdown": str(md_path),
            }
        )
    )
    if baseline_note:
        print(baseline_note)

    print(json.dumps({"profile": args.profile, "json": str(json_path), "markdown": str(md_path)}))
    if baseline_note:
        print(baseline_note)

    # Exit non-zero if any executable scenario FAILED.
    failed = [s for s in results if s.get("status") == STATUS_FAILED]
    if failed:
        print(
            f"{len(failed)} scenario(s) FAILED: " + ", ".join(s["scenario"] for s in failed),
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
- ``NOT_IMPLEMENTED``   - the feature does not exist in dbfbridge 0.1.0;
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
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

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


NOT_IMPLEMENTED = (
    {
        "scenario": "direct_read_bounded",
        "description": (
            "read_records()/iter_records() do not exist in dbfbridge 0.1.0; "
            "the planned Direct Read Core is a Phase 1 feature."
        ),
    },
    {
        "scenario": "field_projection",
        "description": "No fields= projection option exists in dbfbridge 0.1.0.",
    },
    {
        "scenario": "memo_lazy",
        "description": 'memo="lazy" does not exist in dbfbridge 0.1.0 (skip/inline/null only).',
    },
    {
        "scenario": "raw_mode_none",
        "description": (
            'raw_mode="none" does not exist in dbfbridge 0.1.0; the raw-record '
            "property is always written to JSON/JSONL."
        ),
    },
)


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
    ("median_read_amplification", "median read amp"),
    ("median_write_amplification", "median write amp"),
    ("max_temporary_bytes_written", "max temporary written (MiB)"),
]

# Aggregate columns rendered in MiB units.
_MIB_COLUMNS = {"max_peak_rss_bytes", "max_output_bytes", "max_temporary_bytes_written"}


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
    ``benchmarks/baselines/``.  A versioned baseline is only allowed from a
    **full, clean, complete** run with ``psutil`` available:

    - the report contains only ``MEASURED`` / ``FAILED`` / ``NOT_IMPLEMENTED``
      entries, every scenario name exactly once, all names inside the full
      profile contract (``reconstruction_memo_190k`` included);
    - exactly the full-profile set of MEASURED scenarios (20), the exact
      NOT_IMPLEMENTED set (4), zero FAILED;
    - for every MEASURED scenario: ``len(samples) == environment["repetitions"]``
      and **every** sample is ``MEASURED`` with all required metrics;
      ``len(warmup_samples) == environment["warmup"]`` and **every** warm-up
      sample is ``MEASURED`` — a missing/extra/FAILED warm-up rejects the
      baseline independent of ``aggregated.valid_baseline``;
    - ``reconstruction_memo_190k`` samples additionally require the real
      DBF+FPT metrics (see :func:`_memo_sample_missing_metrics`).
    """

    reasons: list[str] = []
    env = payload.get("environment", {})
    git = env.get("git", {})
    all_scenarios = [s for s in payload.get("scenarios", []) if isinstance(s, dict)]
    statuses = [s.get("status") for s in all_scenarios]
    names = [s.get("scenario") for s in all_scenarios]
    measured = [s for s in all_scenarios if s.get("status") == STATUS_MEASURED]
    not_implemented = [s for s in all_scenarios if s.get("status") == STATUS_NOT_IMPLEMENTED]
    failed = [s for s in all_scenarios if s.get("status") == STATUS_FAILED]

    if env.get("profile") != "full":
        reasons.append(f"profile is {env.get('profile')!r}; a baseline requires --profile full")
    if not psutil_available():
        reasons.append("psutil is not available; a baseline requires RSS/IO metrics")

    warmup_expected = env.get("warmup")
    reps_expected = env.get("repetitions")
    if not _is_positive_int(warmup_expected, 1):
        reasons.append(f"warmup is {warmup_expected!r}; a baseline requires warmup >= 1")
    if not _is_positive_int(reps_expected, 3):
        reasons.append(f"repetitions is {reps_expected!r}; a baseline requires repetitions >= 3")

    # ------------------------------------------------------------------ report shape
    allowed = {STATUS_MEASURED, STATUS_FAILED, STATUS_NOT_IMPLEMENTED}
    unknown = sorted(str(st) for st in statuses if st not in allowed)
    if unknown:
        reasons.append(f"unknown scenario status(es) in the report: {', '.join(map(str, unknown))}")
    duplicates = sorted({str(n) for n in names if names.count(n) > 1})
    if duplicates:
        reasons.append("duplicate scenario names in the report: " + ", ".join(duplicates))
    contract = set(_scenario_names("full")) | {e["scenario"] for e in NOT_IMPLEMENTED}
    foreign = sorted({str(n) for n in names if n not in contract})
    if foreign:
        reasons.append("scenario name(s) outside the full-profile contract: " + ", ".join(foreign))

    expected_full = list(_scenario_names("full"))
    measured_names = [s["scenario"] for s in measured]
    expected_set = set(expected_full)
    have_set = set(measured_names)
    missing = [n for n in expected_full if n not in have_set]
    extra = sorted(str(n) for n in have_set - expected_set if n is not None)
    if missing:
        reasons.append(
            f"{len(missing)} expected full-profile MEASURED scenario(s) missing: "
            + ", ".join(missing)
        )
    if extra:
        reasons.append(
            "unexpected MEASURED scenario(s) not in the full profile: " + ", ".join(extra)
        )
    if len(measured) != len(expected_full):
        reasons.append(
            f"expected exactly {len(expected_full)} MEASURED scenarios, found {len(measured)}"
        )

    expected_not_impl = {s["scenario"] for s in NOT_IMPLEMENTED}
    have_not_impl = {s["scenario"] for s in not_implemented}
    if have_not_impl != expected_not_impl:
        reasons.append(
            "NOT_IMPLEMENTED set mismatch; expected "
            f"{sorted(expected_not_impl)}, found {sorted(have_not_impl)}"
        )

    if failed:
        reasons.append(
            f"{len(failed)} scenario(s) FAILED: " + ", ".join(s["scenario"] for s in failed)
        )

    invalid = [
        s["scenario"] for s in measured if not (s.get("aggregated") or {}).get("valid_baseline")
    ]
    if invalid:
        reasons.append(
            f"{len(invalid)} MEASURED scenario(s) without a valid baseline: " + ", ".join(invalid)
        )

    # Per-scenario completeness: exact sample counts AND every sample complete.
    incomplete: list[str] = []
    for s in measured:
        problems: list[str] = []
        samples = s.get("samples")
        if not isinstance(samples, list):
            problems.append("no sample list")
        else:
            if _is_positive_int(reps_expected, 0) and len(samples) != reps_expected:
                problems.append(
                    f"{len(samples)} samples but the run declares {reps_expected} repetitions"
                )
            for i, sample in enumerate(samples, start=1):
                if not isinstance(sample, dict) or sample.get("status") != STATUS_MEASURED:
                    shown = sample.get("status") if isinstance(sample, dict) else "absent"
                    problems.append(f"rep{i}: sample status is {shown!r}, expected MEASURED")
                    continue
                extra_missing = _sample_missing_metrics(sample)
                if s.get("scenario") == "reconstruction_memo_190k":
                    extra_missing += _memo_sample_missing_metrics(sample)
                if extra_missing:
                    problems.append(f"rep{i}: missing {','.join(extra_missing)}")
        warmups = s.get("warmup_samples")
        if not isinstance(warmups, list) or (
            _is_positive_int(warmup_expected, 0) and len(warmups) != warmup_expected
        ):
            found = len(warmups) if isinstance(warmups, list) else "absent"
            problems.append(f"warmup count mismatch: {found} != {warmup_expected}")
        elif isinstance(warmups, list):
            for i, warm in enumerate(warmups, start=1):
                if not isinstance(warm, dict) or warm.get("status") != STATUS_MEASURED:
                    shown = warm.get("status") if isinstance(warm, dict) else "absent"
                    problems.append(f"warmup{i}: status is {shown!r}, expected MEASURED")
        if problems:
            incomplete.append(f"{s['scenario']} ({'; '.join(problems)})")
    if incomplete:
        reasons.append(
            f"{len(incomplete)} MEASURED scenario(s) with incomplete samples or warm-ups: "
            + " | ".join(incomplete)
        )

    commit = str(git.get("commit") or "")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
        reasons.append(f"commit SHA is not a full 40-hex value: {commit!r}")
    if git.get("worktree_dirty", True):
        reasons.append("worktree was dirty before the run; a baseline requires a clean worktree")
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
        "# dbfbridge Phase 0 benchmark baseline",
        "",
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
        "- Warm-up runs are excluded from the reported samples; each measured repetition writes "
        "into its own fresh `out/<scenario>/rep-<n>/` directory (no inherited output).",
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

    payload: dict[str, Any] = {
        "environment": {
            "git": git_state(REPO_ROOT),
            "system": system_info(),
            "packages": package_versions(),
            "profile": args.profile,
            "repetitions": args.repetitions,
            "warmup": args.warmup,
            "aggregation": "median-of-measured-repetitions",
        },
        "fixtures": _fixture_manifest(work_dir),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "scenarios": results,
    }

    # ALWAYS write reports, even if scenarios failed.
    args.results_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"-{'_'.join(requested)}" if requested else ""
    json_path = args.results_dir / f"phase-0-{args.profile}{suffix}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path = args.results_dir / f"phase-0-{args.profile}{suffix}.md"
    md_path.write_text(render_markdown(payload), encoding="utf-8")

    # A versioned baseline is created ONLY when the full gate passes.
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
        baseline_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(json_path, baseline_dir / json_path.name)
        shutil.copyfile(md_path, baseline_dir / md_path.name)
        baseline_note = f" baseline={baseline_dir / json_path.name}"

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

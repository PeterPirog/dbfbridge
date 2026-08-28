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
        proc = subprocess.run(
            command,
            cwd=str(REPO_ROOT),
            env=env,
            stdout=log_path.open("wb"),
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
]


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
        cells = [
            _fmt(agg.get(key), "MiB" if key in {"max_peak_rss_bytes", "max_output_bytes"} else "")
            for key, _label in AGG_METRIC_COLUMNS
        ]
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
        "- `temporary_bytes` is `NOT_AVAILABLE`: the library's atomic `.partial` files cannot be "
        "reliably attributed per operation from the outside, so it is never estimated.",
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
    parser.add_argument("--scenario", help="Run a single scenario by name (see --list)")
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

    scenario_names = _scenario_names(args.profile)
    if args.list:
        for name in scenario_names:
            print(name)
        return 0
    if args.scenario and args.scenario not in scenario_names:
        parser.error(f"unknown scenario {args.scenario!r}; use --list")

    work_dir: Path = args.work_dir
    logs_dir = work_dir / "logs"
    names = [args.scenario] if args.scenario else scenario_names

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
    suffix = f"-{args.scenario}" if args.scenario else ""
    json_path = args.results_dir / f"phase-0-{args.profile}{suffix}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path = args.results_dir / f"phase-0-{args.profile}{suffix}.md"
    md_path.write_text(render_markdown(payload), encoding="utf-8")

    baseline_note = ""
    if args.baseline:
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

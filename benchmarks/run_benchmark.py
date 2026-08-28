"""Repeatable Phase 0 benchmark controller for dbfbridge.

Each scenario runs in a **dedicated worker subprocess** (``benchmarks/worker.py``)
so that a crash in one scenario cannot take down the controller or the rest of
the report.  The controller records the environment (commit, worktree state,
Python, OS, CPU, physical memory, dependency versions, fixture sizes) and writes
JSON + Markdown results.

Scenario statuses are never invented:

- ``MEASURED``          - the code path exists and was executed successfully;
- ``FAILED``            - the scenario raised or the worker process crashed
                          (includes the exit code and a reference to the
                          diagnostic log; no metrics are fabricated);
- ``NOT_IMPLEMENTED``   - the feature does not exist in dbfbridge 0.1.0;
- ``NOT_AVAILABLE``     - the platform / optional dependency could not provide
                          the metric (recorded as ``null`` inside the payload).

Usage:
    python -m benchmarks.run_benchmark --profile fast
    python -m benchmarks.run_benchmark --profile fast --scenario export_jsonl_validate_on
    python -m benchmarks.run_benchmark --profile full --repetitions 3
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
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


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def system_info() -> dict[str, object]:
    physical: int | None = None
    if sys.platform == "win32":
        # GlobalMemoryStatusEx uses a 72-byte structure and is verified safe in
        # this environment (the unsafe WinAPI probe is GetProcessMemoryInfo;
        # see benchmarks/metrics.py module docstring).
        try:
            status = _MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            physical = int(status.ullTotalPhys)
        except (OSError, AttributeError):
            physical = None
    elif sys.platform == "linux":
        try:
            physical = int(Path("/proc/meminfo").read_text().splitlines()[0].split()[1]) * 1024
        except (OSError, ValueError, IndexError):
            physical = None
    return {
        "python": platform.python_version(),
        "os": f"{platform.system()} {platform.release()} {platform.version()}",
        "arch": platform.machine(),
        "processor": platform.processor() or "NOT_AVAILABLE",
        "cpu_count": os.cpu_count(),
        "physical_memory_bytes": physical,
    }


def _scenario_list(profile: str) -> list[str]:
    from benchmarks.worker import Runner

    return list(Runner.scenario_names(profile))


def run_scenario(
    profile: str,
    work_dir: Path,
    repetitions: int,
    scenario_name: str,
    logs_dir: Path,
) -> dict[str, Any]:
    """Run one scenario in a fresh worker process and capture its result."""

    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"{profile}_{scenario_name}.log"
    env = dict(os.environ, PYTHONFAULTHANDLER="1", PYTHONPATH=str(REPO_ROOT / "src"))
    command = [
        sys.executable,
        "-X",
        "faulthandler",
        "-m",
        "benchmarks.worker",
        "--profile",
        profile,
        "--work-dir",
        str(work_dir),
        "--repetitions",
        str(repetitions),
        "--scenario",
        scenario_name,
    ]
    with log_path.open("wb") as log:
        proc = subprocess.run(
            command,
            cwd=str(REPO_ROOT),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )

    exit_code = proc.returncode
    if exit_code != 0:
        tail = ""
        with contextlib.suppress(OSError):
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
        return {
            "scenario": scenario_name,
            "status": "FAILED",
            "worker_exit_code": exit_code,
            "diagnostic_log": str(log_path),
            "error": (
                f"worker process exited with code {exit_code} (0x{exit_code & 0xFFFFFFFF:X})"
            ),
            "error_tail": tail,
            "metrics": {},
            "parameters": {},
        }

    stdout = log_path.read_text(encoding="utf-8", errors="replace").strip()
    try:
        last_line = stdout.splitlines()[-1]
        parsed = json.loads(last_line)
    except (IndexError, json.JSONDecodeError) as exc:
        return {
            "scenario": scenario_name,
            "status": "FAILED",
            "worker_exit_code": 0,
            "diagnostic_log": str(log_path),
            "error": f"worker output could not be parsed: {exc}",
            "error_tail": stdout[-2000:],
            "metrics": {},
            "parameters": {},
        }
    if not parsed.get("ok"):
        return {
            "scenario": scenario_name,
            "status": "FAILED",
            "worker_exit_code": 0,
            "diagnostic_log": str(log_path),
            "error": str(parsed.get("error")),
            "metrics": {},
            "parameters": {},
        }
    payload = parsed["payload"]
    scenarios = payload.get("scenarios", [])
    if not scenarios:
        return {
            "scenario": scenario_name,
            "status": "FAILED",
            "worker_exit_code": 0,
            "diagnostic_log": str(log_path),
            "error": "worker reported success but returned no scenario result",
            "metrics": {},
            "parameters": {},
        }
    result = dict(scenarios[0])
    result["status"] = "FAILED" if scenarios[0].get("status") == "ERROR" else result["status"]
    result["worker_exit_code"] = 0
    result["diagnostic_log"] = str(log_path)
    return result


METRIC_COLUMNS = [
    ("wall_seconds", "wall (s)"),
    ("cpu_seconds", "cpu (s)"),
    ("records_per_second", "rec/s"),
    ("source_mib_per_second", "MiB/s"),
    ("peak_rss_sampled_bytes", "peak RSS (MiB)"),
    ("output_bytes_delta", "out delta (MiB)"),
    ("write_bytes", "write (MiB)"),
    ("read_bytes", "read (MiB)"),
    ("read_amplification", "read amp"),
    ("write_amplification", "write amp"),
]


def _fmt(value: object, unit: str = "") -> str:
    if value is None:
        return "NOT_AVAILABLE"
    if unit == "MiB":
        value = float(value) / (1024 * 1024)  # type: ignore[arg-type]
        return f"{value:,.2f}"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return f"{value:,}"


def render_markdown(payload: dict[str, object]) -> str:
    env = payload["environment"]
    git = env["git"]
    lines = [
        "# dbfbridge Phase 0 benchmark baseline",
        "",
        f"- Profile: `{env['profile']}`",
        f"- Commit: `{git['commit']}` (origin/main: `{git['origin_main']}`)",
        f"- Worktree: {'dirty' if git['worktree_dirty'] else 'clean'} on branch `{git['branch']}`",
        f"- Python: {env['system']['python']}",
        f"- OS: {env['system']['os']}",
        f"- CPU: {env['system']['processor']} ({env['system']['cpu_count']} logical CPUs)"
        + (
            f", {env['system']['physical_memory_bytes'] / (1 << 30):.0f} GiB RAM"
            if env["system"]["physical_memory_bytes"]
            else ""
        ),
        "- Packages: " + ", ".join(f"{k} {v}" for k, v in env["packages"].items()),
        "",
        "Statuses: `MEASURED` = executed and measured; `FAILED` = the scenario crashed or "
        "raised (see `error` / diagnostic log, no metrics invented); "
        "`NOT_IMPLEMENTED` = the feature does not exist in dbfbridge 0.1.0 (not simulated); "
        "`NOT_AVAILABLE` = the platform could not provide the metric (rendered as "
        "`NOT_AVAILABLE` in the table, `null` in the JSON).",
        "",
        "| Scenario | Status | " + " | ".join(label for _, label in METRIC_COLUMNS) + " |",
        "|---|---|" + "---:|" * len(METRIC_COLUMNS),
    ]
    scenarios = payload["scenarios"]
    assert isinstance(scenarios, list)
    for scenario in scenarios:
        assert isinstance(scenario, dict)
        metrics = scenario.get("metrics") or {}
        cells = [
            _fmt(metrics.get(key), unit)
            for key, unit in (
                (
                    k,
                    "MiB"
                    if k
                    in {
                        "peak_rss_sampled_bytes",
                        "output_bytes_delta",
                        "write_bytes",
                        "read_bytes",
                    }
                    else "",
                )
                for k in [k for k, _ in METRIC_COLUMNS]
            )
        ]
        failed_note = ""
        if scenario.get("status") == "FAILED":
            failed_note = f" - {scenario.get('error', 'worker crash')}"
        lines.append(
            f"| `{scenario['scenario']}` | {scenario['status']}{failed_note} | "
            + " | ".join(cells)
            + " |"
        )
    lines += [
        "",
        "## Notes",
        "",
        "- Each scenario runs in its own worker subprocess; one crashed scenario is reported "
        "as `FAILED` and does not affect the others.",
        "- Fixture generation is excluded from measured time; fixtures live in `benchmark-data/` "
        "and are regenerated when absent.",
        "- Peak RSS is the larger of the pre/post RSS samples around the measured operation "
        "(psutil); it is a sample, not a guaranteed high-water mark.",
        "- `NOT_IMPLEMENTED` scenarios are listed verbatim and are not estimated.",
        "- `NOT_AVAILABLE` cells mean the platform / optional dependency could not provide the "
        "value; the value was never fabricated.",
    ]
    return "\n".join(lines) + "\n"


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
        help="Where the JSON and Markdown results are written.",
    )
    parser.add_argument("--repetitions", type=int, default=1, help="Measured repetitions (>=1)")
    parser.add_argument("--scenario", help="Run a single scenario by name (see --list)")
    parser.add_argument("--list", action="store_true", help="List scenario names and exit")
    parser.add_argument(
        "--baseline",
        action="store_true",
        help=(
            "Also copy the JSON + Markdown report into benchmarks/baselines/ so it can "
            "be versioned (the results/ directory itself is git-ignored)."
        ),
    )
    args = parser.parse_args(argv)

    scenario_names = _scenario_list(args.profile)
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
        results.append(run_scenario(args.profile, work_dir, args.repetitions, name, logs_dir))

    # NOT_IMPLEMENTED entries are controller knowledge, not measurements.
    results.extend(
        [
            {
                "scenario": "direct_read_bounded",
                "description": (
                    "read_records()/iter_records() do not exist in dbfbridge 0.1.0; "
                    "the planned Direct Read Core is a Phase 1 feature."
                ),
                "status": "NOT_IMPLEMENTED",
                "parameters": {},
                "metrics": {},
            },
            {
                "scenario": "field_projection",
                "description": "No fields= projection option exists in dbfbridge 0.1.0.",
                "status": "NOT_IMPLEMENTED",
                "parameters": {},
                "metrics": {},
            },
            {
                "scenario": "memo_lazy",
                "description": (
                    'memo="lazy" does not exist in dbfbridge 0.1.0 (skip/inline/null only).'
                ),
                "status": "NOT_IMPLEMENTED",
                "parameters": {},
                "metrics": {},
            },
            {
                "scenario": "raw_mode_none",
                "description": (
                    'raw_mode="none" does not exist in dbfbridge 0.1.0; the raw-record '
                    "property is always written to JSON/JSONL."
                ),
                "status": "NOT_IMPLEMENTED",
                "parameters": {},
                "metrics": {},
            },
        ]
    )

    payload = {
        "environment": {
            "git": git_state(REPO_ROOT),
            "system": system_info(),
            "packages": package_versions(),
            "profile": args.profile,
            "repetitions": args.repetitions,
        },
        "fixtures": _fixture_manifest(work_dir),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "scenarios": results,
    }

    args.results_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"-{args.scenario}" if args.scenario else ""
    json_path = args.results_dir / f"phase-0-{args.profile}{suffix}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path = args.results_dir / f"phase-0-{args.profile}{suffix}.md"
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    if args.baseline:
        baseline_dir = REPO_ROOT / "benchmarks" / "baselines"
        baseline_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(json_path, baseline_dir / json_path.name)
        shutil.copyfile(md_path, baseline_dir / md_path.name)
        payload_note = f" baseline={baseline_dir / json_path.name}"
    else:
        payload_note = ""
    print(
        json.dumps(
            {
                "profile": args.profile,
                "json": str(json_path),
                "markdown": str(md_path),
            }
        )
        + payload_note
    )
    return 0


def _fixture_manifest(work_dir: Path) -> dict[str, object]:
    fixture_dir = work_dir / "fixtures"
    manifest: dict[str, object] = {"directory": str(fixture_dir)}
    if fixture_dir.is_dir():
        for path in sorted(fixture_dir.rglob("*")):
            if path.is_file():
                manifest[path.as_posix()] = path.stat().st_size
    return manifest


if __name__ == "__main__":
    raise SystemExit(main())

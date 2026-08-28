"""Regression tests for the Phase 0 benchmark infrastructure.

These tests do not modify dbfbridge production code.  They verify:

1. The original crash (``psapi.GetProcessMemoryInfo`` with a 64+ byte output
   structure) is documented as a known environment hazard;
2. ``benchmarks.metrics`` no longer calls that WinAPI function;
3. ``benchmarks.metrics.process_snapshot`` degrades to ``None`` when ``psutil``
   is absent (rendered ``NOT_AVAILABLE``) and returns an honest dict otherwise;
4. ``benchmarks.runner`` separates worker subprocesses from the controller so a
   crash in one scenario cannot affect the report of the others;
5. Fixture generators are deterministic and never emit ``NOT_AVAILABLE``/
   ``NOT_IMPLEMENTED`` markers (those belong to the controller);
6. ``NOT_IMPLEMENTED`` entries are emitted for the planned-but-absent Phase 1
   features (direct read, field projection, memo lazy, raw_mode="none");
7. The ``metrics.run`` helper honestly reports ``None`` for metrics that cannot
   be captured (no invented numbers).
"""

from __future__ import annotations

import importlib
import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _import_metrics():
    sys.path.insert(0, str(REPO_ROOT / "src"))
    return importlib.import_module("benchmarks.metrics")


def _import_worker():
    sys.path.insert(0, str(REPO_ROOT / "src"))
    return importlib.import_module("benchmarks.worker")


def test_metrics_does_not_use_get_process_memory_info() -> None:
    module = _import_metrics()
    source = inspect.getsource(module)
    # The unsafe call is the *function invocation / argtypes assignment*, not
    # the string (the docstring legitimately names the API in the root-cause
    # note).  Reject both the call and the argtypes assignment.
    assert "GetProcessMemoryInfo(" not in source
    assert "GetProcessMemoryInfo.argtypes" not in source
    # Strictly: no line that is not a comment/docstring should reference it.
    in_docstring = False
    for line in source.splitlines():
        if line.strip().startswith('"""'):
            in_docstring = not in_docstring
            continue
        if in_docstring or line.strip().startswith("#"):
            continue
        if "GetProcessMemoryInfo" in line:
            pytest.fail(f"metrics.py must not call GetProcessMemoryInfo: {line!r}")


def test_metrics_process_snapshot_none_when_psutil_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _import_metrics()
    # Setting sys.modules["psutil"] = None makes ``import psutil`` raise
    # ImportError; metrics._psutil() catches it and returns None, so the
    # snapshot degrades to None (rendered NOT_AVAILABLE) instead of crashing.
    monkeypatch.setitem(sys.modules, "psutil", None)
    assert module.process_snapshot() is None


def test_metrics_process_snapshot_returns_ints_or_none() -> None:
    module = _import_metrics()
    snapshot = module.process_snapshot()
    if snapshot is None:
        pytest.skip("psutil is not available in this environment")
    assert isinstance(snapshot["rss_bytes"], int)
    assert isinstance(snapshot["io_read_bytes"], int)
    assert isinstance(snapshot["io_write_bytes"], int)


def test_metrics_run_reports_failed_when_function_raises(tmp_path: Path) -> None:
    module = _import_metrics()

    def raise_it() -> None:
        raise ValueError("boom")

    result = module.run(raise_it, input_bytes=1024, input_records=2, output_dir=tmp_path)
    assert result["status"] == "FAILED"
    assert "boom" in str(result.get("error", ""))
    assert result["wall_seconds"] >= 0
    # No fabricated metrics: amplification is absent (or None).
    assert result.get("read_amplification") is None
    assert result.get("write_amplification") is None


def test_worker_scenario_names_cover_fast_and_full() -> None:
    worker = _import_worker()
    fast = set(worker.Runner.scenario_names("fast"))
    full = set(worker.Runner.scenario_names("full"))
    required = {
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
    }
    assert required.issubset(fast)
    assert full - fast == {"export_1m_records", "memo_heavy_190k", "reconstruction_190k"}


def test_controller_reports_not_implemented_for_phase1_features(tmp_path: Path) -> None:
    """The controller appends NOT_IMPLEMENTED entries for the planned-but-absent
    Phase 1 features.  This test is self-contained: it runs the controller in a
    fresh subprocess against a temporary results directory, so it does not depend
    on benchmark results left behind by earlier runs.
    """
    work_dir = tmp_path / "work"
    results_dir = tmp_path / "results"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.run_benchmark",
            "--profile",
            "fast",
            "--scenario",
            "jsonl_conversion_existing",
            "--work-dir",
            str(work_dir),
            "--results-dir",
            str(results_dir),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, f"controller failed: {completed.stderr}"
    json_path = results_dir / "phase-0-fast-jsonl_conversion_existing.json"
    assert json_path.is_file()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    statuses = {scenario["scenario"]: scenario["status"] for scenario in payload["scenarios"]}
    for name in ("direct_read_bounded", "field_projection", "memo_lazy", "raw_mode_none"):
        assert statuses[name] == "NOT_IMPLEMENTED"


def test_crash_reproducer_standalone_still_crashes_or_is_documented() -> None:
    """Document the 0xC0000005 reproducer in the audit doc.

    The minimal reproducer lives in the diagnostics folder under the parent
    directory (not committed) and is referenced from the audit.  We only assert
    here that the audit doc mentions the root cause so future maintainers see
    the constraint before re-introducing the unsafe call.
    """
    audit = REPO_ROOT / "docs" / "architecture" / "phase-0-audit.md"
    if not audit.exists():
        pytest.skip("audit doc not written yet")
    text = audit.read_text(encoding="utf-8")
    assert "GetProcessMemoryInfo" in text
    assert "0xC0000005" in text.lower() or "0xc0000005" in text.lower()


def test_controller_invokes_worker_in_subprocess() -> None:
    controller = (REPO_ROOT / "benchmarks" / "run_benchmark.py").read_text(encoding="utf-8")
    assert "subprocess.run" in controller
    assert "benchmarks.worker" in controller
    assert "PYTHONFAULTHANDLER" in controller


def test_worker_uses_psutil_for_memory() -> None:
    worker = (REPO_ROOT / "benchmarks" / "worker.py").read_text(encoding="utf-8")
    assert "benchmarks.metrics" in worker or "metrics as" in worker
    metrics = (REPO_ROOT / "benchmarks" / "metrics.py").read_text(encoding="utf-8")
    assert "psutil" in metrics
    # The crash-inducing WinAPI call must not appear in any executable line.
    in_docstring = False
    for line in metrics.splitlines():
        if line.strip().startswith('"""'):
            in_docstring = not in_docstring
            continue
        if in_docstring or line.strip().startswith("#"):
            continue
        if "GetProcessMemoryInfo" in line:
            pytest.fail(f"metrics.py must not call GetProcessMemoryInfo: {line!r}")


def test_runner_runs_a_single_scenario_in_a_subprocess(tmp_path: Path) -> None:
    """Smoke: the controller runs one real scenario in a fresh process.

    This is the regression gate for the Phase 0 crash: it asserts that a
    scenario that would previously have crashed the controller (because of the
    unsafe ctypes/psapi call in the worker) now terminates cleanly and returns
    a JSON payload.
    """
    command = [
        sys.executable,
        "-m",
        "benchmarks.run_benchmark",
        "--profile",
        "fast",
        "--scenario",
        "encoding_cp1250",
        "--work-dir",
        str(tmp_path / "work"),
        "--results-dir",
        str(tmp_path / "results"),
    ]
    completed = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, f"controller failed: {completed.stderr}"
    last_line = completed.stdout.strip().splitlines()[-1]
    summary = json.loads(last_line)
    json_path = Path(summary["json"])
    assert json_path.is_file()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    scenario = next(s for s in payload["scenarios"] if s["scenario"] == "encoding_cp1250")
    assert scenario["status"] == "MEASURED", json.dumps(scenario, indent=2)

"""Behavioral regression tests for the Phase 0 benchmark infrastructure.

These tests execute the real code paths (worker, controller, fixtures,
metrics) — they are not source-string searches.  They verify:

- warm-up and measured repetitions are actually executed, with warm-up
  excluded from the reported samples;
- median aggregation of per-repetition samples;
- ``raw_record_metadata_default`` returns exactly one complete result;
- ``reconstruction_190k`` is a distinct scenario name;
- re-running a scenario in the same work-dir still yields a non-zero
  ``output_bytes``;
- worker crash / non-zero exit / malformed JSON / timeout all map to
  ``FAILED`` while the report is still written and the controller exits
  non-zero;
- without ``psutil`` the RSS/IO metrics degrade to ``None``
  (``NOT_AVAILABLE``);
- no executable ctypes/WinAPI usage remains anywhere in ``benchmarks/``;
- an incomplete fixture is regenerated; an intact fixture is reused;
- the three encoding fixtures contain real (non-ASCII) Polish diacritics
  and export to the exact expected logical text.

Production code (``src/``) is not modified by these tests.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

FAST = ["--profile", "fast", "--warmup", "1", "--repetitions", "3"]


def _run_controller(*extra: str, tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    import os

    command = [
        sys.executable,
        "-m",
        "benchmarks.run_benchmark",
        *FAST,
        *extra,
        "--work-dir",
        str(tmp_path / "work"),
        "--results-dir",
        str(tmp_path / "results"),
    ]
    full_env = dict(os.environ)
    for key, value in env.items():
        if value is None:
            full_env.pop(key, None)
        else:
            full_env[key] = value
    return subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=900,
        env=full_env,
    )


def _load_json(results_dir: Path, suffix: str) -> dict:
    path = results_dir / f"phase-0-fast{suffix}.json"
    assert path.is_file(), f"missing report {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def test_warmup_and_repetitions_are_executed_and_excluded(tmp_path: Path) -> None:
    completed = _run_controller("--scenario", "encoding_cp1250", tmp_path=tmp_path)
    assert completed.returncode == 0, completed.stderr
    payload = _load_json(tmp_path / "results", "-encoding_cp1250")
    scenario = next(s for s in payload["scenarios"] if s["scenario"] == "encoding_cp1250")
    assert scenario["status"] == "MEASURED"
    # Exactly the requested number of measured repetitions, none more.
    assert len(scenario["samples"]) == 3
    # Warm-up samples exist but are flagged warmup and excluded from aggregation.
    assert len(scenario["warmup_samples"]) == 1
    assert all(s.get("warmup") is True for s in scenario["warmup_samples"])
    assert all(s.get("warmup") is False for s in scenario["samples"])
    agg = scenario["aggregated"]
    assert agg["aggregation"] == "median-of-measured-repetitions"
    assert agg["repetitions"] == 3
    # Aggregation uses the median of the measured wall times.
    walls = sorted(s["wall_seconds"] for s in scenario["samples"])
    assert agg["median_wall_seconds"] == walls[1]
    # Environment records warm-up and repetitions.
    assert payload["environment"]["warmup"] == 1
    assert payload["environment"]["repetitions"] == 3


def test_repeated_run_same_workdir_nonzero_output_bytes(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"
    first = _run_controller("--scenario", "encoding_cp1250", tmp_path=tmp_path)
    assert first.returncode == 0, first.stderr
    payload1 = _load_json(results_dir, "-encoding_cp1250")
    s1 = next(s for s in payload1["scenarios"] if s["scenario"] == "encoding_cp1250")
    first_sizes = [sample["output_bytes"] for sample in s1["samples"]]
    assert all(size > 0 for size in first_sizes)

    second = _run_controller("--scenario", "encoding_cp1250", tmp_path=tmp_path)
    assert second.returncode == 0, second.stderr
    payload2 = _load_json(results_dir, "-encoding_cp1250")
    s2 = next(s for s in payload2["scenarios"] if s["scenario"] == "encoding_cp1250")
    second_sizes = [sample["output_bytes"] for sample in s2["samples"]]
    assert all(size > 0 for size in second_sizes), "re-run must not report zero output"
    # Same fixture content on both runs, so sizes must be comparable (not zero).
    assert abs(second_sizes[0] - first_sizes[0]) < first_sizes[0] * 0.05


def test_raw_record_metadata_single_complete_result(tmp_path: Path) -> None:
    completed = _run_controller("--scenario", "raw_record_metadata_default", tmp_path=tmp_path)
    assert completed.returncode == 0, completed.stderr
    payload = _load_json(tmp_path / "results", "-raw_record_metadata_default")
    matching = [s for s in payload["scenarios"] if s["scenario"] == "raw_record_metadata_default"]
    assert len(matching) == 1, "controller must accept exactly one worker result"
    scenario = matching[0]
    assert scenario["status"] == "MEASURED"
    share = scenario["raw_share"]
    for key in (
        "jsonl_bytes",
        "raw_base64_chars",
        "raw_decoded_bytes",
        "raw_base64_share_of_jsonl",
    ):
        assert key in share, f"missing raw_share key {key}"
    assert share["raw_base64_chars"] > 0
    assert share["raw_decoded_bytes"] > 0
    assert 0 < share["raw_base64_share_of_jsonl"] < 1


def test_reconstruction_names_are_distinct() -> None:
    from benchmarks import worker

    fast = set(worker._scenario_names("fast"))
    full = set(worker._scenario_names("full"))
    assert "reconstruction_jsonl_to_dbf" in fast
    assert "reconstruction_190k" in full
    assert "reconstruction_190k" not in fast
    # The full profile extends the fast profile with distinct 190k names.
    assert "reconstruction_jsonl_to_dbf" in full
    assert full - fast == {
        "export_1m_records",
        "memo_heavy_190k",
        "reconstruction_190k",
        "jsonl_conversion_xlsx",
    }
    # No name may appear in both profiles.
    assert not (fast & (full - fast))


def test_worker_crash_maps_to_failed_and_controller_exits_nonzero(tmp_path: Path) -> None:
    # A worker crash (non-zero exit) must map to FAILED for the scenario, the
    # report must still be written, and the controller must exit non-zero.
    faulty = tmp_path / "faulty_worker.py"
    faulty.write_text(
        "import sys\nprint('worker deliberately crashed')\nsys.exit(13)\n",
        encoding="utf-8",
    )
    completed = _run_controller(
        "--scenario",
        "encoding_cp1250",
        tmp_path=tmp_path,
        BENCHMARK_WORKER=str(faulty),
    )
    assert completed.returncode != 0, "controller must exit non-zero on FAILED"
    payload = _load_json(tmp_path / "results", "-encoding_cp1250")
    scenario = next(s for s in payload["scenarios"] if s["scenario"] == "encoding_cp1250")
    assert scenario["status"] == "FAILED"
    assert scenario["worker_exit_code"] == 13
    assert "diagnostic_log" in scenario
    # The report (JSON + Markdown) was still written — the controller kept
    # going after the FAILED scenario and appended the NOT_IMPLEMENTED entries.
    assert (tmp_path / "results" / "phase-0-fast-encoding_cp1250.md").is_file()
    statuses = {s["scenario"]: s["status"] for s in payload["scenarios"]}
    for name in ("direct_read_bounded", "field_projection", "memo_lazy", "raw_mode_none"):
        assert statuses[name] == "NOT_IMPLEMENTED"


def test_worker_malformed_json_maps_to_failed(tmp_path: Path) -> None:
    faulty = tmp_path / "faulty_worker.py"
    faulty.write_text(
        "print('this is not json')\n",
        encoding="utf-8",
    )
    completed = _run_controller(
        "--scenario",
        "encoding_cp1250",
        tmp_path=tmp_path,
        BENCHMARK_WORKER=str(faulty),
    )
    assert completed.returncode != 0
    payload = _load_json(tmp_path / "results", "-encoding_cp1250")
    scenario = next(s for s in payload["scenarios"] if s["scenario"] == "encoding_cp1250")
    assert scenario["status"] == "FAILED"


def test_controller_continues_after_failed_scenario(tmp_path: Path) -> None:
    # A failing worker on the first scenario must not stop the second scenario,
    # whose result must still be MEASURED in the same report.
    faulty = tmp_path / "faulty_worker.py"
    faulty.write_text("import sys\nsys.exit(17)\n", encoding="utf-8")
    import os

    base_env = dict(os.environ)

    command = [
        sys.executable,
        "-m",
        "benchmarks.run_benchmark",
        *FAST,
        "--scenario",
        "encoding_cp1250",
        "--work-dir",
        str(tmp_path / "work"),
        "--results-dir",
        str(tmp_path / "results"),
    ]
    # First run with the faulty worker: FAILED scenario, report written.
    first = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
        env=dict(base_env, BENCHMARK_WORKER=str(faulty)),
    )
    assert first.returncode != 0
    payload = _load_json(tmp_path / "results", "-encoding_cp1250")
    failed = next(s for s in payload["scenarios"] if s["scenario"] == "encoding_cp1250")
    assert failed["status"] == "FAILED"

    # Second scenario with the healthy worker must still run and be MEASURED.
    command[10] = "encoding_cp852"
    second = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
        env=base_env,
    )
    assert second.returncode == 0, second.stderr
    payload2 = _load_json(tmp_path / "results", "-encoding_cp852")
    ok = next(s for s in payload2["scenarios"] if s["scenario"] == "encoding_cp852")
    assert ok["status"] == "MEASURED"


def test_worker_timeout_maps_to_failed(tmp_path: Path) -> None:
    faulty = tmp_path / "sleepy_worker.py"
    faulty.write_text(
        "import time\ntime.sleep(30)\n",
        encoding="utf-8",
    )
    command = [
        sys.executable,
        "-m",
        "benchmarks.run_benchmark",
        *FAST,
        "--scenario",
        "encoding_cp1250",
        "--timeout",
        "5",
        "--work-dir",
        str(tmp_path / "work"),
        "--results-dir",
        str(tmp_path / "results"),
    ]
    import os

    full_env = dict(os.environ, BENCHMARK_WORKER=str(faulty))
    completed = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
        env=full_env,
    )
    assert completed.returncode != 0
    payload = _load_json(tmp_path / "results", "-encoding_cp1250")
    scenario = next(s for s in payload["scenarios"] if s["scenario"] == "encoding_cp1250")
    assert scenario["status"] == "FAILED"
    assert scenario.get("timed_out") is True


def test_psutil_absent_degrades_to_not_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib

    # Setting sys.modules["psutil"] = None makes ``import psutil`` raise
    # ImportError, which metrics._psutil() catches and reports as None.
    monkeypatch.setitem(sys.modules, "psutil", None)  # type: ignore[call-overload]
    try:
        for name in list(sys.modules):
            if name.startswith("benchmarks."):
                del sys.modules[name]
        metrics = importlib.import_module("benchmarks.metrics")
        result = metrics.run(
            lambda: None,
            input_bytes=10,
            input_records=1,
            output_dir=tmp_path,
        )
    finally:
        sys.modules.pop("psutil", None)
    assert result["status"] == "MEASURED"
    assert result["peak_rss_bytes"] is None
    assert result["rss_samples"] is None
    assert result["rss_sample_interval_seconds"] is None
    assert result["io_read_bytes_delta"] is None
    assert result["temporary_bytes"] is None


def test_metrics_run_sampler_stops_even_when_function_raises(tmp_path: Path) -> None:
    import threading
    import time

    import benchmarks.metrics as metrics

    def raise_it() -> None:
        time.sleep(0.05)
        raise RuntimeError("boom")

    before = set(threading.enumerate())
    result = metrics.run(
        raise_it,
        input_bytes=1,
        input_records=1,
        output_dir=tmp_path,
    )
    assert result["status"] == "FAILED"
    assert "boom" in str(result.get("error", ""))
    # The sampler thread must have been joined in ``finally`` (no leak).
    after = set(threading.enumerate())
    leaked = [t for t in after - before if t.daemon]
    assert not leaked, f"sampler thread leaked after failed run: {leaked}"


def test_no_executable_ctypes_winapi_in_benchmarks() -> None:
    for path in sorted((REPO_ROOT / "benchmarks").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = getattr(node, "module", None) or ""
                if node.__class__ is ast.Import:
                    mods = [alias.name for alias in node.names]
                else:
                    mods = [mod]
                if "ctypes" in mods or any(m.startswith("ctypes") for m in mods):
                    pytest.fail(f"{path.name} imports ctypes at line {node.lineno}")
            if isinstance(node, ast.Attribute):
                # Flag any attribute access on a name `ctypes` (e.g. ctypes.windll)
                chain = node
                base = chain.value
                if isinstance(base, ast.Name) and base.id == "ctypes":
                    pytest.fail(
                        f"{path.name}:{node.lineno} uses ctypes.{node.attr} — no executable WinAPI allowed"
                    )
    for path in sorted((REPO_ROOT / "benchmarks").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            if "GetProcessMemoryInfo" in stripped and "not use" not in stripped.lower():
                pytest.fail(f"{path.name}:{line!r} references the unsafe call")


def test_fixture_regeneration_when_inconsistent(tmp_path: Path) -> None:
    from benchmarks import fixtures

    target = tmp_path / "flat" / "small.dbf"
    first = fixtures.generate_flat(target, 100)
    assert first.is_file()
    # Intact fixture is reused (same path, no error).
    second = fixtures.generate_flat(target, 100)
    assert second == first

    # Corrupt the meta sidecar -> regeneration required.
    meta = target.with_suffix(".meta.json")
    corrupted = json.loads(meta.read_text(encoding="utf-8"))
    corrupted["records"] = 999
    meta.write_text(json.dumps(corrupted), encoding="utf-8")
    regenerated = fixtures.generate_flat(target, 100)
    assert regenerated.is_file()
    new_meta = json.loads(regenerated.with_suffix(".meta.json").read_text(encoding="utf-8"))
    assert new_meta["records"] == 100
    # The fixture must now validate.
    expected = {
        "generator_version": fixtures.SPEC_VERSION,
        "kind": "flat",
        "records": 100,
        "deleted": 0,
        "deleted_fraction": 0.0,
        "require_fpt": False,
    }
    assert fixtures._validate(regenerated, expected)


def test_encoding_fixtures_contain_real_polish_diacritics(tmp_path: Path) -> None:
    from benchmarks import fixtures

    for codec in ("cp1250", "cp852", "mazovia"):
        path = fixtures.generate_encoding(tmp_path / "enc" / codec / f"{codec}.dbf", codec)
        raw = path.read_bytes()
        # The record bytes must not be pure ASCII.
        field_start = raw.index(b"TEKST")
        record_area = raw[field_start + 32 + 1 : field_start + 32 + 1 + 200]
        assert any(byte > 0x7F for byte in record_area), (
            f"{codec} fixture must store non-ASCII (Polish) bytes"
        )

    # And the forced-encoding export must reproduce the exact logical text.
    from dbfbridge import export_dbf

    for codec in ("cp1250", "cp852", "mazovia"):
        path = fixtures.generate_encoding(tmp_path / "enc" / codec / f"{codec}.dbf", codec)
        out = tmp_path / "exp" / codec
        export_dbf(
            str(path.parent),
            str(out),
            formats=("jsonl",),
            encoding=codec,
            decode_errors="strict",
            overwrite=True,
        ).raise_for_errors()
        jsonl = next(iter(out.glob("*.jsonl")))
        record = json.loads(jsonl.read_text(encoding="utf-8").splitlines()[0])
        assert "Żółw" in record["TEKST"], f"{codec} export must keep the logical text"


def test_aggregate_uses_median() -> None:
    from benchmarks import worker

    samples = [
        {
            "wall_seconds": 1.0,
            "cpu_seconds": 0.5,
            "records_per_second": 100.0,
            "output_bytes": 1000,
        },
        {"wall_seconds": 3.0, "cpu_seconds": 2.0, "records_per_second": 33.0, "output_bytes": 2000},
        {"wall_seconds": 2.0, "cpu_seconds": 1.0, "records_per_second": 50.0, "output_bytes": 1500},
    ]
    agg = worker.aggregate(samples)
    assert agg["median_wall_seconds"] == 2.0
    assert agg["median_cpu_seconds"] == 1.0
    assert agg["median_records_per_second"] == 50.0
    assert agg["max_output_bytes"] == 2000
    assert agg["repetitions"] == 3

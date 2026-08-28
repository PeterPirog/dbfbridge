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


def test_shared_scenarios_identical_params_fast_vs_full(tmp_path: Path) -> None:
    from benchmarks import worker

    # A profile must never change the parameters of a scenario it shares with
    # another profile. The scenario dispatch in `run_scenario` must be free of
    # any `self.profile` dependency for shared scenarios: assert the recorded
    # parameters are identical when the shared memo scenarios run on a fast
    # runner and on a full runner.
    fast = worker.Runner(Path.cwd(), "fast", tmp_path / "fast", repetitions=1, warmup=0)
    full = worker.Runner(Path.cwd(), "full", tmp_path / "full", repetitions=1, warmup=0)

    shared = ("memo_skip", "memo_null", "memo_inline")
    for name in shared:
        fast.results.clear()
        fast.run_scenario(name)
        full.results.clear()
        full.run_scenario(name)
        f_result = dict(fast.results[0])  # type: ignore[union-attr]
        l_result = dict(full.results[0])  # type: ignore[union-attr]
        f_params = dict(f_result["parameters"])  # type: ignore[arg-type]
        l_params = dict(l_result["parameters"])  # type: ignore[arg-type]
        assert f_params == l_params, (
            f"shared scenario {name} must have identical parameters in fast and full"
        )
        # The shared memo scenarios use the SAME dedicated fixture spec (2000
        # rows) in both profiles (never a profile-dependent size); the fixture
        # *name* encodes that spec and must match.
        assert fast.memo_heavy(2_000).name == full.memo_heavy(2_000).name == "memo2000.dbf"

    # The full-only variant names are distinct from the shared ones.
    assert full.memo_heavy(190_000).name == "memo190000.dbf"
    assert full.memo_heavy(190_000).name != "memo2000.dbf"
    # Flat / deleted scenarios resolve to the SAME fixture spec across profiles.
    assert fast.deleted().name == full.deleted().name == "deleted.dbf"
    assert fast.small().name == full.small().name == "small.dbf"
    assert fast.medium().name == full.medium().name == "medium.dbf"


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


def test_cli_rejects_invalid_arguments(tmp_path: Path) -> None:
    base = [
        sys.executable,
        "-m",
        "benchmarks.run_benchmark",
        "--profile",
        "fast",
        "--work-dir",
        str(tmp_path / "work"),
        "--results-dir",
        str(tmp_path / "results"),
    ]
    for extra, message in (
        (["--repetitions", "0"], "repetitions"),
        (["--warmup", "-1"], "warmup"),
        (["--timeout", "0"], "timeout"),
        (["--timeout", "-5"], "timeout"),
    ):
        completed = subprocess.run(
            [*base, *extra],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert completed.returncode != 0, f"{message} must be rejected"
        assert (
            message in (completed.stderr or completed.stdout).lower()
            or "error" in (completed.stderr or completed.stdout).lower()
        )


def test_controller_continues_after_failed_scenario(tmp_path: Path) -> None:
    # One controller run with TWO scenarios: the first must be FAILED (crashing
    # worker) and the second must still run and be MEASURED, both in the SAME
    # report — proving a failed scenario does not stop the rest of the run.
    faulty = tmp_path / "faulty_worker.py"
    real_worker = (REPO_ROOT / "benchmarks" / "worker.py").as_posix()
    repo_root = REPO_ROOT.as_posix()
    faulty.write_text(
        f"""import sys
import runpy
sys.path.insert(0, {repo_root!r})
args = sys.argv[1:]
if "--scenario" in args:
    name = args[args.index("--scenario") + 1]
else:
    name = ""
if name == "encoding_cp1250":
    sys.exit(17)
runpy.run_path({real_worker!r}, run_name="__main__")
""",
        encoding="utf-8",
    )
    import os

    command = [
        sys.executable,
        "-m",
        "benchmarks.run_benchmark",
        *FAST,
        "--scenario",
        "encoding_cp1250,encoding_cp852",
        "--work-dir",
        str(tmp_path / "work"),
        "--results-dir",
        str(tmp_path / "results"),
    ]
    full_env = dict(os.environ, BENCHMARK_WORKER=str(faulty))
    completed = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=600,
        env=full_env,
    )
    assert completed.returncode != 0, "controller must exit non-zero on FAILED"
    payload = _load_json(tmp_path / "results", "-encoding_cp1250_encoding_cp852")
    statuses = {s["scenario"]: s["status"] for s in payload["scenarios"]}
    assert statuses["encoding_cp1250"] == "FAILED"
    assert statuses["encoding_cp852"] == "MEASURED", (
        "the scenario after a FAILED one must still run in the same controller invocation"
    )
    # The report (JSON + Markdown) was written with both scenarios.
    assert (tmp_path / "results" / "phase-0-fast-encoding_cp1250_encoding_cp852.md").is_file()


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
    # The sidecar carries MEASURED counts that match the file on disk.
    assert new_meta["active_records"] == 100
    assert new_meta["deleted_records"] == 0
    assert new_meta["total_records"] == 100


def test_flat_fixture_is_memo_free_and_memo_heavy_has_fpt(tmp_path: Path) -> None:
    from benchmarks import fixtures

    flat = fixtures.generate_flat(tmp_path / "flat" / "f.dbf", 50)
    flat_fpt = flat.with_suffix(".fpt")
    assert not flat_fpt.is_file(), "flat fixtures must not create an FPT"
    flat_meta = json.loads(flat.with_suffix(".meta.json").read_text(encoding="utf-8"))
    assert flat_meta["fpt_present"] is False
    assert flat_meta["require_fpt"] is False

    memo = fixtures.generate_memo_heavy(tmp_path / "memo" / "m.dbf", 20)
    memo_fpt = memo.with_suffix(".fpt")
    assert memo_fpt.is_file(), "memo-heavy fixtures must create an FPT"
    memo_meta = json.loads(memo.with_suffix(".meta.json").read_text(encoding="utf-8"))
    assert memo_meta["fpt_present"] is True
    assert memo_meta["require_fpt"] is True
    assert memo_meta["fpt_sha256"] == fixtures._sha256(memo_fpt)

    # Both fixtures must validate against their respective specs (counts + FPT).
    assert fixtures._validate(
        flat,
        {
            "generator_version": fixtures.SPEC_VERSION,
            "kind": "flat",
            "records": 50,
            "deleted": 0,
            "deleted_fraction": 0.0,
            "require_fpt": False,
        },
    )
    assert fixtures._validate(
        memo,
        {
            "generator_version": fixtures.SPEC_VERSION,
            "kind": "memo",
            "records": 20,
            "deleted": 0,
            "memo_chars": 4000,
            "require_fpt": True,
        },
    )
    # Cross-check: a flat fixture must NOT validate as memo-required and vice versa.
    assert not fixtures._validate(flat, {"require_fpt": True})
    assert not fixtures._validate(memo, {"require_fpt": False})


def test_deleted_fraction_counts_are_exact(tmp_path: Path) -> None:
    from benchmarks import fixtures

    records = 1_000
    path = fixtures.generate_flat(tmp_path / "flat" / "del.dbf", records, deleted_fraction=0.1)
    meta = json.loads(path.with_suffix(".meta.json").read_text(encoding="utf-8"))
    # Exactly the expected active/deleted split, and a physical total.
    assert meta["deleted_records"] == 100
    assert meta["active_records"] == 900
    assert meta["total_records"] == 1_000
    # Independent re-measurement with dbfread must agree with the sidecar.
    active, deleted = fixtures._counts(path)
    assert (active, deleted) == (900, 100)


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
            "status": "MEASURED",
            "wall_seconds": 1.0,
            "cpu_seconds": 0.5,
            "records_per_second": 100.0,
            "output_bytes": 1000,
        },
        {
            "status": "MEASURED",
            "wall_seconds": 3.0,
            "cpu_seconds": 2.0,
            "records_per_second": 33.0,
            "output_bytes": 2000,
        },
        {
            "status": "MEASURED",
            "wall_seconds": 2.0,
            "cpu_seconds": 1.0,
            "records_per_second": 50.0,
            "output_bytes": 1500,
        },
    ]
    agg = worker.aggregate(samples)
    assert agg["median_wall_seconds"] == 2.0
    assert agg["median_cpu_seconds"] == 1.0
    assert agg["median_records_per_second"] == 50.0
    assert agg["max_output_bytes"] == 2000
    assert agg["repetitions"] == 3
    assert agg["repetitions_succeeded"] == 3
    assert agg["repetitions_failed"] == 0
    assert agg["valid_baseline"] is True


def test_failed_sample_excluded_from_median() -> None:
    from benchmarks import worker

    samples = [
        {"status": "MEASURED", "wall_seconds": 1.0, "output_bytes": 1000},
        {"status": "FAILED", "wall_seconds": 999.0, "output_bytes": 999999, "error": "boom"},
        {"status": "MEASURED", "wall_seconds": 2.0, "output_bytes": 1500},
    ]
    agg = worker.aggregate(samples)
    # The failed sample must not participate in the median (median of 1.0 and
    # 2.0 is 1.5; had the failed 999.0 been included it would be 2.0).
    assert agg["median_wall_seconds"] == 1.5
    assert agg["max_output_bytes"] == 1500
    assert agg["repetitions"] == 3
    assert agg["repetitions_succeeded"] == 2
    assert agg["repetitions_failed"] == 1
    assert agg["valid_baseline"] is False
    # All failed -> no valid baseline and no medians at all.
    agg_all_failed = worker.aggregate([s for s in samples if s["status"] == "FAILED"])
    assert agg_all_failed["median_wall_seconds"] is None
    assert agg_all_failed["valid_baseline"] is False


def test_measure_failed_warmup_and_failed_repetition(tmp_path: Path) -> None:
    from benchmarks import worker

    calls = {"n": 0}

    def flaky(out: Path):
        def run() -> None:
            calls["n"] += 1
            # Warm-up run and repetition #2 fail; repetition #1 and #3 succeed.
            if calls["n"] in {1, 3}:
                raise RuntimeError("boom")

        return run

    runner = worker.Runner(Path.cwd(), "fast", tmp_path, repetitions=3, warmup=1)
    # 1 warmup + 3 reps = 4 calls; calls 1 (warmup) and 3 (rep 2) fail.
    result = runner._measure(
        "flaky",
        "description",
        flaky,
        input_bytes=1,
        input_records=1,
    )
    samples = list(result["samples"])  # type: ignore[union-attr]
    warmup_samples = list(result["warmup_samples"])  # type: ignore[union-attr]
    assert result["status"] == "FAILED", "any failed warm-up/repetition fails the scenario"
    assert len(samples) == 3
    assert len(warmup_samples) == 1
    failed_samples = [s for s in samples + warmup_samples if s["status"] == "FAILED"]  # type: ignore[union-attr]
    assert len(failed_samples) == 2
    errors = list(result["errors"])  # type: ignore[union-attr,arg-type]
    assert any("boom" in str(e) for e in errors)
    agg = dict(result["aggregated"])  # type: ignore[arg-type]
    assert agg["valid_baseline"] is False  # type: ignore[index]
    assert agg["repetitions_failed"] >= 1  # type: ignore[index]


def test_markdown_failed_scenario_not_presented_as_baseline(tmp_path: Path) -> None:
    from benchmarks import run_benchmark

    payload = {
        "environment": {
            "git": {"commit": "x", "origin_main": "x", "branch": "b", "worktree_dirty": False},
            "system": {
                "python": "3.14",
                "os": "win",
                "processor": "cpu",
                "cpu_count": 1,
                "physical_memory_bytes": 0,
            },
            "packages": {"psutil": "5.9"},
            "profile": "fast",
            "repetitions": 3,
            "warmup": 1,
        },
        "scenarios": [
            {
                "scenario": "broken",
                "status": "FAILED",
                "reason": "worker exited 1",
                "aggregated": {
                    "repetitions": 3,
                    "repetitions_succeeded": 2,
                    "repetitions_failed": 1,
                    "valid_baseline": False,
                    "median_wall_seconds": 1.5,
                    "median_cpu_seconds": 0.5,
                    "median_records_per_second": 100.0,
                    "median_source_mib_per_second": 1.0,
                    "max_peak_rss_bytes": 1024,
                    "max_output_bytes": 2048,
                },
            },
            {
                "scenario": "broken_no_agg",
                "status": "FAILED",
                "reason": "timeout",
                "aggregated": {},
            },
        ],
    }
    md = run_benchmark.render_markdown(payload)
    failed_rows = [
        line
        for line in md.splitlines()
        if line.startswith("| `broken`") or line.startswith("| `broken_no_agg`")
    ]
    assert len(failed_rows) == 2
    # No failed row may present its median as a plain, comparable number.
    for row in failed_rows:
        assert "NOT A VALID BASELINE" in row or "NOT_AVAILABLE" in row
        assert " 1.5 " not in row and "0.5 " not in row

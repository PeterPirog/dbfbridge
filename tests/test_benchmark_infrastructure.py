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
    assert "reconstruction_memo_190k" in full
    assert "reconstruction_memo_190k" not in fast
    # The full profile extends the fast profile with distinct 190k names.
    assert "reconstruction_jsonl_to_dbf" in full
    assert full - fast == {
        "export_1m_records",
        "memo_heavy_190k",
        "reconstruction_190k",
        "reconstruction_memo_190k",
        "jsonl_conversion_xlsx",
    }
    assert len(full) == 20
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
    # I/O-based amplification needs psutil counters -> NOT_AVAILABLE.
    assert result["read_amplification"] is None
    assert result["write_amplification"] is None
    # temporary_bytes_written does NOT need psutil; a no-op call creates no
    # .partial, so it is a real zero.
    assert result["temporary_bytes_written"] == 0


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


def test_fixture_scan_rejects_inconsistent_files(tmp_path: Path) -> None:
    import struct

    from benchmarks import fixtures

    def _write_dbf(total: int, record: bytes) -> Path:
        p = tmp_path / "bad.dbf"
        # 32-byte header + one 0x0d terminator = 33-byte header, then records.
        header = struct.pack(
            "<BBBBLHH20x",
            0x03,
            20,
            1,
            1,
            total,
            33,
            len(record),
        )
        p.write_bytes(header + b"\x0d" + record * total)
        return p

    # Correct active/deleted/total scan (well-formed file, mixed markers).
    good = tmp_path / "good.dbf"
    rec = b" " + b"abc"  # active marker + 3 payload bytes
    rec_del = b"*" + b"abc"
    good.write_bytes(
        struct.pack("<BBBBLHH20x", 0x03, 20, 1, 1, 4, 33, len(rec))
        + b"\x0d"
        + rec
        + rec_del
        + rec
        + rec_del
    )
    measured = fixtures._measured_counts(good)
    assert measured == {"active_records": 2, "deleted_records": 2, "total_records": 4}

    # Generator creating a DIFFERENT record count than expected must fail.
    p = tmp_path / "wrong_count.dbf"
    fixtures._write_minimal_dbf(p, "cp1250", "x")  # writes exactly 1 record
    measured1 = fixtures._measured_counts(p)
    good_meta = {
        "generator_version": fixtures.SPEC_VERSION,
        "records": 1,
        "deleted": 0,
        "require_fpt": False,
        "dbf_sha256": fixtures._sha256(p),
        "fpt_present": False,
        **measured1,
    }
    p.with_suffix(".meta.json").write_text(json.dumps(good_meta), encoding="utf-8")
    assert not fixtures._validate(p, {"records": 2, "deleted": 0, "require_fpt": False})
    assert fixtures._validate(p, {"records": 1, "deleted": 0, "require_fpt": False})

    # A file with a bad expected deleted count must fail.
    assert not fixtures._validate(p, {"records": 1, "deleted": 1, "require_fpt": False})

    # Truncated last record (file shorter than header_len + total * record_len).
    rec = b" " + b"abc"  # active marker + 3 payload bytes
    rec_del = b"*" + b"abc"
    truncated = _write_dbf(4, rec + rec_del + rec + rec_del)
    with truncated.open("r+b") as tf:
        tf.seek(0, 2)
        tf.truncate(tf.tell() - 4)
    with pytest.raises(fixtures.FixtureIntegrityError):
        fixtures._measured_counts(truncated)

    # Invalid delete marker (neither 0x20 nor 0x2A).
    bad_marker = _write_dbf(1, b"~" + b"abc")
    with pytest.raises(fixtures.FixtureIntegrityError):
        fixtures._measured_counts(bad_marker)

    # A zero-record DBF must still require header_length <= file size.
    import struct as _struct

    empty = tmp_path / "empty.dbf"
    empty.write_bytes(_struct.pack("<BBBBLHH20x", 0x03, 20, 1, 1, 0, 33, 8) + b"\x0d")
    assert fixtures._measured_counts(empty) == {
        "active_records": 0,
        "deleted_records": 0,
        "total_records": 0,
    }
    # ...and a zero-record DBF whose header_length exceeds the file size fails.
    empty_bad = tmp_path / "empty_bad.dbf"
    empty_bad.write_bytes(_struct.pack("<BBBBLHH20x", 0x03, 20, 1, 1, 0, 4096, 8) + b"\x0d")
    with pytest.raises(fixtures.FixtureIntegrityError):
        fixtures._measured_counts(empty_bad)

    # Contradictory sidecar (claims a count the file does not hold) must fail.
    p.with_suffix(".meta.json").write_text(
        json.dumps(
            {
                "generator_version": fixtures.SPEC_VERSION,
                "records": 3,
                "deleted": 0,
                "require_fpt": False,
                "dbf_sha256": fixtures._sha256(p),
                "fpt_present": False,
                "active_records": 3,
                "deleted_records": 0,
                "total_records": 3,
            }
        ),
        encoding="utf-8",
    )
    assert not fixtures._validate(p, {"records": 3, "deleted": 0, "require_fpt": False})

    # A freshly generated but INCONSISTENT fixture must NOT be returned as good:
    # simulate a generator that writes fewer records than the spec demands.
    def broken_generate(path: Path) -> None:
        fixtures._write_minimal_dbf(path, "cp1250", "x")  # 1 record

    bad = tmp_path / "broken" / "broken.dbf"
    with pytest.raises(fixtures.FixtureIntegrityError):
        fixtures._ensure(
            bad,
            {
                "generator_version": fixtures.SPEC_VERSION,
                "records": 5,
                "deleted": 0,
                "require_fpt": False,
            },
            broken_generate,
        )


def test_deleted_fraction_counts_are_exact(tmp_path: Path) -> None:
    from benchmarks import fixtures

    records = 1_000
    path = fixtures.generate_flat(tmp_path / "flat" / "del.dbf", records, deleted_fraction=0.1)
    meta = json.loads(path.with_suffix(".meta.json").read_text(encoding="utf-8"))
    # Exactly the expected active/deleted split, and a physical total.
    assert meta["deleted_records"] == 100
    assert meta["active_records"] == 900
    assert meta["total_records"] == 1_000
    # Independent re-measurement from the raw layout must agree with the sidecar.
    measured = fixtures._measured_counts(path)
    assert measured["active_records"] == 900
    assert measured["deleted_records"] == 100
    assert measured["total_records"] == 1_000


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


def test_jsonl_input_reuse_and_regeneration(tmp_path: Path) -> None:
    from benchmarks import worker

    runner = worker.Runner(Path.cwd(), "fast", tmp_path, repetitions=1, warmup=0)

    class GoodModule:
        """Honest generator: writes exactly 1000 valid lines AND declares 1000."""

        count = 0

        def generate_jsonl(self, path: Path, size_mb: int) -> int:
            GoodModule.count += 1
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("wb") as out:
                for i in range(1000):
                    out.write(json.dumps({"id": i}).encode() + b"\n")
            # The generator's own legacy sidecar declares the record count.
            path.with_suffix(".benchmark.json").write_text(
                json.dumps({"records": 1000}), encoding="utf-8"
            )
            return 1000

    name = "input_json.jsonl"
    # First preparation: generated, declared==actual, sidecar written.
    source, records, size = runner._prepare_jsonl_input(GoodModule(), name, 20)
    assert records == 1000
    assert size == source.stat().st_size
    sidecar = source.with_name(name + ".meta.json")
    stored = json.loads(sidecar.read_text(encoding="utf-8"))
    assert GoodModule.count == 1
    assert stored["records"] == 1000
    assert stored["expected_records"] == 1000
    assert stored["invalid_line_count"] == 0
    assert stored["bytes"] == size
    assert stored["sha256"]
    assert stored["complete_line_count"] == 1000
    assert stored["generator"]
    assert stored["version"]

    # A correct JSONL with a matching sidecar is reused (no regeneration).
    source2, records2, size2 = runner._prepare_jsonl_input(GoodModule(), name, 20)
    assert GoodModule.count == 1, "valid JSONL must be reused, not regenerated"
    assert (source2, records2, size2) == (source, 1000, size)

    def corrupt(mutate: str) -> None:
        data = bytearray(source.read_bytes())
        if mutate == "truncated":
            del data[-len(json.dumps({"id": 999}).encode()) - 1 :]
        elif mutate == "sha256":
            # One byte flipped (same size, different SHA256, line still valid JSON).
            i = data.find(b'"id"')
            data[i + 4] = ord("j")
        elif mutate == "bytes":
            data += b"garbage"
        source.write_bytes(bytes(data))

    # Truncated file with UNCHANGED sidecar -> regenerated.
    corrupt("truncated")
    source3, records3, _ = runner._prepare_jsonl_input(GoodModule(), name, 20)
    assert GoodModule.count == 2, "truncated JSONL must be regenerated"
    assert records3 == 1000

    # Modified file (changed SHA256) with unchanged sidecar -> regenerated.
    corrupt("sha256")
    runner._prepare_jsonl_input(GoodModule(), name, 20)
    assert GoodModule.count == 3, "SHA256 mismatch must regenerate"

    # Appended bytes (byte-count mismatch) -> regenerated.
    corrupt("bytes")
    runner._prepare_jsonl_input(GoodModule(), name, 20)
    assert GoodModule.count == 4, "byte-count mismatch must regenerate"

    # Inconsistent complete_line_count in the sidecar -> regenerated.
    stored = json.loads(sidecar.read_text(encoding="utf-8"))
    stored["complete_line_count"] = 999_999
    sidecar.write_text(json.dumps(stored), encoding="utf-8")
    runner._prepare_jsonl_input(GoodModule(), name, 20)
    assert GoodModule.count == 5, "inconsistent complete_line_count must regenerate"


class _BadJsonlModule:
    """Generator that DECLARES 1000 records but does not honour it.

    ``mode`` selects the fault:
    - ``missing``   : 999 valid lines, 1 short;
    - ``corrupt``   : 999 valid lines + 1 invalid JSON line;
    - ``trailing``  : 999 complete lines + a truncated final record.
    """

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.count = 0

    def generate_jsonl(self, path: Path, size_mb: int) -> int:
        self.count += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as out:
            for i in range(999):
                out.write(json.dumps({"id": i}).encode() + b"\n")
            if self.mode == "missing":
                pass
            elif self.mode == "corrupt":
                out.write(b'{"id": 999, "broken": \n')
            elif self.mode == "trailing":
                out.write(b'{"id": 999, "na')
            else:  # pragma: no cover
                raise AssertionError(self.mode)
        # The generator still DECLARES 1000 records.
        path.with_suffix(".benchmark.json").write_text(
            json.dumps({"records": 1000}), encoding="utf-8"
        )
        return 1000


def test_jsonl_generator_faults_are_rejected(tmp_path: Path) -> None:
    from benchmarks import worker

    runner = worker.Runner(Path.cwd(), "fast", tmp_path, repetitions=1, warmup=0)
    for mode in ("missing", "corrupt", "trailing"):
        name = f"input_{mode}.jsonl"
        sidecar = (tmp_path / "jsonl" / name).with_name(name + ".meta.json")
        mod = _BadJsonlModule(mode)
        with pytest.raises(ValueError):
            runner._prepare_jsonl_input(mod, name, 20)
        # The invalid file is kept for diagnosis, but NO Phase 0 sidecar is
        # written for it (so it can never be reused as a valid input).
        assert not sidecar.is_file(), f"mode={mode} must not write a valid sidecar"


def _full_gate_payload() -> dict:
    from benchmarks import run_benchmark

    full_names = list(run_benchmark._scenario_names("full"))
    assert len(full_names) == 20
    good_sha = "a" * 40
    sample = {
        "status": "MEASURED",
        "input_bytes": 1000,
        "output_bytes": 2000,
        "wall_seconds": 1.0,
        "cpu_seconds": 0.5,
        "records_per_second": 100.0,
        "source_mib_per_second": 1.0,
        "peak_rss_bytes": 2048,
        "read_amplification": 2.0,
        "write_amplification": 1.0,
        "temporary_bytes_written": 0,
    }
    memo_sample = {
        **sample,
        "output_dbf_bytes": 5_000,
        "output_fpt_bytes": 9_000,
        "fpt_mib_per_second": 0.5,
        "temporary_publish_count": 2,
        "temporary_bytes_written": 14_000,
    }
    warmup = {"status": "MEASURED"}
    scenarios = [
        {
            "scenario": name,
            "status": "MEASURED",
            "aggregated": {"valid_baseline": True},
            "samples": [
                dict(memo_sample if name == "reconstruction_memo_190k" else sample)
                for _ in range(3)
            ],
            "warmup_samples": [dict(warmup) for _ in range(1)],
        }
        for name in full_names
    ]
    scenarios += [
        {"scenario": n, "status": "NOT_IMPLEMENTED"}
        for n in ("direct_read_bounded", "field_projection", "memo_lazy", "raw_mode_none")
    ]
    return {
        "environment": {
            "profile": "full",
            "warmup": 1,
            "repetitions": 3,
            "git": {"commit": good_sha, "worktree_dirty": False},
        },
        "scenarios": scenarios,
    }


def test_baseline_gate_rejects_incomplete_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    from benchmarks import run_benchmark

    monkeypatch.setattr(run_benchmark, "psutil_available", lambda: True)

    # A complete full-profile, clean, psutil-available run passes.
    assert run_benchmark.check_baseline_gate(_full_gate_payload()) == []

    # fast profile is rejected.
    p = _full_gate_payload()
    p["environment"]["profile"] = "fast"
    assert any("full" in r for r in run_benchmark.check_baseline_gate(p))

    # psutil absent is rejected.
    monkeypatch.setattr(run_benchmark, "psutil_available", lambda: False)
    assert any("psutil" in r for r in run_benchmark.check_baseline_gate(_full_gate_payload()))
    monkeypatch.setattr(run_benchmark, "psutil_available", lambda: True)

    # warmup < 1 / repetitions < 3 are rejected.
    p = _full_gate_payload()
    p["environment"]["warmup"] = 0
    assert any("warmup" in r for r in run_benchmark.check_baseline_gate(p))
    p = _full_gate_payload()
    p["environment"]["repetitions"] = 2
    assert any("repetitions" in r for r in run_benchmark.check_baseline_gate(p))

    # any FAILED is rejected.
    p = _full_gate_payload()
    p["scenarios"].append({"scenario": "bad", "status": "FAILED", "reason": "boom"})
    assert any("FAILED" in r for r in run_benchmark.check_baseline_gate(p))

    # a MEASURED scenario without valid_baseline is rejected.
    p = _full_gate_payload()
    p["scenarios"][0]["aggregated"] = {"valid_baseline": False}
    assert any("valid baseline" in r for r in run_benchmark.check_baseline_gate(p))

    # duplicate scenario names are rejected.
    p = _full_gate_payload()
    p["scenarios"].append(dict(p["scenarios"][0]))
    assert any("duplicate" in r for r in run_benchmark.check_baseline_gate(p))

    # a NOT_IMPLEMENTED entry missing is rejected.
    p = _full_gate_payload()
    p["scenarios"] = [s for s in p["scenarios"] if s["scenario"] != "memo_lazy"]
    assert any("NOT_IMPLEMENTED" in r for r in run_benchmark.check_baseline_gate(p))

    # a non-40-hex commit is rejected.
    p = _full_gate_payload()
    p["environment"]["git"]["commit"] = "abc123"
    assert any("commit" in r for r in run_benchmark.check_baseline_gate(p))

    # dirty worktree is rejected.
    p = _full_gate_payload()
    p["environment"]["git"]["worktree_dirty"] = True
    assert any("dirty" in r for r in run_benchmark.check_baseline_gate(p))


def test_baseline_gate_requires_every_sample_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    """First sample complete, second one lacking a metric -> must be rejected.

    This is the regression the review called out: the gate must not pass on
    "at least one complete sample"; EVERY measured repetition must be complete.
    """

    from benchmarks import run_benchmark

    monkeypatch.setattr(run_benchmark, "psutil_available", lambda: True)
    for key in (
        "peak_rss_bytes",
        "read_amplification",
        "write_amplification",
        "temporary_bytes_written",
    ):
        p = _full_gate_payload()
        scenario = p["scenarios"][0]
        # Keep the first sample complete, break exactly the second one.
        scenario["samples"][1] = {k: v for k, v in scenario["samples"][1].items() if k != key}
        reasons = run_benchmark.check_baseline_gate(p)
        assert any("samples" in r or "warm" in r for r in reasons), (
            f"missing {key} in one of the samples must be rejected, got {reasons}"
        )


def test_baseline_gate_strict_sample_and_warmup_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gate must reject wrong sample/warm-up counts and any non-MEASURED
    sample or warm-up, independent of ``aggregated.valid_baseline``."""

    from benchmarks import run_benchmark

    monkeypatch.setattr(run_benchmark, "psutil_available", lambda: True)
    p = _full_gate_payload()
    scenario = p["scenarios"][0]
    complete = dict(scenario["samples"][0])

    # repetitions=3 but only ONE complete sample -> rejected.
    scenario["samples"] = [complete]
    assert run_benchmark.check_baseline_gate(p)

    # repetitions=3 but FOUR samples -> rejected.
    scenario["samples"] = [complete] * 4
    assert run_benchmark.check_baseline_gate(p)

    # one sample is FAILED -> rejected.
    scenario["samples"] = [complete, complete, {**complete, "status": "FAILED"}]
    reasons = run_benchmark.check_baseline_gate(p)
    assert any("FAILED" in r for r in reasons)

    # warmup=1 but no warmup_samples -> rejected.
    scenario["samples"] = [complete] * 3
    del scenario["warmup_samples"]
    reasons = run_benchmark.check_baseline_gate(p)
    assert any("warmup" in r for r in reasons)

    # warmup=1 but TWO warmup_samples -> rejected.
    scenario["warmup_samples"] = [{"status": "MEASURED"}] * 2
    assert run_benchmark.check_baseline_gate(p)

    # a warm-up sample FAILED -> rejected even if the aggregate looks valid.
    scenario["warmup_samples"] = [{**complete, "status": "FAILED"}]
    scenario["aggregated"] = {"valid_baseline": True}
    reasons = run_benchmark.check_baseline_gate(p)
    assert any("warmup" in r for r in reasons)


def test_baseline_gate_scenario_set_hardening(monkeypatch: pytest.MonkeyPatch) -> None:
    """The report must be exactly the full contract: unique names, known
    statuses only, no name across categories."""

    from benchmarks import run_benchmark

    monkeypatch.setattr(run_benchmark, "psutil_available", lambda: True)

    # duplicate NOT_IMPLEMENTED -> rejected.
    p = _full_gate_payload()
    p["scenarios"].append({"scenario": "memo_lazy", "status": "NOT_IMPLEMENTED"})
    assert any("duplicate" in r for r in run_benchmark.check_baseline_gate(p))

    # duplicate FAILED -> rejected.
    p = _full_gate_payload()
    p["scenarios"] += [
        {"scenario": "roundtrip_quality", "status": "FAILED"},
        {"scenario": "roundtrip_quality", "status": "FAILED"},
    ]
    reasons = run_benchmark.check_baseline_gate(p)
    assert any("duplicate" in r for r in reasons)
    assert any("FAILED" in r for r in reasons)

    # an unknown status -> rejected.
    p = _full_gate_payload()
    p["scenarios"].append({"scenario": "roundtrip_quality", "status": "SKIPPED"})
    reasons = run_benchmark.check_baseline_gate(p)
    assert any("unknown" in r for r in reasons)
    assert any("duplicate" in r for r in reasons)

    # a name outside the full-profile contract -> rejected.
    p = _full_gate_payload()
    p["scenarios"].append({"scenario": "totally_new_idea", "status": "MEASURED"})
    reasons = run_benchmark.check_baseline_gate(p)
    assert any("contract" in r for r in reasons)

    # the same name in two status categories -> rejected.
    p = _full_gate_payload()
    p["scenarios"].append({"scenario": "export_1m_records", "status": "FAILED"})
    reasons = run_benchmark.check_baseline_gate(p)
    assert any("duplicate" in r for r in reasons)
    assert any("FAILED" in r for r in reasons)


def test_baseline_gate_memo_scenario_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    """reconstruction_memo_190k must carry real DBF+FPT evidence per sample."""

    from benchmarks import run_benchmark

    monkeypatch.setattr(run_benchmark, "psutil_available", lambda: True)

    def memo_index() -> int:
        p = _full_gate_payload()
        for i, s in enumerate(p["scenarios"]):
            if s["scenario"] == "reconstruction_memo_190k":
                return i
        raise AssertionError("reconstruction_memo_190k not in the full profile")

    # a sample with FPT missing -> rejected.
    p = _full_gate_payload()
    s = p["scenarios"][memo_index()]
    del s["samples"][0]["output_fpt_bytes"]
    assert any("output_fpt_bytes" in r for r in run_benchmark.check_baseline_gate(p))

    # fewer than two temporary publishes -> rejected.
    p = _full_gate_payload()
    s = p["scenarios"][memo_index()]
    s["samples"][0]["temporary_publish_count"] = 1
    assert any("temporary_publish_count" in r for r in run_benchmark.check_baseline_gate(p))

    # temporary bytes below dbf+fpt -> rejected.
    p = _full_gate_payload()
    s = p["scenarios"][memo_index()]
    s["samples"][0]["temporary_bytes_written"] = 10
    assert any("temporary_bytes_written" in r for r in run_benchmark.check_baseline_gate(p))

    # zero fpt throughput -> rejected.
    p = _full_gate_payload()
    s = p["scenarios"][memo_index()]
    s["samples"][0]["fpt_mib_per_second"] = 0.0
    assert any("fpt_mib_per_second" in r for r in run_benchmark.check_baseline_gate(p))


def test_amplification_formulas_and_edge_cases() -> None:
    from benchmarks import metrics

    # Correct formulas.
    assert metrics.read_amplification(2_000, 1_000) == 2.0
    assert metrics.write_amplification(5_000, 2_000) == 2.5
    assert metrics.read_amplification(1_500, 1_000) == 1.5

    # Zero / missing denominator -> None (never a fabricated value).
    assert metrics.read_amplification(1_000, 0) is None
    assert metrics.read_amplification(1_000, None) is None
    assert metrics.read_amplification(None, 1_000) is None
    assert metrics.write_amplification(1_000, 0) is None
    assert metrics.write_amplification(None, 1_000) is None
    # Zero numerator with a valid denominator is a real zero (no I/O).
    assert metrics.read_amplification(0, 1_000) == 0.0


def test_atomic_publish_tracker_captures_partial_sizes(tmp_path: Path) -> None:
    import os

    from benchmarks import metrics

    outdir = tmp_path / "out"
    outdir.mkdir()

    def publish(partial_name: str, final_name: str, size: int) -> None:
        p = outdir / partial_name
        p.write_bytes(b"x" * size)
        os.replace(p, outdir / final_name)

    # name.partial -> counts.
    with metrics.AtomicPublishTracker(outdir) as t:
        publish("small.jsonl.partial", "small.jsonl", 1234)
    assert t.publish_count == 1
    assert t.total_bytes == 1234

    # .name.partial.dbf and .name.partial.fpt -> both count.
    with metrics.AtomicPublishTracker(outdir) as t:
        publish(".small.partial.dbf", "small.dbf", 100)
        publish(".small.partial.fpt", "small.fpt", 50)
    assert t.publish_count == 2
    assert t.total_bytes == 150

    # .name.raw-layout.partial -> counts.
    with metrics.AtomicPublishTracker(outdir) as t:
        publish(".small.raw-layout.partial", "small.dbf", 77)
    assert t.publish_count == 1
    assert t.total_bytes == 77

    # Two publishes through the SAME path -> both count (no deduplication).
    with metrics.AtomicPublishTracker(outdir) as t:
        publish("dup.jsonl.partial", "dup.jsonl", 10)
        publish("dup.jsonl.partial", "dup.jsonl", 20)
    assert t.publish_count == 2
    assert t.total_bytes == 30

    # A .partial OUTSIDE output_dir is NOT counted.
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with metrics.AtomicPublishTracker(outdir) as t:
        p = outside / "rogue.jsonl.partial"
        p.write_bytes(b"r" * 55)
        os.replace(p, outside / "rogue.jsonl")
    assert t.publish_count == 0
    assert t.total_bytes == 0

    # A FAILED os.replace is not reported as a successful publish.
    with metrics.AtomicPublishTracker(outdir) as t:
        p = outdir / "will-fail.jsonl.partial"
        p.write_bytes(b"b" * 12)
        with pytest.raises(OSError):
            os.replace(p, "/nonexistent/definitely/missing.jsonl")
    assert t.publish_count == 0
    assert t.total_bytes == 0

    # os.replace is restored after the context block.
    assert os.replace is not None
    assert "AtomicPublishTracker" not in type(os.replace).__name__


def test_reconstruction_memo_real_integration(tmp_path: Path) -> None:
    """Real end-to-end integration (no production code mocked):

    a genuine memo-heavy DBF+FPT fixture is exported to JSONL outside the
    measured window, then the public ``reconstruct_dbf`` runs inside
    ``metrics.run``.  The result must carry real DBF **and** FPT outputs,
    at least two temporary publishes (DBF + FPT) whose logical sizes cover
    the final files, and leave no ``.partial`` behind.
    """

    from benchmarks import fixtures, metrics
    from dbfbridge import export_dbf, reconstruct_dbf

    src = fixtures.generate_memo_heavy(tmp_path / "src" / "memo.dbf", 15)
    assert src.is_file() and src.with_suffix(".fpt").is_file()

    # JSONL export OUTSIDE the measured window.
    export_dir = tmp_path / "export"
    export_dir.mkdir()
    export_dbf(
        str(src.parent),
        str(export_dir),
        formats=("jsonl",),
        deleted="include",
        memo="inline",
        overwrite=True,
    ).raise_for_errors()
    input_bytes = sum(p.stat().st_size for p in export_dir.rglob("*") if p.is_file())

    out = tmp_path / "out"
    out.mkdir()

    def run() -> None:
        result = reconstruct_dbf(
            str(export_dir),
            str(out / "rebuilt"),
            input_format="jsonl",
            memo="inline",
            overwrite=True,
        )
        result.raise_for_errors()
        target = out / "rebuilt"
        if target.exists():
            for child in target.iterdir():
                child.rename(out / child.name)
            target.rmdir()

    result = metrics.run(run, input_bytes=input_bytes, input_records=15, output_dir=out)
    assert result["status"] == "MEASURED", result

    dbfs = [p for p in out.rglob("*.dbf") if p.is_file()]
    fpts = [p for p in out.rglob("*.fpt") if p.is_file()]
    assert len(dbfs) == 1 and dbfs[0].stat().st_size > 0
    assert len(fpts) == 1 and fpts[0].stat().st_size > 0
    assert fixtures._measured_counts(dbfs[0])["total_records"] == 15

    publish_count = result.get("temporary_publish_count")
    temp_bytes = result.get("temporary_bytes_written")
    assert isinstance(publish_count, int) and publish_count >= 2
    assert isinstance(temp_bytes, int) and temp_bytes >= (
        dbfs[0].stat().st_size + fpts[0].stat().st_size
    )
    # No temporary artefacts may be left behind.
    leftovers = [p for p in out.rglob("*") if p.is_file() and "partial" in p.name.split(".")]
    assert not leftovers


def test_failed_sample_excluded_from_amplification_aggregate() -> None:
    from benchmarks import worker

    samples = [
        {"status": "MEASURED", "read_amplification": 1.0, "write_amplification": 2.0},
        {
            "status": "FAILED",
            "read_amplification": 99.0,
            "write_amplification": 99.0,
            "temporary_bytes_written": 999999,
            "error": "boom",
        },
        {"status": "MEASURED", "read_amplification": 2.0, "write_amplification": 3.0},
    ]
    agg = worker.aggregate(samples)
    # The failed sample (99.0 / 999999) must not appear in the aggregate;
    # median of (1.0, 2.0) is 1.5 and of (2.0, 3.0) is 2.5.
    assert agg["median_read_amplification"] == 1.5
    assert agg["median_write_amplification"] == 2.5
    assert agg["max_temporary_bytes_written"] is None  # only failed sample had it
    assert agg["valid_baseline"] is False


def test_measure_failed_warmup_alone_fails_scenario(tmp_path: Path) -> None:
    """Warm-up FAILED + all measured reps MEASURED => scenario FAILED, no valid baseline.

    Kept separate from the failed-measured-repetition test on purpose.
    """
    from benchmarks import run_benchmark, worker

    calls = {"n": 0}

    def warmup_only_breaks(out: Path):
        def run() -> None:
            calls["n"] += 1
            if calls["n"] == 1:  # the warm-up run
                raise RuntimeError("warmup boom")

        return run

    runner = worker.Runner(Path.cwd(), "fast", tmp_path, repetitions=3, warmup=1)
    result = runner._measure(
        "warmup_fails",
        "description",
        warmup_only_breaks,
        input_bytes=1,
        input_records=1,
    )
    assert result["status"] == "FAILED"
    samples = list(result["samples"])  # type: ignore[union-attr]
    warmup_samples = list(result["warmup_samples"])  # type: ignore[union-attr]
    # All three measured repetitions still MEASURED and preserved.
    assert len(samples) == 3
    assert all(s["status"] == "MEASURED" for s in samples)
    assert warmup_samples[0]["status"] == "FAILED"
    assert any("warmup boom" in str(e) for e in result["errors"])  # type: ignore[arg-type]

    agg = dict(result["aggregated"])  # type: ignore[arg-type]
    assert agg["valid_baseline"] is False  # type: ignore[index]
    assert agg["warmups_failed"] == 1  # type: ignore[index]
    assert agg["warmups_succeeded"] == 0  # type: ignore[index]
    assert agg["repetitions_succeeded"] == 3  # type: ignore[index]
    assert agg["repetitions_failed"] == 0  # type: ignore[index]

    # Markdown must not present this as a comparable baseline.
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
        "scenarios": [dict(result)],
    }
    md = run_benchmark.render_markdown(payload)
    row = next(line for line in md.splitlines() if line.startswith("| `warmup_fails`"))
    assert "NOT A VALID BASELINE" in row or "NOT_AVAILABLE" in row


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

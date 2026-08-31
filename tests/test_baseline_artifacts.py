"""Phase 1 baseline artifact hygiene tests (names, atomic publish, compare).

These tests verify that Phase 1 reports/baselines can never collide with the
preserved Phase 0 BEFORE artifacts and that the BEFORE/AFTER comparison CLI
behaves honestly.  Nothing touches the real ``benchmarks/baselines/`` —
every test runs inside ``tmp_path``.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from benchmarks import artifacts

SRC_ROOT = Path(__file__).parents[1] / "src"
REPO_ROOT = Path(__file__).parents[1]

_BEFORE_SHA256 = "d3b5ab454706b5e7085811c49fc06f8a421f127498695ae1178a1efc07453aa6"
_BEFORE_MD_SHA256 = "137ade61b31b1be2638a9fb081bf61097e78c04b9bc2860df48f6114f06eff0c"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _payload(**overrides: Any) -> dict[str, Any]:
    env: dict[str, Any] = {
        "benchmark_contract": artifacts.BENCHMARK_CONTRACT,
        "profile": "full",
        "system": {"os": "SameOS", "python": "3.12", "arch": "AMD64"},
        "packages": {"dbfbridge": "0.1.0"},
        "git": {"commit": "2" * 40},
    }
    env.update(overrides)
    return {"environment": env, "scenarios": []}


def _measured(name: str, **aggregated: Any) -> dict[str, Any]:
    agg: dict[str, Any] = {
        "median_wall_seconds": 0.5,
        "median_cpu_seconds": 0.4,
        "median_records_per_second": 1000.0,
        "median_source_mib_per_second": 0.5,
        "max_peak_rss_bytes": 10 << 20,
        "median_read_amplification": 1.5,
        "median_write_amplification": 1.2,
        "max_output_bytes": 1000,
        "max_temporary_bytes_written": 900,
    }
    agg.update(aggregated)
    return {"scenario": name, "status": "MEASURED", "aggregated": agg}


def _placeholder(name: str) -> dict[str, Any]:
    return {"scenario": name, "status": "NOT_IMPLEMENTED", "aggregated": {}}


_BEFORE_ENV = {
    "profile": "full",
    "system": {
        "python": "3.12.10",
        "os": "SameOS",
        "arch": "AMD64",
        "processor": "CPU-X",
        "cpu_count": 8,
        "physical_memory_bytes": 64 << 30,
    },
    "packages": {"dbfbridge": "0.1.0", "psutil": "7.2.2"},
    "git": {"commit": "1" * 40},
}


def _before_payload() -> dict[str, Any]:
    return {
        "environment": _BEFORE_ENV,
        "scenarios": [
            _measured("export_jsonl_validate_on"),
            _placeholder("direct_read_bounded"),
            _placeholder("field_projection"),
            _placeholder("memo_lazy"),
            _placeholder("raw_mode_none"),
        ],
    }


def _after_payload(**env_overrides: Any) -> dict[str, Any]:
    env = {
        "benchmark_contract": artifacts.BENCHMARK_CONTRACT,
        "profile": "full",
        "system": dict(_BEFORE_ENV["system"]),
        "packages": dict(_BEFORE_ENV["packages"]),
        "git": {"commit": "2" * 40},
    }
    env.update(env_overrides)
    return {
        "environment": env,
        "scenarios": [
            _measured("export_jsonl_validate_on"),
            _measured(
                "direct_read_bounded",
                median_wall_seconds=0.25,
                median_cpu_seconds=0.2,
                median_records_per_second=2000.0,
                median_source_mib_per_second=1.0,
                max_peak_rss_bytes=8 << 20,
                median_read_amplification=0.05,
                median_write_amplification=None,
                max_output_bytes=0,
                max_temporary_bytes_written=0,
            ),
            _measured("field_projection", median_wall_seconds=0.0),
            _measured("memo_lazy"),
            _measured("raw_mode_none"),
        ],
    }


def _write_reports(root: Path, name: str, payload: dict[str, Any]) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / f"{name}.json"
    md_path = root / f"{name}.md"
    json_path.write_text(json.dumps(payload), encoding="utf-8")
    md_path.write_text(f"# report {name}\n", encoding="utf-8")
    return json_path, md_path


def _no_partials(directory: Path) -> bool:
    return not any(True for _ in directory.glob("*partial*"))


def _run_compare(before: Path, after: Path, tmp_dir: Path) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        "-m",
        "benchmarks.compare_baselines",
        str(before),
        str(after),
        "--json",
        str(tmp_dir / "comparison.json"),
        "--markdown",
        str(tmp_dir / "comparison.md"),
        "--quiet",
    ]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(SRC_ROOT), str(REPO_ROOT))))
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
        timeout=300,
    )


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# artifact naming
# ---------------------------------------------------------------------------


def test_phase1_reports_use_the_contract_prefix() -> None:
    assert artifacts.report_stem(artifacts.BENCHMARK_CONTRACT, "fast") == (
        "phase-1-direct-read-fast"
    )
    assert artifacts.report_stem(artifacts.BENCHMARK_CONTRACT, "full") == (
        "phase-1-direct-read-full"
    )
    assert artifacts.report_stem(artifacts.BENCHMARK_CONTRACT, "fast", "encoding_cp1250") == (
        "phase-1-direct-read-fast-encoding_cp1250"
    )
    # The Phase 0 prefix appears only for the explicitly given legacy contract.
    assert artifacts.contract_report_prefix(None) == "phase-0"


def test_phase1_baseline_never_targets_phase0_names() -> None:
    json_name, md_name = artifacts.baseline_target_names(artifacts.BENCHMARK_CONTRACT, "full")
    assert (json_name, md_name) == (
        "phase-1-direct-read-full.json",
        "phase-1-direct-read-full.md",
    )
    # The preserved Phase 0 BEFORE pair is the only artifact with those names:
    # no derived Phase 1 target may ever collide with it.
    assert "phase-1-direct-read-full.json" not in artifacts.RESERVED_PHASE_0_BASELINE_FILES
    assert "phase-0-full.json" in artifacts.RESERVED_PHASE_0_BASELINE_FILES
    # A legacy contract can name only legacy reports, never a baseline target.
    assert artifacts.contract_report_prefix(None) == "phase-0"
    assert artifacts.contract_report_prefix("phase-0") == "phase-0"
    with pytest.raises(artifacts.UnknownBenchmarkContractError):
        artifacts.baseline_target_names("phase-0", "full")  # type: ignore[arg-type]
    for invalid in (None, "phase-2"):
        with pytest.raises(artifacts.UnknownBenchmarkContractError):
            artifacts.baseline_target_names(invalid, "full")  # type: ignore[arg-type]
    with pytest.raises(artifacts.UnknownBenchmarkContractError):
        artifacts.baseline_target_names(artifacts.BENCHMARK_CONTRACT, "fast")


# ---------------------------------------------------------------------------
# atomic baseline publication
# ---------------------------------------------------------------------------


def test_successful_publish_creates_exactly_the_phase1_pair(tmp_path: Path) -> None:
    json_path, md_path = _write_reports(tmp_path, "results-report", _payload())
    target_dir = tmp_path / "baselines"
    published = artifacts.publish_baseline_pair(json_path, md_path, target_dir, payload=_payload())

    assert published["json"].name == "phase-1-direct-read-full.json"
    assert published["markdown"].name == "phase-1-direct-read-full.md"
    assert sorted(entry.name for entry in target_dir.iterdir()) == [
        "phase-1-direct-read-full.json",
        "phase-1-direct-read-full.md",
    ]
    assert artifacts.sha256_file(published["json"]) == published["json_sha256"]
    assert artifacts.sha256_file(published["markdown"]) == published["markdown_sha256"]
    assert _sha256_bytes((published["json"]).read_bytes()) == published["json_sha256"]
    assert _no_partials(target_dir)


def test_existing_baseline_target_refuses_overwrite(tmp_path: Path) -> None:
    json_path, md_path = _write_reports(tmp_path, "results-report", _payload())
    target_dir = tmp_path / "baselines"
    target_dir.mkdir()
    (target_dir / "phase-1-direct-read-full.json").write_text("{}", encoding="utf-8")

    with pytest.raises(artifacts.BaselinePublishError) as error:
        artifacts.publish_baseline_pair(json_path, md_path, target_dir, payload=_payload())
    assert "overwrite" in str(error.value)
    # The pre-existing target is untouched and nothing else was published.
    assert (target_dir / "phase-1-direct-read-full.json").read_text() == "{}"
    assert not (target_dir / "phase-1-direct-read-full.md").exists()
    assert _no_partials(target_dir)


def test_phase0_files_are_untouched_by_a_phase1_publish(tmp_path: Path) -> None:
    json_path, md_path = _write_reports(tmp_path, "results-report", _payload())
    target_dir = tmp_path / "baselines"
    target_dir.mkdir()
    preserved_json = target_dir / "phase-0-full.json"
    preserved_md = target_dir / "phase-0-full.md"
    preserved_json.write_text("PHASE0-JSON", encoding="utf-8")
    preserved_md.write_text("PHASE0-MD", encoding="utf-8")

    artifacts.publish_baseline_pair(json_path, md_path, target_dir, payload=_payload())
    assert preserved_json.read_text() == "PHASE0-JSON"
    assert preserved_md.read_text() == "PHASE0-MD"
    assert artifacts.sha256_file(preserved_json) == _sha256_bytes(b"PHASE0-JSON")


def test_markdown_publish_failure_leaves_no_json(tmp_path: Path, monkeypatch) -> None:
    json_path, md_path = _write_reports(tmp_path, "results-report", _payload())
    target_dir = tmp_path / "baselines"
    real_replace = os.replace

    def failing_replace(src, dst, *args, **kwargs):
        if str(dst).endswith("phase-1-direct-read-full.md"):
            raise OSError("simulated markdown publish failure")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(artifacts.os, "replace", failing_replace)
    with pytest.raises(artifacts.BaselinePublishError):
        artifacts.publish_baseline_pair(json_path, md_path, target_dir, payload=_payload())
    monkeypatch.undo()
    # Half-pair protection: the already-published JSON is rolled back.
    assert not (target_dir / "phase-1-direct-read-full.json").exists()
    assert not (target_dir / "phase-1-direct-read-full.md").exists()
    assert _no_partials(target_dir)


def test_json_write_failure_leaves_no_markdown(tmp_path: Path, monkeypatch) -> None:
    json_path, md_path = _write_reports(tmp_path, "results-report", _payload())
    target_dir = tmp_path / "baselines"
    real_write = type(json_path).write_bytes

    def corrupting_write(self: Path, data: bytes) -> int:
        if self.name.endswith(".json.partial"):  # corrupt the staged JSON partial
            return real_write(self, data + b"CORRUPT")
        return real_write(self, data)

    monkeypatch.setattr(type(json_path), "write_bytes", corrupting_write)
    with pytest.raises(artifacts.BaselinePublishError) as error:
        artifacts.publish_baseline_pair(json_path, md_path, target_dir, payload=_payload())
    monkeypatch.setattr(type(json_path), "write_bytes", real_write)
    assert "round-trip" in str(error.value)
    assert not (target_dir / "phase-1-direct-read-full.md").exists()
    assert _no_partials(target_dir)


def test_publish_refuses_missing_wrong_contract_or_fast_profile(tmp_path: Path) -> None:
    json_path, md_path = _write_reports(tmp_path, "results-report", _payload())
    for payload in (
        _payload(benchmark_contract=None),
        _payload(benchmark_contract="phase-0-v9"),
        _payload(profile="fast"),
    ):
        with pytest.raises(artifacts.BaselinePublishError) as error:
            artifacts.publish_baseline_pair(json_path, md_path, tmp_path, payload=payload)
        message = str(error.value)
        assert "benchmark_contract" in message or "full" in message
    # Nothing was published and no partial remains (sources are in the same
    # tmp directory by design).
    assert not (tmp_path / "phase-1-direct-read-full.json").exists()
    assert not (tmp_path / "phase-1-direct-read-full.md").exists()
    assert _no_partials(tmp_path)


# ---------------------------------------------------------------------------
# BEFORE/AFTER comparison CLI
# ---------------------------------------------------------------------------


def test_comparison_recognizes_newly_measured(tmp_path: Path) -> None:
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    before_path.write_text(json.dumps(_before_payload()), encoding="utf-8")
    after_path.write_text(json.dumps(_after_payload()), encoding="utf-8")

    completed = _run_compare(before_path, after_path, tmp_path)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads((tmp_path / "comparison.json").read_text(encoding="utf-8"))
    assert payload["newly_measured"] == [
        "direct_read_bounded",
        "field_projection",
        "memo_lazy",
        "raw_mode_none",
    ]
    for entry in payload["comparisons"]:
        if entry["status"] == "NEWLY_MEASURED":
            assert entry["before_status"] == "NOT_IMPLEMENTED"
            assert "no speedup" in entry["note"].lower()
    same = next(c for c in payload["comparisons"] if c["status"] == "SAME_MEASURED")
    row = same["metrics"]["median_wall_seconds"]
    assert row["before"] == 0.5 and row["after"] == 0.5
    assert row["ratio"] == pytest.approx(1.0)
    assert row["change_percent"] == pytest.approx(0.0)
    markdown = (tmp_path / "comparison.md").read_text(encoding="utf-8")
    assert "NEWLY_MEASURED" in markdown
    assert "No speedup is claimed" in markdown


def test_comparison_never_divides_by_notavailable_or_zero(tmp_path: Path) -> None:
    before = _before_payload()
    before["scenarios"][0]["aggregated"]["median_records_per_second"] = 0
    before["scenarios"][0]["aggregated"]["max_output_bytes"] = None
    after = _after_payload()
    for entry in after["scenarios"]:
        if entry["scenario"] == "export_jsonl_validate_on":
            entry["aggregated"]["median_records_per_second"] = None
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    before_path.write_text(json.dumps(before), encoding="utf-8")
    after_path.write_text(json.dumps(after), encoding="utf-8")

    completed = _run_compare(before_path, after_path, tmp_path)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads((tmp_path / "comparison.json").read_text(encoding="utf-8"))
    entry = next(c for c in payload["comparisons"] if c["scenario"] == "export_jsonl_validate_on")
    assert entry["metrics"]["median_records_per_second"]["ratio"] == "NOT_AVAILABLE"
    assert entry["metrics"]["max_output_bytes"]["ratio"] == "NOT_AVAILABLE"
    # The NEWLY_MEASURED Direct Read scenarios carry no ratio metrics at all.
    for entry in payload["comparisons"]:
        if entry["status"] == "NEWLY_MEASURED":
            assert "metrics" not in entry


def test_comparison_warns_about_different_environments(tmp_path: Path) -> None:
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    after = _after_payload()
    after["environment"]["system"]["os"] = "Linux-DifferentHost"
    before_path.write_text(json.dumps(_before_payload()), encoding="utf-8")
    after_path.write_text(json.dumps(after), encoding="utf-8")

    completed = _run_compare(before_path, after_path, tmp_path)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads((tmp_path / "comparison.json").read_text(encoding="utf-8"))
    assert payload["environments_comparable"] is False
    assert any(difference == "os" for difference in payload["environment_differences"])
    assert any("must NOT label" in warning for warning in payload["warnings"])
    markdown = (tmp_path / "comparison.md").read_text(encoding="utf-8")
    assert "NOT comparable" in markdown


def test_comparison_rejects_swapped_or_wrong_contract(tmp_path: Path) -> None:
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    before_path.write_text(json.dumps(_before_payload()), encoding="utf-8")
    after_path.write_text(json.dumps(_after_payload()), encoding="utf-8")

    swapped = _run_compare(after_path, before_path, tmp_path)
    assert swapped.returncode != 0
    assert "swap" in swapped.stderr.lower()

    wrong = _after_payload()
    wrong["environment"]["benchmark_contract"] = "phase-2-v1"
    wrong_path = tmp_path / "wrong.json"
    wrong_path.write_text(json.dumps(wrong), encoding="utf-8")
    wrong_run = _run_compare(before_path, wrong_path, tmp_path)
    assert wrong_run.returncode != 0
    assert "benchmark_contract" in wrong_run.stderr

    missing = _after_payload()
    del missing["environment"]["benchmark_contract"]
    missing_path = tmp_path / "missing.json"
    missing_path.write_text(json.dumps(missing), encoding="utf-8")
    missing_run = _run_compare(before_path, missing_path, tmp_path)
    assert missing_run.returncode != 0
    assert "benchmark_contract" in missing_run.stderr


def test_comparison_rejects_broken_json(tmp_path: Path) -> None:
    before_path = tmp_path / "before.json"
    before_path.write_text(json.dumps(_before_payload()), encoding="utf-8")
    broken = tmp_path / "broken.json"
    broken.write_text("{not-json", encoding="utf-8")
    completed = _run_compare(before_path, broken, tmp_path)
    assert completed.returncode != 0
    assert "cannot read baseline" in completed.stderr.lower()


def test_comparison_cli_never_touches_the_real_phase0_baseline(tmp_path: Path) -> None:
    """The real Phase 0 files stay byte-identical even when passed to the CLI."""
    import subprocess as sp

    repo_baselines = REPO_ROOT / "benchmarks" / "baselines"
    before_json = repo_baselines / "phase-0-full.json"
    before_md = repo_baselines / "phase-0-full.md"
    sha_json_before = _sha256_bytes(before_json.read_bytes())
    sha_md_before = _sha256_bytes(before_md.read_bytes())
    assert sha_json_before == _BEFORE_SHA256
    assert sha_md_before == _BEFORE_MD_SHA256
    try:
        # BEFORE passed twice: the "after" side carries no Phase 1 contract,
        # so the comparison is refused before anything is written.
        completed = sp.run(
            [
                sys.executable,
                "-m",
                "benchmarks.compare_baselines",
                str(before_json),
                str(before_json),
                "--quiet",
            ],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            timeout=300,
        )
        assert completed.returncode != 0
        assert "phase" in completed.stderr.lower()
    finally:
        assert _sha256_bytes(before_json.read_bytes()) == _BEFORE_SHA256
        assert _sha256_bytes(before_md.read_bytes()) == _BEFORE_MD_SHA256
        baseline_names = {
            entry.name for entry in repo_baselines.iterdir() if entry.name.startswith("phase-0")
        }
        assert baseline_names == {"phase-0-full.json", "phase-0-full.md"}
        assert not any(entry.name.startswith("phase-1") for entry in repo_baselines.iterdir())


# ---------------------------------------------------------------------------
# Phase 1 scenario contract (programmatic pre-baseline check)
# ---------------------------------------------------------------------------


def test_phase1_scenario_contract() -> None:
    from benchmarks import worker

    fast = list(worker._scenario_names("fast"))
    full = list(worker._scenario_names("full"))
    assert len(fast) == 19 == len(set(fast)), fast
    assert len(full) == 24 == len(set(full)), full
    assert set(fast) < set(full), "full must be a strict superset of fast"
    assert set(full) - set(fast) == {
        "export_1m_records",
        "memo_heavy_190k",
        "reconstruction_190k",
        "reconstruction_memo_190k",
        "jsonl_conversion_xlsx",
    }
    # Exactly the four former Phase 0 NOT_IMPLEMENTED placeholders are
    # MEASURED scenarios of both profiles — never renamed, never dropped.
    former_placeholders = (
        "direct_read_bounded",
        "field_projection",
        "memo_lazy",
        "raw_mode_none",
    )
    for name in former_placeholders:
        assert name in fast and name in full
    # Every contract scenario has a dedicated worker implementation.
    for name in former_placeholders:
        assert hasattr(worker.Runner, f"scenario_{name}"), name

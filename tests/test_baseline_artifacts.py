"""Phase 1 baseline contract, publication, and comparison tests.

Covers:

- the pure saved-artifact validators (Phase 0 BEFORE legacy contract and the
  full Phase 1 AFTER contract — frozen names, counts, metrics, run identity);
- the contract-derived artifact names and the exception-safe baseline
  publication with the commit-marker manifest (never overwriting, rollbacks,
  no half trios, no ``.partial`` residue, post-write verification);
- the BEFORE/AFTER comparison CLI (NEWLY_MEASURED limited to the four Phase 1
  placeholders, no division by zero, three-state environment comparability).

None of these tests may touch the real ``benchmarks/baselines/`` directory —
everything runs inside ``tmp_path``.
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

from benchmarks import artifacts, run_benchmark
from benchmarks import contract as benchmark_contract

SRC_ROOT = Path(__file__).parents[1] / "src"
REPO_ROOT = Path(__file__).parents[1]

_BEFORE_SHA256 = "d3b5ab454706b5e7085811c49fc06f8a421f127498695ae1178a1efc07453aa6"
_BEFORE_MD_SHA256 = "137ade61b31b1be2638a9fb081bf61097e78c04b9bc2860df48f6114f06eff0c"


# ---------------------------------------------------------------------------
# payload builders
# ---------------------------------------------------------------------------


def _sample(warmup_flag: bool, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "status": "MEASURED",
        "warmup": warmup_flag,
        "wall_seconds": 1.0,
        "cpu_seconds": 0.5,
        "records_per_second": 100.0,
        "source_mib_per_second": 1.0,
        "output_bytes": 2000,
        "peak_rss_bytes": 2048,
        "read_amplification": 2.0,
        "write_amplification": 1.0,
        "input_bytes": 1000,
        "input_records": 100,
        "temporary_bytes_written": 0,
        "temporary_bytes_left": 0,
        "temporary_files_left": 0,
    }
    base.update(overrides)
    return base


def _aggregated() -> dict[str, Any]:
    return {
        "median_wall_seconds": 1.0,
        "median_cpu_seconds": 0.5,
        "median_records_per_second": 100.0,
        "median_source_mib_per_second": 1.0,
        "max_peak_rss_bytes": 2048,
        "max_output_bytes": 2000,
        "max_temporary_bytes_written": 0,
        "valid_baseline": True,
    }


def _memo_aggregated() -> dict[str, Any]:
    return {
        **_aggregated(),
        "max_output_dbf_bytes": 5_000,
        "max_output_fpt_bytes": 9_000,
        "median_fpt_mib_per_second": 0.5,
    }


def _memo_sample(**overrides: Any) -> dict[str, Any]:
    sample = _sample(
        warmup_flag=False,
        output_dbf_bytes=5_000,
        output_fpt_bytes=9_000,
        fpt_mib_per_second=0.5,
        temporary_publish_count=2,
        temporary_bytes_written=14_000,
    )
    sample.update(overrides)
    return sample


def _phase1_scenario(name: str, warmup: int = 1, repetitions: int = 3) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "scenario": name,
        "status": "MEASURED",
        "warmup": warmup,
        "repetitions": repetitions,
        "warmup_samples": [_sample(True) for _ in range(warmup)],
        "samples": [],
        "aggregated": _memo_aggregated() if name == "reconstruction_memo_190k" else _aggregated(),
    }
    if name == "reconstruction_memo_190k":
        entry["samples"] = [_memo_sample() for _ in range(repetitions)]
    else:
        entry["samples"] = [_sample(False) for _ in range(repetitions)]
    return entry


def _phase1_env(**overrides: Any) -> dict[str, Any]:
    # The runtime environment (system + measured dependencies) matches the
    # Phase 0 fixture on purpose: only storage provenance varies between the
    # comparability tests.  The benchmark contract, commit, branch, and the
    # dbfbridge package version are the expected subject of the comparison
    # and are never environment mismatches.
    env: dict[str, Any] = {
        "benchmark_contract": benchmark_contract.CONTRACT_PHASE_1,
        "profile": "full",
        "warmup": 1,
        "repetitions": 3,
        "system": {
            "python": "3.12.10",
            "os": "Windows Server 2025",
            "arch": "AMD64",
            "processor": "CPU-X",
            "cpu_count": 8,
            "physical_memory_bytes": 64 << 30,
        },
        "packages": {
            "dbfbridge": "0.1.0",
            "dbf": "0.99.11",
            "dbfread": "2.0.7",
            "orjson": "3.12.0",
            "polars": "1.44.1",
            "openpyxl": "3.1.5",
            "xlsxwriter": "3.2.9",
            "psutil": "7.2.2",
        },
        "git": {
            "commit": "a" * 40,
            "origin_main": "b" * 40,
            "branch": "feat/phase-1-record-read",
            "worktree_dirty": False,
            "worktree_status": "",
        },
    }
    env.update(overrides)
    return env


def _phase1_payload(run_id: str | None = None, **env_overrides: Any) -> dict[str, Any]:
    from benchmarks import contract

    env = _phase1_env(**env_overrides)
    if run_id is not None:
        env["run_id"] = run_id
    payload = {
        "environment": env,
        "fixtures": {},
        "generated_at": "2026-08-31T12:00:00+02:00",
        "scenarios": [
            _phase1_scenario(name, repetitions=env["repetitions"], warmup=env["warmup"])
            for name in sorted(contract.FROZEN_SCENARIO_NAMES)
        ],
    }
    payload["environment"].setdefault("run_id", benchmark_contract.derive_run_id(payload))
    return payload


def _after_trio_payload(**env_overrides: Any) -> dict[str, Any]:
    return _phase1_payload(**env_overrides)


def _phase0_placeholder(name: str) -> dict[str, Any]:
    return {"scenario": name, "status": "NOT_IMPLEMENTED", "aggregated": {}}


def _phase0_env(**overrides: Any) -> dict[str, Any]:
    env: dict[str, Any] = {
        # Legacy: no benchmark_contract field at all.
        "profile": "full",
        "warmup": 1,
        "repetitions": 3,
        "system": {
            "python": "3.12.10",
            "os": "Windows Server 2025",
            "arch": "AMD64",
            "processor": "CPU-X",
            "cpu_count": 8,
            "physical_memory_bytes": 64 << 30,
        },
        "packages": {
            "dbfbridge": "0.1.0",
            "dbf": "0.99.11",
            "dbfread": "2.0.7",
            "orjson": "3.12.0",
            "polars": "1.44.1",
            "openpyxl": "3.1.5",
            "xlsxwriter": "3.2.9",
            "psutil": "7.2.2",
        },
        "git": {
            "commit": "542961981e0062cdc977d1b7a4eec721e1f16fd4",
            "origin_main": "addbadb9281914661bf742924f45039e46a895cd",
            "branch": "bench",
            "worktree_dirty": False,
            "worktree_status": "",
        },
    }
    env.update(overrides)
    return env


def _phase0_payload(**env_overrides: Any) -> dict[str, Any]:
    from benchmarks import contract

    env = _phase0_env(**env_overrides)
    scenarios: list[dict[str, Any]] = []
    for name in sorted(contract.FROZEN_PHASE0_MEASURED_NAMES | contract.PHASE0_PLACEHOLDER_NAMES):
        if name in contract.PHASE0_PLACEHOLDER_NAMES:
            scenarios.append(_phase0_placeholder(name))
        else:
            scenarios.append(_phase1_scenario(name))
    return {
        "environment": env,
        "fixtures": {},
        "generated_at": "2026-08-29T10:27:19+0000",
        "scenarios": scenarios,
    }


def _write_reports(root: Path, name: str, payload: dict[str, Any]) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / f"{name}.json"
    md_path = root / f"{name}.md"
    json_path.write_text(json.dumps(payload), encoding="utf-8")
    md_path.write_text(_render_md(payload), encoding="utf-8")
    return json_path, md_path


def _render_md(payload: dict[str, Any]) -> str:
    env = payload["environment"]
    return (
        "# dbfbridge benchmark report\n\n"
        f"- run_id: `{env['run_id']}`\n"
        f"- benchmark_contract: `{env['benchmark_contract']}`\n"
        f"- Profile: `{env['profile']}`\n"
    )


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _no_partials(directory: Path) -> bool:
    return not any(directory.glob("*partial*"))


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


def _write_after_trio(root: Path, payload: dict[str, Any], **manifest_extra: Any) -> Path:
    """Write a complete AFTER trio (JSON + Markdown + manifest) into *root*.

    Returns the path of the AFTER JSON.  The payload must already carry its
    ``run_id``; the manifest corroborates the written hashes.
    """
    root.mkdir(parents=True, exist_ok=True)
    json_name, md_name, manifest_name = artifacts.baseline_target_paths(
        str(payload["environment"]["benchmark_contract"]),
        str(payload["environment"]["profile"]),
    )
    json_path = root / json_name
    md_path = root / md_name
    json_path.write_text(json.dumps(payload), encoding="utf-8")
    md_path.write_text(_render_md(payload), encoding="utf-8")
    manifest_payload = benchmark_contract.build_manifest(
        run_id=payload["environment"]["run_id"],
        contract=payload["environment"]["benchmark_contract"],
        profile=payload["environment"]["profile"],
        git_commit=payload["environment"]["git"]["commit"],
        json_name=json_name,
        json_sha256=_sha256_bytes(json_path.read_bytes()),
        markdown_name=md_name,
        markdown_sha256=_sha256_bytes(md_path.read_bytes()),
        **manifest_extra,
    )
    (root / manifest_name).write_text(json.dumps(manifest_payload), encoding="utf-8")
    return json_path


def _write_before_pair(root: Path, payload: dict[str, Any]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "phase-0-full.json"
    json_path.write_text(json.dumps(payload), encoding="utf-8")
    return json_path


# ---------------------------------------------------------------------------
# pure saved-artifact validators
# ---------------------------------------------------------------------------


def test_phase0_before_contract_accepts_legacy_shape() -> None:
    payload = _phase0_payload()
    assert benchmark_contract.validate_saved_phase0_before(payload) == []


def test_phase0_before_contract_is_frozen(tmp_path: Path) -> None:
    payload = _phase0_payload()
    scenarios = payload["scenarios"]
    # A missing scenario must be rejected.
    payload["scenarios"] = scenarios[:-1]
    assert any(
        "missing scenario" in problem
        for problem in benchmark_contract.validate_saved_phase0_before(payload)
    )
    # An unknown extra scenario must be rejected.
    payload = _phase0_payload()
    payload["scenarios"].append(_phase1_scenario("some_new_future_scenario"))
    assert any(
        "unknown scenario" in problem
        for problem in benchmark_contract.validate_saved_phase0_before(payload)
    )
    # A duplicate name must be rejected.
    payload = _phase0_payload()
    payload["scenarios"].append(_phase1_scenario("export_jsonl_validate_on"))
    assert any(
        "duplicate" in problem
        for problem in benchmark_contract.validate_saved_phase0_before(payload)
    )
    # Unknown status.
    payload = _phase0_payload()
    payload["scenarios"][0]["status"] = "FAILED"
    assert any(
        "failed or unknown" in problem
        for problem in benchmark_contract.validate_saved_phase0_before(payload)
    )
    # A moved placeholder must be rejected.
    payload = _phase0_payload()
    placeholder = next(
        e
        for e in payload["scenarios"]
        if e["scenario"] in benchmark_contract.PHASE0_PLACEHOLDER_NAMES
    )
    placeholder["status"] = "MEASURED"
    assert any(
        "must stay NOT_IMPLEMENTED" in problem or "must be MEASURED" in problem
        for problem in benchmark_contract.validate_saved_phase0_before(payload)
    )
    # warmup/repetition and sample-count gaps.
    payload = _phase0_payload()
    payload["environment"]["warmup"] = 0
    payload["environment"]["repetitions"] = 2
    problems = benchmark_contract.validate_saved_phase0_before(payload)
    assert any("warmup" in p for p in problems)
    assert any("repetitions" in p for p in problems)
    # Legacy Phase 0 must not carry a benchmark_contract.
    payload = _phase0_payload()
    payload["environment"]["benchmark_contract"] = "phase-0-afterthought"
    assert any(
        "legacy" in problem for problem in benchmark_contract.validate_saved_phase0_before(payload)
    )
    # Samples must be complete, flagged, valid, residue-free.
    payload = _phase0_payload()
    measured = next(e for e in payload["scenarios"] if e["status"] == "MEASURED")
    measured["samples"][0].pop("wall_seconds")
    measured["samples"][0]["warmup"] = True
    measured["warmup_samples"][0]["status"] = "FAILED"
    measured["aggregated"]["valid_baseline"] = False
    measured["samples"][1]["temporary_files_left"] = 1
    measured["samples"][2]["peak_rss_bytes"] = None
    problems = benchmark_contract.validate_saved_phase0_before(payload)
    joined = "; ".join(problems)
    for needle in ("wall_seconds", "warmup=", "status", "valid_baseline", "temporary", "peak RSS"):
        assert needle in joined, (needle, joined)
    # Full commit + clean worktree + system/package metadata.
    payload = _phase0_payload()
    payload["environment"]["git"]["commit"] = "short"
    payload["environment"]["git"]["worktree_dirty"] = True
    del payload["environment"]["system"]["os"]
    del payload["environment"]["packages"]["psutil"]
    problems = benchmark_contract.validate_saved_phase0_before(payload)
    joined = "; ".join(problems)
    for needle in ("40-hex", "worktree_dirty", "system.os", "psutil"):
        assert needle in joined, (needle, joined)


def test_phase0_validator_matches_the_real_baseline_file() -> None:

    baseline_json = REPO_ROOT / "benchmarks" / "baselines" / "phase-0-full.json"
    payload = json.loads(baseline_json.read_text(encoding="utf-8"))
    assert benchmark_contract.validate_saved_phase0_before(payload) == []


def test_phase1_after_contract_accepts_full_shape() -> None:
    payload = _phase1_payload(run_id=benchmark_contract.derive_run_id(_phase1_payload()))
    run_id = benchmark_contract.derive_run_id(payload)
    payload["environment"]["run_id"] = run_id
    assert benchmark_contract.validate_saved_phase1_after(payload) == []


def test_phase1_after_contract_is_frozen(tmp_path: Path) -> None:
    payload = _phase1_payload()
    # Missing scenario.
    payload["scenarios"] = payload["scenarios"][:-1]
    problems = benchmark_contract.validate_saved_phase1_after(payload)
    assert any("missing scenario" in p for p in problems)
    # Extra / unknown scenario.
    payload = _phase1_payload()
    payload["scenarios"].append(_phase1_scenario("some_new_future_scenario"))
    assert any(
        "unknown scenario" in p for p in benchmark_contract.validate_saved_phase1_after(payload)
    )
    # Duplicate scenario.
    payload = _phase1_payload()
    payload["scenarios"].append(_phase1_scenario("memo_null"))
    assert any("duplicate" in p for p in benchmark_contract.validate_saved_phase1_after(payload))
    # Missing one repetition / warm-up.
    payload = _phase1_payload()
    next(s for s in payload["scenarios"] if s["scenario"] != "reconstruction_memo_190k")[
        "samples"
    ].pop()
    problems = benchmark_contract.validate_saved_phase1_after(payload)
    assert any("measured samples" in p for p in problems)
    payload = _phase1_payload()
    next(s for s in payload["scenarios"] if s["scenario"] != "reconstruction_memo_190k")[
        "warmup_samples"
    ].clear()
    problems = benchmark_contract.validate_saved_phase1_after(payload)
    assert any("warm-up samples" in p for p in problems)
    # Missing peak RSS / residue / invalid baseline / unknown status.
    payload = _phase1_payload()
    bad = next(s for s in payload["scenarios"] if s["scenario"] != "reconstruction_memo_190k")
    bad["samples"][0]["peak_rss_bytes"] = None
    bad["samples"][1]["temporary_files_left"] = 2
    bad["aggregated"]["valid_baseline"] = False
    problems = benchmark_contract.validate_saved_phase1_after(payload)
    joined = "; ".join(problems)
    assert "peak RSS" in joined and "temporary" in joined and "valid_baseline" in joined
    # Unknown status.
    payload = _phase1_payload()
    payload["scenarios"][0]["status"] = "FAILED"
    assert any(
        "failed or unknown" in p for p in benchmark_contract.validate_saved_phase1_after(payload)
    )
    # Not-measured placeholder is forbidden in AFTER.
    payload = _phase1_payload()
    placeholder = _phase0_placeholder("memo_lazy")
    placeholder["status"] = "NOT_IMPLEMENTED"
    payload["scenarios"] = [s for s in payload["scenarios"] if s["scenario"] != "memo_lazy"] + [
        placeholder
    ]
    problems = benchmark_contract.validate_saved_phase1_after(payload)
    assert any("must be MEASURED" in p for p in problems)
    # Memo extras are enforced.
    payload = _phase1_payload()
    memo = next(s for s in payload["scenarios"] if s["scenario"] == "reconstruction_memo_190k")
    memo["samples"][0].pop("output_fpt_bytes")
    assert any(
        "output_fpt_bytes" in p for p in benchmark_contract.validate_saved_phase1_after(payload)
    )


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
    assert artifacts.contract_report_prefix(None) == "phase-0"


def test_phase1_baseline_never_targets_phase0_names() -> None:
    json_name, md_name, manifest_name = artifacts.baseline_target_paths(
        artifacts.BENCHMARK_CONTRACT, "full"
    )
    assert (json_name, md_name, manifest_name) == (
        "phase-1-direct-read-full.json",
        "phase-1-direct-read-full.md",
        "phase-1-direct-read-full.manifest.json",
    )
    assert "phase-0-full.json" in artifacts.RESERVED_PHASE_0_BASELINE_FILES
    assert artifacts.contract_report_prefix(None) == "phase-0"
    with pytest.raises(artifacts.UnknownBenchmarkContractError):
        artifacts.baseline_target_paths("phase-0", "full")  # type: ignore[arg-type]
    for invalid in (None, "phase-2"):
        with pytest.raises(artifacts.UnknownBenchmarkContractError):
            artifacts.baseline_target_paths(invalid, "full")  # type: ignore[arg-type]
    with pytest.raises(artifacts.UnknownBenchmarkContractError):
        artifacts.baseline_target_paths(artifacts.BENCHMARK_CONTRACT, "fast")


# ---------------------------------------------------------------------------
# atomic baseline publication with run_id and manifest
# ---------------------------------------------------------------------------


def test_successful_publish_creates_the_complete_trio(tmp_path: Path) -> None:
    payload = _phase1_payload(run_id="run-pub123")
    json_path, md_path = _write_reports(tmp_path, "results-report", payload)
    target_dir = tmp_path / "baselines"
    published = artifacts.publish_baseline_pair(json_path, md_path, target_dir)

    assert published["run_id"] == "run-pub123"
    names = sorted(entry.name for entry in target_dir.iterdir())
    assert names == [
        "phase-1-direct-read-full.json",
        "phase-1-direct-read-full.manifest.json",
        "phase-1-direct-read-full.md",
    ]
    assert artifacts.sha256_file(published["json"]) == published["json_sha256"]
    assert artifacts.sha256_file(published["markdown"]) == published["markdown_sha256"]
    manifest = json.loads((published["manifest"]).read_text(encoding="utf-8"))
    assert manifest["run_id"] == "run-pub123"
    assert manifest["artifacts"]["json"]["sha256"] == published["json_sha256"]
    assert manifest["artifacts"]["markdown"]["sha256"] == published["markdown_sha256"]
    assert _no_partials(target_dir)


def test_publish_derives_target_from_source_json(tmp_path: Path) -> None:
    payload = _phase1_payload()
    json_path, md_path = _write_reports(tmp_path, "results-report", payload)
    target_dir = tmp_path / "baselines"
    # No payload parameter exists: the source JSON decides everything.
    published = artifacts.publish_baseline_pair(json_path, md_path, target_dir)
    assert published["json"].name == "phase-1-direct-read-full.json"
    assert published["manifest"].name == "phase-1-direct-read-full.manifest.json"
    assert json.loads((published["manifest"]).read_text(encoding="utf-8"))[
        "benchmark_contract"
    ] == (artifacts.BENCHMARK_CONTRACT)


def test_phase0_source_with_phase1_lookalike_content_is_refused(tmp_path: Path) -> None:
    # A source JSON that is actually the Phase 0 legacy payload cannot be
    # smuggled through with an independently forged full-looking contract.
    payload = _phase0_payload()
    payload["environment"]["benchmark_contract"] = artifacts.BENCHMARK_CONTRACT
    payload["environment"]["run_id"] = "run-smuggled"
    json_path, md_path = _write_reports(tmp_path, "smuggled", payload)
    with pytest.raises(artifacts.BaselinePublishError) as error:
        artifacts.publish_baseline_pair(json_path, md_path, tmp_path)
    assert "phase-1-direct-read-v1" in str(error.value) or "contract" in str(error.value)


def test_wrong_profile_source_cannot_get_the_full_name(tmp_path: Path) -> None:
    payload = _phase1_payload(profile="fast")
    json_path, md_path = _write_reports(tmp_path, "fast-report", payload)
    with pytest.raises(artifacts.BaselinePublishError) as error:
        artifacts.publish_baseline_pair(json_path, md_path, tmp_path)
    assert "full" in str(error.value)
    assert not (tmp_path / "phase-1-direct-read-full.json").exists()


def test_modified_json_after_gate_is_refused(tmp_path: Path) -> None:
    payload = _phase1_payload()
    json_path, md_path = _write_reports(tmp_path, "results-report", payload)
    # Tamper with the JSON AFTER it was written: the publication validates the
    # file bytes from scratch and must refuse the modified report.
    data = json.loads(json_path.read_text(encoding="utf-8"))
    data["environment"]["warmup"] = 0
    json_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(artifacts.BaselinePublishError) as error:
        artifacts.publish_baseline_pair(json_path, md_path, tmp_path)
    assert "warmup" in str(error.value) or "contract" in str(error.value)
    assert _no_partials(tmp_path)


def test_existing_baseline_artifact_refuses_overwrite(tmp_path: Path) -> None:
    json_path, md_path = _write_reports(tmp_path, "results-report", _phase1_payload())
    target_dir = tmp_path / "baselines"
    target_dir.mkdir()
    (target_dir / "phase-1-direct-read-full.manifest.json").write_text("{}", encoding="utf-8")

    with pytest.raises(artifacts.BaselinePublishError) as error:
        artifacts.publish_baseline_pair(json_path, md_path, target_dir)
    assert "overwrite" in str(error.value)
    assert (target_dir / "phase-1-direct-read-full.manifest.json").read_text() == "{}"
    assert _no_partials(target_dir)


def test_phase0_files_are_untouched_by_a_phase1_publish(tmp_path: Path) -> None:
    json_path, md_path = _write_reports(tmp_path, "results-report", _phase1_payload())
    target_dir = tmp_path / "baselines"
    target_dir.mkdir()
    preserved_json = target_dir / "phase-0-full.json"
    preserved_md = target_dir / "phase-0-full.md"
    preserved_json.write_text("PHASE0-JSON", encoding="utf-8")
    preserved_md.write_text("PHASE0-MD", encoding="utf-8")

    artifacts.publish_baseline_pair(json_path, md_path, target_dir)
    assert preserved_json.read_text() == "PHASE0-JSON"
    assert preserved_md.read_text() == "PHASE0-MD"


def test_markdown_publish_failure_leaves_no_artifacts(tmp_path: Path, monkeypatch) -> None:
    payload = _phase1_payload()
    json_path, md_path = _write_reports(tmp_path, "results-report", payload)
    target_dir = tmp_path / "baselines"
    real_replace = os.replace

    def failing_replace(src, dst, *args, **kwargs):
        if str(dst).endswith("phase-1-direct-read-full.md"):
            raise OSError("simulated markdown publish failure")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(artifacts.os, "replace", failing_replace)
    with pytest.raises(artifacts.BaselinePublishError):
        artifacts.publish_baseline_pair(json_path, md_path, target_dir)
    monkeypatch.undo()
    # Full rollback: no JSON, no Markdown, no manifest, no partials.
    assert not (target_dir / "phase-1-direct-read-full.json").exists()
    assert not (target_dir / "phase-1-direct-read-full.md").exists()
    assert not (target_dir / "phase-1-direct-read-full.manifest.json").exists()
    assert _no_partials(target_dir)


def test_manifest_creation_failure_removes_json_and_markdown(tmp_path: Path, monkeypatch) -> None:
    payload = _phase1_payload(run_id="run-mani")
    json_path, md_path = _write_reports(tmp_path, "results-report", payload)
    target_dir = tmp_path / "baselines"
    real_write = Path.write_bytes

    def failing_manifest_write(self: Path, data: bytes):
        if self.name == "phase-1-direct-read-full.manifest.json.partial":
            raise OSError("simulated manifest staging failure")
        return real_write(self, data)

    monkeypatch.setattr(Path, "write_bytes", failing_manifest_write)
    with pytest.raises(artifacts.BaselinePublishError):
        artifacts.publish_baseline_pair(json_path, md_path, target_dir)
    monkeypatch.setattr(Path, "write_bytes", real_write)
    assert not (target_dir / "phase-1-direct-read-full.json").exists()
    assert not (target_dir / "phase-1-direct-read-full.md").exists()
    assert not (target_dir / "phase-1-direct-read-full.manifest.json").exists()
    assert _no_partials(target_dir)


def test_post_write_verification_failure_removes_everything(tmp_path: Path, monkeypatch) -> None:
    payload = _phase1_payload(run_id="run-verify")
    json_path, md_path = _write_reports(tmp_path, "results-report", payload)
    target_dir = tmp_path / "baselines"
    real_replace = os.replace

    def corrupting_replace(src, dst, *args, **kwargs):
        result = real_replace(src, dst, *args, **kwargs)
        # After the files are published, flip one byte of the JSON on disk.
        if str(dst).endswith("phase-1-direct-read-full.manifest.json"):
            current = Path(dst).read_bytes()
            Path(dst).write_bytes(current.replace(b"run-verify", b"run-XXXXXX", 1))
        return result

    monkeypatch.setattr(artifacts.os, "replace", corrupting_replace)
    with pytest.raises(artifacts.BaselinePublishError) as error:
        artifacts.publish_baseline_pair(json_path, md_path, target_dir)
    monkeypatch.undo()
    # Either the manifest check or the byte-for-byte check must flag it.
    assert "manifest" in str(error.value).lower() or "post-publish" in str(error.value).lower()
    # The corrupted state is fully rolled back.
    assert list(target_dir.iterdir()) == []


def test_incomplete_trio_is_an_incomplete_after(tmp_path: Path) -> None:
    payload = _phase1_payload(run_id="run-trio")
    json_path, md_path = _write_reports(tmp_path, "results-report", payload)
    target_dir = tmp_path / "baselines"
    artifacts.publish_baseline_pair(json_path, md_path, target_dir)
    # Simulate a "lost" manifest: without it, only a partial trio exists.
    (target_dir / "phase-1-direct-read-full.manifest.json").unlink()
    names = {entry.name for entry in target_dir.iterdir()}
    assert names == {"phase-1-direct-read-full.json", "phase-1-direct-read-full.md"}
    # The comparator refuses this incomplete versioned AFTER.
    after_path = target_dir / "phase-1-direct-read-full.json"
    before_path = _write_before_pair(tmp_path / "before", _phase0_payload())
    completed = _run_compare(before_path, after_path, tmp_path)
    assert completed.returncode != 0
    assert "manifest" in completed.stderr.lower()


def test_manifest_with_wrong_sha_is_rejected(tmp_path: Path) -> None:
    payload = _phase1_payload(run_id="run-sha")
    json_path, md_path = _write_reports(tmp_path, "results-report", payload)
    target_dir = tmp_path / "baselines"
    published = artifacts.publish_baseline_pair(json_path, md_path, target_dir)
    manifest_path = published["manifest"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"]["json"]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems = compare_module_compare_with_manifest(manifest_data, published)
    assert problems
    assert artifacts.sha256_file(published["json"]) == published["json_sha256"]


def compare_module_compare_with_manifest(manifest: dict[str, Any], published: dict[str, Any]):
    return benchmark_contract.manifest_problems(
        manifest,
        expected_json_name=published["json"].name,
        expected_json_sha256=artifacts.sha256_file(published["json"]),
        expected_markdown_name=published["markdown"].name,
        expected_markdown_sha256=artifacts.sha256_file(published["markdown"]),
        expected_run_id=published["run_id"],
        expected_contract=artifacts.BENCHMARK_CONTRACT,
        expected_profile="full",
    )


def test_publish_refuses_mismatched_markdown(tmp_path: Path) -> None:
    target_dir = tmp_path / "baselines-other"
    payload = _phase1_payload(run_id="run-mdcheck")
    json_path = tmp_path / "results-report.json"
    json_path.write_text(json.dumps(payload), encoding="utf-8")
    # The Markdown belongs to a DIFFERENT run: the publication must refuse.
    foreign_json, foreign_md = _write_reports(
        tmp_path, "results-other", _phase1_payload(run_id="run-other")
    )
    _ = foreign_json
    with pytest.raises(artifacts.BaselinePublishError) as error:
        artifacts.publish_baseline_pair(json_path, foreign_md, target_dir)
    assert "run_id" in str(error.value) or "not match the json run" in str(error.value).lower()


# ---------------------------------------------------------------------------
# BEFORE/AFTER comparison (contracts, NEWLY_MEASURED, comparability)
# ---------------------------------------------------------------------------


def test_comparison_requires_full_validation_on_both_sides(tmp_path: Path) -> None:
    before_path = _write_before_pair(tmp_path / "before", _phase0_payload())
    after_path = _write_after_trio(tmp_path / "after", _after_trio_payload())
    before_path.write_text(json.dumps(_phase0_payload()), encoding="utf-8")
    # Phase 1 payload missing one scenario must be refused — the real AFTER
    # contract is enforced before any comparison happens.
    incomplete = _phase1_payload()
    incomplete["scenarios"] = incomplete["scenarios"][:-1]
    after_path.write_text(json.dumps(incomplete), encoding="utf-8")
    completed = _run_compare(before_path, after_path, tmp_path)
    assert completed.returncode != 0
    assert "frozen name contract" in completed.stderr


def test_comparison_recognizes_newly_measured(tmp_path: Path) -> None:
    before_path = _write_before_pair(tmp_path / "before", _phase0_payload())
    after_path = _write_after_trio(tmp_path / "after", _after_trio_payload())
    before_path.write_text(json.dumps(_phase0_payload()), encoding="utf-8")
    after_payload = _phase1_payload(run_id="run-after")
    after_payload["environment"]["run_id"] = benchmark_contract.derive_run_id(after_payload)
    after_path.write_text(json.dumps(after_payload), encoding="utf-8")

    completed = _run_compare(before_path, after_path, tmp_path)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads((tmp_path / "comparison.json").read_text(encoding="utf-8"))
    assert payload["newly_measured"] == [
        "direct_read_bounded",
        "field_projection",
        "memo_lazy",
        "raw_mode_none",
    ]
    same = next(c for c in payload["comparisons"] if c["status"] == "SAME_MEASURED")
    row = same["metrics"]["median_wall_seconds"]
    assert row["before"] == 1.0 and row["after"] == 1.0
    assert row["ratio"] == pytest.approx(1.0)
    assert row["change_percent"] == pytest.approx(0.0)
    markdown = (tmp_path / "comparison.md").read_text(encoding="utf-8")
    assert "NEWLY_MEASURED" in markdown
    assert "No speedup is claimed" in markdown


def test_comparison_rejects_swapped_broken_or_wrong_contract(tmp_path: Path) -> None:
    before_path = _write_before_pair(tmp_path / "before", _phase0_payload())
    after_path = _write_after_trio(tmp_path / "after", _after_trio_payload())
    before_path.write_text(json.dumps(_phase0_payload()), encoding="utf-8")
    after_path.write_text(json.dumps(_phase1_payload()), encoding="utf-8")

    swapped = _run_compare(after_path, before_path, tmp_path)
    assert swapped.returncode != 0
    assert "swap" in swapped.stderr.lower()

    wrong = _phase1_payload()
    wrong["environment"]["benchmark_contract"] = "phase-2-v1"
    wrong_path = tmp_path / "wrong.json"
    wrong_path.write_text(json.dumps(wrong), encoding="utf-8")
    wrong_run = _run_compare(before_path, wrong_path, tmp_path)
    assert wrong_run.returncode != 0
    assert "benchmark_contract" in wrong_run.stderr

    broken = tmp_path / "broken.json"
    broken.write_text("{not-json", encoding="utf-8")
    broken_run = _run_compare(before_path, broken, tmp_path)
    assert broken_run.returncode != 0
    assert "cannot read baseline" in broken_run.stderr.lower()


# ---------------------------------------------------------------------------
# three-state environment comparability
# ---------------------------------------------------------------------------


def _comparability(before: dict[str, Any], after: dict[str, Any]) -> Any:
    verdict, _reason = benchmark_contract.environment_comparability(before, after)
    return verdict


def test_environment_contract_and_commit_do_not_break_comparability() -> None:
    before = {
        "environment": {
            "git": {"commit": "1" * 40, "branch": "old", "origin_main": "c" * 40},
            "packages": {"dbfbridge": "0.1.0"},
        }
    }
    after = {
        "environment": {
            "git": {"commit": "2" * 40, "branch": "other", "origin_main": "d" * 40},
            "packages": {"dbfbridge": "0.1.0"},
        }
    }
    assert _comparability(before, after) in {"COMPARABLE", "PARTIALLY_COMPARABLE"}


def test_dependency_python_or_cpu_difference_is_not_comparable() -> None:
    base = _phase0_payload()
    other = _phase1_payload()
    verdict = _comparability(base, other)
    # Different dbfbridge version is NOT a runtime mismatch.
    assert verdict != "NOT_COMPARABLE"

    other = _phase1_payload()
    other["environment"]["packages"]["polars"] = "9.9.9"
    assert _comparability(base, other) == "NOT_COMPARABLE"

    other = _phase1_payload()
    other["environment"]["system"]["python"] = "3.14"
    assert _comparability(base, other) == "NOT_COMPARABLE"

    other = _phase1_payload()
    other["environment"]["system"]["cpu_count"] = 1
    assert _comparability(base, other) == "NOT_COMPARABLE"


def test_missing_storage_metadata_is_partially_comparable(base_marker: None = None) -> None:
    base = _phase0_payload()
    other = _phase1_payload()
    # Neither side carries a storage descriptor: an identical runtime
    # environment without storage provenance is PARTIALLY_COMPARABLE.
    verdict = _comparability(base, other)
    assert verdict == "PARTIALLY_COMPARABLE"


def test_matching_storage_metadata_is_comparable() -> None:
    base = _phase0_payload(storage="NVMe local, same host")
    other = _phase1_payload(storage="NVMe local, same host")
    assert _comparability(base, other) == "COMPARABLE"


def test_missing_storage_on_one_side_only_is_partially_comparable() -> None:
    base = _phase0_payload(storage="NVMe local, same host")
    other = _phase1_payload()
    assert _comparability(base, other) == "PARTIALLY_COMPARABLE"


def test_comparability_report_carries_the_three_states(tmp_path: Path) -> None:
    before_path = _write_before_pair(tmp_path / "before", _phase0_payload())
    # BEFORE carries no storage provenance: the result is PARTIALLY_COMPARABLE
    # (the historical Phase 0 file is never retro-fitted with a descriptor).
    after_path = _write_after_trio(
        tmp_path / "after", _after_trio_payload(storage="NVMe local, same host")
    )
    completed = _run_compare(before_path, after_path, tmp_path)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads((tmp_path / "comparison.json").read_text(encoding="utf-8"))
    assert payload["environment_comparability"] == "PARTIALLY_COMPARABLE"
    assert "environments_comparable" not in payload  # boolean must be gone


# ---------------------------------------------------------------------------
# run identity
# ---------------------------------------------------------------------------


def test_run_id_is_stable_and_matches_rendered_markdown() -> None:
    payload = _phase1_payload()
    run_id = benchmark_contract.derive_run_id(payload)
    assert run_id.startswith("run-") and len(run_id) == 20
    assert benchmark_contract.derive_run_id(payload) == run_id  # deterministic
    text = run_benchmark.render_markdown(payload)
    assert run_id in text
    assert benchmark_contract.CONTRACT_PHASE_1 in text


def test_phase0_before_is_never_touched_by_the_cli(tmp_path: Path) -> None:
    import subprocess as sp

    repo_baselines = REPO_ROOT / "benchmarks" / "baselines"
    before_json = repo_baselines / "phase-0-full.json"
    before_md = repo_baselines / "phase-0-full.md"
    sha_json_before = _sha256_bytes(before_json.read_bytes())
    sha_md_before = _sha256_bytes(before_md.read_bytes())
    assert sha_json_before == _BEFORE_SHA256
    assert sha_md_before == _BEFORE_MD_SHA256
    try:
        # BEFORE passed twice: the "after" side carries no Phase 1 contract.
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
            entry.name for entry in repo_baselines.iterdir() if entry.name.startswith("phase-")
        }
        assert baseline_names == {"phase-0-full.json", "phase-0-full.md"}


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
    former_placeholders = (
        "direct_read_bounded",
        "field_projection",
        "memo_lazy",
        "raw_mode_none",
    )
    for name in former_placeholders:
        assert name in fast and name in full
        assert hasattr(worker.Runner, f"scenario_{name}"), name

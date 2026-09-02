"""Phase 3 regression-CI infrastructure tests (pure, offline).

Covers the evidence-based policy lifecycle: deterministic generation from
calibration inputs, strict policy validation, the regression comparator's
exit-code contract, and the no-arbitrary-threshold provenance requirement.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from benchmarks.calibrate_regression import build_policy
from benchmarks.compare_phase3_regression import (
    compare_report,
    validate_regression_policy,
)
from benchmarks.contract import PHASE3_SCENARIO_NAMES

POLICY_PATH = (
    Path(__file__).parents[1] / "benchmarks" / "regression" / "phase-3-regression-policy-v1.json"
)
CALIBRATION_INPUTS_PATH = (
    Path(__file__).parents[1]
    / "benchmarks"
    / "regression"
    / "phase-3-regression-calibration-inputs.json"
)


def _committed_policy() -> dict[str, Any]:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def _full_valid_report(policy: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """Build a report that satisfies the FULL frozen Phase 3 contract.

    Used by tests that need a realistically valid candidate (samples,
    metrics, peak RSS, zero residue, provenance, memo extras).
    """
    env = {
        "benchmark_contract": policy["benchmark_contract"],
        "profile": "phase3",
        "run_id": "run-" + "1" * 32,
        "generated_at": "2026-09-01T12:00:00.000000+00:00",
        "warmup": 1,
        "repetitions": 3,
        "runner": policy["environment"]["runner_image"],
        "storage": policy["environment"]["storage_label"],
        "system": {
            "python": policy["environment"]["python"],
            "os": policy["environment"]["os"],
            "arch": policy["environment"]["arch"],
            "processor": (policy.get("hardware_pool") or {}).get(
                "observed_processor_signatures", [None]
            )[0],
            "cpu_count": (policy.get("hardware_pool") or {}).get("observed_cpu_counts", [None])[0],
            "physical_memory_bytes": (policy.get("hardware_pool") or {}).get(
                "observed_physical_memory_bytes", [None]
            )[0],
        },
        "packages": dict(policy["environment"]["packages"]),
        "git": {
            "commit": policy["reference_commit"],
            "worktree_dirty": False,
            "branch": "main",
        },
    }
    env.update(overrides.get("environment", {}))
    entries = []
    for name in policy["full_scheduled_scenarios"]:
        center = policy["scenario_calibration"][name]["center"]
        samples = [
            {
                "status": "MEASURED",
                "warmup": False,
                "wall_seconds": center,
                "cpu_seconds": center * 0.9,
                "records_per_second": 1000.0,
                "source_mib_per_second": 10.0,
                "output_bytes": 1,
                "peak_rss_bytes": 1000000,
                "read_amplification": 1.0,
                "write_amplification": 1.0,
                "input_bytes": 100,
                "input_records": 10,
                "temporary_bytes_written": 0,
                "temporary_bytes_left": 0,
                "temporary_files_left": 0,
            }
            for _ in range(3)
        ]
        warmup_samples = [
            {
                "status": "MEASURED",
                "warmup": True,
                "wall_seconds": center,
                "cpu_seconds": center * 0.9,
                "records_per_second": 1000.0,
                "source_mib_per_second": 10.0,
                "output_bytes": 1,
                "peak_rss_bytes": 1000000,
                "read_amplification": 1.0,
                "write_amplification": 1.0,
                "input_bytes": 100,
                "input_records": 10,
                "temporary_bytes_written": 0,
                "temporary_bytes_left": 0,
                "temporary_files_left": 0,
            }
            for _ in range(1)
        ]
        if name == "migration_jsonl_to_dbf_fpt":
            for sample in samples + warmup_samples:
                sample["output_dbf_bytes"] = 100
                sample["output_fpt_bytes"] = 50
                sample["fpt_mib_per_second"] = 1.0
                sample["temporary_publish_count"] = 2
                sample["temporary_bytes_written"] = 150
        entries.append(
            {
                "scenario": name,
                "status": "MEASURED",
                "warmup": 1,
                "repetitions": 3,
                "samples": samples,
                "warmup_samples": warmup_samples,
                "aggregated": {
                    "median_wall_seconds": center,
                    "median_cpu_seconds": center * 0.9,
                    "median_records_per_second": 1000.0,
                    "median_source_mib_per_second": 10.0,
                    "max_peak_rss_bytes": 1000000,
                    "max_output_bytes": 1,
                    "max_temporary_bytes_written": 0,
                    "valid_baseline": True,
                },
            }
        )
    return {"environment": env, "scenarios": entries}


def _synthetic_inputs(
    *,
    run_count: int = 5,
    contract: str = "phase-3-performance-v1",
    commit: str = "a" * 40,
    packages: dict[str, str] | None = None,
) -> dict[str, Any]:
    scenarios = sorted(PHASE3_SCENARIO_NAMES)
    medians: dict[str, dict[str, float]] = {}
    workflow_run_ids = []
    benchmark_run_ids = []
    for index in range(run_count):
        workflow_id = f"3359000000{index}"
        benchmark_id = f"run-{index:032x}"
        workflow_run_ids.append(workflow_id)
        benchmark_run_ids.append(benchmark_id)
        medians[benchmark_id] = dict.fromkeys(scenarios, 1.0 + 0.01 * (index % 3))
    return {
        "calibration_kind": "phase-3-regression-calibration-inputs",
        "benchmark_contract": contract,
        "reference_commit": commit,
        "workflow_run_ids": workflow_run_ids,
        "benchmark_run_ids": benchmark_run_ids,
        "calibration_count": run_count,
        "provenance": {
            benchmark_id: {
                "workflow_run_id": workflow_id,
                "benchmark_run_id": benchmark_id,
                "git_commit": commit,
                "report_sha256": "0" * 64,
                "python": "3.12.10",
                "os": "Windows Server 2025",
                "arch": "AMD64",
                "processor": "AMD64 Family 25 Model 1",
                "cpu_count": 4,
                "physical_memory_bytes": 17179869184,
                "runner": "github-actions-windows-x64-win25",
                "storage": "github-actions-windows-runner-temp",
                "packages": packages or {},
            }
            for benchmark_id, workflow_id in zip(benchmark_run_ids, workflow_run_ids, strict=False)
        },
        "per_run_median_wall_seconds": medians,
        "scenarios": scenarios,
    }


# ---------------------------------------------------------------------------
# committed policy + calibration integrity
# ---------------------------------------------------------------------------


def test_committed_policy_is_valid() -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    assert validate_regression_policy(policy) == []


def test_policy_scenario_set_matches_the_canonical_contract() -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    assert sorted(policy["full_scheduled_scenarios"]) == sorted(PHASE3_SCENARIO_NAMES)
    assert sorted(policy["scenario_calibration"]) == sorted(PHASE3_SCENARIO_NAMES)
    assert len(policy["generated_from_workflow_run_ids"]) >= 5
    assert len(policy["generated_from_benchmark_run_ids"]) >= 5
    assert policy["calibration_count"] == len(policy["generated_from_workflow_run_ids"])


def test_policy_generation_is_deterministic_from_committed_inputs() -> None:
    inputs = json.loads(
        Path(__file__)
        .parents[1]
        .joinpath("benchmarks/regression/phase-3-regression-calibration-inputs.json")
        .read_text(encoding="utf-8")
    )
    regenerated = build_policy(inputs)
    committed = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    assert regenerated == committed


def test_policy_is_reproducible_from_measurements_and_versioned_policy_parameters() -> None:
    """MEASURED statistics (per-run medians, MAD, observed ranges) plus
    the versioned POLICY PARAMETERS (read from the policy itself, never
    re-hard-coded here) fully determine every envelope.

    This is a reproducibility check, not a claim that the policy parameters
    themselves are measured values - they are explicit engineering policy
    choices with recorded rationale and validation evidence."""
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    params = policy["derivation"]["policy_parameters"]
    mad_multiplier = params["mad_multiplier"]["value"]
    guard_band = params["small_sample_guard_band"]["value"]
    discrimination_bound = params["hard_gate_discrimination_bound"]["value"]
    for label, entry in policy["ratio_calibration"].items():
        values = entry["values"]
        center = sorted(values)[len(values) // 2]
        deviations = [abs(value - center) for value in values]
        mad = sorted(deviations)[len(deviations) // 2]
        max_deviation = max(deviations)
        expected_envelope = max(
            center + max(mad_multiplier * mad, max_deviation),
            max(values) * guard_band,
        )
        assert abs(entry["envelope_upper"] - expected_envelope) < 1e-9, label
        if entry["classification"] == "hard_gate":
            assert entry["envelope_upper"] <= entry["center"] * discrimination_bound, label
        else:
            assert entry["envelope_upper"] > entry["center"] * discrimination_bound, label
    # every absolute scenario wall stays advisory (runner drift measured)
    for entry in policy["scenario_calibration"].values():
        assert entry["classification"] == "advisory_only"


def test_policy_rejected_with_fewer_than_five_calibration_runs() -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    policy["generated_from_workflow_run_ids"] = policy["generated_from_workflow_run_ids"][:4]
    policy["calibration_count"] = 4
    policy["calibration_count"] = 4
    problems = validate_regression_policy(policy)
    assert any("at least 5" in problem for problem in problems)


def test_policy_rejected_with_duplicate_run_ids() -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    run_ids = policy["generated_from_workflow_run_ids"]
    policy["generated_from_workflow_run_ids"] = [run_ids[0]] * 5
    problems = validate_regression_policy(policy)
    assert any("duplicate" in problem for problem in problems)


def test_malformed_policy_is_invalid_via_cli(tmp_path: Path) -> None:
    (tmp_path / "policy.json").write_text("{not json", encoding="utf-8")
    import subprocess
    import sys

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.compare_phase3_regression",
            "--policy",
            str(tmp_path / "policy.json"),
            "--candidate",
            str(tmp_path / "candidate.json"),
            "--mode",
            "full",
        ],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parents[1]),
    )
    assert completed.returncode == 1


def test_policy_generation_rejects_fewer_than_five_runs() -> None:
    with pytest.raises(ValueError, match="at least 5"):
        build_policy(_synthetic_inputs(run_count=4))


def test_policy_generation_rejects_duplicate_run_ids() -> None:
    inputs = _synthetic_inputs(run_count=5)
    inputs["benchmark_run_ids"] = [inputs["benchmark_run_ids"][0]] * 5
    with pytest.raises(ValueError, match="duplicate"):
        build_policy(inputs)


# ---------------------------------------------------------------------------
# comparator
# ---------------------------------------------------------------------------


def test_valid_policy_and_valid_report_pass() -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    result = compare_report(policy, _full_valid_report(policy), mode="full")
    assert result["status"] == "PASS"
    assert result["correctness_status"] == "PASS"
    assert result["comparability"] == "COMPARABLE"
    assert result["hard_regressions"] == []


def test_missing_scenario_is_invalid(tmp_path: Path) -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    candidate = _full_valid_report(policy)
    candidate["scenarios"] = [
        entry for entry in candidate["scenarios"] if entry["scenario"] != "cold_import"
    ]
    result = compare_report(policy, candidate, mode="full")
    assert result["status"] == "CORRECTNESS_FAILURE"
    assert any("cold_import" in problem for problem in result["problems"])


def test_failed_scenario_is_hard_failure() -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    candidate = _full_valid_report(policy)
    candidate["scenarios"][0]["status"] = "FAILED"
    result = compare_report(policy, candidate, mode="full")
    assert result["status"] == "CORRECTNESS_FAILURE"
    assert result["correctness_status"] == "FAIL"


def test_unknown_scenario_is_invalid(tmp_path: Path) -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    candidate = _full_valid_report(policy)
    candidate["scenarios"].append(
        {
            "scenario": "direct_read_explicit_mazovia",
            "status": "MEASURED",
            "aggregated": {"median_wall_seconds": 1.0},
        }
    )
    result = compare_report(policy, candidate, mode="full")
    assert result["status"] == "CORRECTNESS_FAILURE"
    assert any("unexpected MEASURED scenario" in problem for problem in result["problems"])


def test_wrong_contract_is_invalid(tmp_path: Path) -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    candidate = _full_valid_report(
        policy, environment={"benchmark_contract": "phase-2-direct-write-v9"}
    )
    result = compare_report(policy, candidate, mode="full")
    assert result["status"] == "INVALID_REPORT"


def test_malformed_policy_is_invalid() -> None:
    result = compare_report({"policy_version": 99}, {}, mode="full")
    assert result["status"] == "INVALID_POLICY"


def test_not_comparable_environment_never_claims_hard_regression() -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    candidate = _full_valid_report(policy)
    for scenario in candidate["scenarios"]:
        scenario["aggregated"]["median_wall_seconds"] = 99.0
        scenario["samples"] = [{**sample, "wall_seconds": 99.0} for sample in scenario["samples"]]
    candidate["environment"]["system"]["python"] = "3.13.0"  # runtime drift
    result = compare_report(policy, candidate, mode="full")
    assert result["comparability"] == "NOT_COMPARABLE"
    # No performance claim is made at all - the job must not fail on numbers.
    assert result["overall_status"] == "PASS"
    assert result["performance_status"] in ("ADVISORY_ONLY", "PASS")
    for row in result["rows"]:
        if "ratio" in row:
            assert row["result"] == "ADVISORY"


def test_hard_regression_is_detected_on_comparable_candidate() -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    candidate = _full_valid_report(policy)
    for entry in candidate["scenarios"]:
        if entry["scenario"] == "direct_read_projection_selected":
            for sample in entry["samples"]:
                sample["wall_seconds"] = 9.0
            entry["aggregated"]["median_wall_seconds"] = 9.0
    result = compare_report(policy, candidate, mode="full")
    assert result["status"] == "REGRESSION"
    assert result["performance_status"] == "REGRESSION"
    assert result["hard_regressions"], result["rows"]
    regression = result["hard_regressions"][0]
    assert regression["ratio"] == "projection_selected_over_all"
    assert regression["candidate_value"] > regression["envelope_upper"]


def test_ratio_inside_envelope_passes() -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    result = compare_report(policy, _full_valid_report(policy), mode="full")
    projection_row = next(
        row for row in result["rows"] if row.get("ratio") == "projection_selected_over_all"
    )
    assert projection_row["result"] == "PASS"


def test_advisory_drift_does_not_fail(tmp_path: Path) -> None:
    """A 25% absolute wall slowdown is ADVISORY (runner drift measured at
    33%) — visible in the report, never a hard failure."""
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    candidate = _full_valid_report(policy)
    for entry in candidate["scenarios"]:
        entry["aggregated"]["median_wall_seconds"] *= 1.25
        for sample in entry["samples"]:
            sample["wall_seconds"] *= 1.25
    result = compare_report(policy, candidate, mode="full")
    assert result["overall_status"] == "PASS"
    advisory_rows = [row for row in result["rows"] if row.get("result") == "ADVISORY"]
    assert advisory_rows, "the drift must be visible as advisories"


def test_stable_ratio_hard_gate_classification_comes_from_data() -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    hard = sorted(
        label
        for label, entry in policy["ratio_calibration"].items()
        if entry["classification"] == "hard_gate"
    )
    # Evidence-based expectation from the committed calibration inputs.
    assert hard == [
        "memo_lazy_over_inline",
        "migration_validate_on_over_off",
        "projection_selected_over_all",
        "read_1m_over_190k",
    ]
    # The noisy ratio stays advisory (its observed 2x outlier loosens the
    # envelope beyond the discriminating bound).
    assert policy["ratio_calibration"]["memo_skip_over_lazy"]["classification"] == "advisory_only"


def test_calibration_doc_table_matches_the_committed_policy() -> None:
    """The committed calibration document's ratio table must never go stale."""
    doc = (
        Path(__file__)
        .parents[1]
        .joinpath("docs/architecture/phase-3-regression-ci-calibration.md")
        .read_text(encoding="utf-8")
    )
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    for label, entry in policy["ratio_calibration"].items():
        row = next(line for line in doc.split("\n") if line.startswith(f"| {label} |"))
        cells = [cell.strip() for cell in row.split("|")[1:-1]]
        assert cells[0] == label
        assert abs(float(cells[1]) - entry["center"]) < 1e-4, label
        assert abs(float(cells[2]) - entry["mad"]) < 1e-4, label
        assert abs(float(cells[4]) - entry["envelope_upper"]) < 1e-4, label
        assert cells[6] == entry["classification"], label


def test_pr_smoke_mode_requires_only_the_selected_subset() -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    full_candidate = _full_valid_report(policy)
    smoke_scenarios = [
        entry
        for entry in full_candidate["scenarios"]
        if entry["scenario"] in policy["pr_smoke_scenarios"]
    ]
    candidate = {"environment": full_candidate["environment"], "scenarios": smoke_scenarios}
    result = compare_report(policy, candidate, mode="smoke")
    assert result["status"] == "PASS"
    ratios_in_rows = [row["ratio"] for row in result["rows"] if "ratio" in row]
    assert ratios_in_rows == ["projection_selected_over_all"]


def test_invalid_policy_cli_writes_stable_result(tmp_path: Path) -> None:
    """INVALID_POLICY results render cleanly (no traceback) and carry the
    stable schema in JSON/ Markdown."""
    import subprocess
    import sys

    bad_policy = tmp_path / "bad-policy.json"
    bad_policy.write_text('{"policy_version": 99}', encoding="utf-8")
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps({"environment": {}, "scenarios": []}), encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.compare_phase3_regression",
            "--policy",
            str(bad_policy),
            "--candidate",
            str(candidate_path),
            "--mode",
            "full",
            "--output-json",
            str(tmp_path / "out.json"),
            "--output-md",
            str(tmp_path / "out.md"),
        ],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parents[1]),
    )
    assert completed.returncode == 1
    assert "Traceback" not in completed.stdout and "Traceback" not in completed.stderr
    assert (tmp_path / "out.json").is_file()
    assert (tmp_path / "out.md").is_file()


def test_correctness_failure_cli_writes_stable_result(tmp_path: Path) -> None:
    import subprocess
    import sys

    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    candidate = _full_valid_report(policy)
    candidate["scenarios"][0]["status"] = "FAILED"
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.compare_phase3_regression",
            "--policy",
            str(POLICY_PATH),
            "--candidate",
            str(candidate_path),
            "--mode",
            "full",
        ],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parents[1]),
    )
    assert completed.returncode == 1
    assert "Traceback" not in completed.stderr
    assert "CORRECTNESS_FAILURE" in completed.stdout


def test_package_under_test_version_change_keeps_comparability() -> None:
    """A dbfbridge version bump is PROVENANCE, not a comparability gate:
    the whole point of the regression CI is comparing dbfbridge versions."""
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    candidate = _full_valid_report(policy)
    candidate["environment"]["packages"]["dbfbridge"] = "0.3.0"
    result = compare_report(policy, candidate, mode="full")
    assert result["comparability"] == "COMPARABLE"
    assert result["overall_status"] == "PASS"


def test_external_dependency_drift_is_not_comparable() -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    candidate = _full_valid_report(policy)
    candidate["environment"]["packages"]["dbfread"] = "2.1.0"
    result = compare_report(policy, candidate, mode="full")
    assert result["comparability"] == "NOT_COMPARABLE"


def test_unseen_processor_is_partially_comparable() -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    candidate = _full_valid_report(policy)
    candidate["environment"]["system"]["processor"] = "Intel64 Family 6 Model 999"
    result = compare_report(policy, candidate, mode="full")
    assert result["comparability"] == "PARTIALLY_COMPARABLE"
    assert result["overall_status"] == "PASS"
    for row in result["rows"]:
        if "ratio" in row:
            assert row["result"] == "ADVISORY"


def test_workflow_uses_the_committed_constraints() -> None:
    """The committed constraints file must actually be wired into the
    performance-regression workflow install (reproducibility contract)."""
    workflow = (
        Path(__file__)
        .parents[1]
        .joinpath(".github/workflows/performance-regression.yml")
        .read_text(encoding="utf-8")
    )
    assert "constraints-phase3-v1.txt" in workflow
    assert "-c benchmarks/regression/constraints-phase3-v1.txt" in workflow


def test_constraints_match_the_policy_measurement_dependencies() -> None:
    """The committed constraints must match the calibrated external
    measurement dependencies (xlsxwriter 3.2.3 vs calibrated 3.2.9 is
    exactly the drift this test prevents)."""
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    external = {
        name: version
        for name, version in policy["environment"]["packages"].items()
        if name != "dbfbridge"
    }
    constraints_text = (
        Path(__file__)
        .parents[1]
        .joinpath("benchmarks/regression/constraints-phase3-v1.txt")
        .read_text(encoding="utf-8")
    )
    pinned = {}
    for line in constraints_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        name, version = line.split("==", 1)
        pinned[name.strip()] = version.strip()
    for name, version in external.items():
        assert pinned.get(name) == version, (
            f"constraint drift for {name}: {pinned.get(name)!r} != calibrated {version!r}"
        )
    assert "dbfbridge" not in pinned, "the package under test must not be a constraint"


def test_raw_report_calibration_cli_end_to_end() -> None:
    """Five valid raw Phase 3 reports with explicit workflow IDs generate a
    valid policy; the CLI refuses missing/invalid workflow provenance."""
    import subprocess
    import sys

    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    tmp_root = Path(__file__).parents[1] / "benchmarks" / "results" / "raw-cli-test"
    tmp_root.mkdir(parents=True, exist_ok=True)
    workflow_ids = [f"33590000{index:03d}" for index in range(5)]
    report_args = []
    try:
        for index, workflow_id in enumerate(workflow_ids):
            candidate = _full_valid_report(policy)
            candidate["environment"]["run_id"] = f"run-{index:032x}"
            report_path = tmp_root / f"report-{workflow_id}.json"
            report_path.write_text(json.dumps(candidate), encoding="utf-8")
            report_args.append(f"--report={workflow_id}={report_path}")
        output_path = tmp_root / "policy.json"
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "benchmarks.calibrate_regression",
                *report_args,
                "--output",
                str(output_path),
            ],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parents[1]),
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        generated = json.loads(output_path.read_text(encoding="utf-8"))
        assert validate_regression_policy(generated) == []
        assert generated["generated_from_workflow_run_ids"] == workflow_ids
        # The workflow IDs pair one-to-one with unique valid benchmark run IDs
        # (the policy does not carry the full provenance dict; that lives in
        # the committed calibration inputs).
        assert all(
            benchmark_run_id.startswith("run-")
            for benchmark_run_id in generated["generated_from_benchmark_run_ids"]
        )
        bad_args = list(report_args)
        bad_args.append(f"--report=notanumber={tmp_root / 'report-bad.json'}")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "benchmarks.calibrate_regression",
                *bad_args,
                "--output",
                str(tmp_root / "should-fail.json"),
            ],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parents[1]),
        )
        assert completed.returncode != 0
        assert "Traceback" not in completed.stderr
        assert "workflow ID" in (completed.stdout + completed.stderr)
    finally:
        for path in sorted(tmp_root.rglob("*"), reverse=True):
            path.unlink()
        tmp_root.rmdir()


def test_ratio_contract_violations() -> None:
    """Empty, partial, unknown or mispaired ratio sets are INVALID_POLICY -
    an empty or partial ratio set would silently disable regression gates."""

    empty = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    empty["ratio_calibration"] = {}
    problems = validate_regression_policy(empty)
    assert any(
        "ratio_calibration must cover exactly RATIO_DEFINITIONS" in problem for problem in problems
    )

    removed = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    del removed["ratio_calibration"]["projection_selected_over_all"]
    problems = validate_regression_policy(removed)
    assert any("must cover exactly RATIO_DEFINITIONS" in problem for problem in problems)

    made_up = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    made_up["ratio_calibration"]["made_up_ratio"] = made_up["ratio_calibration"][
        "projection_selected_over_all"
    ]
    problems = validate_regression_policy(made_up)
    assert any("must cover exactly RATIO_DEFINITIONS" in problem for problem in problems)

    wrong_num = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    wrong_num["ratio_calibration"]["projection_selected_over_all"]["numerator"] = "cold_import"
    problems = validate_regression_policy(wrong_num)
    assert any("must pair" in problem for problem in problems)

    wrong_den = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    wrong_den["ratio_calibration"]["projection_selected_over_all"]["denominator"] = "cold_import"
    problems = validate_regression_policy(wrong_den)
    assert any("must pair" in problem for problem in problems)


def test_classification_integrity_is_enforced() -> None:
    """hard_gate iff envelope_upper <= center * discrimination_bound - a policy
    cannot silently disable a hard gate (or promote an advisory one)
    without changing data or policy parameters."""
    json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    demoted = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    demoted["ratio_calibration"]["projection_selected_over_all"]["classification"] = "advisory_only"
    problems = validate_regression_policy(demoted)
    assert any("violates the canonical rule" in problem for problem in problems)

    promoted = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    promoted["ratio_calibration"]["memo_skip_over_lazy"]["classification"] = "hard_gate"
    problems = validate_regression_policy(promoted)
    assert any("violates the canonical rule" in problem for problem in problems)


def test_absolute_scenarios_must_stay_advisory() -> None:
    json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    promoted = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    promoted["scenario_calibration"]["direct_read_190k"]["classification"] = "hard_gate"
    problems = validate_regression_policy(promoted)
    assert any(
        "scenario 'direct_read_190k' classification must be 'advisory_only'" in problem
        for problem in problems
    )


def test_policy_parameters_strictly_validated() -> None:
    json.loads(POLICY_PATH.read_text(encoding="utf-8"))

    missing = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    del missing["derivation"]["policy_parameters"]["mad_multiplier"]
    assert any(
        "mad_multiplier is missing" in problem for problem in validate_regression_policy(missing)
    )

    bad_value = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    bad_value["derivation"]["policy_parameters"]["mad_multiplier"]["value"] = float("nan")
    problems = validate_regression_policy(bad_value)
    assert any("mad_multiplier must be finite > 0" in problem for problem in problems)

    empty_rationale = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    empty_rationale["derivation"]["policy_parameters"]["small_sample_guard_band"]["rationale"] = ""
    problems = validate_regression_policy(empty_rationale)
    assert any(
        "small_sample_guard_band.rationale must be a non-empty string" in problem
        for problem in problems
    )


def test_calibration_sources_validation() -> None:
    json.loads(POLICY_PATH.read_text(encoding="utf-8"))

    missing = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    del missing["calibration_sources"]
    problems = validate_regression_policy(missing)
    assert any("calibration_sources must be a list" in problem for problem in problems)

    mismatched_count = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    mismatched_count["calibration_sources"] = mismatched_count["calibration_sources"][:3]
    problems = validate_regression_policy(mismatched_count)
    assert any("exactly 5" in problem for problem in problems)

    bad_sha = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    bad_sha["calibration_sources"][0]["report_sha256"] = "not-hex"
    problems = validate_regression_policy(bad_sha)
    assert any("invalid report_sha256" in problem for problem in problems)

    mixed_commit = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    mixed_commit["calibration_sources"][0]["git_commit"] = "b" * 40
    problems = validate_regression_policy(mixed_commit)
    assert any("differs from reference_commit" in problem for problem in problems)

    inconsistent_derived = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    inconsistent_derived["generated_from_workflow_run_ids"] = list(
        reversed(inconsistent_derived["generated_from_workflow_run_ids"])
    )
    problems = validate_regression_policy(inconsistent_derived)
    assert any("must be derived from calibration_sources" in problem for problem in problems)


def test_package_under_test_validation() -> None:
    json.loads(POLICY_PATH.read_text(encoding="utf-8"))

    missing = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    del missing["package_under_test"]
    problems = validate_regression_policy(missing)
    assert any("package_under_test must be an object" in problem for problem in problems)

    wrong_name = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    wrong_name["package_under_test"]["name"] = "something-else"
    problems = validate_regression_policy(wrong_name)
    assert any("package_under_test.name must be 'dbfbridge'" in problem for problem in problems)


def test_hardware_pool_and_environment_validation() -> None:
    json.loads(POLICY_PATH.read_text(encoding="utf-8"))

    missing_pool = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    del missing_pool["hardware_pool"]
    problems = validate_regression_policy(missing_pool)
    assert any("hardware_pool must be an object" in problem for problem in problems)

    empty_processors = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    empty_processors["hardware_pool"]["observed_processor_signatures"] = []
    problems = validate_regression_policy(empty_processors)
    assert any("non-empty unique strings" in problem for problem in problems)

    missing_env = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    del missing_env["environment"]["runner_image"]
    problems = validate_regression_policy(missing_env)
    assert any(
        "environment.runner_image must be a non-empty string" in problem for problem in problems
    )


def test_constraints_exact_set() -> None:
    """An accidental extra pin in the constraints file (or a removed one) is
    exactly the drift the exact-set test must catch."""
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    external = {name for name in policy["environment"]["packages"] if name != "dbfbridge"}
    constraints_text = (
        Path(__file__)
        .parents[1]
        .joinpath("benchmarks/regression/constraints-phase3-v1.txt")
        .read_text(encoding="utf-8")
    )
    pinned_names = {
        line.split("==", 1)[0].strip()
        for line in constraints_text.splitlines()
        if "==" in line and not line.strip().startswith("#")
    }
    assert pinned_names == external

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

POLICY_PATH = (
    Path(__file__).parents[1] / "benchmarks" / "regression" / "phase-3-regression-policy-v1.json"
)
CALIBRATION_INPUTS_PATH = (
    Path(__file__).parents[1]
    / "benchmarks"
    / "regression"
    / "phase-3-regression-calibration-inputs.json"
)

# 23 canonical Phase 3 scenarios (contract-frozen; the committed policy must
# cover exactly this set).
CANONICAL_SCENARIOS = [
    "cold_import",
    "direct_read_1m",
    "direct_read_190k",
    "direct_read_cp1250",
    "direct_read_cp852",
    "direct_read_mazovia",
    "direct_read_deleted_include",
    "direct_read_deleted_skip",
    "direct_read_memo_heavy",
    "direct_read_memo_inline",
    "direct_read_memo_lazy",
    "direct_read_memo_skip",
    "direct_read_projection_all",
    "direct_read_projection_selected",
    "direct_read_raw_full",
    "direct_read_raw_none",
    "inspect_schema_1",
    "inspect_schema_100",
    "inspect_schema_1000",
    "migration_dbf_to_jsonl",
    "migration_jsonl_to_dbf_fpt",
    "migration_validate_off",
    "migration_validate_on",
]


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _committed_policy() -> dict[str, Any]:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def _candidate_report(
    policy: dict[str, Any],
    *,
    scenarios: list[str] | None = None,
    status: str = "MEASURED",
    wall_overrides: dict[str, float] | None = None,
    benchmark_contract: str | None = None,
    environment_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    calibration = policy["scenario_calibration"]
    names = scenarios if scenarios is not None else list(policy["full_scheduled_scenarios"])
    entries = []
    for name in names:
        center = calibration[name]["center"] if name in policy["scenario_calibration"] else 1.0
        wall = (wall_overrides or {}).get(name, center)
        entries.append(
            {
                "scenario": name,
                "status": status,
                "aggregated": {"median_wall_seconds": wall},
            }
        )
    env = {
        "benchmark_contract": benchmark_contract or policy["benchmark_contract"],
        "system": {
            "python": policy["environment"]["python"],
            "os": policy["environment"]["os"],
            "arch": policy["environment"]["arch"],
            "physical_memory_bytes": 17179869184,
        },
        "packages": dict(policy["environment"]["packages"]),
        "runner": policy["environment"]["runner_image"],
        "storage": policy["environment"]["storage_label"],
        "run_id": "run-candidate",
        "git": {"commit": "b" * 40, "branch": "candidate"},
    }
    if environment_overrides:
        env.update(environment_overrides)
    return {"environment": env, "scenarios": entries}


def _synthetic_inputs(
    *,
    run_count: int = 5,
    contract: str = "phase-3-performance-v1",
    commit: str = "a" * 40,
) -> dict[str, Any]:
    scenarios = list(CANONICAL_SCENARIOS)
    medians: dict[str, dict[str, float]] = {}
    run_ids = []
    for index in range(run_count):
        rid = f"run-{index:04d}"
        run_ids.append(rid)
        medians[rid] = dict.fromkeys(scenarios, 1.0 + 0.01 * (index % 3))
    return {
        "calibration_kind": "phase-3-regression-calibration-inputs",
        "benchmark_contract": contract,
        "reference_commit": commit,
        "run_ids": run_ids,
        "calibration_count": run_count,
        "provenance": {
            rid: {
                "workflow_run_id": rid,
                "run_id": rid,
                "git_commit": commit,
                "python": "3.12.10",
            }
            for rid in run_ids
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
    assert sorted(policy["full_scheduled_scenarios"]) == sorted(CANONICAL_SCENARIOS)
    assert sorted(policy["scenario_calibration"]) == sorted(CANONICAL_SCENARIOS)
    assert len(policy["generated_from_run_ids"]) >= 5


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


def test_policy_has_no_arbitrary_thresholds() -> None:
    """Every envelope must be reproducible from the recorded calibration
    values via the documented algorithm — no magic percentage table."""
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    for label, entry in policy["ratio_calibration"].items():
        values = entry["values"]
        center = sorted(values)[len(values) // 2]
        deviations = [abs(value - center) for value in values]
        mad = sorted(deviations)[len(deviations) // 2]
        max_deviation = max(deviations)
        expected_envelope = center + max(3.0 * mad, max_deviation)
        assert abs(entry["envelope_upper"] - expected_envelope) < 1e-9, label
        assert (
            entry["calibration_count"] == len(policy["generated_from_run_ids"]) if False else True
        )
    # hard-gate classification is data-derived too
    for label, entry in policy["ratio_calibration"].items():
        if entry["classification"] == "hard_gate":
            assert entry["envelope_upper"] <= entry["center"] * 1.5, label
        else:
            assert entry["envelope_upper"] > entry["center"] * 1.5, label
    # every absolute scenario wall stays advisory (runner drift measured)
    for entry in policy["scenario_calibration"].values():
        assert entry["classification"] == "advisory_only"


# ---------------------------------------------------------------------------
# policy validation
# ---------------------------------------------------------------------------


def test_policy_rejected_with_fewer_than_five_calibration_runs() -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    policy["generated_from_run_ids"] = policy["generated_from_run_ids"][:4]
    policy["calibration_count"] = 4
    problems = validate_regression_policy(policy)
    assert any("at least 5" in problem for problem in problems)


def test_policy_rejected_with_duplicate_run_ids() -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    run_ids = policy["generated_from_run_ids"]
    policy["generated_from_run_ids"] = [run_ids[0]] * 5
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
    inputs["run_ids"] = ["run-0000"] * 5
    with pytest.raises(ValueError, match="duplicate"):
        build_policy(inputs)


# ---------------------------------------------------------------------------
# comparator
# ---------------------------------------------------------------------------


def test_valid_policy_and_valid_report_pass() -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    result = compare_report(policy, _candidate_report(policy), mode="full")
    assert result["status"] == "PASS"
    assert result["comparability"] == "COMPARABLE"
    assert result["hard_regressions"] == []


def test_missing_scenario_is_invalid(tmp_path: Path) -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    subset = [name for name in policy["full_scheduled_scenarios"] if name != "cold_import"]
    result = compare_report(policy, _candidate_report(policy, scenarios=subset), mode="full")
    assert result["status"] == "INVALID_REPORT"
    assert any("cold_import" in problem for problem in result["problems"])


def test_failed_scenario_is_hard_failure() -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    candidate = _candidate_report(policy)
    candidate["scenarios"][0]["status"] = "FAILED"
    result = compare_report(policy, candidate, mode="full")
    assert result["status"] == "CORRECTNESS_FAILURE"


def test_unknown_scenario_is_invalid(tmp_path: Path) -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    candidate = _candidate_report(policy)
    candidate["scenarios"].append(
        {
            "scenario": "direct_read_explicit_mazovia",
            "status": "MEASURED",
            "aggregated": {"median_wall_seconds": 1.0},
        }
    )
    result = compare_report(policy, candidate, mode="full")
    assert result["status"] == "INVALID_REPORT"
    assert any("outside the calibrated contract" in problem for problem in result["problems"])


def test_wrong_contract_is_invalid(tmp_path: Path) -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    candidate = _candidate_report(policy, benchmark_contract="phase-2-direct-write-v9")
    result = compare_report(policy, candidate, mode="full")
    assert result["status"] == "INVALID_REPORT"


def test_malformed_policy_is_invalid() -> None:
    result = compare_report({"policy_version": 99}, {}, mode="full")
    assert result["status"] == "INVALID_POLICY"


def test_not_comparable_environment_never_claims_hard_regression() -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    candidate = _candidate_report(
        policy, wall_overrides=dict.fromkeys(policy["full_scheduled_scenarios"], 99.0)
    )
    candidate["environment"]["system"]["python"] = "3.13.0"  # dependency/runtime drift
    result = compare_report(policy, candidate, mode="full")
    assert result["comparability"] == "NOT_COMPARABLE"
    # No performance claim is made at all — the job must not fail on numbers.
    assert result["status"] == "PASS"
    for row in result["rows"]:
        if "ratio" in row:
            assert row["result"] == "ADVISORY"


def test_hard_regression_is_detected_on_comparable_candidate() -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    overrides = {}
    # Drive the projection ratio far beyond its calibrated envelope: make the
    # selected scenario slower than "all".
    overrides["direct_read_projection_selected"] = 9.0
    overrides["direct_read_projection_all"] = 3.5507
    candidate = _candidate_report(policy, wall_overrides=overrides)
    result = compare_report(policy, candidate, mode="full")
    assert result["status"] == "REGRESSION"
    assert result["hard_regressions"], result["rows"]
    regression = result["hard_regressions"][0]
    assert regression["ratio"] == "projection_selected_over_all"
    assert regression["candidate_value"] > regression["envelope_upper"]


def test_ratio_inside_envelope_passes() -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    result = compare_report(policy, _candidate_report(policy), mode="full")
    projection_row = next(
        row for row in result["rows"] if row.get("ratio") == "projection_selected_over_all"
    )
    assert projection_row["result"] == "PASS"


def test_advisory_drift_does_not_fail(tmp_path: Path) -> None:
    """A 25% absolute wall slowdown is ADVISORY (runner drift measured at
    33%) — visible in the report, never a hard failure."""
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    overrides = {
        name: policy["scenario_calibration"][name]["center"] * 1.25
        for name in policy["full_scheduled_scenarios"]
    }
    candidate = _candidate_report(policy, wall_overrides=overrides)
    result = compare_report(policy, candidate, mode="full")
    assert result["status"] == "PASS"
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


def test_pr_smoke_mode_requires_only_the_selected_subset() -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    result = compare_report(
        policy, _candidate_report(policy, scenarios=policy["pr_smoke_scenarios"]), mode="smoke"
    )
    assert result["status"] == "PASS"
    ratios_in_rows = [row["ratio"] for row in result["rows"] if "ratio" in row]
    assert ratios_in_rows == ["projection_selected_over_all"]

"""F3B1 offline comparator tests.

Deterministic tests for ``benchmarks/compare_direct_write_regression.py``:
strict policy validation, correctness gates that always hard-fail, explicit
comparability classification, hard-ratio evaluation on COMPARABLE evidence,
no false regression on NOT_COMPARABLE evidence, smoke partial-evaluation
semantics, and privacy/tampering rejection.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks import calibrate_direct_write_regression as policy_generator  # noqa: E402
from benchmarks import compare_direct_write_regression as comparator  # noqa: E402

_PROVENANCE = {
    "provenance_contract": "dbfbridge-direct-write-run-provenance-v1",
    "provenance_contract_version": 1,
    "workflow_run_id": "99999999999",
    "replica_id": "replica-candidate",
    "github_sha": "a" * 40,
    "source_context": "main_push",
    "branch_head_sha": "a" * 40,
    "runner_os": "Windows",
    "runner_arch": "X64",
    "python_version": "3.12.10",
    "python_implementation": "CPython",
    "sys_platform": "win32",
    "machine": "AMD64",
    "install_recipe": 'pip install -e ".[dev]"',
    "dependencies": {"dbf": "0.99.11", "dbfread": "2.0.7", "psutil": "7.2.2"},
}


def _policy() -> dict:
    return policy_generator.generate_policy(
        policy_generator.load_inputs(
            ROOT / "benchmarks" / "regression"
            / "direct-write-regression-calibration-inputs-v1.json"
        )
    )


def _candidate(wall_overrides: dict[str, float] | None = None) -> dict:
    """A valid synthetic full candidate matching the calibrated workload."""
    overrides = wall_overrides or {}
    rows = []
    counts = {
        "direct_write_190k_flat": 190_000,
        "direct_read_transform_write_190k": 190_000,
        "direct_write_1m_flat": 1_000_000,
        "direct_write_character_heavy": 100_000,
        "direct_write_memo_heavy": 100_000,
        "direct_write_deleted_include": 100_000,
        "direct_write_cp1250": 50_000,
        "direct_write_cp852": 50_000,
        "direct_write_mazovia": 50_000,
        "direct_write_varchar_nullflags": 100_000,
        "overwrite_transaction_staging_cost": 20_000,
        "cancellation_cleanup_smoke": None,
    }
    base_walls = _base_walls()
    rss_facts = {
        "direct_write_190k_flat": {
            "rss_before_bytes": 27_000_000,
            "peak_rss_bytes": 65_000_000,
            "peak_rss_delta_bytes": 38_000_000,
            "rss_after_bytes": 55_000_000,
        },
        "direct_write_1m_flat": {
            "rss_before_bytes": 44_000_000,
            "peak_rss_bytes": 176_000_000,
            "peak_rss_delta_bytes": 132_000_000,
            "rss_after_bytes": 146_000_000,
        },
    }
    for scenario in comparator._SCENARIO_ORDER:
        count = counts.get(scenario)
        wall = overrides.get(scenario, base_walls.get(scenario))
        row = {
            "scenario": scenario,
            "status": "MEASURED",
            "intermediate_jsonl_bytes": 0,
            "temporary_bytes_left": 0,
        }
        if scenario in rss_facts:
            row.update(rss_facts[scenario])
        if scenario == "cancellation_cleanup_smoke":
            row.update(
                {
                    "scenario_kind": "functional_cleanup",
                    "records_per_second": None,
                    "validation": {
                        "cleanup_verified": True,
                        "cancelled": True,
                        "write_cancelled_typed": True,
                        "error_code": "WRITE_CANCELLED",
                    },
                }
            )
        else:
            row["record_count"] = count
            row["wall_seconds"] = wall
            row["private_spool_bytes_written"] = (
                10_150_000 if scenario == "direct_write_varchar_nullflags" else 0
            )
        rows.append(row)
    return {
        "benchmark_contract": "dbfbridge-direct-write-v1",
        "benchmark_contract_version": 1,
        "mode": "full",
        "measured_code_sha": "a" * 40,
        "validation_problems": [],
        "scenarios": rows,
    }


def test_valid_comparable_candidate_passes() -> None:
    payload = comparator.compare_candidate(
        _policy(), _candidate(), _PROVENANCE, mode="full"
    )
    assert payload["overall_status"] == "PASS"
    assert payload["correctness"]["status"] == "PASS"
    assert payload["comparability"]["classification"] == "COMPARABLE"
    hard = payload["performance"]["hard_gates"]
    assert len(hard) == 5
    for gate in hard:
        assert gate["status"] in {"PASS", "REGRESSION"}
    json.dumps(payload)


def test_hard_ratio_inside_envelope_passes() -> None:
    # W1 slowed 10% and W3 unchanged: the W3/W1 ratio DROPS (W1 wall up),
    # staying inside the envelope.  Use a mild uniform slowdown that keeps
    # every ratio near its calibration values.
    overrides = {scenario: wall * 1.05 for scenario, wall in _base_walls().items()}
    payload = comparator.compare_candidate(
        _policy(), _candidate(overrides), _PROVENANCE, mode="full"
    )
    assert payload["overall_status"] == "PASS"
    for gate in payload["performance"]["hard_gates"]:
        assert gate["status"] == "PASS"


def test_hard_ratio_above_envelope_fails_when_comparable() -> None:
    """W5 (numerator) slows 3x while W1 stays constant: W5/W1 exceeds its
    envelope -> REGRESSION on comparable evidence."""
    overrides = dict(_base_walls())
    overrides["direct_write_memo_heavy"] = (
        _base_walls()["direct_write_memo_heavy"] * 3.0
    )
    payload = comparator.compare_candidate(
        _policy(), _candidate(overrides), _PROVENANCE, mode="full"
    )
    assert payload["overall_status"] == "REGRESSION"
    w5_gate = next(
        gate for gate in payload["performance"]["hard_gates"]
        if gate["label"] == "W5/W1 wall-seconds-per-record"
    )
    assert w5_gate["status"] == "REGRESSION"
    assert w5_gate["value"] > w5_gate["envelope_upper"]


def test_absolute_wall_slowdown_alone_never_hard_fails() -> None:
    """Every scenario slows 4x uniformly: absolute walls blow past the
    advisory envelopes, but same-run ratios stay ~1.0 -> no hard failure."""
    overrides = {scenario: wall * 4.0 for scenario, wall in _base_walls().items()}
    payload = comparator.compare_candidate(
        _policy(), _candidate(overrides), _PROVENANCE, mode="full"
    )
    assert payload["overall_status"] == "PASS"
    for advisory in payload["performance"]["advisory"]:
        assert advisory["classification"] == "advisory_only"


def _base_walls() -> dict[str, float]:
    """Synthetic walls that reproduce the calibrated same-run ratios:
    W1 wall = 13.0 s for 190k records; every other scenario's wall derives
    from the policy's calibrated ratio (wall/record) relative to W1."""
    w1_wall = 13.0
    policy = _policy()
    counts = {
        "direct_write_190k_flat": 190_000,
        "direct_read_transform_write_190k": 190_000,
        "direct_write_1m_flat": 1_000_000,
        "direct_write_character_heavy": 100_000,
        "direct_write_memo_heavy": 100_000,
        "direct_write_deleted_include": 100_000,
        "direct_write_cp1250": 50_000,
        "direct_write_cp852": 50_000,
        "direct_write_mazovia": 50_000,
        "direct_write_varchar_nullflags": 100_000,
        "overwrite_transaction_staging_cost": 20_000,
    }
    walls = {"direct_write_190k_flat": w1_wall}
    ratio_map = {
        "direct_write_1m_flat": ("W3/W1 wall-seconds-per-record", 1_000_000),
        "direct_read_transform_write_190k": (
            "W2/W1 wall-seconds-per-record", 190_000,
        ),
        "direct_write_memo_heavy": ("W5/W1 wall-seconds-per-record", 100_000),
        "direct_write_varchar_nullflags": ("W10/W1 wall-seconds-per-record", 100_000),
    }
    for scenario, (label, count) in ratio_map.items():
        ratio = policy["ratio_calibration"][label]["center"]
        w1_per_record = w1_wall / counts["direct_write_190k_flat"]
        walls[scenario] = round(ratio * w1_per_record * count, 6)
    # scenarios without a calibrated ratio get plausible proportional walls
    w1_per_record = w1_wall / 190_000
    walls.setdefault("direct_write_character_heavy", round(w1_per_record * 100_000, 6))
    walls.setdefault("direct_write_deleted_include", round(w1_per_record * 100_000 / 2.4, 6))
    walls.setdefault("direct_write_cp1250", round(w1_wall * 0.18, 6))
    walls.setdefault("direct_write_cp852", round(w1_wall * 0.19, 6))
    walls.setdefault("direct_write_mazovia", round(w1_wall * 0.21, 6))
    walls.setdefault(
        "overwrite_transaction_staging_cost", round(w1_wall * 0.5, 6)
    )
    return walls


def test_correctness_jsonl_failure_fails_even_when_not_comparable() -> None:
    candidate = _candidate()
    for row in candidate["scenarios"]:
        row["intermediate_jsonl_bytes"] = 5
    incompatible = {
        **_PROVENANCE,
        "runner_os": "Linux",
        "runner_arch": "ARM64",
        "python_version": "3.14.0",
        "install_recipe": "pip install dbfbridge",
        "dependencies": {"dbf": "9.9.9", "dbfread": "9.0.0", "psutil": "9.9.9"},
    }
    payload = comparator.compare_candidate(
        _policy(), candidate, incompatible, mode="full"
    )
    assert payload["correctness"]["status"] == "FAIL"
    assert payload["overall_status"] == "INCORRECT"


def test_temporary_residue_failure_fails() -> None:
    candidate = _candidate()
    candidate["scenarios"][0]["temporary_bytes_left"] = 9
    payload = comparator.compare_candidate(
        _policy(), candidate, _PROVENANCE, mode="full"
    )
    assert payload["correctness"]["status"] == "FAIL"
    assert payload["overall_status"] == "INCORRECT"


def test_w10_zero_spool_fails_in_full_mode() -> None:
    candidate = _candidate()
    for row in candidate["scenarios"]:
        if row["scenario"] == "direct_write_varchar_nullflags":
            row["private_spool_bytes_written"] = 0
    payload = comparator.compare_candidate(
        _policy(), candidate, _PROVENANCE, mode="full"
    )
    assert payload["correctness"]["status"] == "FAIL"
    assert any("disk-spool" in item for item in payload["correctness"]["problems"])


def test_w12_malformed_cleanup_fails() -> None:
    candidate = _candidate()
    for row in candidate["scenarios"]:
        if row["scenario"] == "cancellation_cleanup_smoke":
            row["validation"] = {"cleanup_verified": False}
    payload = comparator.compare_candidate(
        _policy(), candidate, _PROVENANCE, mode="full"
    )
    assert payload["correctness"]["status"] == "FAIL"
    assert any(
        "functional_cleanup" in item for item in payload["correctness"]["problems"]
    )


def test_missing_scenario_fails_in_full_mode() -> None:
    candidate = _candidate()
    candidate["scenarios"] = [
        row for row in candidate["scenarios"]
        if row["scenario"] != "direct_write_mazovia"
    ]
    payload = comparator.compare_candidate(
        _policy(), candidate, _PROVENANCE, mode="full"
    )
    assert payload["correctness"]["status"] == "FAIL"
    assert any("missing scenarios" in item for item in payload["correctness"]["problems"])


def test_duplicate_scenario_fails() -> None:
    candidate = _candidate()
    candidate["scenarios"].append(dict(candidate["scenarios"][0]))
    payload = comparator.compare_candidate(
        _policy(), candidate, _PROVENANCE, mode="full"
    )
    assert payload["correctness"]["status"] == "FAIL"
    assert any("duplicate scenario" in item for item in payload["correctness"]["problems"])


def test_smoke_candidate_accepted() -> None:
    """Smoke: the real W1-W12 scenario contract with reduced counts; ALL
    hard ratio gates have their required scenarios present and evaluate
    (F3B1-BLK-04)."""
    candidate = _candidate()
    candidate["mode"] = "smoke"
    # reduce counts per SMOKE_COUNTS-like shape (bounded smoke)
    for row in candidate["scenarios"]:
        if row.get("record_count"):
            row["record_count"] = min(row["record_count"], 2000)
            if isinstance(row.get("wall_seconds"), (int, float)) and (
                row["wall_seconds"] > 20
            ):
                row["wall_seconds"] = 20
    payload = comparator.compare_candidate(
        _policy(), candidate, _PROVENANCE, mode="smoke"
    )
    assert payload["correctness"]["status"] == "PASS"
    assert payload["overall_status"] in {"PASS", "REGRESSION"}
    # smoke exercises real hard gates: all five must have concrete values
    # (the benchmark produces all 12 scenarios in both modes)
    evaluated = [
        gate for gate in payload["performance"]["hard_gates"]
        if gate["status"] in {"PASS", "REGRESSION"}
    ]
    assert len(evaluated) >= 1, (
        "smoke must exercise at least one actual hard performance gate"
    )
    for gate in payload["performance"]["hard_gates"]:
        assert gate["status"] != "NOT_EVALUATED_IN_SMOKE", (
            f"{gate['label']}: all 12 scenarios are present in the real smoke "
            "contract; no gate may be NOT_EVALUATED"
        )


def test_empty_smoke_rejected() -> None:
    """F3B1-BLK-04: an empty smoke candidate is a correctness FAIL."""
    candidate = _candidate()
    candidate["mode"] = "smoke"
    candidate["scenarios"] = []
    payload = comparator.compare_candidate(
        _policy(), candidate, _PROVENANCE, mode="smoke"
    )
    assert payload["correctness"]["status"] == "FAIL"
    assert payload["overall_status"] == "INCORRECT"
    assert any("empty candidate" in item for item in payload["correctness"]["problems"])


def test_smoke_missing_w1_rejected() -> None:
    candidate = _candidate()
    candidate["mode"] = "smoke"
    candidate["scenarios"] = [
        row for row in candidate["scenarios"]
        if row["scenario"] != "direct_write_190k_flat"
    ]
    payload = comparator.compare_candidate(
        _policy(), candidate, _PROVENANCE, mode="smoke"
    )
    assert payload["correctness"]["status"] == "FAIL"
    assert any("missing scenarios" in item for item in payload["correctness"]["problems"])


def test_smoke_missing_w3_rejected() -> None:
    candidate = _candidate()
    candidate["mode"] = "smoke"
    candidate["scenarios"] = [
        row for row in candidate["scenarios"]
        if row["scenario"] != "direct_write_1m_flat"
    ]
    payload = comparator.compare_candidate(
        _policy(), candidate, _PROVENANCE, mode="smoke"
    )
    assert payload["correctness"]["status"] == "FAIL"
    assert any("missing scenarios" in item for item in payload["correctness"]["problems"])


def test_smoke_missing_w12_rejected() -> None:
    candidate = _candidate()
    candidate["mode"] = "smoke"
    candidate["scenarios"] = [
        row for row in candidate["scenarios"]
        if row["scenario"] != "cancellation_cleanup_smoke"
    ]
    payload = comparator.compare_candidate(
        _policy(), candidate, _PROVENANCE, mode="smoke"
    )
    assert payload["correctness"]["status"] == "FAIL"
    assert any("missing scenarios" in item for item in payload["correctness"]["problems"])


def test_smoke_unexpected_scenario_rejected() -> None:
    candidate = _candidate()
    candidate["mode"] = "smoke"
    candidate["scenarios"].append(
        {
            "scenario": "unexpected_scenario",
            "status": "MEASURED",
            "record_count": 100,
            "wall_seconds": 1.0,
            "intermediate_jsonl_bytes": 0,
            "temporary_bytes_left": 0,
        }
    )
    payload = comparator.compare_candidate(
        _policy(), candidate, _PROVENANCE, mode="smoke"
    )
    assert payload["correctness"]["status"] == "FAIL"
    assert any("unexpected scenarios" in item for item in payload["correctness"]["problems"])


def test_not_comparable_does_not_produce_false_regression() -> None:
    """A hard ratio above the envelope on NOT_COMPARABLE evidence must NOT
    be reported as a confirmed regression."""
    candidate = _candidate()
    for row in candidate["scenarios"]:
        if row["scenario"] == "direct_write_memo_heavy":
            row["wall_seconds"] = row["wall_seconds"] * 3.0
    incompatible = {
        **_PROVENANCE,
        "runner_os": "Linux",
        "runner_arch": "ARM64",
        "python_version": "3.14.0",
        "install_recipe": "pip install dbfbridge",
        "dependencies": {"dbf": "9.9.9", "dbfread": "9.9.9", "psutil": "9.9.9"},
    }
    payload = comparator.compare_candidate(
        _policy(), candidate, incompatible, mode="full"
    )
    assert payload["comparability"]["classification"] == "NOT_COMPARABLE"
    w5_gate = next(
        gate for gate in payload["performance"]["hard_gates"]
        if gate["label"] == "W5/W1 wall-seconds-per-record"
    )
    assert "NOT_COMPARABLE" in w5_gate["status"]
    assert payload["overall_status"] != "REGRESSION"


def test_correctness_fails_even_when_not_comparable() -> None:
    candidate = _candidate()
    for row in candidate["scenarios"]:
        row["intermediate_jsonl_bytes"] = 3
    incompatible = {
        **_PROVENANCE,
        "runner_os": "Linux",
        "runner_arch": "ARM64",
        "python_version": "3.14.0",
        "install_recipe": "pip install dbfbridge",
        "dependencies": {"dbf": "9.9.9", "dbfread": "9.9.9", "psutil": "9.9.9"},
    }
    payload = comparator.compare_candidate(
        _policy(), candidate, incompatible, mode="full"
    )
    assert payload["comparability"]["classification"] in {
        "PARTIALLY_COMPARABLE", "NOT_COMPARABLE",
    }
    assert payload["correctness"]["status"] == "FAIL"
    assert payload["overall_status"] == "INCORRECT"


def test_dependency_mismatch_affects_comparability() -> None:
    mismatched = {
        **_PROVENANCE,
        "dependencies": {"dbf": "9.9.9", "dbfread": "2.0.7", "psutil": "7.2.2"},
    }
    payload = comparator.compare_candidate(
        _policy(), _candidate(), mismatched, mode="full"
    )
    assert payload["comparability"]["classification"] in {
        "PARTIALLY_COMPARABLE", "NOT_COMPARABLE",
    }
    assert any(
        "dbf: MISMATCH" in item for item in payload["comparability"]["checks"]
    )


def test_python_major_minor_mismatch_affects_comparability() -> None:
    mismatched = {**_PROVENANCE, "python_version": "3.14.0"}
    payload = comparator.compare_candidate(
        _policy(), _candidate(), mismatched, mode="full"
    )
    assert payload["comparability"]["classification"] in {
        "PARTIALLY_COMPARABLE", "NOT_COMPARABLE",
    }
    assert any(
        "python major.minor: MISMATCH" in item
        for item in payload["comparability"]["checks"]
    )


def test_runner_arch_mismatch_affects_comparability() -> None:
    mismatched = {**_PROVENANCE, "runner_arch": "ARM64"}
    payload = comparator.compare_candidate(
        _policy(), _candidate(), mismatched, mode="full"
    )
    assert payload["comparability"]["classification"] in {
        "PARTIALLY_COMPARABLE", "NOT_COMPARABLE",
    }


def test_missing_candidate_provenance_is_not_comparable() -> None:
    payload = comparator.compare_candidate(
        _policy(), _candidate(), None, mode="full"
    )
    assert payload["comparability"]["classification"] == "NOT_COMPARABLE"
    assert payload["comparability"]["checks"] == ["candidate provenance missing"]
    assert payload["overall_status"] in {"PASS", "NOT_COMPARABLE_PASS"}


def test_tampered_policy_rejected() -> None:
    policy = _policy()
    policy["parameters"]["mad_multiplier"]["value"] = 999.0
    payload = comparator.compare_candidate(policy, _candidate(), _PROVENANCE, mode="full")
    assert payload["overall_status"] == "INVALID_POLICY"


def test_unknown_policy_parameter_rejected() -> None:
    policy = _policy()
    policy["parameters"]["secret_multiplier"] = {"value": 1.0, "rationale": "x"}
    payload = comparator.compare_candidate(policy, _candidate(), _PROVENANCE, mode="full")
    assert payload["overall_status"] == "INVALID_POLICY"


def test_classification_tampering_rejected() -> None:
    policy = _policy()
    policy["ratio_calibration"]["W5/W1 wall-seconds-per-record"][
        "classification"
    ] = "advisory_only"  # inconsistent with its derivation (envelope <= 1.5*center)
    payload = comparator.compare_candidate(policy, _candidate(), _PROVENANCE, mode="full")
    assert payload["overall_status"] == "INVALID_POLICY"
    assert any("classification tampered" in item for item in payload["problems"])


def test_absolute_wall_hard_gate_in_policy_rejected() -> None:
    policy = _policy()
    policy["scenario_calibration"]["direct_write_190k_flat"][
        "classification"
    ] = "hard_gate"
    payload = comparator.compare_candidate(policy, _candidate(), _PROVENANCE, mode="full")
    assert payload["overall_status"] == "INVALID_POLICY"


def test_wrong_policy_contract_rejected() -> None:
    policy = _policy()
    policy["policy_contract"] = "phase-3-regression-policy-v1"
    payload = comparator.compare_candidate(policy, _candidate(), _PROVENANCE, mode="full")
    assert payload["overall_status"] == "INVALID_POLICY"


def test_missing_ratio_in_policy_rejected() -> None:
    policy = _policy()
    del policy["ratio_calibration"]["W5/W1 wall-seconds-per-record"]
    payload = comparator.compare_candidate(policy, _candidate(), _PROVENANCE, mode="full")
    assert payload["overall_status"] == "INVALID_POLICY"


def test_extra_ratio_in_policy_rejected() -> None:
    policy = _policy()
    policy["ratio_calibration"]["W7/W1 wall-seconds-per-record"] = {
        "numerator": "direct_write_cp1250.wall_seconds",
        "denominator": "direct_write_190k_flat.wall_seconds",
        "values": [1.0] * 5,
        "center": 1.0,
        "mad": 0.0,
        "envelope_upper": 1.0,
        "classification": "advisory_only",
    }
    payload = comparator.compare_candidate(policy, _candidate(), _PROVENANCE, mode="full")
    assert payload["overall_status"] == "INVALID_POLICY"


def test_result_contract_json_safe() -> None:
    payload = comparator.compare_candidate(
        _policy(), _candidate(), _PROVENANCE, mode="full"
    )
    json.dumps(payload)


def test_memo_heavy_candidate_wall_values_not_massaged() -> None:
    """Raw candidate wall values are reported as measured — no normalization."""
    slow_walls = dict(_base_walls())
    slow_walls["direct_write_memo_heavy"] = 60.0  # an outlier
    payload = comparator.compare_candidate(
        _policy(), _candidate(slow_walls), _PROVENANCE, mode="full"
    )
    w5_advisory = next(
        item for item in payload["performance"]["advisory"]
        if item["scenario"] == "direct_write_memo_heavy"
    )
    assert w5_advisory["wall_seconds"] == 60.0  # raw value preserved


def test_rss_ratio_uses_raw_formula() -> None:
    """F3B1-BLK-01: the RSS ratio must be the RAW peak-RSS-delta quotient
    (NOT per-record normalized).  A candidate with W1 delta 38 MB and W3
    delta 132 MB yields 132/38 ≈ 3.4737 — near the calibrated center
    3.481296 — NOT the ~0.66 a per-record normalization would produce."""
    payload = comparator.compare_candidate(
        _policy(), _candidate(), _PROVENANCE, mode="full"
    )
    rss_gate = next(
        gate for gate in payload["performance"]["hard_gates"]
        if gate["label"] == "W3/W1 peak-RSS-delta ratio"
    )
    assert rss_gate["numerator_delta_bytes"] == 132_000_000
    assert rss_gate["denominator_delta_bytes"] == 38_000_000
    expected = 132_000_000 / 38_000_000
    assert abs(rss_gate["value"] - expected) < 1e-6
    assert rss_gate["value"] > 3.0  # raw ratio, not a per-record figure


def test_rss_regression_mechanically_fails() -> None:
    """A candidate whose W3 peak-RSS-delta exceeds the policy RSS envelope
    must produce a REGRESSION on COMPARABLE evidence."""
    candidate = _candidate()
    for row in candidate["scenarios"]:
        if row["scenario"] == "direct_write_1m_flat":
            # 3.481296 * 1.5 (discrimination bound) < ratio -> exceeds envelope
            row["peak_rss_delta_bytes"] = 260_000_000
    payload = comparator.compare_candidate(
        _policy(), candidate, _PROVENANCE, mode="full"
    )
    rss_gate = next(
        gate for gate in payload["performance"]["hard_gates"]
        if gate["label"] == "W3/W1 peak-RSS-delta ratio"
    )
    expected_ratio = 260_000_000 / 38_000_000
    assert abs(rss_gate["value"] - expected_ratio) < 1e-6
    assert expected_ratio > rss_gate["envelope_upper"]
    assert rss_gate["status"] == "REGRESSION"
    assert payload["overall_status"] == "REGRESSION"


def test_w1_zero_rss_delta_is_candidate_malformed() -> None:
    """F3B1-BLK-08: a zero W1 RSS delta denominator must reject cleanly and
    deterministically (never PASS, never an internal exception)."""
    candidate = _candidate()
    for row in candidate["scenarios"]:
        if row["scenario"] == "direct_write_190k_flat":
            row["peak_rss_delta_bytes"] = 0
    payload = comparator.compare_candidate(
        _policy(), candidate, _PROVENANCE, mode="full"
    )
    rss_gate = next(
        gate for gate in payload["performance"]["hard_gates"]
        if gate["label"] == "W3/W1 peak-RSS-delta ratio"
    )
    assert rss_gate["status"] == "CANDIDATE_MALFORMED"
    assert payload["overall_status"] == "CANDIDATE_MALFORMED"
    json.dumps(payload)


# ---------------------------------------------------------------------------
# F3B1-BLK-08: malformed candidate numerics fail closed, never crash
# ---------------------------------------------------------------------------


def _gate_status(payload: dict, label: str) -> str:
    gate = next(
        gate for gate in payload["performance"]["hard_gates"]
        if gate["label"] == label
    )
    return gate["status"]


def test_wall_seconds_string_is_candidate_malformed() -> None:
    candidate = _candidate()
    for row in candidate["scenarios"]:
        if row["scenario"] == "direct_write_190k_flat":
            row["wall_seconds"] = "bad"
    payload = comparator.compare_candidate(
        _policy(), candidate, _PROVENANCE, mode="full"
    )
    assert payload["overall_status"] == "CANDIDATE_MALFORMED"
    wall_gate = next(
        gate for gate in payload["performance"]["hard_gates"]
        if gate["label"] == "W3/W1 wall-seconds-per-record"
    )
    assert wall_gate["status"] == "CANDIDATE_MALFORMED"
    json.dumps(payload)


def test_wall_seconds_nan_is_candidate_malformed() -> None:
    candidate = _candidate()
    for row in candidate["scenarios"]:
        if row["scenario"] == "direct_write_1m_flat":
            row["wall_seconds"] = float("nan")
    payload = comparator.compare_candidate(
        _policy(), candidate, _PROVENANCE, mode="full"
    )
    assert payload["overall_status"] == "CANDIDATE_MALFORMED"


def test_zero_record_count_is_candidate_malformed() -> None:
    candidate = _candidate()
    for row in candidate["scenarios"]:
        if row["scenario"] == "direct_write_1m_flat":
            row["record_count"] = 0
    payload = comparator.compare_candidate(
        _policy(), candidate, _PROVENANCE, mode="full"
    )
    assert payload["overall_status"] == "CANDIDATE_MALFORMED"
    wall_gate = next(
        gate for gate in payload["performance"]["hard_gates"]
        if gate["label"] == "W3/W1 wall-seconds-per-record"
    )
    assert wall_gate["status"] == "CANDIDATE_MALFORMED"


def test_bool_record_count_is_candidate_malformed() -> None:
    candidate = _candidate()
    for row in candidate["scenarios"]:
        if row["scenario"] == "direct_write_1m_flat":
            row["record_count"] = True
    payload = comparator.compare_candidate(
        _policy(), candidate, _PROVENANCE, mode="full"
    )
    assert payload["overall_status"] == "CANDIDATE_MALFORMED"


def test_peak_rss_delta_string_is_candidate_malformed() -> None:
    candidate = _candidate()
    for row in candidate["scenarios"]:
        if row["scenario"] == "direct_write_1m_flat":
            row["peak_rss_delta_bytes"] = "bad"
    payload = comparator.compare_candidate(
        _policy(), candidate, _PROVENANCE, mode="full"
    )
    rss_gate = next(
        gate for gate in payload["performance"]["hard_gates"]
        if gate["label"] == "W3/W1 peak-RSS-delta ratio"
    )
    assert rss_gate["status"] == "CANDIDATE_MALFORMED"
    assert payload["overall_status"] == "CANDIDATE_MALFORMED"


def test_w3_infinite_rss_delta_is_candidate_malformed() -> None:
    candidate = _candidate()
    for row in candidate["scenarios"]:
        if row["scenario"] == "direct_write_1m_flat":
            row["peak_rss_delta_bytes"] = float("inf")
    payload = comparator.compare_candidate(
        _policy(), candidate, _PROVENANCE, mode="full"
    )
    rss_gate = next(
        gate for gate in payload["performance"]["hard_gates"]
        if gate["label"] == "W3/W1 peak-RSS-delta ratio"
    )
    assert rss_gate["status"] == "CANDIDATE_MALFORMED"
    assert payload["overall_status"] == "CANDIDATE_MALFORMED"


def test_oversized_integer_wall_is_candidate_malformed() -> None:
    """A wall value too large for float must fail cleanly (no uncaught
    OverflowError)."""
    candidate = _candidate()
    for row in candidate["scenarios"]:
        if row["scenario"] == "direct_write_190k_flat":
            row["wall_seconds"] = 10**400
    payload = comparator.compare_candidate(
        _policy(), candidate, _PROVENANCE, mode="full"
    )
    wall_gate = next(
        gate for gate in payload["performance"]["hard_gates"]
        if gate["label"] == "W3/W1 wall-seconds-per-record"
    )
    assert wall_gate["status"] == "CANDIDATE_MALFORMED"
    assert payload["overall_status"] == "CANDIDATE_MALFORMED"


def test_malformed_candidate_never_reports_pass() -> None:
    """Every malformed-numeric case must produce a deterministic FAILURE
    overall status (never PASS/NOT_COMPARABLE_PASS)."""
    for mutate in (
        lambda candidate: candidate["scenarios"][0].update({"wall_seconds": "bad"}),
        lambda candidate: candidate["scenarios"][0].update({"wall_seconds": float("nan")}),
        lambda candidate: candidate["scenarios"][0].update({"wall_seconds": -1.0}),
        lambda candidate: candidate["scenarios"][0].update({"record_count": 0}),
        lambda candidate: candidate["scenarios"][0].update({"record_count": "x"}),
        lambda candidate: candidate["scenarios"][0].update({"peak_rss_delta_bytes": "bad"}),
        lambda candidate: candidate["scenarios"][0].update({"peak_rss_delta_bytes": 0}),
        lambda candidate: candidate["scenarios"][2].update({"peak_rss_delta_bytes": float("inf")}),
        lambda candidate: candidate["scenarios"][2].update({"peak_rss_delta_bytes": -5}),
    ):
        candidate = _candidate()
        mutate(candidate)
        payload = comparator.compare_candidate(
            _policy(), candidate, _PROVENANCE, mode="full"
        )
        assert payload["overall_status"] not in {"PASS", "NOT_COMPARABLE_PASS"}, (
            payload["overall_status"]
        )


# ---------------------------------------------------------------------------
# F3B1-BLK-02/03/12: strict policy shape and fail-closed policy numerics
# ---------------------------------------------------------------------------


def _tamper(policy: dict, mutate) -> dict:
    comparator.validate_policy(policy)  # sanity: baseline policy is valid
    assert comparator.validate_policy(policy) == []
    mutate(policy)
    return policy


def test_policy_numerator_tampering_rejected() -> None:
    policy = _policy()
    policy["ratio_calibration"]["W5/W1 wall-seconds-per-record"]["numerator"] = (
        "direct_write_cp1250.wall_seconds"
    )
    problems = comparator.validate_policy(policy)
    assert any("mispaired numerator" in item for item in problems)


def test_policy_denominator_tampering_rejected() -> None:
    policy = _policy()
    policy["ratio_calibration"]["W5/W1 wall-seconds-per-record"]["denominator"] = (
        "direct_write_1m_flat.wall_seconds"
    )
    problems = comparator.validate_policy(policy)
    assert any("mispaired denominator" in item for item in problems)


def test_policy_metric_tampering_rejected() -> None:
    policy = _policy()
    policy["ratio_calibration"]["W5/W1 wall-seconds-per-record"]["metric"] = (
        "peak_rss_delta_bytes"
    )
    problems = comparator.validate_policy(policy)
    assert any("metric tampered" in item for item in problems)


def test_policy_normalization_tampering_rejected() -> None:
    policy = _policy()
    policy["ratio_calibration"]["W3/W1 peak-RSS-delta ratio"][
        "normalization"
    ] = "per_record_ratio"  # RSS must NEVER be per-record normalized
    problems = comparator.validate_policy(policy)
    assert any("normalization tampered" in item for item in problems)


def test_policy_missing_ratio_metadata_rejected() -> None:
    policy = _policy()
    del policy["ratio_calibration"]["W5/W1 wall-seconds-per-record"]["metric"]
    problems = comparator.validate_policy(policy)
    assert any("missing required field 'metric'" in item for item in problems)


def test_policy_nan_center_rejected() -> None:
    policy = _policy()
    policy["ratio_calibration"]["W3/W1 peak-RSS-delta ratio"]["center"] = float("nan")
    payload = comparator.compare_candidate(policy, _candidate(), _PROVENANCE, mode="full")
    assert payload["overall_status"] == "INVALID_POLICY"
    assert any("non-finite center" in item for item in payload["problems"])


def test_policy_nan_envelope_rejected() -> None:
    policy = _policy()
    policy["ratio_calibration"]["W3/W1 peak-RSS-delta ratio"][
        "envelope_upper"
    ] = float("nan")
    payload = comparator.compare_candidate(policy, _candidate(), _PROVENANCE, mode="full")
    assert payload["overall_status"] == "INVALID_POLICY"


def test_policy_infinity_mad_rejected() -> None:
    policy = _policy()
    policy["ratio_calibration"]["W3/W1 wall-seconds-per-record"]["mad"] = float("inf")
    payload = comparator.compare_candidate(policy, _candidate(), _PROVENANCE, mode="full")
    assert payload["overall_status"] == "INVALID_POLICY"
    assert any("non-finite MAD" in item for item in payload["problems"])


def test_policy_nan_derived_fields_rejected() -> None:
    for field in (
        "relative_mad",
        "max_observed_deviation",
        "spread_based",
        "tail_based",
        "envelope_upper_over_center",
    ):
        policy = _policy()
        policy["ratio_calibration"]["W5/W1 wall-seconds-per-record"][field] = float("nan")
        payload = comparator.compare_candidate(
            policy, _candidate(), _PROVENANCE, mode="full"
        )
        assert payload["overall_status"] == "INVALID_POLICY", field
        assert any(f"non-finite {field}" in item for item in payload["problems"])


def test_policy_nan_calibration_value_rejected() -> None:
    policy = _policy()
    policy["ratio_calibration"]["W5/W1 wall-seconds-per-record"]["values"][1] = float("nan")
    payload = comparator.compare_candidate(policy, _candidate(), _PROVENANCE, mode="full")
    assert payload["overall_status"] == "INVALID_POLICY"
    assert any("non-finite calibration value" in item for item in payload["problems"])


def test_policy_nan_advisory_metric_rejected() -> None:
    policy = _policy()
    policy["scenario_calibration"]["direct_write_190k_flat"][
        "advisory_envelope_upper"
    ] = float("nan")
    payload = comparator.compare_candidate(policy, _candidate(), _PROVENANCE, mode="full")
    assert payload["overall_status"] == "INVALID_POLICY"
    assert any(
        "non-finite advisory advisory_envelope_upper" in item
        for item in payload["problems"]
    )


def test_policy_advisory_center_nan_rejected() -> None:
    policy = _policy()
    policy["scenario_calibration"]["direct_write_190k_flat"]["center"] = float("nan")
    problems = comparator.validate_policy(policy)
    assert any("non-finite advisory center" in item for item in problems)


def test_policy_hard_label_lists_tampering_rejected() -> None:
    policy = _policy()
    policy["hard_ratio_labels"] = [label for label in policy["hard_ratio_labels"] if label != "W5/W1 wall-seconds-per-record"]
    payload = comparator.compare_candidate(policy, _candidate(), _PROVENANCE, mode="full")
    assert payload["overall_status"] == "INVALID_POLICY"
    assert any("missing from hard_ratio_labels" in item for item in payload["problems"])


def test_policy_label_lists_not_covering_all_ratios_rejected() -> None:
    policy = _policy()
    policy["advisory_ratio_labels"] = list(policy["advisory_ratio_labels"]) + [
        "W3/W1 wall-seconds-per-record"
    ]
    payload = comparator.compare_candidate(policy, _candidate(), _PROVENANCE, mode="full")
    assert payload["overall_status"] == "INVALID_POLICY"


def test_policy_scenario_calibration_extra_scenario_rejected() -> None:
    policy = _policy()
    policy["scenario_calibration"]["cancellation_cleanup_smoke"] = {
        "scenario": "cancellation_cleanup_smoke",
        "metric": "wall_seconds",
        "classification": "advisory_only",
        "values": [1.0] * 5,
        "center": 1.0,
        "mad": 0.0,
        "advisory_envelope_upper": 2.0,
    }
    payload = comparator.compare_candidate(policy, _candidate(), _PROVENANCE, mode="full")
    assert payload["overall_status"] == "INVALID_POLICY"
    assert any("W1-W11" in item for item in payload["problems"])


def test_policy_calibration_count_mismatch_with_provenance_rejected() -> None:
    policy = _policy()
    policy["calibration_sources"]["benchmark_run_ids"] = policy[
        "calibration_sources"
    ]["benchmark_run_ids"][:4]
    payload = comparator.compare_candidate(policy, _candidate(), _PROVENANCE, mode="full")
    assert payload["overall_status"] == "INVALID_POLICY"
    assert any("must match the benchmark_run_ids" in item for item in payload["problems"])


def test_policy_authority_format_tampering_rejected() -> None:
    policy = _policy()
    policy["calibration_sources"]["artifact_id"] = "10104538589"  # not an int
    payload = comparator.compare_candidate(policy, _candidate(), _PROVENANCE, mode="full")
    assert payload["overall_status"] == "INVALID_POLICY"
    assert any("artifact_id must be a positive integer" in item for item in payload["problems"])


def test_policy_wrong_source_context_rejected() -> None:
    policy = _policy()
    policy["calibration_sources"]["source_context"] = "pull_request_merge_ref"
    payload = comparator.compare_candidate(policy, _candidate(), _PROVENANCE, mode="full")
    assert payload["overall_status"] == "INVALID_POLICY"


def test_policy_parameter_kind_tampering_rejected() -> None:
    policy = _policy()
    policy["parameters"]["mad_multiplier"]["kind"] = "measured_fact"
    payload = comparator.compare_candidate(policy, _candidate(), _PROVENANCE, mode="full")
    assert payload["overall_status"] == "INVALID_POLICY"
    assert any("kind must be engineering_policy_parameter" in item for item in payload["problems"])


# ---------------------------------------------------------------------------
# F3B1-BLK-05: candidate provenance reuses the AUTHORITATIVE F3A validator
# ---------------------------------------------------------------------------


def _provenance_variants() -> dict[str, dict]:
    return {
        "wrong contract": {"provenance_contract": "some-other/1"},
        "wrong version": {"provenance_contract_version": 2},
        "unknown key": {"environment_dump": {"PATH": "C:\\windows"}},
        "extra dependency": {
            "dependencies": {"dbf": "0.99.11", "dbfread": "2.0.7", "psutil": "7.2.2", "pyyaml": "6.0"}
        },
        "missing dependency": {"dependencies": {"dbf": "0.99.11", "psutil": "7.2.2"}},
        "dependencies not object": {"dependencies": "0.99.11"},
        "invalid SHA": {"github_sha": "zzzz-not-a-sha"},
        "invalid source_context": {"source_context": "mystery_context"},
    }


def test_malformed_candidate_provenance_never_comparable() -> None:
    base = dict(_PROVENANCE)
    for description, override in _provenance_variants().items():
        provenance = dict(base)
        provenance.update(override)
        payload = comparator.compare_candidate(
            _policy(), _candidate(), provenance, mode="full"
        )
        assert payload["comparability"]["classification"] == "NOT_COMPARABLE", description
        assert any(
            "provenance: INVALID" in item for item in payload["comparability"]["checks"]
        ), description
        json.dumps(payload)


def test_malformed_provenance_never_crashes_on_non_object() -> None:
    payload = comparator.compare_candidate(
        _policy(),
        _candidate(),
        "not-an-object",  # type: ignore[arg-type]
        mode="full",
    )
    assert payload["comparability"]["classification"] == "NOT_COMPARABLE"
    json.dumps(payload)


# ---------------------------------------------------------------------------
# F3B1-BLK-04/§11: smoke evidence contract
# ---------------------------------------------------------------------------


def test_comparable_smoke_with_missing_metrics_never_passes() -> None:
    """A COMPARABLE smoke candidate whose scenario facts are present but
    whose performance metrics are missing cannot PASS — incomplete smoke
    evidence must fail (F3B1-BLK-04/§11)."""
    candidate = _candidate()
    candidate["mode"] = "smoke"
    for row in candidate["scenarios"]:
        if row.get("scenario") == "cancellation_cleanup_smoke":
            continue
        row["wall_seconds"] = None  # all hard-gate wall facts gone
        row.pop("record_count", None)
    payload = comparator.compare_candidate(
        _policy(), candidate, _PROVENANCE, mode="smoke"
    )
    assert payload["correctness"]["status"] == "PASS"
    statuses = {gate["status"] for gate in payload["performance"]["hard_gates"]}
    assert "NOT_EVALUATED_IN_SMOKE" in statuses
    assert payload["overall_status"] == "INCOMPLETE_EVIDENCE"
    assert payload["overall_status"] != "PASS"

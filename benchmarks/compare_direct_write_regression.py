"""Offline comparator for the v1.1 Direct Write regression policy
(DBFB-PERF-006, stage F3B1).

Compares a candidate Direct Write profile (and its run provenance) against
the committed ``dbfbridge-direct-write-regression-policy-v1``:

- CORRECTNESS gates ALWAYS run and hard-fail regardless of comparability;
- performance hard gates apply ONLY on COMPARABLE environment evidence
  (runner_os / runner_arch / Python major.minor / install_recipe /
  dependency versions); NOT_COMPARABLE never creates a false regression;
- ABSOLUTE scenario wall times are ADVISORY ONLY and can never hard-fail;
- smoke mode evaluates only ratios whose numerator and denominator
  scenarios are actually present; unavailable gates report
  ``NOT_EVALUATED_IN_SMOKE`` (never PASS).

Deterministic, offline, stdlib-only; never benchmarks, never touches the
network, never modifies the candidate or the policy.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

RESULT_CONTRACT = "dbfbridge-direct-write-regression-result-v1"

_SCENARIO_ORDER = (
    "direct_write_190k_flat",
    "direct_read_transform_write_190k",
    "direct_write_1m_flat",
    "direct_write_character_heavy",
    "direct_write_memo_heavy",
    "direct_write_deleted_include",
    "direct_write_cp1250",
    "direct_write_cp852",
    "direct_write_mazovia",
    "direct_write_varchar_nullflags",
    "overwrite_transaction_staging_cost",
    "cancellation_cleanup_smoke",
)

_RATIO_DEFINITION_KEYS = {
    "W3/W1 wall-seconds-per-record": (
        "direct_write_1m_flat",
        "direct_write_190k_flat",
    ),
    "W2/W1 wall-seconds-per-record": (
        "direct_read_transform_write_190k",
        "direct_write_190k_flat",
    ),
    "W5/W1 wall-seconds-per-record": (
        "direct_write_memo_heavy",
        "direct_write_190k_flat",
    ),
    "W10/W1 wall-seconds-per-record": (
        "direct_write_varchar_nullflags",
        "direct_write_190k_flat",
    ),
    "W3/W1 peak-RSS-delta ratio": (
        "direct_write_1m_flat",
        "direct_write_190k_flat",
    ),
}

_PROVENANCE_CONTRACT = "dbfbridge-direct-write-run-provenance-v1"
_COMPARED_FIELDS = (
    "runner_os",
    "runner_arch",
    "python_version",
    "install_recipe",
    "dependencies",
)

_ALLOWED_POLICY_PARAMETERS = ("mad_multiplier", "small_sample_guard_band", "hard_gate_discrimination_bound")


# ---------------------------------------------------------------------------
# policy validation (strict — a malformed policy never disables hard gates)
# ---------------------------------------------------------------------------


def validate_policy(policy: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if policy.get("policy_contract") != "dbfbridge-direct-write-regression-policy-v1":
        problems.append("wrong policy contract")
    if policy.get("policy_version") != 1:
        problems.append("wrong policy version")
    if policy.get("benchmark_contract") != "dbfbridge-direct-write-v1":
        problems.append("wrong benchmark contract in policy")
    if policy.get("benchmark_contract_version") != 1:
        problems.append("wrong benchmark contract version in policy")
    if policy.get("calibration_contract") != "dbfbridge-direct-write-calibration-v1":
        problems.append("wrong calibration contract in policy")
    reference_commit = policy.get("reference_commit")
    if not (
        isinstance(reference_commit, str)
        and len(reference_commit) == 40
        and all(ch in "0123456789abcdef" for ch in reference_commit)
    ):
        problems.append("invalid reference_commit in policy")
    calibration_sources = policy.get("calibration_sources") or {}
    for key in ("workflow_run_id", "artifact_id", "artifact_digest", "compact_json_sha256"):
        if not calibration_sources.get(key):
            problems.append(f"policy calibration_sources missing {key}")
    if policy.get("calibration_count", 0) < 5:
        problems.append("policy calibration_count < 5")
    if not policy.get("runtime_recipe"):
        problems.append("policy runtime_recipe missing")
    parameters = policy.get("parameters") or {}
    allowed_parameters = {
        "mad_multiplier": 3.0,
        "small_sample_guard_band": 1.15,
        "hard_gate_discrimination_bound": 1.5,
    }
    if set(parameters) != set(allowed_parameters):
        problems.append(
            "policy parameters must be exactly "
            f"{sorted(allowed_parameters)} (got {sorted(parameters)})"
        )
    for name, expected in allowed_parameters.items():
        spec = parameters.get(name) or {}
        if spec.get("value") != expected:
            problems.append(f"policy parameter {name} tampered: {spec.get('value')!r}")
        if not spec.get("rationale"):
            problems.append(f"policy parameter {name} missing rationale")
    derivation = policy.get("derivation") or ""
    if "median" not in derivation or "hard_gate" not in derivation:
        problems.append("policy derivation description incomplete")
    ratio_calibration = policy.get("ratio_calibration") or {}
    if set(ratio_calibration) != set(_RATIO_DEFINITION_KEYS):
        problems.append(
            "policy ratio set must be exactly the five canonical candidates"
        )
    for label, spec in ratio_calibration.items():
        if label not in _RATIO_DEFINITION_KEYS:
            continue  # already reported above; do not re-derive unknown labels
        expected_numerator, expected_denominator = _RATIO_DEFINITION_KEYS[label]
        if spec.get("numerator") != f"{expected_numerator}.wall_seconds" and not (
            label.endswith("peak-RSS-delta ratio")
            and spec.get("numerator") == f"{expected_numerator}.peak_rss_delta_bytes"
        ):
            problems.append(f"ratio {label!r}: mispaired numerator/denominator")
        values = spec.get("values") or []
        if len(values) < 5:
            problems.append(f"ratio {label!r}: fewer than 5 calibration values")
        center = spec.get("center")
        if not isinstance(center, (int, float)) or center <= 0:
            problems.append(f"ratio {label!r}: invalid center")
        mad = spec.get("mad")
        if not isinstance(mad, (int, float)) or mad < 0:
            problems.append(f"ratio {label!r}: negative MAD")
        envelope = spec.get("envelope_upper")
        if not isinstance(envelope, (int, float)) or envelope <= 0:
            problems.append(f"ratio {label!r}: invalid envelope")
        classification = spec.get("classification")
        if classification not in {"hard_gate", "advisory_only"}:
            problems.append(f"ratio {label!r}: invalid classification")
        # mechanical classification re-derivation
        if (
            isinstance(center, (int, float))
            and isinstance(envelope, (int, float))
            and center > 0
        ):
            expected_classification = (
                "hard_gate"
                if envelope <= center * 1.5
                else "advisory_only"
            )
            if classification != expected_classification:
                problems.append(
                    f"ratio {label!r}: classification tampered (expected "
                    f"{expected_classification}, got {classification!r})"
                )
    scenario_calibration = policy.get("scenario_calibration") or {}
    for scenario, spec in scenario_calibration.items():
        if spec.get("classification") != "advisory_only":
            problems.append(
                f"scenario {scenario}: absolute wall time classified "
                f"{spec.get('classification')!r} — must be advisory_only"
            )
    for key in ("thresholds",):
        if policy.get(key) is not None:
            problems.append(f"policy must not contain thresholds ({key} must be null)")
    return problems


# ---------------------------------------------------------------------------
# correctness gates
# ---------------------------------------------------------------------------


def _correctness_gates(
    candidate: dict[str, Any], mode: str
) -> tuple[list[str], bool]:
    problems: list[str] = []
    if candidate.get("benchmark_contract") != "dbfbridge-direct-write-v1":
        problems.append("wrong benchmark contract")
    if candidate.get("benchmark_contract_version") != 1:
        problems.append("wrong benchmark contract version")
    if candidate.get("mode") != mode:
        problems.append(f"mode must be {mode!r}")
    rows = candidate.get("scenarios") or []
    scenario_names = [row.get("scenario") for row in rows if isinstance(row, dict)]
    if len(scenario_names) != len(set(scenario_names)):
        problems.append("duplicate scenario in candidate")
    if mode == "full":
        missing = sorted(
            set(_SCENARIO_ORDER) - set(scenario_names)
        )
        if missing:
            problems.append(f"missing scenarios {missing}")
        unexpected = sorted(
            str(name) for name in set(scenario_names) - set(_SCENARIO_ORDER)
        )
        if unexpected:
            problems.append(f"unexpected scenarios {unexpected}")
    if candidate.get("validation_problems"):
        problems.append("candidate validation_problems not empty")
    w12_row = next(
        (row for row in rows if row.get("scenario") == "cancellation_cleanup_smoke"),
        None,
    )
    w10_row = next(
        (row for row in rows if row.get("scenario") == "direct_write_varchar_nullflags"),
        None,
    )
    for row in rows:
        scenario = row.get("scenario")
        if row.get("status") != "MEASURED":
            problems.append(f"{scenario}: status must be MEASURED")
        if row.get("intermediate_jsonl_bytes") != 0:
            problems.append(f"{scenario}: intermediate_jsonl_bytes must be 0")
        if scenario != "cancellation_cleanup_smoke" and row.get(
            "temporary_bytes_left"
        ) != 0:
            problems.append(f"{scenario}: temporary residue must be 0")
    if mode == "full" and (
        not scenario_names or len(scenario_names) != len(_SCENARIO_ORDER)
    ):
        problems.append("full mode requires all W1-W12 exactly once")
    if w10_row is not None and mode == "full":
        spool = w10_row.get("private_spool_bytes_written")
        if not isinstance(spool, int) or spool <= 0:
            problems.append("W10 must show real disk-spool evidence in full mode")
    if w12_row is not None:
        validation = w12_row.get("validation") or {}
        if (
            w12_row.get("scenario_kind") != "functional_cleanup"
            or w12_row.get("records_per_second") is not None
            or not validation.get("cleanup_verified")
        ):
            problems.append(
                "W12 must be represented as functional_cleanup evidence"
            )
    return problems, not problems


# ---------------------------------------------------------------------------
# comparability
# ---------------------------------------------------------------------------

_FULLY_COMPARED = (
    ("runner_os", "runner_os"),
    ("runner_arch", "runner_arch"),
    ("install_recipe", "install_recipe"),
    ("dependencies", "dependencies"),
)


def _python_major_minor(version: str) -> str:
    return ".".join(str(version).split(".")[:2])


def _comparability(
    policy: dict[str, Any], provenance: dict[str, Any] | None
) -> tuple[str, list[str]]:
    """Classify candidate provenance against the policy runtime recipe."""
    if provenance is None:
        return "NOT_COMPARABLE", ["candidate provenance missing"]
    checks: list[str] = []
    problems: list[str] = []
    recipe_parts = str(policy.get("runtime_recipe") or "").split("|")
    policy_runner_os = recipe_parts[0] if len(recipe_parts) > 0 else ""
    policy_runner_arch = recipe_parts[1] if len(recipe_parts) > 1 else ""
    policy_python_minor = (
        recipe_parts[2].replace("python-", "") if len(recipe_parts) > 2 else ""
    )
    policy_install = recipe_parts[3] if len(recipe_parts) > 3 else ""
    policy_deps_raw = recipe_parts[4:] if len(recipe_parts) > 4 else []
    policy_deps = dict(part.split("=", 1) for part in policy_deps_raw if "=" in part)

    candidate_runner_os = provenance.get("runner_os")
    candidate_runner_arch = provenance.get("runner_arch")
    candidate_python_minor = ".".join(
        str(provenance.get("python_version", "")).split(".")[:2]
    )
    candidate_install = provenance.get("install_recipe")

    pairs = [
        ("runner_os", candidate_runner_os, policy_runner_os),
        ("runner_arch", candidate_runner_arch, policy_runner_arch),
        ("python major.minor", candidate_python_minor, policy_python_minor),
        ("install_recipe", candidate_install, policy_install),
    ]
    for field, actual, expected in pairs:
        if actual == expected:
            checks.append(f"{field}: MATCH ({actual!r})")
        else:
            checks.append(f"{field}: MISMATCH ({actual!r} vs {expected!r})")
            problems.append(field)
    for dependency in ("dbf", "dbfread", "psutil"):
        actual = (provenance.get("dependencies") or {}).get(dependency)
        expected = policy_deps.get(dependency)
        if actual == expected:
            checks.append(f"{dependency}: MATCH ({actual!r})")
        else:
            checks.append(f"{dependency}: MISMATCH ({actual!r} vs {expected!r})")
            problems.append(dependency)
    if not candidate_runner_os or not candidate_runner_arch:
        return "NOT_COMPARABLE", checks
    if not problems:
        return "COMPARABLE", checks
    if len(problems) <= 2:
        return "PARTIALLY_COMPARABLE", checks
    return "NOT_COMPARABLE", checks

def _evaluate_ratio(
    label: str,
    spec: dict[str, Any],
    rows_by_scenario: dict[str, dict[str, Any]],
    mode: str,
    comparability: str,
) -> dict[str, Any]:
    """Evaluate ONE hard-gate ratio against the policy envelope.

    Not-comparable evidence degrades the gate to NOT_COMPARABLE (never a
    false regression); smoke candidates without the required scenarios
    report NOT_EVALUATED_IN_SMOKE (never PASS).
    """
    numerator_scenario, denominator_scenario = _RATIO_DEFINITION_KEYS[label]
    numerator_row = rows_by_scenario.get(numerator_scenario)
    denominator_row = rows_by_scenario.get(denominator_scenario)
    metric = (
        "peak_rss_delta_bytes"
        if label.endswith("peak-RSS-delta ratio")
        else "wall_seconds"
    )
    if (
        numerator_row is None
        or denominator_row is None
        or numerator_row.get(metric) is None
        or denominator_row.get(metric) is None
    ):
        return {
            "label": label,
            "gate": spec.get("classification"),
            "status": "NOT_EVALUATED_IN_SMOKE",
            "reason": f"{numerator_scenario}/{denominator_scenario} not present in smoke candidate",
        }
    numerator_value = float(numerator_row[metric])
    denominator_value = float(denominator_row[metric])
    numerator_count = numerator_row.get("record_count")
    denominator_count = denominator_row.get("record_count")
    if (
        not isinstance(numerator_count, int)
        or numerator_count <= 0
        or not isinstance(denominator_count, int)
        or denominator_count <= 0
    ):
        return {
            "label": label,
            "gate": spec.get("classification"),
            "status": "NOT_EVALUATED_IN_SMOKE",
            "reason": "record counts missing in smoke candidate",
        }
    value = (numerator_value / numerator_count) / (
        denominator_value / denominator_count
    )
    envelope_upper = float(spec["envelope_upper"])
    center = float(spec["center"])
    if comparability == "COMPARABLE":
        status = "REGRESSION" if value > envelope_upper else "PASS"
    else:
        status = f"NOT_COMPARABLE ({comparability})"
    return {
        "label": label,
        "gate": spec.get("classification"),
        "status": status,
        "value": round(value, 9),
        "envelope_upper": envelope_upper,
        "center": center,
        "numerator_wall": numerator_value,
        "denominator_wall": denominator_value,
        "record_count_ratio": round(numerator_count / denominator_count, 4),
    }


def compare_candidate(
    policy: dict[str, Any],
    candidate: dict[str, Any],
    provenance: dict[str, Any] | None,
    *,
    mode: str,
) -> dict[str, Any]:
    """Full deterministic comparison: correctness, comparability, performance."""
    policy_problems = validate_policy(policy)
    if policy_problems:
        return {
            "result_contract": RESULT_CONTRACT,
            "overall_status": "INVALID_POLICY",
            "correctness": {"status": "NOT_EVALUATED"},
            "comparability": {"classification": None, "checks": []},
            "performance": {"mode": mode, "hard_gates": [], "advisory": []},
            "problems": policy_problems,
        }
    correctness_problems, correctness_ok = _correctness_gates(candidate, mode)
    comparability_classification, comparability_checks = _comparability(
        policy, provenance
    )
    rows_by_scenario = {
        row.get("scenario"): row for row in candidate.get("scenarios", [])
    }
    hard_gates: list[dict[str, Any]] = []
    confirmed_regression = False
    for label in sorted(policy.get("ratio_calibration") or {}):
        spec = policy["ratio_calibration"][label]
        if spec.get("classification") != "hard_gate":
            continue
        gate = _evaluate_ratio(
            label, spec, rows_by_scenario, mode, comparability_classification
        )
        hard_gates.append(gate)
        if (
            gate["status"] == "REGRESSION"
            and comparability_classification == "COMPARABLE"
        ):
            confirmed_regression = True
    advisory = []
    for scenario, spec in (policy.get("scenario_calibration") or {}).items():
        row = rows_by_scenario.get(scenario)
        if row is None:
            advisory.append(
                {"scenario": scenario, "status": "NOT_EVALUATED_IN_SMOKE"}
            )
            continue
        wall = row.get("wall_seconds")
        advisory.append(
            {
                "scenario": scenario,
                "status": "INFORMATIONAL",
                "wall_seconds": wall,
                "advisory_envelope_upper": spec.get("advisory_envelope_upper"),
                "classification": "advisory_only",
            }
        )
    if not correctness_ok:
        overall = "INCORRECT"
    elif confirmed_regression:
        overall = "REGRESSION"
    elif comparability_classification == "NOT_COMPARABLE":
        overall = "NOT_COMPARABLE_PASS"
    else:
        overall = "PASS"
    return {
        "result_contract": RESULT_CONTRACT,
        "overall_status": overall,
        "correctness": {
            "status": "PASS" if correctness_ok else "FAIL",
            "gates": list(_correctness_gate_names()),
            "problems": correctness_problems,
        },
        "comparability": {
            "classification": comparability_classification,
            "checks": comparability_checks,
        },
        "performance": {
            "mode": mode,
            "hard_gates": hard_gates,
            "advisory": advisory,
        },
    }



def _correctness_gate_names() -> list[str]:
    return [
        "benchmark contract/version",
        "mode",
        "scenarios complete/unique",
        "MEASURED status",
        "validation_problems empty",
        "intermediate_jsonl_bytes == 0",
        "temporary residue == 0",
        "W10 spool > 0 (full)",
        "W12 functional_cleanup",
    ]


def markdown_summary(payload: dict[str, Any]) -> str:
    lines = [
        "# Direct Write regression comparison result",
        "",
        f"Overall: `{payload.get('overall_status')}` · ",
    ]
    return "\\n".join(lines) + "\\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--candidate-provenance", type=Path, default=None)
    parser.add_argument("--mode", choices=["smoke", "full"], default="full")
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, default=None)
    args = parser.parse_args(argv)
    policy = json.loads(Path(args.policy).read_text(encoding="utf-8"))
    candidate = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
    provenance = None
    if args.candidate_provenance and Path(args.candidate_provenance).is_file():
        provenance = json.loads(Path(args.candidate_provenance).read_text(encoding="utf-8"))
    payload = compare_candidate(policy, candidate, provenance, mode=args.mode)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\\n",
        encoding="utf-8",
    )
    if args.output_md:
        args.output_md.write_text(markdown_summary(payload), encoding="utf-8")
    print(f"overall_status={payload['overall_status']}")
    return 0 if payload["overall_status"] in ("PASS", "NOT_COMPARABLE_PASS") else 1


if __name__ == "__main__":
    raise SystemExit(main())

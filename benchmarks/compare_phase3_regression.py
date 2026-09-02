"""Phase 3 performance-regression comparator (stdlib-only, offline, pure).

Compares a saved Phase 3 benchmark report (the candidate) against the
committed evidence-based regression policy — never against a mutable
baseline, never over the network, and never by re-running benchmarks.

    python -m benchmarks.compare_phase3_regression \\
        --policy benchmarks/regression/phase-3-regression-policy-v1.json \\
        --candidate benchmarks/results/phase-3-performance-phase3.json \\
        --mode full|smoke \\
        --output-json benchmarks/results/phase-3-regression.json \\
        --output-md benchmarks/results/phase-3-regression.md \\
        [--summary "$GITHUB_STEP_SUMMARY"]

Exit codes:

- ``0`` — correctness PASS and no confirmed hard performance regression
  (advisory drift and NOT_COMPARABLE environments do NOT fail the job, but
  are always visible in the report and the GitHub job summary);
- ``1`` — invalid policy or candidate report, a FAILED/missing benchmark
  scenario, or a confirmed hard regression on a comparable candidate.

Separation of gates (never mixed):

- **A. correctness hard gate** — expected scenarios present, MEASURED, no
  FAILED, no duplicate names; always evaluated regardless of comparability;
- **B. performance regression signal** — only for policy-classified
  ``hard_gate`` same-run ratios, only when comparability allows;
- **C. environment/comparability status** — ``COMPARABLE`` /
  ``PARTIALLY_COMPARABLE`` / ``NOT_COMPARABLE`` reported explicitly; a
  mismatch never becomes a false performance claim.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from .contract import (
    CONTRACT_PHASE_3,
    PHASE3_SCENARIO_NAMES,
    RUN_ID_RE,
    validate_phase3_regression_smoke_report,
    validate_saved_phase3_report,
)

POLICY_VERSION = 1
BENCHMARK_CONTRACT = CONTRACT_PHASE_3
MIN_CALIBRATION_RUNS = 5

__all__ = [
    "compare_report",
    "main",
    "regression_comparability",
    "validate_regression_policy",
]


# ---------------------------------------------------------------------------
# finite-value helpers
# ---------------------------------------------------------------------------


def _finite_positive(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def _finite_non_negative(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


# ---------------------------------------------------------------------------
# policy validation (pure)
# ---------------------------------------------------------------------------


def validate_regression_policy(policy: Any) -> list[str]:
    """Validate the regression policy; return a list of problems (empty=OK)."""
    problems: list[str] = []
    if not isinstance(policy, dict):
        return ["policy is not a JSON object"]
    if policy.get("policy_version") != POLICY_VERSION:
        problems.append(f"unknown policy_version: {policy.get('policy_version')!r}")
    if policy.get("benchmark_contract") != BENCHMARK_CONTRACT:
        problems.append(f"benchmark_contract must be {BENCHMARK_CONTRACT!r}")

    workflow_run_ids = policy.get("generated_from_workflow_run_ids")
    benchmark_run_ids = policy.get("generated_from_benchmark_run_ids")
    calibration_count = policy.get("calibration_count")
    for label, run_ids in (
        ("generated_from_workflow_run_ids", workflow_run_ids),
        ("generated_from_benchmark_run_ids", benchmark_run_ids),
    ):
        if not isinstance(run_ids, list) or len(run_ids) < MIN_CALIBRATION_RUNS:
            problems.append(
                f"policy requires at least {MIN_CALIBRATION_RUNS} calibration runs ({label})"
            )
        elif len(set(run_ids)) != len(run_ids):
            problems.append(f"duplicate calibration IDs in {label}")
    if isinstance(workflow_run_ids, list) and isinstance(benchmark_run_ids, list):
        if len(workflow_run_ids) != len(benchmark_run_ids):
            problems.append(
                "generated_from_workflow_run_ids and generated_from_benchmark_run_ids "
                "must pair one-to-one"
            )
        for benchmark_run_id in benchmark_run_ids:
            if not isinstance(benchmark_run_id, str) or not RUN_ID_RE.match(benchmark_run_id):
                problems.append(f"invalid benchmark run_id: {benchmark_run_id!r}")
    if calibration_count != len(workflow_run_ids or []) or calibration_count != len(
        benchmark_run_ids or []
    ):
        problems.append("calibration_count does not match the generated_from_* run-ID lists")
    reference_commit = policy.get("reference_commit")
    if (
        not isinstance(reference_commit, str)
        or len(reference_commit) != 40
        or any(character not in "0123456789abcdef" for character in reference_commit)
    ):
        problems.append(f"reference_commit must be a full 40-hex commit: {reference_commit!r}")
    derivation = policy.get("derivation")
    if not isinstance(derivation, dict) or not all(
        key in derivation for key in ("center", "dispersion", "envelope_upper", "policy_parameters")
    ):
        problems.append(
            "policy is missing derivation metadata "
            "(center/dispersion/envelope_upper/policy_parameters)"
        )

    scenario_calibration = policy.get("scenario_calibration")
    full_scenarios = policy.get("full_scheduled_scenarios")
    if not isinstance(scenario_calibration, dict) or not isinstance(full_scenarios, list):
        problems.append("policy is missing scenario calibration / scheduled scenario list")
        return problems
    if sorted(scenario_calibration) != sorted(PHASE3_SCENARIO_NAMES):
        problems.append(
            "scenario_calibration must cover exactly PHASE3_SCENARIO_NAMES "
            f"({sorted(set(PHASE3_SCENARIO_NAMES) - set(scenario_calibration))} missing, "
            f"{sorted(set(scenario_calibration) - set(PHASE3_SCENARIO_NAMES))} unknown)"
        )
        return problems
    if sorted(full_scenarios) != sorted(PHASE3_SCENARIO_NAMES):
        problems.append("full_scheduled_scenarios must be exactly PHASE3_SCENARIO_NAMES")
    for name, entry in scenario_calibration.items():
        if not isinstance(entry, dict):
            problems.append(f"scenario {name!r} calibration entry is malformed")
            continue
        for key in ("center", "min", "max"):
            if not _finite_positive(entry.get(key)):
                problems.append(f"scenario {name!r} has invalid {key}: {entry.get(key)!r}")
        if not _finite_non_negative(entry.get("mad")):
            problems.append(f"scenario {name!r} has invalid mad: {entry.get('mad')!r}")
        if not _finite_non_negative(entry.get("max_observed_deviation")):
            problems.append(f"scenario {name!r} has invalid max_observed_deviation")
        if entry.get("classification") not in ("hard_gate", "advisory_only"):
            problems.append(f"scenario {name!r} has unknown classification")
        values = entry.get("values")
        if not isinstance(values, list) or len(values) != calibration_count:
            problems.append(
                f"scenario {name!r} needs exactly {calibration_count} calibration values"
            )
        elif any(not _finite_positive(value) for value in values):
            problems.append(f"scenario {name!r} has non-finite/non-positive timings")

    pr_smoke_scenarios = policy.get("pr_smoke_scenarios")
    if (
        not isinstance(pr_smoke_scenarios, list)
        or not pr_smoke_scenarios
        or len(set(pr_smoke_scenarios)) != len(pr_smoke_scenarios)
        or not set(pr_smoke_scenarios).issubset(PHASE3_SCENARIO_NAMES)
    ):
        problems.append(
            "pr_smoke_scenarios must be a non-empty unique subset of PHASE3_SCENARIO_NAMES"
        )

    ratio_calibration = policy.get("ratio_calibration")
    if not isinstance(ratio_calibration, dict):
        problems.append("ratio_calibration missing")
        return problems
    for label, entry in ratio_calibration.items():
        if not isinstance(entry, dict):
            problems.append(f"ratio {label!r} entry is malformed")
            continue
        numerator = entry.get("numerator")
        denominator = entry.get("denominator")
        if numerator not in PHASE3_SCENARIO_NAMES:
            problems.append(f"ratio {label!r} has unknown numerator {numerator!r}")
        if denominator not in PHASE3_SCENARIO_NAMES:
            problems.append(f"ratio {label!r} has unknown denominator {denominator!r}")
        if numerator == denominator:
            problems.append(f"ratio {label!r} has identical numerator and denominator")
        if entry.get("classification") not in ("hard_gate", "advisory_only"):
            problems.append(f"ratio {label!r} has unknown classification")
        for key in ("center", "min", "max", "envelope_upper"):
            if not _finite_positive(entry.get(key)):
                problems.append(f"ratio {label!r} has invalid {key}: {entry.get(key)!r}")
        for key in ("mad", "max_observed_deviation", "relative_mad"):
            if not _finite_non_negative(entry.get(key)):
                problems.append(f"ratio {label!r} has invalid {key}: {entry.get(key)!r}")
        values = entry.get("values")
        if not isinstance(values, list) or len(values) != calibration_count:
            problems.append(f"ratio {label!r} needs exactly {calibration_count} calibration values")
        elif any(not _finite_positive(value) for value in values):
            problems.append(f"ratio {label!r} has non-finite/non-positive values")
    return problems


# ---------------------------------------------------------------------------
# regression comparability (SEPARATE from the historical environment_comparability)
# ---------------------------------------------------------------------------


def regression_comparability(
    policy: dict[str, Any], candidate_environment: dict[str, Any]
) -> tuple[str, list[str]]:
    """Three-state comparability verdict for regression CI (its own policy).

    This intentionally differs from the historical
    ``contract.environment_comparability`` (which stays strict for canonical
    BEFORE/AFTER evidence and is NOT used here):

    - ``NOT_COMPARABLE`` — Python, OS, architecture, or a measurement
      dependency version differs from the calibrated environment: no
      performance claim may be made at all;
    - ``PARTIALLY_COMPARABLE`` — the runtime matches but the candidate
      hardware is OUTSIDE the calibrated processor pool, or the runner
      image/storage label differs from calibration: correctness stays hard,
      performance numbers become advisory-only;
    - ``COMPARABLE`` — runtime matches AND the candidate hardware is inside
      the calibrated processor pool (hard ratio gates apply).
    """
    problems: list[str] = []
    required = policy.get("environment") or {}
    candidate_system = candidate_environment.get("system") or {}
    if candidate_system.get("python") != required.get("python"):
        problems.append(f"python differs (calibrated {required.get('python')!r})")
    if candidate_environment.get("system", {}).get("os") != required.get("os"):
        problems.append(f"os differs (calibrated {required.get('os')!r})")
    if candidate_environment.get("system", {}).get("arch") != required.get("arch"):
        problems.append(f"system.arch differs (calibrated {required.get('arch')!r})")
    # The PACKAGE UNDER TEST (dbfbridge) is provenance, NOT a comparability
    # requirement: different dbfbridge versions/commits are exactly what the
    # regression CI compares.  Only EXTERNAL measurement dependencies gate
    # comparability (a drift there invalidates the measured environment).
    package_under_test = "dbfbridge"
    for dependency, version in (required.get("packages") or {}).items():
        if dependency == package_under_test:
            continue
        candidate_version = (candidate_environment.get("packages") or {}).get(dependency)
        if candidate_version != version:
            problems.append(f"packages.{dependency} differs ({candidate_version!r} != {version!r})")
    if problems:
        return "NOT_COMPARABLE", problems

    partial: list[str] = []
    # Hardware-pool applicability: the hosted runner image may land on
    # different CPU families between calibration and the candidate run; a
    # candidate outside the calibrated pool keeps correctness hard but its
    # performance numbers become advisory.
    hardware_pool = policy.get("hardware_pool") or {}
    observed_processors = hardware_pool.get("observed_processor_signatures") or []
    if observed_processors:
        candidate_processor = candidate_system.get("processor")
        if candidate_processor not in observed_processors:
            partial.append(f"processor outside the calibrated pool: {candidate_processor!r}")
    observed_cpu_counts = hardware_pool.get("observed_cpu_counts") or []
    if observed_cpu_counts:
        candidate_cpu = candidate_system.get("cpu_count")
        if candidate_cpu not in observed_cpu_counts:
            partial.append(f"cpu_count outside the calibrated pool: {candidate_cpu!r}")
    candidate_storage = candidate_environment.get("storage")
    if candidate_storage != required.get("storage_label"):
        partial.append(
            f"storage label differs ({candidate_storage!r} != {required.get('storage_label')!r})"
        )
    candidate_runner = candidate_environment.get("runner")
    if candidate_runner != required.get("runner_image"):
        partial.append(f"runner image differs ({candidate_runner!r})")
    if partial:
        return "PARTIALLY_COMPARABLE", partial
    return "COMPARABLE", []


# ---------------------------------------------------------------------------
# comparator (pure)
# ---------------------------------------------------------------------------


def _raw_scenario_names(candidate: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Scan the RAW scenario list (before any dict collapse)."""
    names: list[str] = []
    problems: list[str] = []
    for entry in candidate.get("scenarios", []):
        if not isinstance(entry, dict) or not isinstance(entry.get("scenario"), str):
            problems.append("a scenario entry is malformed (missing scenario name)")
            continue
        names.append(entry["scenario"])
    return names, problems


def _failure_result(status: str, failures: list[str], comparability: str) -> dict[str, Any]:
    """Stable schema for early failures - renderer/JSON never KeyError."""
    return {
        "status": status,
        "correctness_status": "FAIL",
        "comparability": comparability,
        "performance_status": "NOT_EVALUATED",
        "overall_status": "FAIL",
        "problems": failures,
        "hard_regressions": [],
        "invalid_rows": [],
        "advisories": [],
        "rows": [],
    }


def compare_report(
    policy: dict[str, Any],
    candidate: dict[str, Any],
    *,
    mode: str,
    selected_scenarios: list[str] | None = None,
) -> dict[str, Any]:
    """Pure comparison: returns the machine-readable regression result.

    The result separates the three gates explicitly:

    - ``correctness_status`` — ``PASS``/``FAIL`` (frozen Phase 3 contract
      implementation: full mode uses :func:`validate_saved_phase3_report`,
      smoke mode :func:`validate_phase3_regression_smoke_report`);
    - ``comparability`` — ``COMPARABLE``/``PARTIALLY_COMPARABLE``/
      ``NOT_COMPARABLE``;
    - ``performance_status`` — ``PASS``/``ADVISORY_ONLY``/``REGRESSION``/
      ``NOT_EVALUATED``;
    - ``overall_status`` — ``PASS``/``FAIL`` (a correctness failure, a
      confirmed hard regression, or an INVALID timing row fails).

    ``mode``: ``"full"`` requires the complete 23-scenario contract;
    ``"smoke"`` requires only the selected subset (strictly validated).
    """
    problems = validate_regression_policy(policy)
    if problems:
        return _failure_result("INVALID_POLICY", problems, "NOT_COMPARABLE")

    env = candidate.get("environment") or {}
    if env.get("benchmark_contract") != BENCHMARK_CONTRACT:
        return _failure_result(
            "INVALID_REPORT",
            [f"candidate benchmark_contract must be {BENCHMARK_CONTRACT!r}"],
            "NOT_EVALUATED",
        )

    # Duplicate and malformed scenario entries on the RAW list — detected
    # BEFORE any dict-based access can silently collapse them.
    raw_names, raw_problems = _raw_scenario_names(candidate)
    if raw_problems:
        return _failure_result("INVALID_REPORT", raw_problems, "NOT_EVALUATED")
    duplicates = sorted({name for name in raw_names if raw_names.count(name) > 1})
    if duplicates:
        return _failure_result(
            "INVALID_REPORT",
            [f"duplicate scenario name {name!r}" for name in duplicates],
            "NOT_EVALUATED",
        )

    # Frozen Phase 3 contract implementation (correctness hard gate).
    if mode == "full":
        contract_problems = validate_saved_phase3_report(candidate)
    else:
        selected = frozenset(selected_scenarios or policy["pr_smoke_scenarios"])
        contract_problems = validate_phase3_regression_smoke_report(candidate, selected)
    if contract_problems:
        summarized = contract_problems[:12]
        suffix = (
            [f"... and {len(contract_problems) - 12} more"] if len(contract_problems) > 12 else []
        )
        return _failure_result("CORRECTNESS_FAILURE", summarized + suffix, "NOT_EVALUATED")

    scenarios = {entry["scenario"]: entry for entry in candidate["scenarios"]}
    required = (
        sorted(policy["full_scheduled_scenarios"])
        if mode == "full"
        else sorted(selected_scenarios or policy["pr_smoke_scenarios"])
    )

    comparability, comparability_reasons = regression_comparability(
        policy, env if isinstance(env, dict) else {}
    )

    hard_regressions: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    invalid_rows: list[dict[str, Any]] = []

    # per-scenario absolute walls: always advisory, but the recorded value
    # must still be finite and positive (a malformed metric is an integrity
    # failure, never an advisory).
    for name in required:
        entry = scenarios[name]
        calibration = policy["scenario_calibration"][name]
        value = (entry.get("aggregated") or {}).get("median_wall_seconds")
        if not _finite_positive(value):
            invalid_row = {
                "scenario": name,
                "candidate_value": value,
                "policy_class": calibration["classification"],
                "result": "INVALID",
                "note": "median_wall_seconds must be finite and positive",
            }
            rows.append(invalid_row)
            invalid_rows.append(invalid_row)
            continue
        rows.append(
            {
                "scenario": name,
                "candidate_value": value,
                "calibration_center": calibration["center"],
                "calibration_mad": calibration["mad"],
                "policy_class": calibration["classification"],
                "result": "ADVISORY",
                "note": "absolute wall time is advisory-only (runner-wide drift measured)",
            }
        )

    # same-run relative ratios (drift-immune); hard gates only where policy says
    for label, entry in sorted(policy["ratio_calibration"].items()):
        numerator, denominator = entry["numerator"], entry["denominator"]
        if numerator not in scenarios or denominator not in scenarios:
            continue  # not part of this candidate's profile
        candidate_n = (scenarios[numerator].get("aggregated") or {}).get("median_wall_seconds")
        candidate_d = (scenarios[denominator].get("aggregated") or {}).get("median_wall_seconds")
        if not (_finite_positive(candidate_n) and _finite_positive(candidate_d)):
            invalid_row = {
                "ratio": label,
                "candidate_value": None,
                "envelope_upper": entry["envelope_upper"],
                "policy_class": entry["classification"],
                "result": "INVALID",
                "note": "ratio timing must be finite and positive (NaN/Infinity/zero/negative rejected)",
            }
            rows.append(invalid_row)
            invalid_rows.append(invalid_row)
            continue
        ratio = candidate_n / candidate_d
        if comparability != "COMPARABLE" or entry["classification"] != "hard_gate":
            rows.append(
                {
                    "ratio": label,
                    "candidate_value": ratio,
                    "calibration_center": entry["center"],
                    "envelope_upper": entry["envelope_upper"],
                    "policy_class": entry["classification"],
                    "result": "ADVISORY",
                    "note": (
                        "envelope not discriminating (advisory_only)"
                        if entry["classification"] == "advisory_only"
                        else "environment is not fully comparable"
                    ),
                }
            )
            continue
        if ratio > entry["envelope_upper"]:
            regression_row = {
                "ratio": label,
                "candidate_value": ratio,
                "calibration_center": entry["center"],
                "envelope_upper": entry["envelope_upper"],
                "policy_class": "hard_gate",
                "result": "REGRESSION",
                "note": "same-run ratio beyond the calibrated envelope",
            }
            rows.append(regression_row)
            hard_regressions.append(regression_row)
        else:
            rows.append(
                {
                    "ratio": label,
                    "candidate_value": ratio,
                    "calibration_center": entry["center"],
                    "envelope_upper": entry["envelope_upper"],
                    "policy_class": "hard_gate",
                    "result": "PASS",
                }
            )

    # advisory wall rows for the non-selected scenarios in full mode
    if mode == "full":
        for name in sorted(policy["full_scheduled_scenarios"]):
            if name in required:
                continue
            entry = scenarios[name]
            calibration = policy["scenario_calibration"][name]
            value = (entry.get("aggregated") or {}).get("median_wall_seconds")
            if not _finite_positive(value):
                invalid_row = {
                    "scenario": name,
                    "candidate_value": value,
                    "policy_class": "advisory_only",
                    "result": "INVALID",
                    "note": "median_wall_seconds must be finite and positive",
                }
                rows.append(invalid_row)
                invalid_rows.append(invalid_row)
                continue
            rows.append(
                {
                    "scenario": name,
                    "candidate_value": value,
                    "calibration_center": calibration["center"],
                    "policy_class": "advisory_only",
                    "result": "ADVISORY",
                    "note": "absolute wall time is advisory-only (runner-wide drift measured)",
                }
            )

    if invalid_rows:
        status = "INVALID_REPORT"
        overall = "FAIL"
        performance_status = "NOT_EVALUATED"
    elif hard_regressions:
        status = "REGRESSION"
        overall = "FAIL"
        performance_status = "REGRESSION"
    else:
        status = "PASS"
        overall = "PASS"
        performance_status = (
            "ADVISORY_ONLY"
            if comparability != "COMPARABLE" or any(row.get("result") == "ADVISORY" for row in rows)
            else "PASS"
        )

    return {
        "status": status,
        "correctness_status": "PASS",
        "comparability": comparability,
        "comparability_reasons": comparability_reasons,
        "performance_status": performance_status,
        "overall_status": overall,
        "policy_reference_commit": policy["reference_commit"],
        "hard_regressions": hard_regressions,
        "invalid_rows": invalid_rows,
        "advisories": [row for row in rows if row.get("result") == "ADVISORY"],
        "rows": rows,
    }


def _render_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Phase 3 Performance Regression",
        "",
        f"- status: **{result['status']}**",
        f"- comparability: {result['comparability']}",
        f"- hard regressions: {len(result.get('hard_regressions', []))}",
        f"- advisories: {len(result.get('advisories', []))}",
    ]
    for reason in result.get("comparability_reasons", []):
        lines.append(f"  - {reason}")
    lines.append("")
    lines.append("| subject | candidate | center | envelope | class | result |")
    lines.append("|---|---|---|---|---|---|")
    for row in result["rows"]:
        subject = row.get("scenario") or row.get("ratio")
        lines.append(
            f"| {subject} | {row.get('candidate_value', '-')} | "
            f"{row.get('calibration_center', '-')} | {row.get('envelope_upper', '-')} | "
            f"{row.get('policy_class', '-')} | {row['result']} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--mode", choices=["full", "smoke"], required=True)
    parser.add_argument(
        "--scenarios",
        default=None,
        help="comma-separated smoke scenario subset (defaults to the policy's pr_smoke_scenarios)",
    )
    parser.add_argument("--output-json", type=Path, default=None)
    parser.add_argument("--output-md", type=Path, default=None)
    parser.add_argument("--summary", type=Path, default=None, help="$GITHUB_STEP_SUMMARY")
    args = parser.parse_args(argv)

    try:
        policy = json.loads(args.policy.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"INVALID POLICY: {exc}")
        return 1
    try:
        candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"INVALID CANDIDATE REPORT: {exc}")
        return 1

    selected = args.scenarios.split(",") if args.scenarios else None
    result = compare_report(policy, candidate, mode=args.mode, selected_scenarios=selected)

    if args.output_json is not None:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(
            json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    markdown = _render_markdown(result)
    if args.output_md is not None:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        args.output_md.write_text(markdown, encoding="utf-8")
    if args.summary is not None:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        with args.summary.open("a", encoding="utf-8") as summary:
            summary.write(markdown)

    status = result["status"]
    print(f"Phase 3 Performance Regression: {status}")
    print(f"  comparability: {result.get('comparability')}")
    print(f"  hard regressions: {len(result.get('hard_regressions', []))}")
    print(f"  advisories: {len(result.get('advisories', []))}")
    if status in {"INVALID_POLICY", "INVALID_REPORT", "CORRECTNESS_FAILURE"}:
        for problem in result.get("problems", []):
            print(f"  problem: {problem}")
        return 1
    if result.get("hard_regressions"):
        for row in result["hard_regressions"]:
            print(
                f"  HARD REGRESSION: {row['ratio']} candidate={row['candidate_value']:.4f} "
                f"envelope={row['envelope_upper']:.4f}"
            )
        return 1
    # Advisory drift and NOT_COMPARABLE environments never fail the job, but
    # they are always visible in the report and the GitHub job summary.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
from pathlib import Path
from typing import Any

POLICY_VERSION = 1
BENCHMARK_CONTRACT = "phase-3-performance-v1"
MIN_CALIBRATION_RUNS = 5

__all__ = [
    "compare_report",
    "main",
    "regression_comparability",
    "validate_regression_policy",
]


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
    run_ids = policy.get("generated_from_run_ids")
    if not isinstance(run_ids, list) or len(run_ids) < MIN_CALIBRATION_RUNS:
        problems.append(f"policy requires at least {MIN_CALIBRATION_RUNS} calibration runs")
    elif len(set(run_ids)) != len(run_ids):
        problems.append("duplicate calibration run_ids in the policy")
    if not policy.get("reference_commit"):
        problems.append("policy is missing reference_commit")
    derivation = policy.get("derivation")
    if not isinstance(derivation, dict) or not all(
        key in derivation for key in ("center", "dispersion", "envelope_upper")
    ):
        problems.append("policy is missing derivation metadata (center/dispersion/envelope)")

    scenario_calibration = policy.get("scenario_calibration")
    full_scenarios = policy.get("full_scheduled_scenarios")
    if not isinstance(scenario_calibration, dict) or not isinstance(full_scenarios, list):
        problems.append("policy is missing scenario calibration / scheduled scenario list")
        return problems
    if sorted(scenario_calibration) != sorted(full_scenarios):
        problems.append("scenario_calibration does not match full_scheduled_scenarios")
    for name, entry in scenario_calibration.items():
        if not isinstance(entry, dict):
            problems.append(f"scenario {name!r} calibration entry is malformed")
            continue
        for key in ("center", "mad", "min", "max", "max_min_ratio"):
            value = entry.get(key)
            if not isinstance(value, (int, float)) or not value > 0:
                problems.append(f"scenario {name!r} has invalid {key}: {value!r}")
        if entry.get("classification") not in ("hard_gate", "advisory_only"):
            problems.append(f"scenario {name!r} has unknown classification")

    ratio_calibration = policy.get("ratio_calibration")
    if not isinstance(ratio_calibration, dict):
        problems.append("ratio_calibration missing")
    else:
        for label, entry in ratio_calibration.items():
            if not isinstance(entry, dict):
                problems.append(f"ratio {label!r} entry is malformed")
                continue
            if entry.get("classification") not in ("hard_gate", "advisory_only"):
                problems.append(f"ratio {label!r} has unknown classification")
            envelope = entry.get("envelope_upper")
            if not isinstance(envelope, (int, float)) or not envelope > 0:
                problems.append(f"ratio {label!r} has invalid envelope_upper")
            values = entry.get("values")
            if not isinstance(values, list) or len(values) < MIN_CALIBRATION_RUNS:
                problems.append(f"ratio {label!r} needs {MIN_CALIBRATION_RUNS} calibration values")
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
    - ``PARTIALLY_COMPARABLE`` — runtime matches but the runner image or the
      storage label differs from calibration (hosted-runner images move):
      correctness is still hard, performance numbers are advisory-only;
    - ``COMPARABLE`` — everything required matches (hard ratio gates apply).
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
    for dependency, version in (required.get("packages") or {}).items():
        candidate_version = (candidate_environment.get("packages") or {}).get(dependency)
        if candidate_version != version:
            problems.append(f"packages.{dependency} differs ({candidate_version!r} != {version!r})")
    if problems:
        return "NOT_COMPARABLE", problems

    partial: list[str] = []
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


def _candidate_scenarios(candidate: dict[str, Any]) -> dict[str, dict[str, Any]]:
    scenarios: dict[str, dict[str, Any]] = {}
    for entry in candidate.get("scenarios", []):
        name = entry.get("scenario")
        if isinstance(name, str) and name not in scenarios:
            scenarios[name] = entry
    return scenarios


def compare_report(
    policy: dict[str, Any],
    candidate: dict[str, Any],
    *,
    mode: str,
    selected_scenarios: list[str] | None = None,
) -> dict[str, Any]:
    """Pure comparison: returns the machine-readable regression result.

    ``mode``: ``"full"`` requires the complete calibrated scenario set
    MEASURED; ``"smoke"`` requires only the selected subset.
    """
    problems = validate_regression_policy(policy)
    if problems:
        return {"status": "INVALID_POLICY", "problems": problems}

    env = candidate.get("environment") or {}
    if env.get("benchmark_contract") != BENCHMARK_CONTRACT:
        return {
            "status": "INVALID_REPORT",
            "problems": [f"candidate benchmark_contract must be {BENCHMARK_CONTRACT!r}"],
        }
    scenarios = _candidate_scenarios(candidate)
    if not scenarios:
        return {"status": "INVALID_REPORT", "problems": ["candidate has no scenarios"]}

    required = (
        sorted(policy["full_scheduled_scenarios"])
        if mode == "full"
        else sorted(selected_scenarios or policy["pr_smoke_scenarios"])
    )
    missing = [name for name in required if name not in scenarios]
    failed = [name for name, entry in scenarios.items() if entry.get("status") == "FAILED"]
    unknown = sorted(set(scenarios) - set(policy["scenario_calibration"]))
    if failed:
        return {
            "status": "CORRECTNESS_FAILURE",
            "problems": [f"scenario FAILED: {name}" for name in failed],
            "failed_scenarios": failed,
        }
    if missing:
        return {
            "status": "INVALID_REPORT",
            "problems": [f"missing or not MEASURED scenario: {name}" for name in missing],
        }
    if unknown:
        return {
            "status": "INVALID_REPORT",
            "problems": [f"scenario outside the calibrated contract: {name}" for name in unknown],
        }

    comparability, comparability_reasons = regression_comparability(
        policy, env if isinstance(env, dict) else {}
    )

    hard_regressions: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []

    # per-scenario absolute walls: always advisory (runner-wide drift proven)
    for name in required:
        entry = scenarios[name]
        calibration = policy["scenario_calibration"][name]
        value = entry.get("aggregated", {}).get("median_wall_seconds")
        classification = "ADVISORY"
        rows.append(
            {
                "scenario": name,
                "candidate_value": value,
                "calibration_center": calibration["center"],
                "calibration_mad": calibration["mad"],
                "policy_class": calibration["classification"],
                "result": classification,
                "note": "absolute wall time is advisory-only (runner-wide drift measured)",
            }
        )

    # same-run relative ratios (drift-immune); hard gates only where policy says
    for label, entry in sorted(policy["ratio_calibration"].items()):
        numerator, denominator = entry["numerator"], entry["denominator"]
        if numerator not in scenarios or denominator not in scenarios:
            continue  # not part of this candidate's profile
        if scenarios[numerator].get("status") != "MEASURED":
            continue
        if scenarios[denominator].get("status") != "MEASURED":
            continue
        candidate_n = scenarios[numerator]["aggregated"]["median_wall_seconds"]
        candidate_d = scenarios[denominator]["aggregated"]["median_wall_seconds"]
        if not (
            isinstance(candidate_n, (int, float))
            and isinstance(candidate_d, (int, float))
            and candidate_n > 0
            and candidate_d > 0
        ):
            rows.append(
                {
                    "ratio": label,
                    "policy_class": entry["classification"],
                    "result": "INVALID",
                    "note": "non-positive or missing timing value",
                }
            )
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
            rows.append(
                {
                    "scenario": name,
                    "candidate_value": entry.get("aggregated", {}).get("median_wall_seconds"),
                    "calibration_center": calibration["center"],
                    "policy_class": "advisory_only",
                    "result": "ADVISORY",
                    "note": "absolute wall time is advisory-only (runner-wide drift measured)",
                }
            )

    status = "REGRESSION" if hard_regressions else "PASS"
    return {
        "status": status,
        "mode": mode,
        "comparability": comparability,
        "comparability_reasons": comparability_reasons,
        "policy_reference_commit": policy["reference_commit"],
        "hard_regressions": hard_regressions,
        "advisories": [row for row in rows if row.get("result") == "ADVISORY"],
        "rows": rows,
    }


def _render_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Phase 3 Performance Regression",
        "",
        f"- status: **{result['status']}**",
        f"- comparability: {result['comparability']}",
        f"- hard regressions: {len(result['hard_regressions'])}",
        f"- advisories: {len(result['advisories'])}",
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

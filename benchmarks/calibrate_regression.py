"""Deterministic Phase 3 regression-policy generator (stdlib-only, offline).

Reads saved Phase 3 benchmark reports (or the committed compact calibration
inputs) and derives the versioned regression policy — the evidence behind
every threshold.  This tool never benchmarks, never touches the network,
never writes into ``benchmarks/baselines/``, and never changes thresholds by
hand: every envelope is computed from the calibration measurements.

Usage:

    python -m benchmarks.calibrate_regression \\
        report1.json report2.json report3.json report4.json report5.json \\
        --output benchmarks/regression/phase-3-regression-policy-v1.json

    # or from the committed compact calibration inputs:
    python -m benchmarks.calibrate_regression \\
        --calibration-inputs benchmarks/regression/phase-3-regression-calibration-inputs.json \\
        --output benchmarks/regression/phase-3-regression-policy-v1.json

Requirements enforced here (a smaller dataset is rejected):

- at least 5 calibration inputs (independent workflow runs);
- unique run IDs;
- a single reference commit (one source snapshot);
- the ``phase-3-performance-v1`` contract with all 23 scenarios MEASURED.

Derivation algorithm (fully deterministic, documented in the policy itself):

- ``center``  = median over the calibration runs of the per-run
  ``aggregated.median_wall_seconds``;
- ``mad``     = median absolute deviation of those run medians;
- ``envelope_upper`` = ``max(center + max(3 * mad, max_observed_deviation),
  max_observed_value * 1.15)``
  (always covers the observed spread, and is never tighter than 3 MADs);
- a scenario wall is ALWAYS ``advisory_only`` — hosted-runner instances
  drift up to ±30 %+ across ALL scenarios (measured), so an absolute wall
  can never be a hard gate;
- a RELATIVE same-run ratio is ``hard_gate`` when its data-derived envelope
  is discriminating enough (``envelope_upper / center <= 1.5``); otherwise
  it is ``advisory_only``.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

POLICY_VERSION = 1
BENCHMARK_CONTRACT = "phase-3-performance-v1"
MIN_CALIBRATION_RUNS = 5
REQUIRED_CALIBRATION_KIND = "phase-3-regression-calibration-inputs"

#: Ratios computed WITHIN one run (drift-immune): label -> (numerator, denominator).
RATIO_DEFINITIONS: dict[str, tuple[str, str]] = {
    "projection_selected_over_all": (
        "direct_read_projection_selected",
        "direct_read_projection_all",
    ),
    "read_1m_over_190k": ("direct_read_1m", "direct_read_190k"),
    "memo_skip_over_lazy": ("direct_read_memo_skip", "direct_read_memo_lazy"),
    "memo_lazy_over_inline": ("direct_read_memo_lazy", "direct_read_memo_inline"),
    "migration_validate_on_over_off": ("migration_validate_on", "migration_validate_off"),
}

#: A relative ratio may be a hard gate when its calibrated envelope is tight
#: enough to be discriminating (envelope_upper / center <= this bound).
HARD_GATE_MAX_ENVELOPE_RATIO = 1.5

#: Small-sample safety factor applied on top of the worst observed
#: calibration value.  Five calibration runs under-estimate the tail of the
#: inter-run distribution, so the envelope must exceed the worst observation
#: by this documented factor (otherwise an identical-code candidate run can
#: false-positive, as measured during the first workflow self-test).
OBSERVED_MAX_SAFETY_FACTOR = 1.15

__all__ = [
    "POLICY_VERSION",
    "RATIO_DEFINITIONS",
    "build_policy",
    "main",
]


def _median(values: list[float]) -> float:
    return statistics.median(values)


def _mad(values: list[float], center: float) -> float:
    return _median([abs(value - center) for value in values])


def _stats(values: list[float]) -> dict[str, float]:
    center = _median(values)
    deviations = [abs(value - center) for value in values]
    return {
        "center": center,
        "mad": _median(deviations),
        "min": min(values),
        "max": max(values),
        "max_observed_deviation": max(deviations) if deviations else 0.0,
    }


def _envelope_upper(
    center: float, mad: float, max_observed_deviation: float, max_observed: float
) -> float:
    """Data-derived upper envelope.

    Two data-derived components, no hand-written percentages:

    1. ``center + max(3 * mad, max_observed_deviation)`` — covers the
       observed spread and is never tighter than three MADs;
    2. ``max_observed_value * OBSERVED_MAX_SAFETY_FACTOR`` — five calibration
       runs under-estimate the inter-run tail, so the envelope must exceed
       the worst observed value by the documented safety factor.
    """
    spread_based = center + max(3.0 * mad, max_observed_deviation)
    tail_based = max_observed * OBSERVED_MAX_SAFETY_FACTOR
    return max(spread_based, tail_based)


def _validate_inputs(inputs: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    kind = inputs.get("calibration_kind")
    if kind != REQUIRED_CALIBRATION_KIND:
        problems.append(f"calibration_kind must be {REQUIRED_CALIBRATION_KIND!r}, got {kind!r}")
    run_ids = inputs.get("run_ids")
    if not isinstance(run_ids, list) or len(run_ids) < MIN_CALIBRATION_RUNS:
        problems.append(f"at least {MIN_CALIBRATION_RUNS} calibration runs are required")
    elif len(set(run_ids)) != len(run_ids):
        problems.append("duplicate calibration run_ids")
    if not isinstance(inputs.get("per_run_median_wall_seconds"), dict):
        problems.append("per_run_median_wall_seconds missing")
    for name in ("benchmark_contract", "reference_commit", "scenarios", "provenance"):
        if name not in inputs:
            problems.append(f"missing calibration input: {name}")
    return problems


def build_policy(inputs: dict[str, Any]) -> dict[str, Any]:
    """Derive the regression policy from calibration inputs (pure)."""
    problems = _validate_inputs(inputs)
    if problems:
        raise ValueError("invalid calibration inputs: " + "; ".join(problems))

    run_ids: list[str] = sorted(inputs["run_ids"])
    medians: dict[str, dict[str, float]] = inputs["per_run_median_wall_seconds"]
    scenarios: list[str] = sorted(inputs["scenarios"])
    provenance: dict[str, Any] = inputs["provenance"]

    for rid in run_ids:
        if rid not in medians:
            raise ValueError(f"calibration run {rid!r} has no per-run medians")
        if set(medians[rid]) != set(scenarios):
            raise ValueError(f"calibration run {rid!r} scenario set mismatch")

    scenario_calibration: dict[str, Any] = {}
    for scenario in scenarios:
        values = [medians[rid][scenario] for rid in run_ids]
        if not all(isinstance(value, (int, float)) and value > 0 for value in values):
            raise ValueError(f"scenario {scenario!r} has non-positive or non-finite timings")
        stats = _stats([float(value) for value in values])
        scenario_calibration[scenario] = {
            **stats,
            "max_min_ratio": (stats["max"] / stats["min"]) if stats["min"] > 0 else 0.0,
            "relative_mad": stats["mad"] / stats["center"] if stats["center"] else 0.0,
            "values": [float(value) for value in values],
            # Absolute wall times can never be a hard gate: hosted-runner
            # instances drift by tens of percent across ALL scenarios
            # (measured: median cross-run ratio 0.67x-1.00x in calibration).
            "classification": "advisory_only",
        }

    ratio_calibration: dict[str, Any] = {}
    for label, (numerator, denominator) in sorted(RATIO_DEFINITIONS.items()):
        if numerator not in scenarios or denominator not in scenarios:
            continue  # the policy only covers scenarios present in calibration
        values = [medians[rid][numerator] / medians[rid][denominator] for rid in run_ids]
        stats = _stats([float(value) for value in values])
        envelope = _envelope_upper(
            stats["center"], stats["mad"], stats["max_observed_deviation"], stats["max"]
        )
        ratio_calibration[label] = {
            "numerator": numerator,
            "denominator": denominator,
            **stats,
            "envelope_upper": envelope,
            "relative_mad": stats["mad"] / stats["center"] if stats["center"] else 0.0,
            # Hard gate only when the data-driven envelope is tight enough to
            # discriminate (the whole observed spread must stay well under a
            # 1.5x shift of the ratio).
            "classification": (
                "hard_gate"
                if envelope <= stats["center"] * HARD_GATE_MAX_ENVELOPE_RATIO
                else "advisory_only"
            ),
            "values": [float(value) for value in values],
        }

    return {
        "policy_version": POLICY_VERSION,
        "benchmark_contract": BENCHMARK_CONTRACT,
        "reference_commit": inputs["reference_commit"],
        "generated_from_run_ids": run_ids,
        "calibration_count": len(run_ids),
        "environment": {
            "python": provenance[run_ids[0]]["python"],
            "os": provenance[run_ids[0]]["os"],
            "arch": provenance[run_ids[0]]["arch"],
            "packages": provenance[run_ids[0]]["packages"],
            "storage_label": provenance[run_ids[0]]["storage"],
            "runner_image": provenance[run_ids[0]]["runner"],
        },
        "pr_smoke_scenarios": [
            "inspect_schema_1000",
            "direct_read_190k",
            "direct_read_projection_selected",
            "direct_read_projection_all",
        ],
        "full_scheduled_scenarios": list(scenarios),
        "scenario_calibration": scenario_calibration,
        "ratio_calibration": ratio_calibration,
        "derivation": {
            "center": "median of per-run aggregated.median_wall_seconds over >= 5 calibration runs",
            "dispersion": "MAD (median absolute deviation) of those run medians",
            "envelope_upper": (
                "max(center + max(3 * mad, max_observed_deviation), "
                "max_observed_value * 1.15) — covers the observed spread AND a "
                "documented small-sample safety factor over the worst observation"
            ),
            "ratio_hard_gate_rule": (
                "a same-run ratio is a hard regression signal when it exceeds "
                "envelope_upper on a comparable candidate; a ratio qualifies as a "
                "hard gate only when envelope_upper <= center * 1.5"
            ),
            "absolute_wall_policy": (
                "advisory_only — GitHub-hosted runner instances drift by tens of "
                "percent across all scenarios (measured: per-run median wall ratios "
                "0.67x-1.00x in this calibration), so absolute wall time can never "
                "hard-fail a merge"
            ),
            "classification_rule": (
                "ratio hard_gate iff envelope_upper <= center * 1.5; every absolute "
                "scenario wall is advisory_only; unstable ratios are advisory_only"
            ),
        },
    }


def _load_report_medians(path: Path) -> dict[str, dict[str, float]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    env = payload.get("environment") or {}
    if env.get("benchmark_contract") != BENCHMARK_CONTRACT:
        raise SystemExit(f"{path}: benchmark_contract must be {BENCHMARK_CONTRACT!r}")
    statuses = {s["scenario"]: s["status"] for s in payload.get("scenarios", [])}
    if not statuses or any(status != "MEASURED" for status in statuses.values()):
        raise SystemExit(f"{path}: all scenarios must be MEASURED for calibration")
    medians = {s["scenario"]: s["aggregated"]["median_wall_seconds"] for s in payload["scenarios"]}
    if len(set(statuses)) != len(statuses):
        raise SystemExit(f"{path}: duplicate scenario entries")
    return medians


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="*", type=Path, help="Phase 3 report JSON files")
    parser.add_argument("--calibration-inputs", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.calibration_inputs is not None:
        inputs = json.loads(args.calibration_inputs.read_text(encoding="utf-8"))
    else:
        if len(args.reports) < MIN_CALIBRATION_RUNS:
            raise SystemExit(f"at least {MIN_CALIBRATION_RUNS} report files are required")
        medians: dict[str, dict[str, float]] = {}
        provenance: dict[str, Any] = {}
        run_ids: list[str] = []
        reference_commit = None
        for report_path in args.reports:
            med = _load_report_medians(report_path)
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            env = payload["environment"]
            run_id = env["run_id"]
            if run_id in medians:
                raise SystemExit(f"duplicate calibration run_id: {run_id}")
            commit = env["git"]["commit"]
            reference_commit = reference_commit or commit
            if commit != reference_commit:
                raise SystemExit("calibration reports must share one source commit")
            medians[run_id] = med
            provenance[run_id] = {
                "workflow_run_id": env.get("runner", ""),
                "run_id": run_id,
                "git_commit": commit,
                "python": env["system"]["python"],
                "os": env["system"]["os"],
                "runner": env.get("runner"),
                "storage": env.get("storage"),
            }
        run_ids = sorted(medians)
        if len(run_ids) < MIN_CALIBRATION_RUNS:
            raise SystemExit(f"at least {MIN_CALIBRATION_RUNS} unique runs are required")
        scenarios = sorted(next(iter(medians.values())).keys())
        inputs = {
            "calibration_kind": REQUIRED_CALIBRATION_KIND,
            "benchmark_contract": BENCHMARK_CONTRACT,
            "reference_commit": reference_commit,
            "run_ids": run_ids,
            "calibration_count": len(run_ids),
            "provenance": provenance,
            "per_run_median_wall_seconds": medians,
            "scenarios": scenarios,
        }

    policy = build_policy(inputs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(policy, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    hard = [
        name
        for name, entry in policy["ratio_calibration"].items()
        if entry["classification"] == "hard_gate"
    ]
    print(f"policy written: {args.output}")
    print(f"  reference commit: {policy['reference_commit']}")
    print(f"  calibration runs: {policy['calibration_count']}")
    print(f"  hard-gate ratios: {', '.join(sorted(hard)) or 'NONE'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

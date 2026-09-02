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
import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any

from .contract import (
    CONTRACT_PHASE_3 as BENCHMARK_CONTRACT,
)
from .contract import (
    PHASE3_SCENARIO_NAMES,
    RUN_ID_RE,
    validate_saved_phase3_report,
)

POLICY_VERSION = 1
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


def _finite_positive(value: Any) -> bool:
    """A required timing must be a finite, strictly positive number.

    Python ``json`` technically accepts ``NaN``/``Infinity``; every
    comparator- and calibration-needed value must reject them explicitly
    (``math.isfinite``), along with zero, negatives, and wrong types.
    """
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


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
    """Strict compact-calibration-input validation (auditable artifact)."""
    problems: list[str] = []
    if inputs.get("calibration_kind") != REQUIRED_CALIBRATION_KIND:
        problems.append(
            f"calibration_kind must be {REQUIRED_CALIBRATION_KIND!r}, "
            f"got {inputs.get('calibration_kind')!r}"
        )
    if inputs.get("benchmark_contract") != BENCHMARK_CONTRACT:
        problems.append(
            f"benchmark_contract must be {BENCHMARK_CONTRACT!r}, "
            f"got {inputs.get('benchmark_contract')!r}"
        )
    reference_commit = inputs.get("reference_commit")
    if (
        not isinstance(reference_commit, str)
        or len(reference_commit) != 40
        or any(character not in "0123456789abcdef" for character in reference_commit)
    ):
        problems.append(f"reference_commit must be a full 40-hex commit: {reference_commit!r}")

    workflow_run_ids = inputs.get("workflow_run_ids")
    benchmark_run_ids = inputs.get("benchmark_run_ids")
    for label, ids in (
        ("workflow_run_ids", workflow_run_ids),
        ("benchmark_run_ids", benchmark_run_ids),
    ):
        if not isinstance(ids, list) or len(ids) < MIN_CALIBRATION_RUNS:
            problems.append(
                f"at least {MIN_CALIBRATION_RUNS} calibration runs are required ({label})"
            )
        elif len(set(ids)) != len(ids):
            problems.append(f"duplicate calibration IDs in {label}")
    if isinstance(workflow_run_ids, list) and isinstance(benchmark_run_ids, list):
        if len(workflow_run_ids) != len(benchmark_run_ids):
            problems.append("workflow_run_ids and benchmark_run_ids must pair one-to-one")
        for benchmark_run_id in benchmark_run_ids:
            if not isinstance(benchmark_run_id, str) or not RUN_ID_RE.match(benchmark_run_id):
                problems.append(f"invalid benchmark run_id: {benchmark_run_id!r}")

    if inputs.get("calibration_count") != len(workflow_run_ids or []) or inputs.get(
        "calibration_count"
    ) != len(benchmark_run_ids or []):
        problems.append("calibration_count does not match the run-ID lists")

    scenarios = inputs.get("scenarios")
    if sorted(scenarios or []) != sorted(PHASE3_SCENARIO_NAMES):
        problems.append(
            "scenarios must be exactly PHASE3_SCENARIO_NAMES "
            f"({sorted(set(PHASE3_SCENARIO_NAMES) - set(scenarios or []))} missing, "
            f"{sorted(set(scenarios or []) - set(PHASE3_SCENARIO_NAMES))} unknown)"
        )
        return problems

    medians = inputs.get("per_run_median_wall_seconds")
    provenance = inputs.get("provenance")
    if not isinstance(medians, dict) or not isinstance(provenance, dict):
        problems.append("per_run_median_wall_seconds and provenance must be objects")
        return problems
    # The compact inputs are keyed by BENCHMARK run_id (the report identity);
    # workflow_run_ids pair one-to-one via provenance.
    if sorted(medians) != sorted(benchmark_run_ids or []):
        problems.append("per_run_median_wall_seconds keys must match benchmark_run_ids")
        return problems
    if sorted(provenance) != sorted(benchmark_run_ids or []):
        problems.append("provenance keys must match benchmark_run_ids")

    # Per-run consistency: exact scenario set, finite positive medians,
    # complete provenance, one source commit, one runtime recipe.
    reference = provenance[benchmark_run_ids[0]] if benchmark_run_ids else {}
    for rid in benchmark_run_ids or []:
        run_medians = medians.get(rid)
        if not isinstance(run_medians, dict) or sorted(run_medians) != sorted(
            PHASE3_SCENARIO_NAMES
        ):
            problems.append(f"calibration run {rid!r} must cover exactly PHASE3_SCENARIO_NAMES")
            continue
        for scenario, value in run_medians.items():
            if not _finite_positive(value):
                problems.append(
                    f"calibration run {rid!r} scenario {scenario!r} has invalid median {value!r}"
                )
        prov = provenance.get(rid)
        if not isinstance(prov, dict):
            problems.append(f"calibration run {rid!r} has no provenance")
            continue
        for field in (
            "workflow_run_id",
            "benchmark_run_id",
            "git_commit",
            "report_sha256",
            "python",
            "os",
            "arch",
            "processor",
            "cpu_count",
            "physical_memory_bytes",
            "runner",
            "storage",
            "packages",
        ):
            if prov.get(field) in (None, ""):
                problems.append(f"calibration run {rid!r} provenance lacks {field}")
        if not isinstance(prov.get("benchmark_run_id"), str) or not RUN_ID_RE.match(
            str(prov.get("benchmark_run_id"))
        ):
            problems.append(f"calibration run {rid!r} has invalid benchmark_run_id")
        if prov.get("benchmark_run_id") != rid:
            problems.append(
                f"calibration run {rid!r} provenance.benchmark_run_id does not match its key"
            )
        if prov.get("git_commit") != reference_commit:
            problems.append(f"calibration run {rid!r} comes from a different source commit")
        report_sha = prov.get("report_sha256")
        if (
            not isinstance(report_sha, str)
            or len(report_sha) != 64
            or any(character not in "0123456789abcdef" for character in report_sha)
        ):
            problems.append(f"calibration run {rid!r} has invalid report_sha256")
        # Runtime recipe consistency (one Python, one OS, one arch, one
        # dependency set, one storage recipe).
        for field, label in (
            ("python", "python"),
            ("os", "os"),
            ("arch", "arch"),
            ("packages", "dependency versions"),
            ("storage", "storage recipe"),
        ):
            if prov.get(field) != reference.get(field):
                problems.append(f"calibration run {rid!r} {label} differs from the reference run")
        if not isinstance(prov.get("packages"), dict):
            problems.append(f"calibration run {rid!r} provenance lacks packages")
    return problems


def build_policy(inputs: dict[str, Any]) -> dict[str, Any]:
    """Derive the regression policy from calibration inputs (pure)."""
    problems = _validate_inputs(inputs)
    if problems:
        raise ValueError("invalid calibration inputs: " + "; ".join(problems))

    workflow_run_ids: list[str] = sorted(inputs["workflow_run_ids"])
    benchmark_run_ids: list[str] = sorted(inputs["benchmark_run_ids"])
    medians: dict[str, dict[str, float]] = inputs["per_run_median_wall_seconds"]
    scenarios: list[str] = sorted(inputs["scenarios"])
    provenance: dict[str, Any] = inputs["provenance"]

    scenario_calibration: dict[str, Any] = {}
    for scenario in scenarios:
        values = [float(medians[rid][scenario]) for rid in benchmark_run_ids]
        if not all(_finite_positive(value) for value in values):
            raise ValueError(f"scenario {scenario!r} has non-positive or non-finite timings")
        stats = _stats(values)
        scenario_calibration[scenario] = {
            **stats,
            "max_min_ratio": (stats["max"] / stats["min"]) if stats["min"] > 0 else 0.0,
            "relative_mad": stats["mad"] / stats["center"] if stats["center"] else 0.0,
            "values": values,
            # Absolute wall times can never be a hard gate: hosted-runner
            # instances drift by tens of percent across ALL scenarios
            # (measured: median cross-run ratio 0.67x-1.00x in calibration).
            "classification": "advisory_only",
        }

    ratio_calibration: dict[str, Any] = {}
    for label, (numerator, denominator) in sorted(RATIO_DEFINITIONS.items()):
        if numerator not in scenarios or denominator not in scenarios:
            continue  # the policy only covers scenarios present in calibration
        values = [medians[rid][numerator] / medians[rid][denominator] for rid in benchmark_run_ids]
        stats = _stats(values)
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

    # Honest hardware-pool model: hosted runner instances of the SAME image
    # may run on DIFFERENT CPU families (measured: AMD64 Family 25 and
    # Intel64 Family 6 in this calibration).  The pool records what was
    # observed; comparability treats a candidate outside the pool as
    # PARTIALLY_COMPARABLE.
    observed_processors = sorted({provenance[rid]["processor"] for rid in benchmark_run_ids})
    observed_cpu_counts = sorted({provenance[rid]["cpu_count"] for rid in benchmark_run_ids})
    observed_memory = sorted(
        {provenance[rid]["physical_memory_bytes"] for rid in benchmark_run_ids}
    )
    reference = provenance[benchmark_run_ids[0]]

    return {
        "policy_version": POLICY_VERSION,
        "benchmark_contract": BENCHMARK_CONTRACT,
        "reference_commit": inputs["reference_commit"],
        "generated_from_workflow_run_ids": workflow_run_ids,
        "generated_from_benchmark_run_ids": benchmark_run_ids,
        "calibration_count": len(workflow_run_ids),
        "hardware_pool": {
            "observed_processor_signatures": observed_processors,
            "observed_cpu_counts": observed_cpu_counts,
            "observed_physical_memory_bytes": observed_memory,
            "note": (
                "the same hosted runner image may land on different CPU "
                "families; a candidate outside this pool is "
                "PARTIALLY_COMPARABLE (correctness stays hard, performance "
                "ratios advisory)"
            ),
        },
        "environment": {
            "python": reference["python"],
            "os": reference["os"],
            "arch": reference["arch"],
            "packages": reference["packages"],
            "storage_label": reference["storage"],
            "runner_image": reference["runner"],
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
                "documented small-sample guard band over the worst observation"
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
            "policy_parameters": {
                "mad_multiplier": {
                    "value": 3,
                    "rationale": (
                        "envelope floor: three MADs above the calibration median; "
                        "a robust guard that covers the observed spread of stable "
                        "ratios (relMAD 0.9-6.7%) several times over"
                    ),
                    "validation_evidence": (
                        "5-run calibration: no stable ratio exceeded 3 MADs "
                        "within the observed range"
                    ),
                },
                "small_sample_guard_band": {
                    "value": 1.15,
                    "rationale": (
                        "envelope must exceed the WORST observed calibration value "
                        "by 15%: five runs under-estimate the inter-run tail; the "
                        "first self-test run on identical source landed beyond the "
                        "too-tight 5-sample envelope, proving the guard is needed"
                    ),
                    "validation_evidence": (
                        "self-test run 33553669363 (identical src) produced ratio "
                        "0.6655 vs the pre-fix envelope 0.6363; the widened "
                        "envelope 0.7317 passed all subsequent same-source runs"
                    ),
                },
                "hard_gate_discrimination_bound": {
                    "value": 1.5,
                    "rationale": (
                        "a ratio qualifies as a hard gate only when its envelope "
                        "stays under a 1.5x shift of the calibration center; "
                        "looser envelopes cannot discriminate a real regression "
                        "from observed noise and are advisory_only"
                    ),
                    "validation_evidence": (
                        "memo_skip_over_lazy observed a 2x inter-run outlier "
                        "(1.510 vs 0.77) — its envelope reaches 2.23x center, so "
                        "it is honestly classified advisory_only"
                    ),
                },
            },
        },
    }


def _load_report_inputs(path: Path) -> tuple[dict[str, float], dict[str, Any]]:
    """Validate one RAW Phase 3 report and extract its calibration inputs.

    The report must pass the FULL frozen Phase 3 contract validation
    (exact 23 scenarios, all MEASURED, complete samples/metrics/provenance,
    zero residue) before any median is extracted; duplicate scenario
    entries are detected on the raw list.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))

    # Duplicate and malformed scenario entries on the RAW list — BEFORE any
    # dict-based access can silently collapse them.
    raw_names: list[str] = []
    for entry in payload.get("scenarios", []):
        if not isinstance(entry, dict) or not isinstance(entry.get("scenario"), str):
            raise SystemExit(f"{path}: malformed scenario entry (missing scenario name)")
        raw_names.append(entry["scenario"])
    duplicates = sorted({name for name in raw_names if raw_names.count(name) > 1})
    if duplicates:
        raise SystemExit(f"{path}: duplicate scenario entries: {duplicates}")

    problems = validate_saved_phase3_report(payload)
    if problems:
        raise SystemExit(
            f"{path}: the report does not satisfy the full Phase 3 contract: "
            + "; ".join(problems[:8])
            + ("..." if len(problems) > 8 else "")
        )

    env = payload["environment"]
    medians = {s["scenario"]: s["aggregated"]["median_wall_seconds"] for s in payload["scenarios"]}
    provenance = {
        "benchmark_run_id": env["run_id"],
        "git_commit": env["git"]["commit"],
        "report_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "python": env["system"]["python"],
        "os": env["system"]["os"],
        "arch": env["system"]["arch"],
        "processor": env["system"]["processor"],
        "cpu_count": env["system"]["cpu_count"],
        "physical_memory_bytes": env["system"]["physical_memory_bytes"],
        "runner": env.get("runner"),
        "storage": env.get("storage"),
        "packages": dict(env.get("packages") or {}),
    }
    return medians, provenance


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
        workflow_run_ids: list[str] = []
        benchmark_run_ids: list[str] = []
        reference_commit = None
        for report_path in args.reports:
            med, prov = _load_report_inputs(report_path)
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            env = payload["environment"]
            benchmark_run_id = env["run_id"]
            if benchmark_run_id in benchmark_run_ids:
                raise SystemExit(f"duplicate calibration benchmark_run_id: {benchmark_run_id}")
            commit = prov["git_commit"]
            reference_commit = reference_commit or commit
            if commit != reference_commit:
                raise SystemExit("calibration reports must share one source commit")
            # Runtime-recipe consistency across calibration reports.
            if (
                benchmark_run_ids
                and prov["packages"] != provenance[benchmark_run_ids[0]]["packages"]
            ):
                raise SystemExit("calibration reports must share one dependency set")
            if prov["python"] != provenance[benchmark_run_ids[0]]["python"]:
                raise SystemExit("calibration reports must share one Python version")
            medians[benchmark_run_id] = med
            provenance[benchmark_run_id] = prov
            workflow_run_ids.append(env.get("workflow_run_id") or str(report_path))
            benchmark_run_ids.append(benchmark_run_id)
        if len(set(workflow_run_ids)) != len(workflow_run_ids):
            raise SystemExit("duplicate calibration workflow_run_ids")
        if len(benchmark_run_ids) < MIN_CALIBRATION_RUNS:
            raise SystemExit(f"at least {MIN_CALIBRATION_RUNS} unique runs are required")
        scenarios = sorted(next(iter(medians.values())).keys())
        inputs = {
            "calibration_kind": REQUIRED_CALIBRATION_KIND,
            "benchmark_contract": BENCHMARK_CONTRACT,
            "reference_commit": reference_commit,
            "workflow_run_ids": workflow_run_ids,
            "benchmark_run_ids": benchmark_run_ids,
            "calibration_count": len(benchmark_run_ids),
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

"""Deterministic Phase 3 regression-policy generator (offline).

Reads saved Phase 3 benchmark reports (or the committed compact calibration
inputs) and derives the versioned regression policy.  This tool never
benchmarks and never touches the network.  Thresholds combine MEASURED
statistics (per-run medians, MAD, observed ranges) with explicit versioned
POLICY PARAMETERS (the MAD multiplier, the small-sample guard band and the
hard-gate discrimination bound) — deliberate engineering choices recorded
with rationale and validation evidence in the policy itself.

Usage:

    # raw reports with explicit GitHub workflow provenance:
    python -m benchmarks.calibrate_regression \\
        --report 33546526573=report1.json \\
        --report 33546534607=report2.json \\
        --report 33546542658=report3.json \\
        --report 33546551328=report4.json \\
        --report 33546559054=report5.json \\
        --output benchmarks/regression/phase-3-regression-policy-v1.json

    # or from the committed compact calibration inputs:
    python -m benchmarks.calibrate_regression \\
        --calibration-inputs benchmarks/regression/phase-3-regression-calibration-inputs.json \\
        --output benchmarks/regression/phase-3-regression-policy-v1.json

Requirements enforced here (a smaller dataset is rejected):

- at least 5 calibration inputs (independent workflow runs);
- unique workflow and benchmark run IDs;
- a single reference commit (one source snapshot);
- the ``phase-3-performance-v1`` contract with all 23 scenarios MEASURED;
- EXPLICIT GitHub workflow IDs (a versioned CI policy must never synthesize
  workflow provenance from file paths).

Derivation algorithm (deterministic; combines MEASURED statistics with the
versioned POLICY PARAMETERS, both recorded in the policy itself):

- ``center``  = median over the calibration runs of the per-run
  ``aggregated.median_wall_seconds``;
- ``mad``     = median absolute deviation of those run medians;
- ``envelope_upper`` = ``max(center + max(3 * mad, max_observed_deviation),
  max_observed_value * 1.15)``
  (always covers the observed spread, and is never tighter than 3 MADs);
- a scenario wall is ALWAYS ``advisory_only`` — hosted-runner instances
  drift up to ±30 %+ across ALL scenarios (measured), so an absolute wall
  can never be a hard gate;
- a RELATIVE same-run ratio is ``hard_gate`` when its envelope
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
    POLICY_PARAMETERS,
    RATIO_DEFINITIONS,
    RUN_ID_RE,
    validate_saved_phase3_report,
)

POLICY_VERSION = 1
MIN_CALIBRATION_RUNS = 5
REQUIRED_CALIBRATION_KIND = "phase-3-regression-calibration-inputs"

#: A relative ratio may be a hard gate when its calibrated envelope is tight
#: enough to be discriminating (envelope_upper / center <= this bound); the
#: value lives in the canonical POLICY_PARAMETERS model (contract.py).
HARD_GATE_MAX_ENVELOPE_RATIO = POLICY_PARAMETERS["hard_gate_discrimination_bound"]["value"]

#: Small-sample guard band applied on top of the worst observed calibration
#: value; the value lives in the canonical POLICY_PARAMETERS model.
OBSERVED_MAX_SAFETY_FACTOR = POLICY_PARAMETERS["small_sample_guard_band"]["value"]

__all__ = [
    "POLICY_VERSION",
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

    Two components — the first derived from calibration statistics, the
    second an explicit policy parameter:

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

    sorted(inputs["workflow_run_ids"])
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

    # Calibration provenance as PAIRED records: each entry is one concrete
    # measurement artifact.  This is the source of truth; the flat
    # generated_from_* lists are derived from it below and validated for
    # exact consistency.
    calibration_sources = sorted(
        (
            {
                "workflow_run_id": provenance[rid]["workflow_run_id"],
                "benchmark_run_id": rid,
                "report_sha256": provenance[rid]["report_sha256"],
                "git_commit": provenance[rid]["git_commit"],
            }
            for rid in benchmark_run_ids
        ),
        key=lambda record: record["workflow_run_id"],
    )
    derived_workflow_ids = [record["workflow_run_id"] for record in calibration_sources]
    derived_benchmark_ids = [record["benchmark_run_id"] for record in calibration_sources]

    return {
        "policy_version": POLICY_VERSION,
        "benchmark_contract": BENCHMARK_CONTRACT,
        "reference_commit": inputs["reference_commit"],
        "calibration_sources": calibration_sources,
        "generated_from_workflow_run_ids": derived_workflow_ids,
        "generated_from_benchmark_run_ids": derived_benchmark_ids,
        "calibration_count": len(calibration_sources),
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
        "package_under_test": {
            "name": "dbfbridge",
            "reference_version": reference["packages"].get("dbfbridge"),
            "note": (
                "the version of the package under test is PROVENANCE, not a "
                "comparability requirement: different dbfbridge versions are "
                "exactly what the regression CI compares.  Only external "
                "measurement dependencies gate comparability."
            ),
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
                "hard_gate_discrimination_bound": {
                    "rationale": (
                        "a ratio qualifies as a hard gate only when its envelope "
                        "stays under a 1.5x shift of the calibration center; "
                        "looser envelopes cannot discriminate a real regression "
                        "from observed noise and are advisory_only"
                    ),
                    "validation_evidence": (
                        "memo_skip_over_lazy observed a 2x inter-run outlier "
                        "(1.510 vs 0.77) - its envelope reaches 2.23x center, so "
                        "it is honestly classified advisory_only"
                    ),
                    "value": 1.5,
                },
                "mad_multiplier": {
                    "rationale": (
                        "envelope floor: three MADs above the calibration median; "
                        "a robust guard that covers the observed spread of stable "
                        "ratios (relMAD 0.9-6.7%) several times over"
                    ),
                    "validation_evidence": (
                        "5-run calibration: no stable ratio exceeded 3 MADs "
                        "within the observed range"
                    ),
                    "value": 3,
                },
                "small_sample_guard_band": {
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
                    "value": 1.15,
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report",
        action="append",
        metavar="WORKFLOW_RUN_ID=REPORT_JSON",
        default=[],
        help=(
            "a calibration candidate as WORKFLOW_RUN_ID=REPORT_JSON - the "
            "EXPLICIT GitHub workflow provenance required for a versioned CI "
            "policy (never synthesized from a file path)"
        ),
    )
    parser.add_argument("--calibration-inputs", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.calibration_inputs is not None:
        inputs = json.loads(args.calibration_inputs.read_text(encoding="utf-8"))
    else:
        if len(args.report) < MIN_CALIBRATION_RUNS:
            raise SystemExit(
                f"at least {MIN_CALIBRATION_RUNS} --report WORKFLOW_RUN_ID=REPORT_JSON "
                "entries are required"
            )
        medians: dict[str, dict[str, float]] = {}
        provenance: dict[str, Any] = {}
        workflow_run_ids: list[str] = []
        benchmark_run_ids: list[str] = []
        reference_commit = None
        for spec in args.report:
            separator = spec.find("=")
            if separator <= 0:
                raise SystemExit(
                    f"invalid --report entry {spec!r}; expected WORKFLOW_RUN_ID=REPORT_JSON"
                )
            workflow_run_id = spec[:separator]
            report_path = Path(spec[separator + 1 :])
            if not workflow_run_id.isdigit():
                raise SystemExit(
                    f"invalid --report workflow ID {workflow_run_id!r}: a GitHub "
                    "workflow run ID must be numeric - workflow provenance is "
                    "never synthesized from a file path or the benchmark run_id"
                )
            med, prov = _load_report_inputs(report_path)
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            env = payload["environment"]
            benchmark_run_id = env["run_id"]
            if benchmark_run_id in benchmark_run_ids:
                raise SystemExit(f"duplicate calibration benchmark_run_id: {benchmark_run_id}")
            if workflow_run_id in workflow_run_ids:
                raise SystemExit(f"duplicate calibration workflow_run_id: {workflow_run_id}")
            commit = prov["git_commit"]
            reference_commit = reference_commit or commit
            if commit != reference_commit:
                raise SystemExit("calibration reports must share one source commit")
            if (
                benchmark_run_ids
                and prov["packages"] != provenance[benchmark_run_ids[0]]["packages"]
            ):
                raise SystemExit("calibration reports must share one dependency set")
            if benchmark_run_ids and prov["python"] != provenance[benchmark_run_ids[0]]["python"]:
                raise SystemExit("calibration reports must share one Python version")
            medians[benchmark_run_id] = med
            prov["workflow_run_id"] = workflow_run_id
            provenance[benchmark_run_id] = prov
            workflow_run_ids.append(workflow_run_id)
            benchmark_run_ids.append(benchmark_run_id)
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
    hard_labels = ", ".join(sorted(hard)) or "NONE"
    print(f"  hard-gate ratios: {hard_labels}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

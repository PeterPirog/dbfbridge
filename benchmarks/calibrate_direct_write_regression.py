"""Deterministic policy generator for the v1.1 Direct Write regression policy
(DBFB-PERF-006, stage F3B1).

Reads the committed authoritative calibration inputs
(``benchmarks/regression/direct-write-regression-calibration-inputs-v1.json``,
captured exclusively from the accepted main_push calibration artifact
10104538589 / workflow 34352345104 on reference commit ``ebc47bf…``) and
emits the versioned Direct Write regression policy JSON.

Deterministic: same inputs -> byte-identical policy.  No thresholds are
invented here; the derivation is mechanical from the versioned parameters
(the repository's proven Phase 3 engineering methodology, adopted with
explicit rationale and Direct Write validation evidence) and the measured
calibration facts.

Rebaseline policy: this generator is NEVER run automatically from CI runs.
Rebaselining requires a separate architecture-reviewed task with new
calibration evidence and a comparison to the previous policy.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any

#: Versioned Direct Write regression policy identity (SEPARATE from Phase 3).
POLICY_CONTRACT = "dbfbridge-direct-write-regression-policy-v1"
POLICY_VERSION = 1

#: Exact ratio candidate set (DBFB-PERF-006 / F3B1 §10) — no hidden sixth.
RATIO_LABELS = (
    "W3/W1 wall-seconds-per-record",
    "W2/W1 wall-seconds-per-record",
    "W5/W1 wall-seconds-per-record",
    "W10/W1 wall-seconds-per-record",
    "W3/W1 peak-RSS-delta ratio",
)

RATIO_DEFINITIONS = {
    "W3/W1 wall-seconds-per-record": (
        "direct_write_1m_flat",
        "direct_write_190k_flat",
        "wall_seconds",
    ),
    "W2/W1 wall-seconds-per-record": (
        "direct_read_transform_write_190k",
        "direct_write_190k_flat",
        "wall_seconds",
    ),
    "W5/W1 wall-seconds-per-record": (
        "direct_write_memo_heavy",
        "direct_write_190k_flat",
        "wall_seconds",
    ),
    "W10/W1 wall-seconds-per-record": (
        "direct_write_varchar_nullflags",
        "direct_write_190k_flat",
        "wall_seconds",
    ),
    "W3/W1 peak-RSS-delta ratio": (
        "direct_write_1m_flat",
        "direct_write_190k_flat",
        "peak_rss_delta_bytes",
    ),
}

#: Versioned engineering policy parameters (repository methodology constants
#: adopted from the proven Phase 3 calibration methodology; NOT measured
#: facts — each carries an explicit rationale and Direct Write validation
#: evidence recorded in the generated policy).
PARAMETERS = {
    "mad_multiplier": {
        "value": 3.0,
        "kind": "engineering_policy_parameter",
        "rationale": (
            "Standard 3-MAD dispersion envelope from the repository's proven "
            "Phase 3 regression methodology; captures expected run-to-run "
            "variance without following hosted-runner outliers."
        ),
    },
    "small_sample_guard_band": {
        "value": 1.15,
        "kind": "engineering_policy_parameter",
        "rationale": (
            "Small-sample tail guard: five calibration samples cannot bound "
            "the true distribution tail, so the envelope additionally covers "
            "15 percent above the maximum observed value."
        ),
    },
    "hard_gate_discrimination_bound": {
        "value": 1.5,
        "kind": "engineering_policy_parameter",
        "rationale": (
            "A ratio is hard-gated only when its envelope stays within 50 "
            "percent of the calibrated center; beyond that the policy could "
            "not distinguish a genuine regression from calibration noise."
        ),
    },
}

#: Absolute scenario wall times are ADVISORY ONLY (never hard gates).
ABSOLUTE_WALL_CLASSIFICATION = "advisory_only"

_CORRECTNESS_GATES = (
    "benchmark contract/version correct",
    "mode matches policy evaluation mode",
    "all W1-W12 present exactly once",
    "all scenarios MEASURED",
    "validation_problems empty",
    "intermediate_jsonl_bytes == 0 for every scenario",
    "temporary_bytes_left == 0 for every measured scenario",
    "W10 private_spool_bytes_written > 0 (full mode)",
    "W12 functional_cleanup with records_per_second null and cleanup verified",
)

SCENARIO_ORDER = (
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

#: Scenarios the smoke mode actually measures (all W1-W11 with reduced
#: counts; W12 is functional in both modes).
SMOKE_SCENARIOS = tuple(
    scenario
    for scenario in SCENARIO_ORDER
    if scenario != "cancellation_cleanup_smoke"
)


def _median_absolute_deviation(values: list[float], center: float) -> float:
    return statistics.median(abs(value - center) for value in values)


def _derive_ratio(label: str, values: list[float], parameters: dict[str, dict]) -> dict[str, Any]:
    """Mechanically derive one ratio calibration (F3B1 §10/§12)."""
    finite = [float(value) for value in values]
    if not finite or any(not math.isfinite(value) for value in finite):
        raise ValueError(f"ratio {label!r} contains non-finite values")
    center = statistics.median(finite)
    mad = _median_absolute_deviation(finite, center)
    max_observed_deviation = max(abs(value - center) for value in finite)
    mad_multiplier = float(parameters["mad_multiplier"]["value"])
    guard_band = float(parameters["small_sample_guard_band"]["value"])
    discrimination_bound = float(parameters["hard_gate_discrimination_bound"]["value"])
    spread_based = center + max(mad_multiplier * mad, max_observed_deviation)
    tail_based = max(finite) * guard_band
    envelope_upper = max(spread_based, tail_based)
    classification = (
        "hard_gate"
        if center > 0 and envelope_upper <= center * discrimination_bound
        else "advisory_only"
    )
    numerator_scenario, denominator_scenario, metric = RATIO_DEFINITIONS[label]
    return {
        "label": label,
        "numerator": f"{numerator_scenario}.{metric}",
        "denominator": f"{denominator_scenario}.{metric}",
        "values": finite,
        "center": center,
        "mad": mad,
        "relative_mad": round(mad / center, 6) if center else None,
        "max_observed_deviation": max_observed_deviation,
        "spread_based": round(spread_based, 9),
        "tail_based": round(tail_based, 9),
        "envelope_upper": round(envelope_upper, 9),
        "envelope_upper_over_center": (
            round(envelope_upper / center, 6) if center else None
        ),
        "classification": classification,
    }


def _derive_scenario_advisory(
    scenario: str, values: list[float], parameters: dict[str, dict]
) -> dict[str, Any]:
    """Absolute wall-time calibration is ADVISORY ONLY (never hard-fails)."""
    finite = [float(value) for value in values]
    if not finite or any(not math.isfinite(value) for value in finite):
        raise ValueError(f"scenario {scenario!r} wall times contain non-finite values")
    center = statistics.median(finite)
    mad = _median_absolute_deviation(finite, center)
    max_observed_deviation = max(abs(value - center) for value in finite)
    mad_multiplier = float(parameters["mad_multiplier"]["value"])
    guard_band = float(parameters["small_sample_guard_band"]["value"])
    spread_based = center + max(mad_multiplier * mad, max_observed_deviation)
    tail_based = max(finite) * guard_band
    envelope_upper = max(spread_based, tail_based)
    return {
        "scenario": scenario,
        "metric": "wall_seconds",
        "classification": ABSOLUTE_WALL_CLASSIFICATION,
        "values": finite,
        "center": center,
        "mad": mad,
        "relative_mad": round(mad / center, 6) if center else None,
        "max_observed_deviation": max_observed_deviation,
        "advisory_envelope_upper": round(envelope_upper, 9),
        "note": (
            "Absolute wall time varies materially across hosted runners; this "
            "envelope is informational only and can NEVER hard-fail."
        ),
    }


def validate_inputs(inputs: dict[str, Any]) -> list[str]:
    """Strict validation of the calibration-input document (fail-closed)."""
    problems: list[str] = []
    if inputs.get("policy_inputs_contract") != (
        "dbfbridge-direct-write-calibration-inputs-v1"
    ):
        problems.append("wrong policy_inputs contract")
    if inputs.get("policy_inputs_version") != 1:
        problems.append("wrong policy_inputs version")
    if inputs.get("benchmark_contract") != "dbfbridge-direct-write-v1":
        problems.append("wrong benchmark contract")
    if inputs.get("calibration_contract") != (
        "dbfbridge-direct-write-calibration-v1"
    ):
        problems.append("wrong calibration contract")
    reference_commit = inputs.get("reference_commit")
    if not (
        isinstance(reference_commit, str)
        and len(reference_commit) == 40
        and all(ch in "0123456789abcdef" for ch in reference_commit)
    ):
        problems.append("invalid reference_commit")
    sample_shas = {
        str(sample.get("measured_code_sha")) for sample in (inputs.get("samples") or [])
    }
    if sample_shas - {reference_commit}:
        problems.append(
            "calibration samples must share the reference_commit "
            f"(got {sorted(sample_shas)})"
        )
    source = inputs.get("source") or {}
    for key in ("workflow_run_id", "artifact_id", "artifact_digest", "compact_json_sha256"):
        if not source.get(key):
            problems.append(f"missing source provenance {key}")
    samples = inputs.get("samples") or []
    if len(samples) < 5:
        problems.append(f"fewer than 5 calibration samples ({len(samples)})")
    identities = set()
    run_ids = set()
    shas = set()
    for sample in samples:
        identity = (sample.get("workflow_run_id"), sample.get("replica_id"))
        if identity in identities:
            problems.append(f"duplicate sample identity {identity}")
        identities.add(identity)
        run_id = sample.get("benchmark_run_id")
        if run_id in run_ids:
            problems.append(f"duplicate benchmark run_id {run_id}")
        run_ids.add(str(run_id))
        sha = sample.get("measured_code_sha")
        shas.add(str(sha))
        report_sha = sample.get("report_sha256")
        if not (
            isinstance(report_sha, str)
            and len(report_sha) == 64
            and all(ch in "0123456789abcdef" for ch in report_sha)
        ):
            problems.append(f"invalid report SHA for {identity[1]}")
    if len(shas) != 1 or shas == {"None"}:
        problems.append("calibration samples must share one valid measured SHA")
    ratio_values = inputs.get("ratio_raw_values") or {}
    if set(ratio_values) != set(RATIO_LABELS):
        missing = sorted(set(RATIO_LABELS) - set(ratio_values))
        extra = sorted(set(ratio_values) - set(RATIO_LABELS))
        if missing:
            problems.append(f"missing ratio candidates {missing}")
        if extra:
            problems.append(f"extra ratio candidates {extra}")
    for label, values in ratio_values.items():
        if len(values) != len(samples):
            problems.append(f"ratio {label!r}: value count != sample count")
        if any(not math.isfinite(float(value)) for value in values):
            problems.append(f"ratio {label!r}: non-finite value")
    wall = inputs.get("wall_seconds_by_scenario") or {}
    for scenario in SCENARIO_ORDER:
        if scenario == "cancellation_cleanup_smoke":
            continue
        if scenario not in wall:
            problems.append(f"missing wall_seconds for {scenario}")
        elif len(wall[scenario]) != len(samples):
            problems.append(f"wall_seconds {scenario}: count != sample count")
    for label, values in ratio_values.items():
        for value in values:
            if isinstance(value, float) and (
                math.isnan(value) or math.isinf(value)
            ):
                problems.append(f"ratio {label}: NaN/Infinity")
    for sentinel in ("records", "NOTE", "PICTURE", "password", "token", "secret"):
        serialized = json.dumps(inputs)
        if f'"{sentinel}"' in serialized and sentinel not in json.dumps(
            inputs.get("ratio_raw_values", {})
        ):
            problems.append(f"privacy sentinel {sentinel!r} in calibration inputs")
    return problems


def generate_policy(inputs: dict[str, Any]) -> dict[str, Any]:
    """Deterministically derive the versioned regression policy."""
    problems = validate_inputs(inputs)
    if problems:
        return {"accepted": False, "problems": problems}
    ratio_calibration: dict[str, Any] = {}
    for label, values in inputs["ratio_raw_values"].items():
        ratio_calibration[label] = _derive_ratio(
            label, values, {name: PARAMETERS[name] for name in PARAMETERS}
        )
    hard_ratios = [
        label
        for label, spec in ratio_calibration.items()
        if spec["classification"] == "hard_gate"
    ]
    if not hard_ratios:
        return {
            "accepted": False,
            "problems": [
                "non-discriminating policy: zero hard performance ratios "
                "(ARCHITECTURE_REVIEW_REQUIRED_NONDISCRIMINATING_POLICY)"
            ],
        }
    scenario_calibration = {}
    for scenario in SCENARIO_ORDER:
        if scenario == "cancellation_cleanup_smoke":
            continue
        scenario_calibration[scenario] = _derive_scenario_advisory(
            scenario, inputs["wall_seconds_by_scenario"][scenario], PARAMETERS
        )
    reference_commit = inputs["reference_commit"]
    source = inputs["source"]
    return {
        "accepted": True,
        "problems": [],
        "policy_contract": POLICY_CONTRACT,
        "policy_version": POLICY_VERSION,
        "benchmark_contract": inputs["benchmark_contract"],
        "benchmark_contract_version": inputs["benchmark_contract_version"],
        "calibration_contract": inputs["calibration_contract"],
        "calibration_contract_version": inputs["calibration_contract_version"],
        "reference_commit": reference_commit,
        "calibration_sources": {
            "source_context": source["source_context"],
            "workflow_run_id": source["workflow_run_id"],
            "artifact_id": source["artifact_id"],
            "artifact_digest": source["artifact_digest"],
            "compact_json_sha256": source["compact_json_sha256"],
            "benchmark_run_ids": sorted(
                sample["benchmark_run_id"] for sample in inputs["samples"]
            ),
            "report_sha256_by_replica": {
                sample["replica_id"]: sample["report_sha256"]
                for sample in inputs["samples"]
            },
            "rebaseline_rule": (
                "The Direct Write policy may NOT be automatically rewritten "
                "from CI runs.  Rebaselining requires a separate explicit "
                "architecture-reviewed task with reason, new calibration "
                "evidence, comparison to the previous policy, no correctness "
                "regression and an explicit policy version decision."
            ),
        },
        "calibration_count": inputs["calibration_count"],
        "runtime_recipe": inputs["runtime_recipe"],
        "parameters": {
            name: {
                "value": spec["value"],
                "kind": spec["kind"],
                "rationale": spec["rationale"],
            }
            for name, spec in PARAMETERS.items()
        },
        "derivation": (
            "center = median(values); mad = median(|v - center|); "
            "envelope_upper = max(center + max(mad_multiplier*mad, "
            "max_observed_deviation), max(values)*small_sample_guard_band); "
            "hard_gate iff envelope_upper <= center * "
            "hard_gate_discrimination_bound"
        ),
        "scenario_calibration": scenario_calibration,
        "ratio_calibration": ratio_calibration,
        "hard_ratio_labels": sorted(hard_ratios),
        "advisory_ratio_labels": sorted(
            label
            for label, spec in ratio_calibration.items()
            if spec["classification"] == "advisory_only"
        ),
        "smoke_scenarios": list(SMOKE_SCENARIOS),
        "correctness_gates": list(_CORRECTNESS_GATES),
        "comparability_contract": {
            "contract": "dbfbridge-direct-write-run-provenance-v1",
            "compared_fields": (
                "runner_os", "runner_arch", "python major.minor",
                "install_recipe", "dbf version", "dbfread version",
                "psutil version",
            ),
            "classifications": (
                "COMPARABLE", "PARTIALLY_COMPARABLE", "NOT_COMPARABLE"
            ),
            "rule": (
                "Performance hard gates apply only on COMPARABLE evidence; "
                "correctness gates ALWAYS run and hard-fail regardless."
            ),
        },
        "privacy_note": (
            "No DBF record values, memo contents or credentials are contained "
            "in this policy."
        ),
    }


def markdown_summary(payload: dict[str, Any]) -> str:
    lines = [
        "# Direct Write regression policy (dbfbridge-direct-write-regression-policy-v1)",
        "",
        f"Reference commit: `{payload.get('reference_commit')}` · "
        f"calibration samples: `{payload.get('calibration_count')}`",
        "",
        "| ratio | classification | center | MAD | envelope_upper | envelope/center |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for label, spec in (payload.get("ratio_calibration") or {}).items():
        lines.append(
            f"| {label} | {spec['classification']} | {spec['center']} | "
            f"{spec['mad']} | {spec['envelope_upper']} | "
            f"{spec['envelope_upper_over_center']} |"
        )
    lines += [
        "",
        f"Hard ratio gates: {payload.get('hard_ratio_labels')}",
        "Absolute scenario wall times are ADVISORY ONLY (never hard-fail).",
        "No Direct Write regression threshold outside this policy; rebaselining "
        "requires an explicit architecture-reviewed task.",
    ]
    return "\n".join(lines) + "\n"


def load_inputs(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inputs", type=Path,
        default=Path(__file__).resolve().parent
        / "regression" / "direct-write-regression-calibration-inputs-v1.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, default=None)
    args = parser.parse_args(argv)
    inputs = load_inputs(args.inputs)
    payload = generate_policy(inputs)
    if not payload.get("accepted"):
        print(f"policy rejected: {payload.get('problems')}")
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if args.markdown:
        args.markdown.write_text(markdown_summary(payload), encoding="utf-8")
    print(
        f"policy written to {args.output} · hard ratios: "
        f"{payload['hard_ratio_labels']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

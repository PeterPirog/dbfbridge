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
import re
from pathlib import Path
from typing import Any

from .direct_write_regression_contract import (
    FULL_SCENARIO_RECORD_COUNTS,
    MEASURED_SCENARIO_ORDER,
    POLICY_PARAMETER_VALUES,
    RATIO_DEFINITIONS_BY_LABEL,
    RATIO_LABELS,
    SERIALIZATION_ROUNDING_TOLERANCE,
    derive_ratio_statistics,
    evaluate_ratio_values,
    parse_runtime_recipe,
)

#: Versioned Direct Write regression policy identity (SEPARATE from Phase 3).
POLICY_CONTRACT = "dbfbridge-direct-write-regression-policy-v1"
POLICY_VERSION = 1

#: The exact number of authoritative calibration samples (F3B1 §6).
AUTHORITATIVE_SAMPLE_COUNT = 5

_WORKFLOW_RUN_ID_PATTERN = re.compile(r"^[0-9]+$")
_SHA64_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")

#: Versioned engineering policy parameters (repository methodology constants
#: adopted from the proven Phase 3 calibration methodology; NOT measured
#: facts — each carries an explicit rationale and Direct Write validation
#: evidence recorded in the generated policy).  The VALUES come from the
#: single contract-module authority; only kind/rationale live here.
PARAMETERS = {
    "mad_multiplier": {
        "value": POLICY_PARAMETER_VALUES["mad_multiplier"],
        "kind": "engineering_policy_parameter",
        "rationale": (
            "Standard 3-MAD dispersion envelope from the repository's proven "
            "Phase 3 regression methodology; captures expected run-to-run "
            "variance without following hosted-runner outliers."
        ),
    },
    "small_sample_guard_band": {
        "value": POLICY_PARAMETER_VALUES["small_sample_guard_band"],
        "kind": "engineering_policy_parameter",
        "rationale": (
            "Small-sample tail guard: five calibration samples cannot bound "
            "the true distribution tail, so the envelope additionally covers "
            "15 percent above the maximum observed value."
        ),
    },
    "hard_gate_discrimination_bound": {
        "value": POLICY_PARAMETER_VALUES["hard_gate_discrimination_bound"],
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

#: Scenarios the smoke mode actually measures (all W1-W11 with reduced
#: counts; W12 is functional in both modes).
SMOKE_SCENARIOS = MEASURED_SCENARIO_ORDER


def _derive_ratio(
    definition: Any, values: list[float], parameters: dict[str, dict]
) -> dict[str, Any]:
    """Mechanically derive one ratio calibration (F3B1 §10/§12).

    The distribution statistics are derived by the SINGLE mathematical
    authority in the contract module (``derive_ratio_statistics``) — the
    comparator re-derives with the same helper, so a finite tampered
    statistic can never masquerade as policy (F3B1-BLK-07).
    """
    finite = [float(value) for value in values]
    if not finite or any(not math.isfinite(value) for value in finite):
        raise ValueError(f"ratio {definition.label!r} contains non-finite values")
    stats = derive_ratio_statistics(
        finite, {name: spec["value"] for name, spec in parameters.items()}
    )
    return {
        "label": definition.label,
        "numerator": (
            f"{definition.numerator_scenario}.{definition.metric}"
        ),
        "denominator": (
            f"{definition.denominator_scenario}.{definition.metric}"
        ),
        "metric": definition.metric,
        "normalization": definition.normalization,
        "values": finite,
        **stats,
    }


def _derive_scenario_advisory(
    scenario: str, values: list[float], parameters: dict[str, dict]
) -> dict[str, Any]:
    """Absolute wall-time calibration is ADVISORY ONLY (never hard-fails).

    Descriptive statistics come from the SAME single derivation helper the
    comparator re-checks (F3B1-BLK-07); the envelope stays informational.
    """
    finite = [float(value) for value in values]
    if not finite or any(not math.isfinite(value) for value in finite):
        raise ValueError(f"scenario {scenario!r} wall times contain non-finite values")
    stats = derive_ratio_statistics(
        finite, {name: spec["value"] for name, spec in parameters.items()}
    )
    return {
        "scenario": scenario,
        "metric": "wall_seconds",
        "classification": ABSOLUTE_WALL_CLASSIFICATION,
        "values": finite,
        "center": stats["center"],
        "mad": stats["mad"],
        "relative_mad": stats["relative_mad"],
        "max_observed_deviation": stats["max_observed_deviation"],
        "advisory_envelope_upper": stats["envelope_upper"],
        "note": (
            "Absolute wall time varies materially across hosted runners; this "
            "envelope is informational only and can NEVER hard-fail."
        ),
    }


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _calibration_authority_problems(inputs: dict[str, Any]) -> list[str]:
    """Strict calibration-authority validation (F3B1 §6, fail-closed).

    The policy may only be derived from the authoritative main_push
    calibration run: exactly five samples of ONE workflow invocation on the
    reference commit, with complete, correctly-shaped source provenance.
    Merely non-empty provenance fields are NOT accepted.
    """
    problems: list[str] = []
    source = inputs.get("source") or {}
    samples = inputs.get("samples") or []
    calibration_count = inputs.get("calibration_count")
    if (
        calibration_count != len(samples)
        or len(samples) != AUTHORITATIVE_SAMPLE_COUNT
    ):
        problems.append(
            "calibration_count must equal the sample count and be exactly "
            f"{AUTHORITATIVE_SAMPLE_COUNT} (calibration_count="
            f"{calibration_count!r}, samples={len(samples)})"
        )
    workflow_run_id = str(source.get("workflow_run_id") or "")
    if not workflow_run_id or not _WORKFLOW_RUN_ID_PATTERN.match(workflow_run_id):
        problems.append("source workflow_run_id must be a non-empty numeric ID")
    for sample in samples:
        if str(sample.get("workflow_run_id")) != workflow_run_id:
            problems.append(
                "calibration samples must share the source workflow_run_id "
                f"({workflow_run_id!r})"
            )
            break
    if source.get("source_context") != "main_push":
        problems.append(
            f"calibration source_context must be main_push "
            f"(got {source.get('source_context')!r})"
        )
    if source.get("github_event") != "push":
        problems.append(
            f"calibration source github_event must be push "
            f"(got {source.get('github_event')!r})"
        )
    if source.get("branch") != "main":
        problems.append(
            f"calibration source branch must be main "
            f"(got {source.get('branch')!r})"
        )
    artifact_id = source.get("artifact_id")
    if (
        not isinstance(artifact_id, int)
        or isinstance(artifact_id, bool)
        or artifact_id <= 0
    ):
        problems.append("source artifact_id must be a positive integer")
    artifact_digest = str(source.get("artifact_digest") or "")
    if not _ARTIFACT_DIGEST_PATTERN.match(artifact_digest):
        problems.append(
            "source artifact_digest must match sha256:<64 lowercase hex>"
        )
    compact_json_sha256 = str(source.get("compact_json_sha256") or "")
    if not _SHA64_PATTERN.match(compact_json_sha256):
        problems.append(
            "source compact_json_sha256 must be 64 lowercase hex characters"
        )
    return problems


def _recompute_ratio_values(
    inputs: dict[str, Any],
) -> tuple[dict[str, list[float]], list[str]]:
    """Mechanically recompute every canonical ratio from RAW calibration
    facts (F3B1-BLK-06).

    The four wall ratios are recomputed from ``wall_seconds_by_scenario``
    plus the canonical FULL scenario record counts; the RSS ratio is
    recomputed from ``memory_facts[*].w3_peak_rss_delta_bytes`` /
    ``memory_facts[*].w1_peak_rss_delta_bytes``.  The deterministic replica
    ordering is the ``samples`` list order; ``memory_facts`` must match it.

    The stored ``ratio_raw_values`` are NEVER trusted blindly: they are
    cross-checked against the recomputed facts using only the documented
    serialization-rounding tolerance (the F3A collector serializes RSS
    delta ratios with 6-decimal rounding).  Inconsistent evidence is
    rejected.  The RECOMPUTED values — not the stored ones — are the
    policy's derivation input.
    """
    problems: list[str] = []
    samples = inputs.get("samples") or []
    memory_facts = inputs.get("memory_facts") or []
    walls = inputs.get("wall_seconds_by_scenario") or {}
    sample_count = len(samples)
    if len(memory_facts) != sample_count:
        problems.append(
            f"memory_facts count ({len(memory_facts)}) does not match the "
            f"sample count ({sample_count})"
        )
        return {}, problems
    for position, (sample, fact) in enumerate(
        zip(samples, memory_facts, strict=True)
    ):
        if str(fact.get("replica_id")) != str(sample.get("replica_id")):
            problems.append(
                f"memory_facts[{position}] replica order does not match "
                "samples (deterministic replica ordering violated)"
            )
            break
    rss_numerator_rows: list[dict[str, Any]] = []
    rss_denominator_rows: list[dict[str, Any]] = []
    for position, fact in enumerate(memory_facts):
        label = f"memory_facts[{position}]"
        w1_count = fact.get("w1_record_count")
        w3_count = fact.get("w3_record_count")
        w1_delta = fact.get("w1_peak_rss_delta_bytes")
        w3_delta = fact.get("w3_peak_rss_delta_bytes")
        if w1_count != FULL_SCENARIO_RECORD_COUNTS["direct_write_190k_flat"]:
            problems.append(
                f"{label}: w1_record_count must equal the canonical FULL "
                f"count ({FULL_SCENARIO_RECORD_COUNTS['direct_write_190k_flat']})"
            )
        if w3_count != FULL_SCENARIO_RECORD_COUNTS["direct_write_1m_flat"]:
            problems.append(
                f"{label}: w3_record_count must equal the canonical FULL "
                f"count ({FULL_SCENARIO_RECORD_COUNTS['direct_write_1m_flat']})"
            )
        for name, value in (
            ("w1_peak_rss_delta_bytes", w1_delta),
            ("w3_peak_rss_delta_bytes", w3_delta),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not math.isfinite(float(value))
                or value < 0
            ):
                problems.append(
                    f"{label}: {name} must be a finite non-negative integer"
                )
        if isinstance(w1_delta, int) and not isinstance(w1_delta, bool) and w1_delta <= 0:
            problems.append(f"{label}: w1_peak_rss_delta_bytes must be > 0")
        rss_numerator_rows.append({"peak_rss_delta_bytes": w3_delta})
        rss_denominator_rows.append({"peak_rss_delta_bytes": w1_delta})

    recomputed: dict[str, list[float]] = {}
    stored_values = inputs.get("ratio_raw_values") or {}
    for definition in (
        RATIO_DEFINITIONS_BY_LABEL[label] for label in RATIO_LABELS
    ):
        if definition.normalization == "per_record_ratio":
            numerator_scenario = definition.numerator_scenario
            denominator_scenario = definition.denominator_scenario
            numerator_walls = walls.get(numerator_scenario) or []
            denominator_walls = walls.get(denominator_scenario) or []
            if len(numerator_walls) != sample_count or len(
                denominator_walls
            ) != sample_count:
                problems.append(
                    f"ratio {definition.label!r}: raw wall facts unavailable"
                )
                continue
            numerator_rows = [
                {
                    "wall_seconds": wall,
                    "record_count": FULL_SCENARIO_RECORD_COUNTS[
                        numerator_scenario
                    ],
                }
                for wall in numerator_walls
            ]
            denominator_rows = [
                {
                    "wall_seconds": wall,
                    "record_count": FULL_SCENARIO_RECORD_COUNTS[
                        denominator_scenario
                    ],
                }
                for wall in denominator_walls
            ]
        else:  # raw_ratio — the W3/W1 peak-RSS-delta ratio
            numerator_rows = rss_numerator_rows
            denominator_rows = rss_denominator_rows
        values = evaluate_ratio_values(
            definition, numerator_rows, denominator_rows
        )
        if any(value is None for value in values):
            problems.append(
                f"ratio {definition.label!r}: raw calibration facts "
                "incomplete/malformed"
            )
            continue
        fact_values = [float(value) for value in values if value is not None]
        recomputed[definition.label] = fact_values
        stored = stored_values.get(definition.label)
        if not isinstance(stored, list) or len(stored) != sample_count:
            problems.append(
                f"ratio {definition.label!r}: stored ratio_raw_values "
                "missing or wrong length"
            )
            continue
        for position, (fact_value, serialized_value) in enumerate(
            zip(fact_values, stored, strict=True)
        ):
            if not _finite_number(serialized_value):
                problems.append(
                    f"ratio {definition.label!r}: stored value "
                    f"{serialized_value!r} is not a finite number"
                )
                continue
            deviation = abs(float(serialized_value) - fact_value)
            if deviation > SERIALIZATION_ROUNDING_TOLERANCE:
                problems.append(
                    f"ratio {definition.label!r}: stored calibration value "
                    f"{float(serialized_value)!r} (replica position "
                    f"{position}) does not match the recomputed fact "
                    f"{fact_value!r} beyond the documented serialization "
                    f"rounding tolerance {SERIALIZATION_ROUNDING_TOLERANCE!r}"
                )
    return recomputed, problems


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
    problems.extend(_calibration_authority_problems(inputs))
    # F3B1-BLK-09: the authoritative runtime recipe must parse with the
    # SINGLE strict grammar parser before it may be copied into the policy.
    _, recipe_problems = parse_runtime_recipe(inputs.get("runtime_recipe"))
    problems.extend(f"runtime_recipe: {problem}" for problem in recipe_problems)
    samples = inputs.get("samples") or []
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
    for scenario in MEASURED_SCENARIO_ORDER:
        if scenario not in wall:
            problems.append(f"missing wall_seconds for {scenario}")
        elif len(wall[scenario]) != len(samples):
            problems.append(f"wall_seconds {scenario}: count != sample count")
        elif any(
            not _finite_number(value) for value in wall[scenario]
        ):
            problems.append(f"wall_seconds {scenario}: non-finite value")
    memory_facts = inputs.get("memory_facts") or []
    if len(memory_facts) != len(samples):
        problems.append(
            f"memory_facts count ({len(memory_facts)}) != sample count "
            f"({len(samples)})"
        )
    for label, values in ratio_values.items():
        for value in values:
            if isinstance(value, float) and (
                math.isnan(value) or math.isinf(value)
            ):
                problems.append(f"ratio {label}: NaN/Infinity")
    # F3B1-BLK-06: mechanically recompute every ratio from the raw
    # calibration facts and cross-check the stored serialized values.
    _, recompute_problems = _recompute_ratio_values(inputs)
    problems.extend(recompute_problems)
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
    recomputed_values, recompute_problems = _recompute_ratio_values(inputs)
    problems.extend(recompute_problems)
    if problems:
        return {"accepted": False, "problems": problems}
    ratio_calibration: dict[str, Any] = {}
    for label in RATIO_LABELS:
        definition = RATIO_DEFINITIONS_BY_LABEL[label]
        ratio_calibration[label] = _derive_ratio(
            definition,
            recomputed_values[label],
            {name: PARAMETERS[name] for name in PARAMETERS},
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
    for scenario in MEASURED_SCENARIO_ORDER:
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
            "github_event": source["github_event"],
            "branch": source["branch"],
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

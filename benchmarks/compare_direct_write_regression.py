"""Offline comparator for the v1.1 Direct Write regression policy
(DBFB-PERF-006, stage F3B1).

Compares a candidate Direct Write profile (and its run provenance) against
the committed ``dbfbridge-direct-write-regression-policy-v1``:

- CORRECTNESS gates ALWAYS run and hard-fail regardless of comparability;
- performance hard gates apply ONLY on COMPARABLE environment evidence
  (runner_os / runner_arch / Python major.minor / install_recipe /
  dependency versions); NOT_COMPARABLE never creates a false regression;
- the candidate provenance document must pass the AUTHORITATIVE F3A
  ``validate_provenance`` contract before it may become COMPARABLE;
- ABSOLUTE scenario wall times are ADVISORY ONLY and can never hard-fail;
- both full AND smoke candidates must carry the exact W1-W12 scenario
  contract (the benchmark produces all 12 scenarios in both modes; smoke
  differs only in reduced counts) — W10 disk-spool evidence stays
  full-only and W12 stays functional-only;
- every hard gate uses the canonical ratio formula from the shared
  contract module (``direct_write_regression_contract``): per-record
  normalized wall ratios and the RAW W3/W1 peak-RSS-delta quotient;
- malformed candidate numerics (non-finite/wrong-typed/overflowing) and
  COMPARABLE-but-unevaluated hard gates are deterministic FAILURES
  (``CANDIDATE_MALFORMED`` / ``INCOMPLETE_EVIDENCE``) — never PASS.

Deterministic, offline, stdlib-only; never benchmarks, never touches the
network, never modifies the candidate or the policy.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

from .direct_write_regression_contract import (
    MEASURED_SCENARIO_ORDER,
    NORMALIZATION_PER_RECORD,
    RATIO_DEFINITIONS_BY_LABEL,
    RATIO_LABELS,
)
from .direct_write_regression_contract import (
    SCENARIO_ORDER as _SCENARIO_ORDER,
)

RESULT_CONTRACT = "dbfbridge-direct-write-regression-result-v1"

#: The EXACT three policy parameters (kind/rationale-bearing; §12).
_POLICY_PARAMETERS = {
    "mad_multiplier": 3.0,
    "small_sample_guard_band": 1.15,
    "hard_gate_discrimination_bound": 1.5,
}

_REQUIRED_RATIO_FIELDS = (
    "label",
    "numerator",
    "denominator",
    "metric",
    "normalization",
    "values",
    "center",
    "mad",
    "envelope_upper",
    "classification",
)

_SHA40_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_SHA64_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


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
    # calibration authority facts (F3B1 §6): merely non-empty is not enough
    policy_workflow_run_id = str(calibration_sources.get("workflow_run_id") or "")
    if not policy_workflow_run_id or not policy_workflow_run_id.isdigit():
        problems.append("policy calibration workflow_run_id must be a non-empty numeric ID")
    policy_artifact_id = calibration_sources.get("artifact_id")
    if (
        not isinstance(policy_artifact_id, int)
        or isinstance(policy_artifact_id, bool)
        or policy_artifact_id <= 0
    ):
        problems.append("policy calibration artifact_id must be a positive integer")
    policy_artifact_digest = str(calibration_sources.get("artifact_digest") or "")
    if not _ARTIFACT_DIGEST_PATTERN.match(policy_artifact_digest):
        problems.append(
            "policy calibration artifact_digest must match sha256:<64 lowercase hex>"
        )
    policy_compact_sha = str(calibration_sources.get("compact_json_sha256") or "")
    if not _SHA64_PATTERN.match(policy_compact_sha):
        problems.append(
            "policy calibration compact_json_sha256 must be 64 lowercase hex characters"
        )
    if calibration_sources.get("source_context") != "main_push":
        problems.append("policy calibration source_context must be main_push")
    if calibration_sources.get("github_event") != "push":
        problems.append("policy calibration github_event must be push")
    if calibration_sources.get("branch") != "main":
        problems.append("policy calibration branch must be main")
    calibration_count = policy.get("calibration_count")
    if (
        not isinstance(calibration_count, int)
        or isinstance(calibration_count, bool)
        or calibration_count < 5
    ):
        problems.append("policy calibration_count must be an integer >= 5")
        calibration_count = None
    benchmark_run_ids = calibration_sources.get("benchmark_run_ids")
    report_shas = calibration_sources.get("report_sha256_by_replica")
    if calibration_count is not None:
        if not isinstance(benchmark_run_ids, list) or len(
            benchmark_run_ids
        ) != calibration_count:
            problems.append(
                "policy calibration_count must match the benchmark_run_ids list"
            )
        elif len(set(benchmark_run_ids)) != calibration_count:
            problems.append("policy benchmark_run_ids must be unique")
        if not isinstance(report_shas, dict) or len(report_shas) != calibration_count:
            problems.append(
                "policy calibration_count must match report_sha256_by_replica"
            )
        elif any(
            not isinstance(sha, str) or not _SHA64_PATTERN.match(sha)
            for sha in report_shas.values()
        ):
            problems.append(
                "policy report SHA entries must be 64 lowercase hex characters"
            )
    if not policy.get("runtime_recipe"):
        problems.append("policy runtime_recipe missing")
    parameters = policy.get("parameters") or {}
    allowed_parameters = _POLICY_PARAMETERS
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
        if spec.get("kind") != "engineering_policy_parameter":
            problems.append(f"policy parameter {name} kind must be engineering_policy_parameter")
    derivation = policy.get("derivation") or ""
    if "median" not in derivation or "hard_gate" not in derivation:
        problems.append("policy derivation description incomplete")
    ratio_calibration = policy.get("ratio_calibration") or {}
    if set(ratio_calibration) != set(RATIO_LABELS):
        problems.append(
            "policy ratio set must be exactly the five canonical candidates"
        )
    for label, spec in ratio_calibration.items():
        definition = RATIO_DEFINITIONS_BY_LABEL.get(label)
        if definition is None:
            continue  # already reported above; do not re-derive unknown labels
        for required_field in _REQUIRED_RATIO_FIELDS:
            if required_field not in spec:
                problems.append(
                    f"ratio {label!r}: missing required field {required_field!r}"
                )
        if spec.get("label") != label:
            problems.append(f"ratio {label!r}: entry label mismatch")
        expected_numerator = f"{definition.numerator_scenario}.{definition.metric}"
        expected_denominator = (
            f"{definition.denominator_scenario}.{definition.metric}"
        )
        if spec.get("numerator") != expected_numerator:
            problems.append(f"ratio {label!r}: mispaired numerator")
        if spec.get("denominator") != expected_denominator:
            problems.append(f"ratio {label!r}: mispaired denominator")
        if spec.get("metric") != definition.metric:
            problems.append(
                f"ratio {label!r}: metric tampered "
                f"({spec.get('metric')!r} != {definition.metric!r})"
            )
        if spec.get("normalization") != definition.normalization:
            problems.append(
                f"ratio {label!r}: normalization tampered "
                f"({spec.get('normalization')!r} != {definition.normalization!r})"
            )
        values = spec.get("values") or []
        if calibration_count is not None and len(values) != calibration_count:
            problems.append(
                f"ratio {label!r}: calibration value count must equal "
                f"calibration_count ({calibration_count})"
            )
        for value in values:
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                problems.append(f"ratio {label!r}: non-finite calibration value")
                break
        center = spec.get("center")
        if not isinstance(center, (int, float)) or not math.isfinite(float(center)):
            problems.append(f"ratio {label!r}: non-finite center")
            center = None
        if isinstance(center, (int, float)) and center <= 0:
            problems.append(f"ratio {label!r}: invalid center")
        mad = spec.get("mad")
        if (
            not isinstance(mad, (int, float))
            or not math.isfinite(float(mad))
            or mad < 0
        ):
            problems.append(f"ratio {label!r}: negative/non-finite MAD")
            mad = None
        envelope = spec.get("envelope_upper")
        if (
            not isinstance(envelope, (int, float))
            or not math.isfinite(float(envelope))
            or envelope <= 0
        ):
            problems.append(f"ratio {label!r}: invalid/non-finite envelope")
            envelope = None
        for numeric_field in (
            "relative_mad",
            "max_observed_deviation",
            "spread_based",
            "tail_based",
            "envelope_upper_over_center",
        ):
            numeric_value = spec.get(numeric_field)
            if numeric_value is not None and (
                not isinstance(numeric_value, (int, float))
                or not math.isfinite(float(numeric_value))
            ):
                problems.append(f"ratio {label!r}: non-finite {numeric_field}")
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
    hard_ratio_labels = policy.get("hard_ratio_labels")
    advisory_ratio_labels = policy.get("advisory_ratio_labels")
    if not isinstance(hard_ratio_labels, list) or not isinstance(
        advisory_ratio_labels, list
    ):
        problems.append(
            "policy hard_ratio_labels/advisory_ratio_labels must be lists"
        )
    elif sorted(hard_ratio_labels) != hard_ratio_labels or sorted(
        advisory_ratio_labels
    ) != advisory_ratio_labels:
        problems.append("policy ratio label lists must be sorted")
    else:
        overlap = set(hard_ratio_labels) & set(advisory_ratio_labels)
        if overlap:
            problems.append(f"policy ratio label lists overlap {sorted(overlap)}")
        if set(hard_ratio_labels) | set(advisory_ratio_labels) != set(RATIO_LABELS):
            problems.append(
                "policy hard/advisory ratio label lists must cover exactly "
                "the five canonical candidates"
            )
        for label, spec in ratio_calibration.items():
            classification = spec.get("classification")
            if classification == "hard_gate" and label not in hard_ratio_labels:
                problems.append(
                    f"ratio {label!r}: classified hard_gate but missing from "
                    "hard_ratio_labels"
                )
            if (
                classification == "advisory_only"
                and label not in advisory_ratio_labels
            ):
                problems.append(
                    f"ratio {label!r}: classified advisory_only but missing "
                    "from advisory_ratio_labels"
                )
    scenario_calibration = policy.get("scenario_calibration") or {}
    if set(scenario_calibration) != set(MEASURED_SCENARIO_ORDER):
        problems.append(
            "policy scenario_calibration must be exactly the W1-W11 "
            "advisory scenario set "
            f"(missing {sorted(set(MEASURED_SCENARIO_ORDER) - set(scenario_calibration))}, "
            f"unexpected {sorted(set(scenario_calibration) - set(MEASURED_SCENARIO_ORDER))})"
        )
    for scenario, spec in scenario_calibration.items():
        if spec.get("classification") != "advisory_only":
            problems.append(
                f"scenario {scenario}: absolute wall time classified "
                f"{spec.get('classification')!r} — must be advisory_only"
            )
        if spec.get("scenario") != scenario:
            problems.append(f"scenario {scenario}: entry scenario field mismatch")
        if spec.get("metric") != "wall_seconds":
            problems.append(
                f"scenario {scenario}: advisory metric tampered "
                f"({spec.get('metric')!r} != 'wall_seconds')"
            )
        advisory_values = spec.get("values") or []
        if calibration_count is not None and len(advisory_values) != calibration_count:
            problems.append(
                f"scenario {scenario}: advisory value count must equal "
                f"calibration_count ({calibration_count})"
            )
        for advisory_value in advisory_values:
            if not isinstance(advisory_value, (int, float)) or not math.isfinite(
                float(advisory_value)
            ):
                problems.append(
                    f"scenario {scenario}: non-finite advisory calibration value"
                )
                break
        for numeric_field in (
            "center",
            "mad",
            "relative_mad",
            "max_observed_deviation",
            "advisory_envelope_upper",
        ):
            numeric_value = spec.get(numeric_field)
            if numeric_value is not None and (
                not isinstance(numeric_value, (int, float))
                or not math.isfinite(float(numeric_value))
            ):
                problems.append(
                    f"scenario {scenario}: non-finite advisory {numeric_field}"
                )
    if policy.get("smoke_scenarios") != list(MEASURED_SCENARIO_ORDER):
        problems.append(
            "policy smoke_scenarios must match the canonical W1-W11 smoke "
            "scenario contract"
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
    """Correctness gates ALWAYS run and hard-fail regardless of comparability.

    Both full AND smoke modes require the exact W1-W12 scenario set (the
    benchmark produces all 12 scenarios in both modes; smoke differs only in
    reduced counts — F3B1-BLK-04).  W10 disk-spool evidence is full-only.
    """
    problems: list[str] = []
    if candidate.get("benchmark_contract") != "dbfbridge-direct-write-v1":
        problems.append("wrong benchmark contract")
    if candidate.get("benchmark_contract_version") != 1:
        problems.append("wrong benchmark contract version")
    if candidate.get("mode") != mode:
        problems.append(f"mode must be {mode!r}")
    rows = candidate.get("scenarios") or []
    if not rows:
        problems.append("empty candidate report")
        return problems, False
    scenario_names = [row.get("scenario") for row in rows if isinstance(row, dict)]
    if len(scenario_names) != len(set(scenario_names)):
        problems.append("duplicate scenario in candidate")
    missing = sorted(set(_SCENARIO_ORDER) - set(scenario_names))
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

def _python_major_minor(version: str) -> str:
    return ".".join(str(version).split(".")[:2])


def _comparability(
    policy: dict[str, Any],
    provenance: dict[str, Any] | None,
    *,
    measured_code_sha: str | None = None,
    workflow_run_id: str | None = None,
) -> tuple[str, list[str], dict[str, Any] | None]:
    """Classify candidate provenance using the AUTHORITATIVE F3A validator.

    The candidate provenance document must pass the strict
    ``dbfbridge-direct-write-run-provenance-v1`` whitelist contract from
    ``benchmarks.direct_write_calibration`` (F3A-BLK-05/F3B1-BLK-05) BEFORE
    the runtime recipe is compared.  A structurally invalid provenance can
    never become COMPARABLE.
    """
    if provenance is None:
        return (
            "NOT_COMPARABLE",
            ["candidate provenance missing"],
            None,
        )
    if not isinstance(provenance, dict):
        return (
            "NOT_COMPARABLE",
            ["candidate provenance must be an object"],
            None,
        )
    from .direct_write_calibration import validate_provenance

    provenance_problems = validate_provenance(
        provenance,
        replica_id=provenance.get("replica_id"),
        workflow_run_id=provenance.get("workflow_run_id"),
        measured_code_sha=measured_code_sha or provenance.get("github_sha"),
    )
    github_sha = provenance.get("github_sha")
    if not (
        isinstance(github_sha, str) and _SHA40_PATTERN.match(github_sha)
    ):
        provenance_problems.append(
            "github_sha must be a 40-character lowercase hex SHA"
        )
    checks: list[str] = []
    for problem in provenance_problems:
        checks.append(f"provenance: INVALID ({problem})")
    if provenance_problems:
        return "NOT_COMPARABLE", checks, None
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
        return "NOT_COMPARABLE", checks, provenance
    if not problems:
        return "COMPARABLE", checks, provenance
    if len(problems) <= 2:
        return "PARTIALLY_COMPARABLE", checks, provenance
    return "NOT_COMPARABLE", checks, provenance

def _finite_float(value: Any) -> float | None:
    """Deterministic, exception-free finite-float conversion (F3B1 §8).

    Rejects bools, strings, None, NaN/±Infinity and integers too large for
    float (OverflowError) — a malformed candidate value NEVER crashes the
    comparator and never turns a comparison into PASS.
    """
    try:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        converted = float(value)
    except (OverflowError, ValueError, TypeError):
        return None
    if not math.isfinite(converted):
        return None
    return converted


def _positive_int(value: Any) -> int | None:
    """Deterministic positive-integer check (bools are never counts)."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _evaluate_ratio(
    label: str,
    spec: dict[str, Any],
    rows_by_scenario: dict[str, dict[str, Any]],
    comparability: str,
) -> dict[str, Any]:
    """Evaluate ONE hard-gate ratio against the policy envelope.

    Uses the canonical contract formula per normalization kind:

    - per_record_ratio (wall): ``(num_wall / num_count) / (den_wall / den_count)``
      with both wall seconds finite > 0 and record counts integer > 0;
    - raw_ratio (RSS): ``numerator.peak_rss_delta_bytes / denominator.peak_rss_delta_bytes``
      (NOT normalized by record count — RSS delta is a retained-memory ratio),
      with the numerator finite >= 0 and the denominator finite > 0.

    Not-comparable evidence degrades the gate to NOT_COMPARABLE (never a
    false regression); candidates lacking the required scenario facts report
    NOT_EVALUATED_IN_SMOKE (never PASS).  Malformed candidate numerics report
    CANDIDATE_MALFORMED deterministically (no internal exception).
    """
    definition = RATIO_DEFINITIONS_BY_LABEL[label]
    numerator_row = rows_by_scenario.get(definition.numerator_scenario)
    denominator_row = rows_by_scenario.get(definition.denominator_scenario)
    count_ratio: float | None = None
    if (
        numerator_row is None
        or denominator_row is None
        or numerator_row.get(definition.metric) is None
        or denominator_row.get(definition.metric) is None
    ):
        return {
            "label": label,
            "gate": spec.get("classification"),
            "status": "NOT_EVALUATED_IN_SMOKE",
            "reason": (
                f"{definition.numerator_scenario}/{definition.denominator_scenario}"
                " scenario/metric not present in candidate"
            ),
        }
    numerator_number = _finite_float(numerator_row[definition.metric])
    if numerator_number is None:
        return {
            "label": label,
            "gate": spec.get("classification"),
            "status": "CANDIDATE_MALFORMED",
            "reason": f"numerator {definition.metric} non-finite/malformed",
        }
    denominator_number = _finite_float(denominator_row[definition.metric])
    if denominator_number is None:
        return {
            "label": label,
            "gate": spec.get("classification"),
            "status": "CANDIDATE_MALFORMED",
            "reason": f"denominator {definition.metric} non-finite/malformed",
        }
    if definition.normalization == NORMALIZATION_PER_RECORD:
        numerator_count = _positive_int(numerator_row.get("record_count"))
        denominator_count = _positive_int(denominator_row.get("record_count"))
        if numerator_number <= 0 or denominator_number <= 0:
            return {
                "label": label,
                "gate": spec.get("classification"),
                "status": "CANDIDATE_MALFORMED",
                "reason": "wall_seconds must be finite > 0",
            }
        if numerator_count is None or denominator_count is None:
            return {
                "label": label,
                "gate": spec.get("classification"),
                "status": "CANDIDATE_MALFORMED",
                "reason": "record counts missing/non-positive in candidate",
            }
        value = (numerator_number / numerator_count) / (
            denominator_number / denominator_count
        )
        count_ratio = round(numerator_count / denominator_count, 4)
    else:  # raw_ratio — the RSS delta is a retained-memory ratio
        if numerator_number < 0:
            return {
                "label": label,
                "gate": spec.get("classification"),
                "status": "CANDIDATE_MALFORMED",
                "reason": "numerator peak_rss_delta_bytes must be >= 0",
            }
        if denominator_number <= 0:
            return {
                "label": label,
                "gate": spec.get("classification"),
                "status": "CANDIDATE_MALFORMED",
                "reason": "denominator peak_rss_delta_bytes must be > 0",
            }
        value = numerator_number / denominator_number
    envelope_upper = float(spec["envelope_upper"])
    center = float(spec["center"])
    if comparability == "COMPARABLE":
        status = "REGRESSION" if value > envelope_upper else "PASS"
    else:
        status = f"NOT_COMPARABLE ({comparability})"
    result: dict[str, Any] = {
        "label": label,
        "gate": spec.get("classification"),
        "status": status,
        "value": round(value, 9),
        "envelope_upper": envelope_upper,
        "center": center,
    }
    if definition.normalization == NORMALIZATION_PER_RECORD:
        result["numerator_wall"] = numerator_number
        result["denominator_wall"] = denominator_number
        result["record_count_ratio"] = count_ratio
    else:
        result["numerator_delta_bytes"] = numerator_number
        result["denominator_delta_bytes"] = denominator_number
    return result


def compare_candidate(
    policy: dict[str, Any],
    candidate: dict[str, Any],
    provenance: dict[str, Any] | None,
    *,
    mode: str,
    measured_code_sha: str | None = None,
    workflow_run_id: str | None = None,
) -> dict[str, Any]:
    """Full deterministic comparison: correctness, comparability, performance.

    ``measured_code_sha``/``workflow_run_id`` are forwarded to the
    authoritative F3A provenance validator for cross-checking against the
    candidate's run provenance when provided.
    """
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
    comparability_classification, comparability_checks, _validated_provenance = (
        _comparability(
            policy,
            provenance,
            measured_code_sha=candidate.get("measured_code_sha"),
            workflow_run_id=candidate.get("workflow_run_id"),
        )
    )
    rows_by_scenario = {
        row.get("scenario"): row for row in candidate.get("scenarios", [])
    }
    hard_gates: list[dict[str, Any]] = []
    confirmed_regression = False
    candidate_malformed = False
    unevaluated_evidence = False
    for label in sorted(policy.get("ratio_calibration") or {}):
        spec = policy["ratio_calibration"][label]
        if spec.get("classification") != "hard_gate":
            continue
        gate = _evaluate_ratio(
            label, spec, rows_by_scenario, comparability_classification
        )
        hard_gates.append(gate)
        if (
            gate["status"] == "REGRESSION"
            and comparability_classification == "COMPARABLE"
        ):
            confirmed_regression = True
        if gate["status"] == "CANDIDATE_MALFORMED":
            candidate_malformed = True
        if gate["status"] == "NOT_EVALUATED_IN_SMOKE":
            unevaluated_evidence = True
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
    # Overall status is DETERMINISTIC and fail-closed (F3B1-BLK-03/04/08):
    # - correctness failures always dominate;
    # - malformed candidate numerics never evaluate to PASS;
    # - COMPARABLE evidence with unevaluated hard gates (e.g. a smoke run
    #   without its real performance facts) can NEVER produce PASS.
    if not correctness_ok:
        overall = "INCORRECT"
    elif candidate_malformed:
        overall = "CANDIDATE_MALFORMED"
    elif confirmed_regression:
        overall = "REGRESSION"
    elif unevaluated_evidence:
        overall = "INCOMPLETE_EVIDENCE"
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
        f"Overall: `{payload.get('overall_status')}`",
        f"Correctness: `{payload['correctness']['status']}`",
        f"Comparability: `{payload['comparability']['classification']}`",
        "",
    ]
    for gate in payload["performance"]["hard_gates"]:
        lines.append(
            f"- {gate['label']}: {gate['status']}"
            + (f" (value {gate.get('value')} vs envelope {gate.get('envelope_upper')})" if gate.get("value") is not None else "")
        )
    return "\n".join(lines) + "\n"


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

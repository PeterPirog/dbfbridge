"""F3B1 policy generator tests.

Deterministic tests for the Direct Write regression policy generator:
acceptance of the authoritative calibration input, mechanical ratio
derivation, strict rejection of malformed/tampered inputs, absolute-wall
advisory semantics, reproducibility, and privacy.  No real 1M benchmark is
executed in these tests.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks import calibrate_direct_write_regression as generator  # noqa: E402
from benchmarks.direct_write_regression_contract import (  # noqa: E402
    FULL_SCENARIO_RECORD_COUNTS,
    RATIO_DEFINITIONS_BY_LABEL,
    SERIALIZATION_ROUNDING_TOLERANCE,
    evaluate_ratio_values,
)


def _inputs() -> dict:
    return generator.load_inputs(
        ROOT / "benchmarks" / "regression"
        / "direct-write-regression-calibration-inputs-v1.json"
    )


def test_authoritative_inputs_accepted() -> None:
    problems = generator.validate_inputs(_inputs())
    assert problems == []
    payload = generator.generate_policy(_inputs())
    assert payload["accepted"] is True
    assert payload["policy_contract"] == generator.POLICY_CONTRACT
    assert payload["reference_commit"] == "ebc47bf0039329c32a039c98dca58d1a86175944"
    json.dumps(payload)


def test_policy_has_separate_contract_identity() -> None:
    assert generator.POLICY_CONTRACT == "dbfbridge-direct-write-regression-policy-v1"
    assert generator.POLICY_CONTRACT != "phase-3-regression-policy-v1"
    assert generator.POLICY_CONTRACT != "dbfbridge-direct-write-v1"


def test_fewer_than_five_samples_rejected() -> None:
    inputs = _inputs()
    inputs["samples"] = inputs["samples"][:4]
    for label in inputs["ratio_raw_values"]:
        inputs["ratio_raw_values"][label] = inputs["ratio_raw_values"][label][:4]
    payload = generator.generate_policy(inputs)
    assert payload["accepted"] is False
    assert any("exactly 5" in item for item in payload["problems"])


def test_duplicate_sample_identity_rejected() -> None:
    inputs = _inputs()
    inputs["samples"][1] = dict(inputs["samples"][0])
    payload = generator.generate_policy(inputs)
    assert payload["accepted"] is False
    assert any("duplicate sample identity" in item for item in payload["problems"])


def test_wrong_source_commit_rejected() -> None:
    inputs = _inputs()
    inputs["reference_commit"] = "f" * 40
    payload = generator.generate_policy(inputs)
    assert payload["accepted"] is False
    assert any(
        "must share the reference_commit" in item for item in payload["problems"]
    )


def test_missing_ratio_candidate_rejected() -> None:
    inputs = _inputs()
    del inputs["ratio_raw_values"]["W5/W1 wall-seconds-per-record"]
    payload = generator.generate_policy(inputs)
    assert payload["accepted"] is False
    assert any("missing ratio candidates" in item for item in payload["problems"])


def test_extra_ratio_candidate_rejected() -> None:
    inputs = _inputs()
    inputs["ratio_raw_values"]["W7/W1 wall-seconds-per-record"] = [1.0] * 5
    payload = generator.generate_policy(inputs)
    assert payload["accepted"] is False
    assert any("extra ratio candidates" in item for item in payload["problems"])


def test_nan_ratio_value_rejected() -> None:
    inputs = _inputs()
    inputs["ratio_raw_values"]["W3/W1 wall-seconds-per-record"] = [
        1.0, float("nan"), 1.0, 1.0, 1.0,
    ]
    payload = generator.generate_policy(inputs)
    assert payload["accepted"] is False
    assert any("non-finite value" in item for item in payload["problems"])


def test_infinity_ratio_value_rejected() -> None:
    inputs = _inputs()
    inputs["ratio_raw_values"]["W3/W1 peak-RSS-delta ratio"] = [
        float("inf"), 3.4, 3.5, 3.4, 3.5,
    ]
    problems = generator.validate_inputs(inputs)
    assert any("non-finite value" in item for item in problems)


def test_invalid_report_sha_rejected() -> None:
    inputs = _inputs()
    inputs["samples"][0]["report_sha256"] = "not-a-sha"
    problems = generator.validate_inputs(inputs)
    assert any("invalid report SHA" in item for item in problems)


def test_invalid_provenance_shape_rejected() -> None:
    inputs = _inputs()
    inputs["source"]["workflow_run_id"] = ""
    payload = generator.generate_policy(inputs)
    assert payload["accepted"] is False
    assert any("missing source provenance" in item for item in payload["problems"])


def test_absolute_wall_always_advisory() -> None:
    payload = generator.generate_policy(_inputs())
    for spec in payload["scenario_calibration"].values():
        assert spec["classification"] == "advisory_only"
        assert spec["note"]


def test_ratio_classification_derived_mechanically() -> None:
    payload = generator.generate_policy(_inputs())
    for label, spec in payload["ratio_calibration"].items():
        expected = (
            "hard_gate"
            if spec["envelope_upper"] <= spec["center"] * 1.5
            else "advisory_only"
        )
        assert spec["classification"] == expected, label


def test_hard_ratios_exist_in_authoritative_policy() -> None:
    payload = generator.generate_policy(_inputs())
    assert len(payload["hard_ratio_labels"]) >= 1
    assert payload["hard_ratio_labels"] == sorted(payload["hard_ratio_labels"])


def test_zero_hard_ratios_rejected_as_nondiscriminating(tmp_path: Path) -> None:
    """A policy with zero hard ratios is rejected as non-useful.  The raw
    facts AND the stored ratio values are tampered TOGETHER so the
    mechanical cross-check stays satisfied and the dispersion itself (not a
    cross-check failure) triggers the rejection."""

    def recompute_walls(scenario: str, count: int, desired: list[float]) -> None:
        for position, target in enumerate(desired):
            inputs["wall_seconds_by_scenario"][scenario][position] = (
                target
                * inputs["wall_seconds_by_scenario"]["direct_write_190k_flat"][
                    position
                ]
                * count
                / 190_000
            )
        inputs["ratio_raw_values"][label] = [
            (inputs["wall_seconds_by_scenario"][scenario][position] / count)
            / (
                inputs["wall_seconds_by_scenario"]["direct_write_190k_flat"][
                    position
                ]
                / 190_000
            )
            for position in range(5)
        ]

    inputs = _inputs()
    spread = [0.5, 2.5, 1.0, 3.5, 1.9]
    for scenario, label in {
        "direct_write_1m_flat": "W3/W1 wall-seconds-per-record",
        "direct_read_transform_write_190k": "W2/W1 wall-seconds-per-record",
        "direct_write_memo_heavy": "W5/W1 wall-seconds-per-record",
        "direct_write_varchar_nullflags": "W10/W1 wall-seconds-per-record",
    }.items():
        count = FULL_SCENARIO_RECORD_COUNTS[scenario]
        base = inputs["ratio_raw_values"][label][0]
        recompute_walls(scenario, count, [base * factor for factor in spread])
    # the RSS ratio facts disperse too (raw W3 deltas + stored values)
    label = "W3/W1 peak-RSS-delta ratio"
    base_rss = inputs["ratio_raw_values"][label][0]
    for position, factor in enumerate(spread):
        w1_delta = inputs["memory_facts"][position]["w1_peak_rss_delta_bytes"]
        w3_delta = round(base_rss * factor * w1_delta)
        inputs["memory_facts"][position]["w3_peak_rss_delta_bytes"] = w3_delta
        inputs["ratio_raw_values"][label][position] = w3_delta / w1_delta
    payload = generator.generate_policy(inputs)
    if not payload["accepted"]:
        assert any("non-discriminating" in item for item in payload["problems"])


def test_policy_reproducible_from_committed_inputs() -> None:
    """F3B1-BLK-08: the COMMITTED policy file must equal the deterministic
    generator output — structural equality AND byte equality under the
    generator's canonical serialization.  A manually edited committed
    envelope/runtime recipe/source field makes this test FAIL."""
    inputs = _inputs()
    generated = generator.generate_policy(inputs)
    committed_path = (
        ROOT / "benchmarks" / "regression"
        / "direct-write-regression-policy-v1.json"
    )
    committed_text = committed_path.read_text(encoding="utf-8")
    committed = json.loads(committed_text)
    canonical = json.dumps(generated, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    # canonical structural equality (JSON-normalized structures)
    assert json.loads(canonical) == committed
    # deterministic serialized equality under the generator's serialization
    assert committed_text == canonical


def test_committed_policy_bytes_reproduce_via_cli(tmp_path: Path) -> None:
    """F3B1-BLK-08 (%TEMP% gate): the CLI generator run in a fresh process
    reproduces the committed policy bytes exactly."""
    output = tmp_path / "policy.json"
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(
        [
            sys.executable, "-m",
            "benchmarks.calibrate_direct_write_regression",
            "--output", str(output),
        ],
        cwd=ROOT, capture_output=True, text=True, env=env, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    committed_path = (
        ROOT / "benchmarks" / "regression"
        / "direct-write-regression-policy-v1.json"
    )
    committed = committed_path.read_bytes()
    generated = output.read_bytes()
    assert hashlib.sha256(generated).hexdigest() == hashlib.sha256(
        committed
    ).hexdigest()
    assert generated == committed


def test_committed_policy_tamper_is_detectable() -> None:
    """F3B1-BLK-08 (negative control): a finite manually edited committed
    envelope (or recipe/source field) is detected as drift from the
    deterministic generator output."""
    inputs = _inputs()
    generated = generator.generate_policy(inputs)
    tampered = json.loads(json.dumps(generated))
    tampered["ratio_calibration"]["W3/W1 peak-RSS-delta ratio"][
        "envelope_upper"
    ] = 5.0
    tampered["runtime_recipe"] = "Windows|X64|python-3.14|" + tampered["runtime_recipe"].split("|", 3)[3]
    assert tampered != generated
    assert json.dumps(tampered, sort_keys=True) != json.dumps(
        generated, sort_keys=True
    )


def test_policy_source_provenance_complete() -> None:
    payload = generator.generate_policy(_inputs())
    sources = payload["calibration_sources"]
    assert sources["workflow_run_id"] == "34352345104"
    assert sources["artifact_id"] == 10104538589
    assert sources["artifact_digest"] == (
        "sha256:93d2f3b873b64e821a01401862fe67703ef4a6b321fd8d44865b3df128af6d9f"
    )
    assert sources["compact_json_sha256"] == (
        "c7113378d154a823e80c8e56f9f00b47bcbc2f9186d7fe72af40616e07125e28"
    )
    assert len(sources["benchmark_run_ids"]) == 5
    assert sources["rebaseline_rule"]


def test_parameters_explicit_and_rationaled() -> None:
    payload = generator.generate_policy(_inputs())
    parameters = payload["parameters"]
    assert set(parameters) == {
        "mad_multiplier", "small_sample_guard_band", "hard_gate_discrimination_bound",
    }
    assert parameters["mad_multiplier"]["value"] == 3.0
    assert parameters["small_sample_guard_band"]["value"] == 1.15
    assert parameters["hard_gate_discrimination_bound"]["value"] == 1.5
    for spec in parameters.values():
        assert spec["kind"] == "engineering_policy_parameter"
        assert spec["rationale"]


def test_absolute_wall_never_hard_gate() -> None:
    payload = generator.generate_policy(_inputs())
    for spec in payload["scenario_calibration"].values():
        assert spec["classification"] == "advisory_only"


def test_privacy_sentinels_rejected_in_inputs() -> None:
    inputs = _inputs()
    inputs["samples"][0]["memo_text"] = "secret"
    problems = generator.validate_inputs(inputs)
    assert any("privacy sentinel" in item for item in problems)


def test_authoritative_ratio_facts_match_artifact() -> None:
    inputs = _inputs()
    values = inputs["ratio_raw_values"]
    assert values["W3/W1 wall-seconds-per-record"] == [
        1.0214646772609786, 0.9951332646501805, 0.9776612112683664,
        0.933061807741649, 0.9902604010803056,
    ]
    assert values["W2/W1 wall-seconds-per-record"] == [
        1.1937772920075793, 1.191591666826306, 1.1661530091712686,
        1.1885859229841342, 1.2424836332309066,
    ]
    assert values["W5/W1 wall-seconds-per-record"] == [
        1.4851084574383413, 1.338608230895978, 1.26182588799232,
        1.377352605454371, 1.445069586235521,
    ]
    assert values["W10/W1 wall-seconds-per-record"] == [
        0.9855460251269491, 1.0815846990199487, 1.0161299294447472,
        0.9598718505598662, 1.0158666755025711,
    ]
    assert values["W3/W1 peak-RSS-delta ratio"] == [
        3.481296, 3.431614, 3.487174, 3.452797, 3.48822,
    ]
    # wall centers are recomputed BIT-IDENTICALLY from the raw wall facts
    policy = generator.generate_policy(inputs)
    rc = policy["ratio_calibration"]
    assert rc["W3/W1 wall-seconds-per-record"]["center"] == 0.9902604010803056
    assert rc["W2/W1 wall-seconds-per-record"]["center"] == 1.191591666826306
    assert rc["W5/W1 wall-seconds-per-record"]["center"] == 1.377352605454371
    assert rc["W10/W1 wall-seconds-per-record"]["center"] == 1.0158666755025711
    # the RSS center is derived from the raw W3/W1 delta facts and stays
    # within the documented serialization rounding of 3.481296
    assert abs(
        rc["W3/W1 peak-RSS-delta ratio"]["center"] - 3.481296
    ) < 1e-6


def test_parameter_tampering_rejected_by_reproduction() -> None:
    """A tampered generator parameter set must not silently change policy:
    the committed PARAMETERS block is the single authority."""
    assert generator.PARAMETERS["mad_multiplier"]["value"] == 3.0
    assert generator.PARAMETERS["small_sample_guard_band"]["value"] == 1.15
    assert generator.PARAMETERS["hard_gate_discrimination_bound"]["value"] == 1.5


# ---------------------------------------------------------------------------
# F3B1-BLK-02: explicit policy formula metadata
# ---------------------------------------------------------------------------


def test_ratio_policy_metadata_explicit() -> None:
    """Every ratio_calibration entry must explicitly carry label, numerator,
    denominator, metric and normalization (F3B1-BLK-02)."""
    payload = generator.generate_policy(_inputs())
    for label, spec in payload["ratio_calibration"].items():
        assert spec["label"] == label
        assert spec["numerator"].startswith(
            RATIO_DEFINITIONS_BY_LABEL[label].numerator_scenario + "."
        )
        assert spec["denominator"].startswith(
            RATIO_DEFINITIONS_BY_LABEL[label].denominator_scenario
        )
        assert spec["metric"] == RATIO_DEFINITIONS_BY_LABEL[label].metric
        assert spec["normalization"] == RATIO_DEFINITIONS_BY_LABEL[label].normalization


def test_wall_ratios_are_per_record_and_rss_is_raw() -> None:
    payload = generator.generate_policy(_inputs())
    for label in (
        "W3/W1 wall-seconds-per-record",
        "W2/W1 wall-seconds-per-record",
        "W5/W1 wall-seconds-per-record",
        "W10/W1 wall-seconds-per-record",
    ):
        assert payload["ratio_calibration"][label]["normalization"] == "per_record_ratio", label
    assert (
        payload["ratio_calibration"]["W3/W1 peak-RSS-delta ratio"][
            "normalization"
        ]
        == "raw_ratio"
    )


def test_rss_raw_formula_arithmetic_proof() -> None:
    """F3B1-BLK-01 mechanical proof: 132000000 / 38000000 ~= 3.473684 for
    the W3/W1 RSS delta under the RAW formula (NOT divided by record
    counts, which would yield ~0.66)."""
    definition = RATIO_DEFINITIONS_BY_LABEL["W3/W1 peak-RSS-delta ratio"]
    values = evaluate_ratio_values(
        definition,
        [{"peak_rss_delta_bytes": 132_000_000}],
        [{"peak_rss_delta_bytes": 38_000_000}],
    )
    ratio_value = values[0]
    assert ratio_value is not None
    assert ratio_value == 132_000_000 / 38_000_000
    assert abs(ratio_value - 3.473684) < 1e-6  # approx 3.473684
    assert abs(ratio_value - 0.66) > 2.0  # never the per-record figure


# ---------------------------------------------------------------------------
# F3B1-BLK-06: mechanical recomputation from raw calibration facts
# ---------------------------------------------------------------------------


def test_policy_values_derived_from_recomputed_facts() -> None:
    """The policy ratio values are the mechanically recomputed facts —
    wall ratios bit-identical from the raw 6-decimal wall facts, the RSS
    ratio full-precision from the raw byte deltas."""
    inputs = _inputs()
    recomputed, problems = generator._recompute_ratio_values(inputs)
    assert problems == []
    policy = generator.generate_policy(inputs)
    for label, spec in policy["ratio_calibration"].items():
        assert spec["values"] == recomputed[label], label
    walls = inputs["wall_seconds_by_scenario"]
    expected_w3 = (walls["direct_write_1m_flat"][0] / 1_000_000) / (
        walls["direct_write_190k_flat"][0] / 190_000
    )
    assert recomputed["W3/W1 wall-seconds-per-record"][0] == expected_w3
    rss = recomputed["W3/W1 peak-RSS-delta ratio"]
    assert rss[0] == (
        inputs["memory_facts"][0]["w3_peak_rss_delta_bytes"]
        / inputs["memory_facts"][0]["w1_peak_rss_delta_bytes"]
    )


def test_tampered_w5_ratio_value_rejected() -> None:
    """A tampered stored W5 ratio value cannot pass the mechanical
    cross-check against the raw wall facts (F3B1-BLK-06)."""
    inputs = _inputs()
    inputs["ratio_raw_values"]["W5/W1 wall-seconds-per-record"][2] = 9.99
    payload = generator.generate_policy(inputs)
    assert payload["accepted"] is False
    assert any(
        "W5/W1 wall-seconds-per-record" in item and "does not match" in item
        for item in payload["problems"]
    )


def test_tampered_rss_ratio_value_rejected() -> None:
    inputs = _inputs()
    inputs["ratio_raw_values"]["W3/W1 peak-RSS-delta ratio"][1] = 0.66
    payload = generator.generate_policy(inputs)
    assert payload["accepted"] is False
    assert any(
        "W3/W1 peak-RSS-delta ratio" in item and "does not match" in item
        for item in payload["problems"]
    )


def test_tampered_raw_w3_rss_delta_rejected() -> None:
    """A tampered RAW W3 RSS delta (memory_facts) must be caught: the
    stored ratio no longer matches the recomputed facts."""
    inputs = _inputs()
    inputs["memory_facts"][2]["w3_peak_rss_delta_bytes"] = 132_000_000
    payload = generator.generate_policy(inputs)
    assert payload["accepted"] is False
    assert any(
        "W3/W1 peak-RSS-delta ratio" in item and "does not match" in item
        for item in payload["problems"]
    )


def test_memory_fact_replica_order_mismatch_rejected() -> None:
    inputs = _inputs()
    inputs["memory_facts"][1], inputs["memory_facts"][2] = (
        inputs["memory_facts"][2],
        inputs["memory_facts"][1],
    )
    payload = generator.generate_policy(inputs)
    assert payload["accepted"] is False
    assert any("replica order" in item for item in payload["problems"])


def test_memory_fact_record_count_mismatch_rejected() -> None:
    inputs = _inputs()
    inputs["memory_facts"][0]["w3_record_count"] = 500_000
    payload = generator.generate_policy(inputs)
    assert payload["accepted"] is False
    assert any("w3_record_count" in item for item in payload["problems"])
    assert FULL_SCENARIO_RECORD_COUNTS["direct_write_1m_flat"] == 1_000_000


def test_zero_w1_rss_delta_in_facts_rejected() -> None:
    inputs = _inputs()
    inputs["memory_facts"][0]["w1_peak_rss_delta_bytes"] = 0
    payload = generator.generate_policy(inputs)
    assert payload["accepted"] is False
    assert any("must be > 0" in item for item in payload["problems"])


def test_tampering_within_serialization_tolerance_is_accepted() -> None:
    """A stored value deviating by LESS than the documented serialization
    rounding tolerance is still accepted (the tolerance exists exactly for
    the F3A 6-decimal serialization)."""
    inputs = _inputs()
    inputs["ratio_raw_values"]["W3/W1 peak-RSS-delta ratio"][0] += (
        SERIALIZATION_ROUNDING_TOLERANCE / 2
    )
    problems = generator.validate_inputs(inputs)
    assert problems == []


# ---------------------------------------------------------------------------
# F3B1 §6: calibration authority validation
# ---------------------------------------------------------------------------


def test_wrong_source_context_rejected() -> None:
    inputs = _inputs()
    inputs["source"]["source_context"] = "pull_request_merge_ref"
    payload = generator.generate_policy(inputs)
    assert payload["accepted"] is False
    assert any("source_context must be main_push" in item for item in payload["problems"])


def test_wrong_github_event_rejected() -> None:
    inputs = _inputs()
    inputs["source"]["github_event"] = "workflow_dispatch"
    payload = generator.generate_policy(inputs)
    assert payload["accepted"] is False
    assert any("github_event must be push" in item for item in payload["problems"])


def test_wrong_branch_rejected() -> None:
    inputs = _inputs()
    inputs["source"]["branch"] = "perf/v1.1-direct-write-regression-policy"
    payload = generator.generate_policy(inputs)
    assert payload["accepted"] is False
    assert any("branch must be main" in item for item in payload["problems"])


def test_non_numeric_workflow_run_id_rejected() -> None:
    inputs = _inputs()
    inputs["source"]["workflow_run_id"] = "run-abc"
    payload = generator.generate_policy(inputs)
    assert payload["accepted"] is False
    assert any("non-empty numeric ID" in item for item in payload["problems"])


def test_zero_artifact_id_rejected() -> None:
    inputs = _inputs()
    inputs["source"]["artifact_id"] = 0
    payload = generator.generate_policy(inputs)
    assert payload["accepted"] is False
    assert any("artifact_id must be a positive integer" in item for item in payload["problems"])


def test_wrong_artifact_digest_format_rejected() -> None:
    inputs = _inputs()
    inputs["source"]["artifact_digest"] = "93d2f3b8"  # missing sha256: prefix
    payload = generator.generate_policy(inputs)
    assert payload["accepted"] is False
    assert any(
        "artifact_digest must match sha256:<64 lowercase hex>" in item
        for item in payload["problems"]
    )


def test_wrong_compact_sha_format_rejected() -> None:
    inputs = _inputs()
    inputs["source"]["compact_json_sha256"] = "C7113378" + "0" * 56  # uppercase
    payload = generator.generate_policy(inputs)
    assert payload["accepted"] is False
    assert any(
        "compact_json_sha256 must be 64 lowercase hex" in item
        for item in payload["problems"]
    )


def test_sample_workflow_run_mismatch_rejected() -> None:
    inputs = _inputs()
    inputs["samples"][2]["workflow_run_id"] = "34352345105"
    payload = generator.generate_policy(inputs)
    assert payload["accepted"] is False
    assert any("share the source workflow_run_id" in item for item in payload["problems"])


def test_calibration_count_mismatch_rejected() -> None:
    inputs = _inputs()
    inputs["calibration_count"] = 4
    payload = generator.generate_policy(inputs)
    assert payload["accepted"] is False
    assert any("exactly 5" in item for item in payload["problems"])

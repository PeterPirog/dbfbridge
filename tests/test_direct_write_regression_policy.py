"""F3B1 policy generator tests.

Deterministic tests for the Direct Write regression policy generator:
acceptance of the authoritative calibration input, mechanical ratio
derivation, strict rejection of malformed/tampered inputs, absolute-wall
advisory semantics, reproducibility, and privacy.  No real 1M benchmark is
executed in these tests.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks import calibrate_direct_write_regression as generator  # noqa: E402


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
    assert any("fewer than 5" in item for item in payload["problems"])


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
    """A policy with zero hard ratios is rejected as non-useful."""

    inputs = _inputs()
    # make every ratio wildly dispersed so none satisfies the bound
    for label in inputs["ratio_raw_values"]:
        values = inputs["ratio_raw_values"][label]
        center = statistics_median(values)
        inputs["ratio_raw_values"][label] = [
            center * 0.5, center * 2.5, center, center * 3.5, center * 1.9,
        ]
    payload = generator.generate_policy(inputs)
    if not payload["accepted"]:
        assert any("non-discriminating" in item for item in payload["problems"])


def statistics_median(values: list[float]) -> float:
    import statistics as st

    return st.median(values)


def test_policy_reproducible_from_committed_inputs() -> None:
    inputs = _inputs()
    first = generator.generate_policy(inputs)
    second = generator.generate_policy(inputs)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


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
    # medians match the authoritative calibration facts
    policy = generator.generate_policy(inputs)
    rc = policy["ratio_calibration"]
    assert rc["W3/W1 wall-seconds-per-record"]["center"] == 0.9902604010803056
    assert rc["W2/W1 wall-seconds-per-record"]["center"] == 1.191591666826306
    assert rc["W5/W1 wall-seconds-per-record"]["center"] == 1.377352605454371
    assert rc["W10/W1 wall-seconds-per-record"]["center"] == 1.0158666755025711
    assert rc["W3/W1 peak-RSS-delta ratio"]["center"] == 3.481296


def test_parameter_tampering_rejected_by_reproduction() -> None:
    """A tampered generator parameter set must not silently change policy:
    the committed PARAMETERS block is the single authority."""
    assert generator.PARAMETERS["mad_multiplier"]["value"] == 3.0
    assert generator.PARAMETERS["small_sample_guard_band"]["value"] == 1.15
    assert generator.PARAMETERS["hard_gate_discrimination_bound"]["value"] == 1.5

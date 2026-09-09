"""F3A calibration-contract tests for the Direct Write regression pipeline.

Deterministic offline tests using SMALL SYNTHETIC serialized report objects
derived from the real ``dbfbridge-direct-write-v1`` full-artifact shape.
They prove the strict rejection rules of the calibration collector
(fail-closed on ANY of the listed defects) and the acceptance of one valid
five-sample calibration set that shares ONE ``workflow_run_id`` across five
unique replica identities (the matrix sample-identity model).  No real 1M
benchmark is executed in these tests.  No performance threshold is
established.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks import direct_write_calibration as calibration  # noqa: E402

WORKFLOW_RUN_ID = "34270200000"
SCENARIOS = calibration.SCENARIO_IDS


def _base_sample(replica_id: str, run_id: str) -> dict:
    """A minimal VALID synthetic full-report object (real artifact shape)."""
    return {
        "benchmark_contract": calibration.BENCHMARK_CONTRACT,
        "benchmark_contract_version": calibration.BENCHMARK_CONTRACT_VERSION,
        "mode": "full",
        "measured_code_sha": "a" * 40,
        "git_sha": "b" * 40,
        "run_id": run_id,
        "replica_id": replica_id,
        "workflow_run_id": "30000000000",
        "report_sha256": "c" * 64,
        "validation_problems": [],
        "scenarios": [
            {
                "scenario": "direct_write_190k_flat",
                "status": "MEASURED",
                "record_count": 190_000,
                "wall_seconds": 13.0,
                "cpu_seconds": 12.0,
                "records_per_second": 14_600.0,
                "rss_before_bytes": 27_000_000,
                "peak_rss_bytes": 65_000_000,
                "peak_rss_delta_bytes": 38_000_000,
                "rss_after_bytes": 55_000_000,
                "temporary_publish_bytes_written": 5_700_000,
                "private_spool_bytes_written": 0,
                "temporary_bytes_written": 5_700_000,
                "temporary_bytes_left": 0,
                "final_output_bytes": 5_700_000,
                "intermediate_jsonl_bytes": 0,
            },
            {
                "scenario": "direct_read_transform_write_190k",
                "status": "MEASURED",
                "record_count": 190_000,
                "wall_seconds": 16.0,
                "source_mib_per_second": 0.34,
                "intermediate_jsonl_bytes": 0,
                "temporary_bytes_left": 0,
            },
            {
                "scenario": "direct_write_1m_flat",
                "status": "MEASURED",
                "record_count": 1_000_000,
                "wall_seconds": 66.0,
                "rss_before_bytes": 44_000_000,
                "peak_rss_bytes": 188_000_000,
                "peak_rss_delta_bytes": 144_000_000,
                "rss_after_bytes": 146_000_000,
                "intermediate_jsonl_bytes": 0,
                "temporary_bytes_left": 0,
            },
            {
                "scenario": "direct_write_character_heavy",
                "status": "MEASURED",
                "record_count": 100_000,
                "wall_seconds": 7.6,
                "intermediate_jsonl_bytes": 0,
                "temporary_bytes_left": 0,
            },
            {
                "scenario": "direct_write_memo_heavy",
                "status": "MEASURED",
                "record_count": 100_000,
                "wall_seconds": 33.0,
                "intermediate_jsonl_bytes": 0,
                "temporary_bytes_left": 0,
            },
            {
                "scenario": "direct_write_deleted_include",
                "status": "MEASURED",
                "record_count": 100_000,
                "wall_seconds": 5.4,
                "intermediate_jsonl_bytes": 0,
                "temporary_bytes_left": 0,
            },
            {
                "scenario": "direct_write_cp1250",
                "status": "MEASURED",
                "record_count": 50_000,
                "wall_seconds": 2.5,
                "intermediate_jsonl_bytes": 0,
                "temporary_bytes_left": 0,
            },
            {
                "scenario": "direct_write_cp852",
                "status": "MEASURED",
                "record_count": 50_000,
                "wall_seconds": 2.6,
                "intermediate_jsonl_bytes": 0,
                "temporary_bytes_left": 0,
            },
            {
                "scenario": "direct_write_mazovia",
                "status": "MEASURED",
                "record_count": 50_000,
                "wall_seconds": 2.8,
                "intermediate_jsonl_bytes": 0,
                "temporary_bytes_left": 0,
            },
            {
                "scenario": "direct_write_varchar_nullflags",
                "status": "MEASURED",
                "record_count": 100_000,
                "wall_seconds": 7.4,
                "private_spool_bytes_written": 10_150_000,
                "temporary_publish_bytes_written": 6_600_000,
                "intermediate_jsonl_bytes": 0,
                "temporary_bytes_left": 0,
            },
            {
                "scenario": "overwrite_transaction_staging_cost",
                "status": "MEASURED",
                "record_count": 20_000,
                "wall_seconds": 6.5,
                "backup_logical_bytes_moved": 7_100_000,
                "intermediate_jsonl_bytes": 0,
                "temporary_bytes_left": 0,
            },
            {
                "scenario": "cancellation_cleanup_smoke",
                "scenario_kind": "functional_cleanup",
                "status": "MEASURED",
                "records_per_second": None,
                "intermediate_jsonl_bytes": 0,
                "temporary_bytes_left": 0,
                "validation": {
                    "cleanup_verified": True,
                    "cancelled": True,
                    "write_cancelled_typed": True,
                    "error_code": "WRITE_CANCELLED",
                },
            },
        ],
    }


def _valid_set(replica_count: int = 5, *, run_id: str = "30000000000") -> list[dict]:
    return [
        {
            **_base_sample(f"replica-{index}", f"run-{index:03d}"),
            "workflow_run_id": run_id,
        }
        for index in range(1, replica_count + 1)
    ]


def _rejects(samples: list[dict]) -> list[str]:
    """True when the collector rejects the set (fail-closed)."""
    return calibration.validate_sample_reports(samples)


def test_valid_five_sample_set_with_shared_workflow_run_id_is_accepted() -> None:
    problems = calibration.validate_sample_reports(_valid_set())
    assert problems == []
    payload = calibration.build_calibration(
        _valid_set(),
        reference_commit="a" * 40,
        runtime_recipe="windows-latest + Python 3.12 + pip install -e \".[dev]\"",
        workflow_run_id="30000000000",
    )
    assert payload["accepted"] is True
    assert payload["calibration_contract"] == calibration.CALIBRATION_CONTRACT
    assert payload["calibration_count"] == 5
    json.dumps(payload)
    # one shared workflow_run_id + five unique replica identities: ACCEPTED
    assert payload["workflow_run_ids"] == ["30000000000"]
    assert len(payload["replica_ids"]) == 5
    assert payload["thresholds"] is None
    assert payload["descriptive_statistics"]["descriptive_only"] is True


def test_duplicate_sample_identity_under_same_workflow_is_rejected() -> None:
    samples = _valid_set()
    samples[1]["replica_id"] = samples[0]["replica_id"]  # same identity, same workflow
    assert any("duplicate sample identity" in item for item in _rejects(samples))


def test_duplicate_benchmark_run_id_is_rejected() -> None:
    samples = _valid_set()
    samples[1]["run_id"] = samples[0]["run_id"]
    assert any("duplicate benchmark run_id" in item for item in _rejects(samples))


def test_fewer_than_five_samples_is_rejected() -> None:
    assert any("fewer than 5" in item for item in _rejects(_valid_set(4)))


def test_mixed_source_shas_are_rejected() -> None:
    samples = _valid_set()
    samples[2]["measured_code_sha"] = "f" * 40
    assert any("mixed measured source SHAs" in item for item in _rejects(samples))


def test_mixed_contract_versions_are_rejected() -> None:
    samples = _valid_set()
    samples[3]["benchmark_contract_version"] = 2
    assert any("mixed benchmark contract versions" in item for item in _rejects(samples))


def test_wrong_benchmark_contract_is_rejected() -> None:
    samples = _valid_set()
    samples[0]["benchmark_contract"] = "phase-3-performance-v1"
    assert any("benchmark contract must be" in item for item in _rejects(samples))


def test_smoke_mode_is_rejected() -> None:
    samples = _valid_set()
    samples[0]["mode"] = "smoke"
    assert any("mode=full" in item for item in _rejects(samples))


def test_missing_scenario_is_rejected() -> None:
    samples = _valid_set()
    samples[1]["scenarios"] = [
        row for row in samples[1]["scenarios"]
        if row["scenario"] != "direct_write_mazovia"
    ]
    assert any("missing scenarios" in item for item in _rejects(samples))


def test_duplicate_scenario_is_rejected() -> None:
    samples = _valid_set()
    samples[2]["scenarios"].append(dict(samples[2]["scenarios"][0]))
    assert any("duplicate scenario" in item for item in _rejects(samples))


def test_failed_scenario_is_rejected() -> None:
    samples = _valid_set()
    samples[4]["scenarios"][0]["status"] = "FAILED"
    assert any("status must be MEASURED" in item for item in _rejects(samples))


def test_nonzero_jsonl_is_rejected() -> None:
    samples = _valid_set()
    samples[0]["scenarios"][0]["intermediate_jsonl_bytes"] = 99
    assert any("intermediate_jsonl_bytes must be 0" in item for item in _rejects(samples))


def test_temporary_residue_is_rejected() -> None:
    samples = _valid_set()
    samples[0]["scenarios"][0]["temporary_bytes_left"] = 7
    assert any("temporary residue must be 0" in item for item in _rejects(samples))


def test_w10_without_disk_spool_evidence_is_rejected() -> None:
    samples = _valid_set()
    for sample in samples:
        for row in sample["scenarios"]:
            if row["scenario"] == "direct_write_varchar_nullflags":
                row["private_spool_bytes_written"] = 0
    assert any("disk-spool evidence" in item for item in _rejects(samples))


def test_malformed_w12_semantics_are_rejected() -> None:
    samples = _valid_set()
    for sample in samples:
        for row in sample["scenarios"]:
            if row["scenario"] == "cancellation_cleanup_smoke":
                row["scenario_kind"] = "throughput"  # represented as throughput
    assert any("functional_cleanup" in item for item in _rejects(samples))


def test_validation_problems_must_be_empty() -> None:
    samples = _valid_set()
    samples[0]["validation_problems"] = ["x: missing keys"]
    assert any("validation_problems not empty" in item for item in _rejects(samples))


def test_privacy_sentinel_content_is_rejected() -> None:
    """F3A privacy: memo/record-payload/secret values must never enter
    calibration evidence.  The accepted F2 artifact's synthetic first/last
    fixture markers are NOT sentinels (deterministic benchmark values)."""
    samples = _valid_set()
    for sample in samples:
        for row in sample["scenarios"]:
            if row["scenario"] == "direct_write_memo_heavy":
                row["validation"] = {
                    "NOTE": "secret memo text",  # memo content sentinel
                    "PICTURE": b"binary",  # memo binary sentinel
                }
    assert any("privacy sentinel" in item for item in _rejects(samples))
    # and credential-like sentinels
    samples = _valid_set()
    for sample in samples:
        for row in sample["scenarios"]:
            if row["scenario"] == "direct_write_memo_heavy":
                row["validation"] = {"token": "hunter2"}
    assert any("privacy sentinel" in item for item in _rejects(samples))
    # the accepted F2 synthetic fixture facts are NOT sentinels
    assert _rejects(_valid_set()) == []


def test_descriptive_statistics_contain_no_thresholds() -> None:
    payload = calibration.build_calibration(
        _valid_set(),
        reference_commit="a" * 40,
        runtime_recipe="test recipe",
    )
    serialized = json.dumps(payload)
    for forbidden in ("fail_threshold", "hard_gate", "regression_envelope", "hard threshold"):
        assert forbidden not in serialized
    # the only threshold-bearing key must be the explicit null policy marker
    assert payload["thresholds"] is None
    statistics_payload = payload["descriptive_statistics"]
    assert statistics_payload["descriptive_only"] is True
    wall_stats = statistics_payload["statistics"][
        "direct_write_190k_flat.wall_seconds"
    ]
    assert wall_stats["min"] == 13.0
    assert wall_stats["median"] == 13.0
    for fact in payload["ratio_facts"]:
        assert fact["classification"] == "DESCRIPTIVE_ONLY"


def test_memory_facts_retained_per_sample() -> None:
    payload = calibration.build_calibration(
        _valid_set(), reference_commit="a" * 40, runtime_recipe="test"
    )
    for entry in payload["memory_facts"]:
        assert entry["assessment"] == "MEASURED_FACTS_ONLY"
        assert entry["w1_peak_rss_delta_bytes"] == 38_000_000
        assert entry["w3_peak_rss_delta_bytes"] == 144_000_000
        assert entry["record_count_ratio"] == round(1_000_000 / 190_000, 4)
        assert entry["peak_rss_delta_ratio"] == round(144_000_000 / 38_000_000, 4)


def test_markdown_summary_derives_from_the_payload() -> None:
    payload = calibration.build_calibration(
        _valid_set(), reference_commit="a" * 40, runtime_recipe="test"
    )
    markdown = calibration.markdown_summary(payload)
    assert "dbfbridge-direct-write-calibration-v1" in markdown
    assert "DESCRIPTIVE_ONLY" in markdown
    assert "No Direct Write regression threshold" in markdown
    json.dumps(payload)


def test_ratio_facts_are_descriptive_only() -> None:
    payload = calibration.build_calibration(
        _valid_set(), reference_commit="a" * 40, runtime_recipe="test"
    )
    labels = [fact["label"] for fact in payload["ratio_facts"]]
    assert labels == [
        "W3/W1 wall-seconds-per-record",
        "W2/W1 wall-seconds-per-record",
        "W5/W1 wall-seconds-per-record",
        "W10/W1 wall-seconds-per-record",
        "W3/W1 peak-RSS-delta ratio",
    ]
    # W3/W1 wall-per-record: 66/1e6 divided by 13/190k
    w3w1 = next(
        fact for fact in payload["ratio_facts"]
        if fact["label"] == "W3/W1 wall-seconds-per-record"
    )
    expected = (66.0 / 1_000_000) / (13.0 / 190_000)
    assert all(
        value is None or abs(value - expected) < 1e-9 for value in w3w1["values"]
    )

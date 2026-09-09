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


def _valid_provenance(replica_id: str, workflow_run_id: str) -> dict:
    """A minimal VALID run-provenance document (strict whitelist contract)."""
    return {
        "provenance_contract": calibration.PROVENANCE_CONTRACT,
        "provenance_contract_version": calibration.PROVENANCE_CONTRACT_VERSION,
        "workflow_run_id": workflow_run_id,
        "replica_id": replica_id,
        "github_sha": "a" * 40,
        "source_context": "main_push",
        "runner_os": "Windows",
        "runner_arch": "X64",
        "python_version": "3.12.10",
        "python_implementation": "CPython",
        "sys_platform": "win32",
        "machine": "AMD64",
        "install_recipe": 'pip install -e ".[dev]"',
        "dependencies": {"dbf": "0.99.13", "dbfread": "2.0.7", "psutil": "7.0.0"},
    }


def _provenance_for(samples: list[dict]) -> list[dict]:
    return [
        _valid_provenance(sample["replica_id"], sample["workflow_run_id"])
        for sample in samples
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
        workflow_run_id="30000000000",
        provenance_entries=_provenance_for(_valid_set()),
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
    # provenance is authoritative and populated (never empty for GitHub runs)
    assert "provenance" not in payload  # normalized per-sample (F3A-BLK-04)
    sample0 = payload["samples"][0]
    assert sample0["runner_os"] == "Windows"
    assert sample0["runner_arch"] == "X64"
    assert sample0["machine"] == "AMD64"
    assert sample0["sys_platform"] == "win32"
    assert sample0["source_context"] == "main_push"
    assert sample0["install_recipe"] == 'pip install -e ".[dev]"'
    assert sample0["dependency_versions"] == {
        "dbf": "0.99.13", "dbfread": "2.0.7", "psutil": "7.0.0",
    }


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
        provenance_entries=_provenance_for(_valid_set()),
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


# ---------------------------------------------------------------------------
# F3A provenance acceptance repairs (F3A-BLK-01..05)
# ---------------------------------------------------------------------------


def test_provenance_missing_is_rejected() -> None:
    payload = calibration.build_calibration(
        _valid_set(),
        reference_commit="a" * 40,
        provenance_entries=[],
    )
    assert payload["accepted"] is False
    assert any("provenance count" in item for item in payload["problems"])


def test_duplicate_provenance_replica_is_rejected() -> None:
    samples = _valid_set()
    provenance = _provenance_for(samples)
    provenance[1] = _valid_provenance(
        samples[0]["replica_id"], samples[0]["workflow_run_id"]
    )
    payload = calibration.build_calibration(
        samples, reference_commit="a" * 40, provenance_entries=provenance
    )
    assert payload["accepted"] is False
    assert any("duplicate provenance replica" in item for item in payload["problems"])


def test_provenance_missing_entry_for_replica_is_rejected() -> None:
    samples = _valid_set()
    provenance = _provenance_for(samples)[:-1]
    payload = calibration.build_calibration(
        samples, reference_commit="a" * 40, provenance_entries=provenance
    )
    assert payload["accepted"] is False
    assert any("does not match sample count" in item for item in payload["problems"])


def test_provenance_unknown_field_is_rejected() -> None:
    samples = _valid_set()
    provenance = _provenance_for(samples)
    provenance[0]["environment_dump"] = {"PATH": "C:\\windows"}
    payload = calibration.build_calibration(
        samples, reference_commit="a" * 40, provenance_entries=provenance
    )
    assert payload["accepted"] is False
    assert any("unknown provenance fields" in item for item in payload["problems"])


def test_provenance_wrong_contract_is_rejected() -> None:
    samples = _valid_set()
    provenance = _provenance_for(samples)
    provenance[0]["provenance_contract"] = "some-other/1"
    payload = calibration.build_calibration(
        samples, reference_commit="a" * 40, provenance_entries=provenance
    )
    assert payload["accepted"] is False
    assert any("provenance contract must be" in item for item in payload["problems"])


def test_provenance_wrong_version_is_rejected() -> None:
    samples = _valid_set()
    provenance = _provenance_for(samples)
    provenance[0]["provenance_contract_version"] = 2
    payload = calibration.build_calibration(
        samples, reference_commit="a" * 40, provenance_entries=provenance
    )
    assert payload["accepted"] is False
    assert any("provenance contract version" in item for item in payload["problems"])


def test_provenance_missing_required_field_is_rejected() -> None:
    samples = _valid_set()
    provenance = _provenance_for(samples)
    del provenance[0]["machine"]
    payload = calibration.build_calibration(
        samples, reference_commit="a" * 40, provenance_entries=provenance
    )
    assert payload["accepted"] is False
    assert any("missing provenance field" in item for item in payload["problems"])


def test_provenance_replica_mismatch_is_rejected() -> None:
    """Replica mismatch is caught at the validate_provenance level: an entry
    keyed under replica-1 whose replica_id field says replica-9 fails."""
    problems = calibration.validate_provenance(
        _valid_provenance("replica-9", "30000000000"),
        replica_id="replica-1",
        workflow_run_id="30000000000",
        measured_code_sha="a" * 40,
    )
    assert any("replica_id mismatch" in item for item in problems)
    samples = _valid_set()
    provenance = _provenance_for(samples)
    provenance[0] = {**provenance[0], "replica_id": "replica-9"}
    payload = calibration.build_calibration(
        samples, reference_commit="a" * 40, provenance_entries=provenance
    )
    assert payload["accepted"] is False


def test_provenance_workflow_mismatch_is_rejected() -> None:
    samples = _valid_set()
    provenance = _provenance_for(samples)
    provenance[0] = _valid_provenance(samples[0]["replica_id"], "99999999999")
    payload = calibration.build_calibration(
        samples, reference_commit="a" * 40, provenance_entries=provenance
    )
    assert payload["accepted"] is False
    assert any("workflow_run_id mismatch" in item for item in payload["problems"])


def test_provenance_github_sha_mismatch_is_rejected() -> None:
    samples = _valid_set()
    provenance = _provenance_for(samples)
    provenance[0]["github_sha"] = "f" * 40
    payload = calibration.build_calibration(
        samples, reference_commit="a" * 40, provenance_entries=provenance
    )
    assert payload["accepted"] is False
    assert any("github_sha mismatch" in item for item in payload["problems"])


def test_reference_commit_must_equal_measured_sha() -> None:
    samples = _valid_set()
    provenance = _provenance_for(samples)
    payload = calibration.build_calibration(
        samples, reference_commit="f" * 40, provenance_entries=provenance
    )
    assert payload["accepted"] is False
    assert any(
        "does not equal the common measured_code_sha" in item
        for item in payload["problems"]
    )


def test_invalid_measured_sha_is_rejected() -> None:
    samples = _valid_set()
    for sample in samples:
        sample["measured_code_sha"] = None
    provenance = _provenance_for(samples)
    payload = calibration.build_calibration(
        samples, reference_commit="", provenance_entries=provenance
    )
    assert payload["accepted"] is False
    assert any(
        "exactly one valid SHA-shaped measured_code_sha" in item
        for item in payload["problems"]
    )


def test_install_recipe_mismatch_is_rejected() -> None:
    samples = _valid_set()
    provenance = _provenance_for(samples)
    provenance[1] = {
        **provenance[1],
        "install_recipe": "pip install dbfbridge",
    }
    payload = calibration.build_calibration(
        samples, reference_commit="a" * 40, provenance_entries=provenance
    )
    assert payload["accepted"] is False
    assert any("install recipes" in item for item in payload["problems"])


def test_dependency_version_mismatches_are_rejected() -> None:
    for dependency, other in (
        ("dbf", "1.0.0"),
        ("dbfread", "3.0.0"),
        ("psutil", "6.9.9"),
    ):
        samples = _valid_set()
        provenance = _provenance_for(samples)
        provenance[2] = {
            **provenance[2],
            "dependencies": {**provenance[2]["dependencies"], dependency: other},
        }
        payload = calibration.build_calibration(
            samples, reference_commit="a" * 40, provenance_entries=provenance
        )
        assert payload["accepted"] is False, dependency
        assert any(
            "dependency versions" in item for item in payload["problems"]
        ), dependency


def test_python_major_minor_mismatch_is_rejected() -> None:
    samples = _valid_set()
    provenance = _provenance_for(samples)
    provenance[1] = {**provenance[1], "python_version": "3.13.1"}
    payload = calibration.build_calibration(
        samples, reference_commit="a" * 40, provenance_entries=provenance
    )
    assert payload["accepted"] is False
    assert any("Python major.minor" in item for item in payload["problems"])


def test_runner_arch_mismatch_is_rejected() -> None:
    samples = _valid_set()
    provenance = _provenance_for(samples)
    provenance[1] = {**provenance[1], "runner_arch": "ARM64"}
    payload = calibration.build_calibration(
        samples, reference_commit="a" * 40, provenance_entries=provenance
    )
    assert payload["accepted"] is False
    assert any("runner architectures" in item for item in payload["problems"])


def test_provenance_secret_fields_are_rejected() -> None:
    samples = _valid_set()
    provenance = _provenance_for(samples)
    for secret_key in ("TOKEN", "secret", "password", "api_key", "authorization", "cookie"):
        provenance[0] = {
            **provenance[0],
            secret_key: "hunter2",
        }
    payload = calibration.build_calibration(
        samples, reference_commit="a" * 40, provenance_entries=provenance
    )
    assert payload["accepted"] is False
    assert any("provenance secret field" in item for item in payload["problems"])


def test_malformed_provenance_json_is_rejected() -> None:
    problems = calibration.validate_provenance(
        "not-an-object",
        replica_id="replica-1",
        workflow_run_id="30000000000",
        measured_code_sha="a" * 40,
    )
    assert problems == ["provenance[replica-1]: provenance must be an object"]


def test_pull_request_merge_ref_semantics_are_represented() -> None:
    samples = _valid_set()
    provenance = _provenance_for(samples)
    for entry in provenance:
        entry["source_context"] = "pull_request_merge_ref"
        entry["branch_head_sha"] = "c" * 40
        entry["base_sha"] = "d" * 40
    payload = calibration.build_calibration(
        samples, reference_commit="a" * 40, provenance_entries=provenance
    )
    assert payload["accepted"] is True
    sample0 = payload["samples"][0]
    assert sample0["source_context"] == "pull_request_merge_ref"
    assert sample0["branch_head_sha"] == "c" * 40
    # the checked-out merge-ref SHA (measured_code_sha) is NOT the branch head
    assert sample0["measured_code_sha"] == "a" * 40
    assert sample0["measured_code_sha"] != sample0["branch_head_sha"]


# ---------------------------------------------------------------------------
# workflow contract (static assertions, not human YAML inspection)
# ---------------------------------------------------------------------------


def _workflow_text() -> str:
    return (ROOT / ".github" / "workflows" / "direct-write-calibration.yml").read_text(
        encoding="utf-8"
    )


def test_workflow_creates_json_provenance_and_uploads_it() -> None:
    text = _workflow_text()
    assert "direct-write-provenance.json" in text
    assert "json.dumps(payload" in text  # real JSON document, not plain text
    # provenance uploaded with the raw replica artifact
    upload = text.split("Upload raw replica report", 1)[1]
    assert "direct-write-provenance.json" in upload


def test_aggregate_passes_five_provenance_args() -> None:
    text = _workflow_text()
    aggregate = " ".join(text.split("Aggregate calibration evidence", 1)[1].split())
    # the aggregate loop emits --provenance args for replicas 1..5
    assert text.count('"--provenance"') == 1
    assert (
        'replica-$replica=raw-reports/dw-calibration-replica-$replica/'
        'direct-write-provenance.json'
    ) in aggregate


def test_workflow_uses_five_windows_replicas_and_python_312() -> None:
    text = _workflow_text()
    assert "matrix:\n        replica: [1, 2, 3, 4, 5]" in text
    assert 'runs-on: windows-latest' in text
    assert 'python-version: "3.12"' in text


def test_workflow_has_no_repository_write_and_no_commit() -> None:
    text = _workflow_text()
    assert "permissions:\n  contents: read" in text
    assert "git push" not in text
    assert "git commit" not in text
    assert "actions/upload-artifact" in text  # evidence is uploaded, not committed
    # the workflow comment explicitly disclaims threshold establishment
    assert "NEVER establishes a performance threshold" in text


def test_memory_facts_retained_per_sample() -> None:
    payload = calibration.build_calibration(
        _valid_set(),
        reference_commit="a" * 40,
        provenance_entries=_provenance_for(_valid_set()),
    )
    for entry in payload["memory_facts"]:
        assert entry["assessment"] == "MEASURED_FACTS_ONLY"
        assert entry["w1_peak_rss_delta_bytes"] == 38_000_000
        assert entry["w3_peak_rss_delta_bytes"] == 144_000_000
        assert entry["record_count_ratio"] == round(1_000_000 / 190_000, 4)
        assert entry["peak_rss_delta_ratio"] == round(144_000_000 / 38_000_000, 4)


def test_markdown_summary_derives_from_the_payload() -> None:
    payload = calibration.build_calibration(
        _valid_set(),
        reference_commit="a" * 40,
        provenance_entries=_provenance_for(_valid_set()),
    )
    markdown = calibration.markdown_summary(payload)
    assert "dbfbridge-direct-write-calibration-v1" in markdown
    assert "DESCRIPTIVE_ONLY" in markdown
    assert "No Direct Write regression threshold" in markdown
    json.dumps(payload)


def test_ratio_facts_are_descriptive_only() -> None:
    payload = calibration.build_calibration(
        _valid_set(),
        reference_commit="a" * 40,
        provenance_entries=_provenance_for(_valid_set()),
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

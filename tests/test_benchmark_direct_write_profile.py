"""Phase F1 regressions for the v1.1 Direct Write benchmark profile.

Proves the SEPARATE Direct Write benchmark contract
(``dbfbridge-direct-write-v1``), the W1/W3/W10/W12 scenario coverage, the
required metric schema, the explicit ``intermediate_jsonl_bytes == 0`` gate,
the lazy (non-materialized) W3/W1 inputs, W12's functional semantics and the
immutability of the historical Phase 0/1/3 baselines.  Structural gates only
— no performance thresholds (DBFB-PERF-006: the first profile is MEASURED
EVIDENCE, not a regression baseline).
"""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks import direct_write_profile as profile  # noqa: E402
from benchmarks.contract import CONTRACT_PHASE_1, CONTRACT_PHASE_3  # noqa: E402

# ---------------------------------------------------------------------------
# contract identity (DBFB-PERF-002)
# ---------------------------------------------------------------------------


def test_direct_write_contract_is_separate_and_versioned() -> None:
    assert profile.CONTRACT_DIRECT_WRITE == "dbfbridge-direct-write-v1"
    assert profile.CONTRACT_DIRECT_WRITE_VERSION == 1
    # never confused with the historical migration/read contracts
    assert profile.CONTRACT_DIRECT_WRITE not in {CONTRACT_PHASE_1, CONTRACT_PHASE_3}


def test_all_core_scenario_ids_exist() -> None:
    assert set(profile.SCENARIO_IDS) == {
        profile.SCENARIO_W1,
        profile.SCENARIO_W3,
        profile.SCENARIO_W10,
        profile.SCENARIO_W12,
    }
    assert profile.SCENARIO_KINDS[profile.SCENARIO_W12] == "functional_cleanup"


# ---------------------------------------------------------------------------
# artifact structure / structural gates
# ---------------------------------------------------------------------------


def _tiny_counts() -> dict[str, int]:
    return {
        profile.SCENARIO_W1: 100,
        profile.SCENARIO_W3: 150,
        profile.SCENARIO_W10: 60,
        profile.SCENARIO_W12: 50,
    }


def test_smoke_profile_executes_and_satisfies_the_structural_gates(
    tmp_path: Path,
) -> None:
    payload = profile.build_artifact("smoke", _tiny_counts(), tmp_path / "scenarios")
    problems = profile.validate_artifact(payload)
    assert problems == [], problems
    assert payload["benchmark_contract"] == profile.CONTRACT_DIRECT_WRITE
    json.dumps(payload)  # JSON-safe
    rows = {row["scenario"]: row for row in payload["scenarios"]}
    assert set(rows) == set(profile.SCENARIO_IDS)
    for scenario in (profile.SCENARIO_W1, profile.SCENARIO_W3, profile.SCENARIO_W10):
        row = rows[scenario]
        assert row["status"] == "MEASURED"
        assert row["intermediate_jsonl_bytes"] == 0
        assert row["temporary_bytes_left"] == 0
        assert row["validation"]["record_count"] == row["record_count"]
        assert "peak_rss_bytes" in row  # measured or explicit NOT_AVAILABLE (None)
    # required metric keys exist on every row
    for row in rows.values():
        for key in profile.REQUIRED_ROW_KEYS:
            assert key in row, key


def test_validator_rejects_nonzero_intermediate_jsonl() -> None:
    payload = {
        "benchmark_contract": profile.CONTRACT_DIRECT_WRITE,
        "benchmark_contract_version": 1,
        "scenarios": [
            {
                "scenario": profile.SCENARIO_W1,
                "scenario_kind": "throughput",
                "status": "MEASURED",
                "record_count": 10,
                "intermediate_jsonl_bytes": 12,
                "temporary_bytes_left": 0,
                "validation": {"record_count": 10},
            }
        ],
    }
    problems = profile.validate_artifact(payload)
    assert any("intermediate_jsonl_bytes must be 0" in item for item in problems)


def test_validator_rejects_residue_and_missing_scenarios() -> None:
    payload = {
        "benchmark_contract": profile.CONTRACT_DIRECT_WRITE,
        "benchmark_contract_version": 1,
        "scenarios": [
            {
                "scenario": profile.SCENARIO_W1,
                "scenario_kind": "throughput",
                "status": "MEASURED",
                "record_count": 10,
                "intermediate_jsonl_bytes": 0,
                "temporary_bytes_left": 5,
                "validation": {"record_count": 10},
            }
        ],
    }
    problems = profile.validate_artifact(payload)
    assert any("temporary residue" in item for item in problems)
    assert any("missing scenarios" in item for item in problems)


def test_w12_is_functional_not_throughput(tmp_path: Path) -> None:
    payload = profile.build_artifact("smoke", _tiny_counts(), tmp_path)
    row = next(
        item
        for item in payload["scenarios"]
        if item["scenario"] == profile.SCENARIO_W12
    )
    assert row["scenario_kind"] == "functional_cleanup"
    assert row["records_per_second"] is None
    validation = row["validation"]
    assert validation["cancelled"] is True
    assert validation["write_cancelled_typed"] is True
    assert validation["error_code"] == "WRITE_CANCELLED"
    assert validation["destination_absent"] is True
    assert validation["no_partial_residue"] is True
    assert validation["no_backup_residue"] is True


def test_artifact_markdown_summary_matches_the_payload(tmp_path: Path) -> None:
    payload = profile.build_artifact("smoke", _tiny_counts(), tmp_path / "scenarios")
    markdown = profile.markdown_summary(payload)
    for scenario in profile.SCENARIO_IDS:
        assert scenario in markdown
    assert "functional_cleanup" in markdown
    assert "MEASURED EVIDENCE" in markdown


# ---------------------------------------------------------------------------
# W3/W1 lazy input design (DBFB-PERF-004)
# ---------------------------------------------------------------------------


def test_w1_w3_inputs_are_lazy_generators_never_materialized() -> None:
    import ast

    assert inspect.isgeneratorfunction(profile.flat_records)
    assert inspect.isgeneratorfunction(profile.varchar_records)
    source = (ROOT / "benchmarks" / "direct_write_profile.py").read_text(
        encoding="utf-8"
    )
    # AST-based: no list()/tuple() CALL may wrap the record streams (the
    # docstring's prohibition of that pattern is not source code).
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"list", "tuple"} or not any(
                isinstance(arg, ast.Name) and "record" in arg.id.lower()
                for arg in node.args
            ), f"full-input materialization at line {node.lineno}"
    stream = profile.flat_records(5)
    consumed = list(stream)  # a small consumption is fine; the STREAM is lazy
    assert len(consumed) == 5


def test_w10_uses_the_varchar_nullflags_replay_path(tmp_path: Path) -> None:
    counts = dict(_tiny_counts())
    payload = profile.build_artifact("smoke", counts, tmp_path / "scenarios")
    row = next(
        item
        for item in payload["scenarios"]
        if item["scenario"] == profile.SCENARIO_W10
    )
    assert row["scenario_kind"] == "throughput_replay_path"
    validation = row["validation"]
    assert validation["varchar_null_semantics_verified"] is True
    assert validation["null_varchar_count"] > 0
    assert validation["null_note_count"] > 0
    # the private spool bytes are honestly NOT_AVAILABLE, never derived
    assert row["private_spool_bytes"] is None
    assert "NOT_AVAILABLE" in row["private_spool_bytes_reason"]


# ---------------------------------------------------------------------------
# historical baselines are immutable (DBFB-PERF-005)
# ---------------------------------------------------------------------------


def test_historical_phase_baselines_are_byte_for_byte_unchanged() -> None:
    def _git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout

    dirty = _git("status", "--porcelain", "benchmarks/baselines")
    assert dirty == "", dirty
    changed = _git(
        "diff", "--name-only", "origin/main...HEAD", "--", "benchmarks/baselines"
    )
    assert changed == "", changed


def test_existing_phase3_contract_machinery_is_untouched() -> None:
    import benchmarks.contract as contract

    assert contract.CONTRACT_PHASE_3 == "phase-3-performance-v1"
    assert contract.CONTRACT_PHASE_1 == "phase-1-direct-read-v1"


# ---------------------------------------------------------------------------
# public API only / no runtime coupling
# ---------------------------------------------------------------------------


def test_profile_uses_the_public_api_only() -> None:
    source = (ROOT / "benchmarks" / "direct_write_profile.py").read_text(
        encoding="utf-8"
    )
    assert "from dbfbridge import write_table" in source
    for forbidden in (
        "dbf_bridge.write.backend",
        "dbf_bridge.importer",
        "dbf_bridge.write.api",
        "from dbf_bridge.write import",
        "import dbf_bridge",
    ):
        assert forbidden not in source, forbidden


def test_library_does_not_import_benchmark_infrastructure() -> None:
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "benchmarks" not in text.replace("benchmark-", "").replace(
            "benchmarks/baselines", ""
        ), path


def test_smoke_artifact_writing_is_deterministic_in_structure(
    tmp_path: Path, monkeypatch
) -> None:
    out = tmp_path / "evidence"
    monkeypatch.setattr(sys, "argv", ["prog"])
    exit_code = profile.main(["--mode", "smoke", "--out", str(out)])
    assert exit_code == 0
    payload = json.loads((out / "direct-write-v1-smoke.json").read_text(encoding="utf-8"))
    assert payload["benchmark_contract"] == profile.CONTRACT_DIRECT_WRITE
    assert (out / "direct-write-v1-smoke.md").is_file()
    json.dumps(payload)
    # a second run produces the same STRUCTURE (values are measurements)
    payload2 = profile.build_artifact("smoke", dict(profile.SMOKE_COUNTS), tmp_path / "s2")
    assert [row["scenario"] for row in payload2["scenarios"]] == [
        row["scenario"] for row in payload["scenarios"]
    ]
    problems = profile.validate_artifact(payload2)
    assert problems == []


def test_full_counts_match_the_architecture() -> None:
    assert profile.FULL_COUNTS[profile.SCENARIO_W1] == 190_000
    assert profile.FULL_COUNTS[profile.SCENARIO_W3] == 1_000_000
    assert profile.FULL_COUNTS[profile.SCENARIO_W10] >= 1
    assert profile.SMOKE_COUNTS[profile.SCENARIO_W3] < 10_000


def test_peak_rss_is_sampled_or_explicitly_not_available(tmp_path: Path) -> None:
    counts = dict(_tiny_counts())
    counts.update({profile.SCENARIO_W1: 30, profile.SCENARIO_W3: 40})
    payload = profile.build_artifact("smoke", counts, tmp_path / "scenarios")
    for row in payload["scenarios"]:
        peak = row.get("peak_rss_bytes")
        assert peak is None or isinstance(peak, int) and peak > 0
        if peak is None:
            assert row.get("rss_samples") is None

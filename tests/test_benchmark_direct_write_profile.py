"""Phase F1 regressions for the v1.1 Direct Write benchmark profile.

Proves the SEPARATE Direct Write benchmark contract
(``dbfbridge-direct-write-v1``), the W1/W3/W10/W12 scenario coverage, the
required metric schema (incl. RSS provenance and the DISTINCT temporary
byte model: publish + spool = total), the explicit
``intermediate_jsonl_bytes == 0`` gate, the lazy (non-materialized) W3/W1
inputs, bounded O(page) validation, workspace cleanup, W12's functional
semantics and the immutability of the historical Phase 0/1/3 baselines.
Structural gates only — no performance thresholds (DBFB-PERF-006: the first
profile is MEASURED EVIDENCE, not a regression baseline).
"""

from __future__ import annotations

import ast
import inspect
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks import direct_write_profile as profile  # noqa: E402
from benchmarks.contract import CONTRACT_PHASE_1, CONTRACT_PHASE_3  # noqa: E402

# ---------------------------------------------------------------------------
# contract identity / provenance (DBFB-PERF-002)
# ---------------------------------------------------------------------------


def test_direct_write_contract_is_separate_and_versioned() -> None:
    assert profile.CONTRACT_DIRECT_WRITE == "dbfbridge-direct-write-v1"
    assert profile.CONTRACT_DIRECT_WRITE_VERSION == 1
    assert profile.CONTRACT_DIRECT_WRITE not in {CONTRACT_PHASE_1, CONTRACT_PHASE_3}


def test_all_core_scenario_ids_exist() -> None:
    assert set(profile.SCENARIO_IDS) == {
        profile.SCENARIO_W1,
        profile.SCENARIO_W3,
        profile.SCENARIO_W10,
        profile.SCENARIO_W12,
    }
    assert profile.SCENARIO_KINDS[profile.SCENARIO_W12] == "functional_cleanup"


def test_artifact_carries_truthful_provenance(tmp_path: Path) -> None:
    payload = profile.build_artifact("smoke", _tiny_counts(), tmp_path / "scenarios")
    assert payload["measured_code_sha"]
    for row in payload["scenarios"]:
        assert row["measured_code_sha"] == payload["measured_code_sha"]


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
        assert row["validation"]["bounded_validation"] is True
    for row in rows.values():
        for key in profile.REQUIRED_ROW_KEYS:
            assert key in row, key


def test_validator_rejects_nonzero_intermediate_jsonl() -> None:
    payload = {
        "benchmark_contract": profile.CONTRACT_DIRECT_WRITE,
        "benchmark_contract_version": 1,
        "measured_code_sha": "x",
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


def test_validator_rejects_residue_missing_scenarios_and_provenance() -> None:
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
    assert any("measured_code_sha" in item for item in problems)


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
    assert validation["no_spool_residue"] is True
    assert validation["spool_applicable"] is False  # flat path: truthfully stated


def test_artifact_markdown_summary_matches_the_payload(tmp_path: Path) -> None:
    payload = profile.build_artifact("smoke", _tiny_counts(), tmp_path / "scenarios")
    markdown = profile.markdown_summary(payload)
    for scenario in profile.SCENARIO_IDS:
        assert scenario in markdown
    assert "functional_cleanup" in markdown
    assert "MEASURED EVIDENCE" in markdown


# ---------------------------------------------------------------------------
# temporary byte model (DBFB-STREAM-006 / DBFB-COST-002)
# ---------------------------------------------------------------------------


def test_w10_full_profile_measures_disk_spool_bytes(tmp_path: Path) -> None:
    """W10 full count (100k > spool memory threshold) MUST spill: the spool
    bytes are OBSERVED at unlink time inside the scenario staging area
    (> 0), included in the architecture total, with zero residue."""
    counts = dict(_tiny_counts())
    counts[profile.SCENARIO_W10] = profile.FULL_COUNTS[profile.SCENARIO_W10]
    payload = profile.build_artifact("smoke", counts, tmp_path / "scenarios")
    row = next(
        item for item in payload["scenarios"]
        if item["scenario"] == profile.SCENARIO_W10
    )
    assert row["status"] == "MEASURED"
    assert row["private_spool_bytes_written"] > 0, row
    assert (
        row["temporary_bytes_written"]
        == row["temporary_publish_bytes_written"] + row["private_spool_bytes_written"]
    )
    assert row["temporary_bytes_written"] >= row["private_spool_bytes_written"]
    assert row["temporary_bytes_left"] == 0
    assert row["temporary_residue_paths"] == []
    assert row["intermediate_jsonl_bytes"] == 0
    assert row["validation"]["varchar_null_semantics_verified"] is True


def test_flat_paths_measure_zero_spool_bytes(tmp_path: Path) -> None:
    """W1/W3 never create a spool: zero spool bytes are MEASURED (observed),
    not assumed — and the total equals the publish bytes."""
    payload = profile.build_artifact("smoke", _tiny_counts(), tmp_path / "scenarios")
    for scenario in (profile.SCENARIO_W1, profile.SCENARIO_W3):
        row = next(item for item in payload["scenarios"] if item["scenario"] == scenario)
        assert row["private_spool_bytes_written"] == 0
        assert (
            row["temporary_bytes_written"]
            == row["temporary_publish_bytes_written"] + row["private_spool_bytes_written"]
        )
        assert row["temporary_publish_bytes_written"] > 0
        assert row["temporary_bytes_left"] == 0


def test_w12_reports_spool_as_not_applicable(tmp_path: Path) -> None:
    payload = profile.build_artifact("smoke", _tiny_counts(), tmp_path)
    row = next(
        item for item in payload["scenarios"]
        if item["scenario"] == profile.SCENARIO_W12
    )
    assert row["validation"]["spool_applicable"] is False
    assert row["private_spool_bytes_written"] == 0
    assert row["intermediate_jsonl_bytes"] == 0


# ---------------------------------------------------------------------------
# RSS provenance (DBFB-PERF-001)
# ---------------------------------------------------------------------------


def test_rss_provenance_fields_are_recorded(tmp_path: Path) -> None:
    counts = dict(_tiny_counts())
    counts.update({profile.SCENARIO_W1: 40, profile.SCENARIO_W3: 60})
    payload = profile.build_artifact("smoke", counts, tmp_path / "scenarios")
    for row in payload["scenarios"]:
        assert "rss_before_bytes" in row
        assert "rss_after_bytes" in row
        assert "peak_rss_bytes" in row
        assert "peak_rss_delta_bytes" in row
        peak, before = row.get("peak_rss_bytes"), row.get("rss_before_bytes")
        delta = row.get("peak_rss_delta_bytes")
        if isinstance(peak, int) and isinstance(before, int):
            assert delta == max(0, peak - before)
        else:
            assert delta is None


def test_memory_comparison_reports_measured_facts(tmp_path: Path) -> None:
    counts = dict(_tiny_counts())
    payload = profile.build_artifact("smoke", counts, tmp_path)
    comparison = payload["w1_w3_comparison"]
    for key in (
        "w1_record_count", "w1_peak_rss_bytes", "w1_peak_rss_delta_bytes",
        "w3_record_count", "w3_peak_rss_bytes", "w3_peak_rss_delta_bytes",
        "record_count_ratio", "peak_rss_delta_ratio", "conclusion",
    ):
        assert key in comparison, key
    assert comparison["conclusion"] in {
        "NO_INPUT_MATERIALIZATION_EVIDENCE",
        "INCONCLUSIVE",
        "NOT_AVAILABLE",
        "POTENTIAL_DBFB_PERF_004_BLOCKER",
    }


# ---------------------------------------------------------------------------
# lazy inputs + bounded validation (DBFB-PERF-004)
# ---------------------------------------------------------------------------


def test_w1_w3_inputs_are_lazy_generators_never_materialized() -> None:
    assert inspect.isgeneratorfunction(profile.flat_records)
    assert inspect.isgeneratorfunction(profile.varchar_records)
    source = (ROOT / "benchmarks" / "direct_write_profile.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"list", "tuple"} or not any(
                isinstance(arg, ast.Name) and "record" in arg.id.lower()
                for arg in node.args
            ), f"full-input materialization at line {node.lineno}"
    assert len(list(profile.flat_records(5))) == 5


def test_validation_is_bounded_no_full_record_accumulation() -> None:
    """AST regression: the validation pass must never accumulate all records
    (no ``collected``-style list, no ``.extend(page.records)``); it may only
    retain running counters and first/last facts."""
    source = (ROOT / "benchmarks" / "direct_write_profile.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            object_name = (
                node.func.value.id if isinstance(node.func.value, ast.Name) else ""
            )
            if node.func.attr == "extend":
                assert not any(
                    token in object_name.lower()
                    for token in ("record", "page_record", "collected")
                ), f"record-list accumulation reintroduced at line {node.lineno}"
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.List):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assert "collected" not in target.id.lower(), target.id
    assert "bounded_validation" in source


# ---------------------------------------------------------------------------
# workspace cleanup (F1-BLK-04)
# ---------------------------------------------------------------------------


def test_benchmark_workspace_is_cleaned_after_the_cli(tmp_path: Path, monkeypatch) -> None:
    temp_root = Path(tempfile.gettempdir())
    before = set(temp_root.glob("dbfbridge-dw-profile-*"))
    exit_code = profile.main(["--mode", "smoke", "--out", str(tmp_path / "evidence")])
    assert exit_code == 0
    after = set(temp_root.glob("dbfbridge-dw-profile-*"))
    assert after == before, "the benchmark scenario workspace must be cleaned up"
    # the evidence survives
    assert (tmp_path / "evidence" / "direct-write-v1-smoke.json").is_file()
    assert (tmp_path / "evidence" / "direct-write-v1-smoke.md").is_file()


# ---------------------------------------------------------------------------
# historical baselines are immutable (DBFB-PERF-005)
# ---------------------------------------------------------------------------

#: Git blob SHAs of every tracked historical baseline file at the approved
#: main lineage (``git ls-tree -r HEAD -- benchmarks/baselines``).  ANY
#: modification committed to a Phase 0/1/3 baseline artifact breaks this
#: test — hermetic (needs only HEAD, CI shallow-checkout safe, immune to
#: working-tree line-ending conversion).
_FROZEN_BASELINE_BLOBS = {
    "phase-0-full.json": "654452b139cf1e7d458efbe49952831aa7337e1a",
    "phase-0-full.md": "140b209188cf4005874e0e78e9657bf354909b24",
    "phase-0-vs-phase-1.json": "716a55ec5c4cbb440ba954c6df4b1c8abd5e6d8e",
    "phase-0-vs-phase-1.md": "87f61eff092858bc4bbb2bce7aff4083fca9ac4e",
    "phase-1-direct-read-full.json": "f1f69601d5aa1295feb2a47dac7dd632e6af7926",
    "phase-1-direct-read-full.manifest.json": "b1e92dfe4ad76fe20c2896c08a9abbae63c5a696",
    "phase-1-direct-read-full.md": "a7d3162c249db5af156d32a6e9e9f42872ca828f",
    "phase-3-performance-full.json": "2e579e31b0bdcd16d0e8f5cd4667a945cca85b37",
    "phase-3-performance-full.manifest.json": "4bd29f809a8f8500123023225641ba8d956de0b2",
    "phase-3-performance-full.md": "937bad8b873991a015707e37e2ce26c83c86bf7e",
}


def test_historical_phase_baselines_are_byte_for_byte_unchanged() -> None:
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "benchmarks/baselines"],
        cwd=ROOT, capture_output=True, text=True,
    ).stdout
    assert dirty == "", dirty
    listing = subprocess.run(
        ["git", "ls-tree", "-r", "HEAD", "--", "benchmarks/baselines"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    committed: dict[str, str] = {}
    for line in listing.splitlines():
        meta, name = line.split("\t", 1)
        _mode, _kind, blob = meta.split()
        if name.endswith(".gitkeep"):
            continue
        committed[name.removeprefix("benchmarks/baselines/")] = blob
    assert committed == _FROZEN_BASELINE_BLOBS, (
        "historical Phase 0/1/3 baselines were modified"
    )


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
        "RecordSpool",
        "import dbf_bridge",
    ):
        assert forbidden not in source, forbidden


def test_library_does_not_import_benchmark_infrastructure() -> None:
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "benchmarks" not in text.replace("benchmark-", "").replace(
            "benchmarks/baselines", ""
        ), path


def test_full_counts_match_the_architecture() -> None:
    assert profile.FULL_COUNTS[profile.SCENARIO_W1] == 190_000
    assert profile.FULL_COUNTS[profile.SCENARIO_W3] == 1_000_000
    assert profile.FULL_COUNTS[profile.SCENARIO_W10] == 100_000
    assert profile.SMOKE_COUNTS[profile.SCENARIO_W3] < 10_000


def test_smoke_artifact_writing_is_deterministic_in_structure(
    tmp_path: Path,
) -> None:
    out = tmp_path / "evidence"
    exit_code = profile.main(["--mode", "smoke", "--out", str(out)])
    assert exit_code == 0
    payload = json.loads(
        (out / "direct-write-v1-smoke.json").read_text(encoding="utf-8")
    )
    assert payload["benchmark_contract"] == profile.CONTRACT_DIRECT_WRITE
    assert (out / "direct-write-v1-smoke.md").is_file()
    json.dumps(payload)
    payload2 = profile.build_artifact(
        "smoke", dict(profile.SMOKE_COUNTS), tmp_path / "s2"
    )
    assert [row["scenario"] for row in payload2["scenarios"]] == [
        row["scenario"] for row in payload["scenarios"]
    ]
    assert profile.validate_artifact(payload2) == []

"""Offline calibration collector for the v1.1 Direct Write regression policy
pipeline (DBFB-PERF-006, stage F3A).

This module is DETERMINISTIC and OFFLINE.  It never benchmarks, never touches
the network and never imports private writer/runtime internals.  It consumes
SAVED ``dbfbridge-direct-write-v1`` full-profile reports (JSON, as produced by
``python -m benchmarks.direct_write_profile --mode full``) and emits compact,
reviewable calibration evidence for the later F3B policy derivation.

Contract identity
-----------------
``dbfbridge-direct-write-calibration-v1`` — a separate saved-evidence
contract, NOT a new benchmark contract and NOT a second benchmark
implementation.  The benchmark contract stays
``dbfbridge-direct-write-v1`` (version 1).

Sample identity model
---------------------
A calibration SAMPLE is ONE fresh GitHub-hosted runner job:

    one fresh runner job + one complete W1-W12 full profile
    + unique replica/job identity + one measured source commit
    + one runtime/dependency recipe.

Five matrix jobs of ONE workflow invocation legitimately share a single
``workflow_run_id``; sample uniqueness is enforced on
``(workflow_run_id, replica_id)`` (plus the job id when available) and on the
benchmark ``run_id`` — duplicate identities are rejected, fail-closed.

Calibration methodology note
----------------------------
The ``>= 5`` independent fresh-runner samples requirement is the
repository's calibration methodology informed by the proven Phase 3
multi-run approach; it is NOT an explicit architecture number.

Thresholds
----------
This stage produces NO performance thresholds.  It records descriptive
statistics (min / median / max / MAD / relative MAD) and clearly labelled
DESCRIPTIVE_ONLY same-run ratio facts.  The versioned regression policy is
F3B's job, derived after architecture review of the measured dispersion.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any

#: Versioned calibration-contract identity (SEPARATE from the benchmark).
CALIBRATION_CONTRACT = "dbfbridge-direct-write-calibration-v1"
CALIBRATION_CONTRACT_VERSION = 1

#: The benchmark contract calibration consumes (unchanged from F2).
BENCHMARK_CONTRACT = "dbfbridge-direct-write-v1"
BENCHMARK_CONTRACT_VERSION = 1

#: The full W1-W12 scenario contract consumed from accepted F2.
SCENARIO_IDS = (
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

#: Minimum independent fresh-runner samples (repository calibration method,
#: informed by the proven Phase 3 multi-run approach — not an architecture
#: number).
MINIMUM_SAMPLE_COUNT = 5

#: Numeric metrics retained for every W1-W11 measured scenario.
_RETAINED_METRICS = (
    "wall_seconds",
    "cpu_seconds",
    "records_per_second",
    "output_dbf_fpt_mib_per_second",
    "rss_before_bytes",
    "peak_rss_bytes",
    "peak_rss_delta_bytes",
    "rss_after_bytes",
    "temporary_publish_bytes_written",
    "private_spool_bytes_written",
    "temporary_bytes_written",
    "temporary_bytes_left",
    "final_output_bytes",
    "intermediate_jsonl_bytes",
    "source_mib_per_second",
    "backup_logical_bytes_moved",
)

#: Privacy sentinels that must NEVER appear in calibration evidence.
_PRIVACY_SENTINEL_KEYS = (
    "records",
    "NOTE",
    "PICTURE",
    "memo_text",
    "memo_bytes",
    "password",
    "token",
    "secret",
    "api_key",
)


def _report_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


# ---------------------------------------------------------------------------
# input validation (fail-closed; a bad set is REJECTED, never trimmed)
# ---------------------------------------------------------------------------


def validate_sample_reports(
    reports: list[dict[str, Any]],
    *,
    minimum_count: int = MINIMUM_SAMPLE_COUNT,
) -> list[str]:
    """Strict calibration-input validation — the complete set fails closed.

    Returns the list of problems; an empty list means the set is a valid
    calibration input.  Bad samples are NEVER silently dropped.
    """
    problems: list[str] = []
    if len(reports) < minimum_count:
        problems.append(
            f"fewer than {minimum_count} calibration reports ({len(reports)})"
        )
    identities: set[tuple[Any, Any]] = set()
    run_ids: set[str] = set()
    commit_shas: set[str] = set()
    contract_versions: set[Any] = set()
    modes: set[str] = set()
    scenario_sets: set[tuple[str, ...]] = set()
    runtime_recipes: set[str] = set()
    for position, report in enumerate(reports):
        label = f"report[{position}]"
        if not isinstance(report, dict):
            problems.append(f"{label}: report must be an object")
            continue
        replica_id = report.get("replica_id")
        workflow_run_id = report.get("workflow_run_id")
        identity = (workflow_run_id, replica_id)
        if identity in identities:
            problems.append(f"{label}: duplicate sample identity {identity}")
        identities.add(identity)
        run_id = report.get("run_id")
        if run_id is not None:
            if run_id in run_ids:
                problems.append(f"{label}: duplicate benchmark run_id {run_id}")
            run_ids.add(str(run_id))
        commit = report.get("measured_code_sha") or report.get("git_sha")
        commit_shas.add(str(commit))
        contract_version = report.get("benchmark_contract_version")
        contract_versions.add(contract_version)
        if report.get("benchmark_contract") != BENCHMARK_CONTRACT:
            problems.append(
                f"{label}: benchmark contract must be {BENCHMARK_CONTRACT!r}"
            )
        modes.add(str(report.get("mode")))
        if report.get("mode") != "full":
            problems.append(f"{label}: calibration requires mode=full")
        rows = report.get("scenarios")
        if not isinstance(rows, list):
            problems.append(f"{label}: no scenario rows")
            continue
        scenario_ids = tuple(
            row.get("scenario") for row in rows if isinstance(row, dict)
        )
        scenario_sets.add(tuple(sorted(str(item) for item in scenario_ids if item)))
        if len(set(scenario_ids)) != len(list(scenario_ids)):
            problems.append(f"{label}: duplicate scenario in report")
        missing = [sid for sid in SCENARIO_IDS if sid not in scenario_ids]
        if missing:
            problems.append(f"{label}: missing scenarios {missing}")
        unexpected = [sid for sid in scenario_ids if sid not in SCENARIO_IDS]
        if unexpected:
            problems.append(f"{label}: unexpected scenarios {unexpected}")
        if report.get("validation_problems"):
            problems.append(f"{label}: validation_problems not empty")
        problems.extend(_check_privacy(label, report))
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
            if scenario == "direct_write_varchar_nullflags":
                spool = row.get("private_spool_bytes_written")
                if not isinstance(spool, int) or spool <= 0:
                    problems.append(
                        "W10 must show real disk-spool evidence (spool bytes > 0)"
                    )
            if scenario == "cancellation_cleanup_smoke":
                validation = row.get("validation") or {}
                if (
                    row.get("scenario_kind") != "functional_cleanup"
                    or row.get("records_per_second") is not None
                    or not validation.get("cleanup_verified")
                ):
                    problems.append(
                        "W12 must be represented as functional_cleanup evidence"
                    )
    if len(commit_shas) > 1:
        problems.append("mixed measured source SHAs across calibration set")
    if contract_versions != {BENCHMARK_CONTRACT_VERSION}:
        problems.append("mixed benchmark contract versions")
    if modes - {"full"}:
        problems.append("non-full mode mixed into calibration")
    if len(scenario_sets) > 1:
        problems.append("different scenario sets across reports")
    if len(runtime_recipes) > 1:
        problems.append("incompatible runtime recipes")
    return problems


def _check_privacy(label: str, payload: dict[str, Any]) -> list[str]:
    """Sentinel scan: memo/record-payload/secret content must never enter
    calibration evidence.

    The accepted F2 artifact's synthetic first/last canonical facts
    (``first_code``/``first_nazwa``/…) are deterministic benchmark-fixture
    markers (``K0000000``-style), committed as part of the accepted F2
    contract and are NOT user data; they are deliberately NOT sentinels.
    Prohibited content is: DBF record payloads, memo text/binary content and
    credential-like values.
    """
    problems: list[str] = []
    for row in payload.get("scenarios", []):
        validation = row.get("validation") or {}
        for key in _PRIVACY_SENTINEL_KEYS:
            if key in validation:
                problems.append(f"{label}/{row.get('scenario')}: privacy sentinel {key!r}")
    return problems


# ---------------------------------------------------------------------------
# compact extraction
# ---------------------------------------------------------------------------


def _compact_row(row: dict[str, Any]) -> dict[str, Any]:
    scenario = row.get("scenario")
    compact: dict[str, Any] = {
        "scenario": scenario,
        "status": row.get("status"),
    }
    if scenario == "cancellation_cleanup_smoke":
        compact.update(
            {
                "scenario_kind": row.get("scenario_kind"),
                "functional_cleanup_verified": bool(
                    (row.get("validation") or {}).get("cleanup_verified")
                ),
            }
        )
        return compact
    for metric in _RETAINED_METRICS:
        if metric in row:
            compact[metric] = row.get(metric)
    validation = row.get("validation") or {}
    if scenario == "direct_read_transform_write_190k":
        compact["source_unchanged"] = validation.get("source_unchanged")
    if scenario == "direct_write_memo_heavy":
        compact["memo_semantics_verified"] = validation.get("memo_semantics_verified")
        compact["code_mismatches"] = validation.get("code_mismatches")
    if scenario == "direct_write_deleted_include":
        compact["deleted_semantics_verified"] = validation.get(
            "deleted_semantics_verified"
        )
        compact["deleted_count"] = validation.get("deleted_count")
    if scenario in {
        "direct_write_cp1250",
        "direct_write_cp852",
        "direct_write_mazovia",
    }:
        compact["encoding_round_trip_verified"] = validation.get(
            "encoding_round_trip_verified"
        )
        compact["schema_driver_verified"] = validation.get("schema_driver_verified")
    if scenario == "overwrite_transaction_staging_cost":
        compact["new_generation_verified"] = validation.get(
            "new_generation_verified"
        )
        compact["old_new_dbf_differ"] = validation.get("old_new_dbf_differ")
        compact["old_new_fpt_differ"] = validation.get("old_new_fpt_differ")
        compact["preexisting_dbf_bytes"] = validation.get("preexisting_dbf_bytes")
        compact["preexisting_fpt_bytes"] = validation.get("preexisting_fpt_bytes")
        compact["final_dbf_bytes"] = validation.get("final_dbf_bytes")
        compact["final_fpt_bytes"] = validation.get("final_fpt_bytes")
    if scenario == "cancellation_cleanup_smoke":
        compact["validation"] = validation
    return compact


def _descriptive(values: list[Any]) -> dict[str, Any]:
    finite = [float(value) for value in values if _finite_number(value)]
    if not finite:
        return {
            "min": None,
            "median": None,
            "max": None,
            "mad": None,
            "relative_mad": None,
        }
    median = statistics.median(finite)
    mad = statistics.median(abs(value - median) for value in finite)
    return {
        "min": min(finite),
        "median": median,
        "max": max(finite),
        "mad": mad,
        "relative_mad": round(mad / median, 6) if median else None,
    }


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _ratio_facts(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Same-run ratio CANDIDATE facts — DESCRIPTIVE_ONLY, never a gate.

    Every ratio normalizes the numerator wall time by its OWN record count
    and divides by the analogously normalized W1 value, so record-count
    differences do not distort the comparison.
    """
    w1_id = "direct_write_190k_flat"
    ratio_specs = [
        ("W3/W1 wall-seconds-per-record", "direct_write_1m_flat"),
        ("W2/W1 wall-seconds-per-record", "direct_read_transform_write_190k"),
        ("W5/W1 wall-seconds-per-record", "direct_write_memo_heavy"),
        ("W10/W1 wall-seconds-per-record", "direct_write_varchar_nullflags"),
    ]
    facts: list[dict[str, Any]] = []
    for label, numerator_id in ratio_specs:
        values: list[Any] = []
        for sample in samples:
            rows = {item.get("scenario"): item for item in sample.get("scenarios", [])}
            numerator_value = rows.get(numerator_id, {}).get("wall_seconds")
            denominator_value = rows.get(w1_id, {}).get("wall_seconds")
            numerator_count = rows.get(numerator_id, {}).get("record_count")
            denominator_count = rows.get(w1_id, {}).get("record_count")
            if not (
                _finite_number(numerator_value)
                and _finite_number(denominator_value)
                and isinstance(numerator_count, int)
                and numerator_count > 0
                and isinstance(denominator_count, int)
                and denominator_count > 0
            ):
                values.append(None)
                continue
            values.append(
                (float(numerator_value) / numerator_count)
                / (float(denominator_value) / denominator_count)
            )
        facts.append(
            {
                "label": label,
                "numerator": f"{numerator_id}.wall_seconds / record_count",
                "denominator": f"{w1_id}.wall_seconds / record_count",
                "formula": (
                    "(numerator.wall_seconds / numerator.record_count) / "
                    "(denominator.wall_seconds / denominator.record_count)"
                ),
                "values": values,
                "classification": "DESCRIPTIVE_ONLY",
                **_descriptive(values),
            }
        )

    # W3/W1 peak-RSS-delta ratio (memory growth candidate fact).
    delta_values: list[Any] = []
    for sample in samples:
        rows = {item.get("scenario"): item for item in sample.get("scenarios", [])}
        w3_delta = rows.get("direct_write_1m_flat", {}).get("peak_rss_delta_bytes")
        w1_delta = rows.get(w1_id, {}).get("peak_rss_delta_bytes")
        if (
            isinstance(w3_delta, int)
            and isinstance(w1_delta, int)
            and w1_delta > 0
        ):
            delta_values.append(round(w3_delta / w1_delta, 6))
        else:
            delta_values.append(None)
    facts.append(
        {
            "label": "W3/W1 peak-RSS-delta ratio",
            "numerator": "direct_write_1m_flat / peak_rss_delta_bytes",
            "denominator": "direct_write_190k_flat / peak_rss_delta_bytes",
            "formula": (
                "direct_write_1m_flat.peak_rss_delta_bytes / "
                "direct_write_190k_flat.peak_rss_delta_bytes"
            ),
            "values": delta_values,
            "classification": "DESCRIPTIVE_ONLY",
            **_descriptive(delta_values),
        }
    )
    return facts


def memory_facts(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-sample W1/W3 bounded-memory facts (DBFB-PERF-004) — facts only."""
    memory: list[dict[str, Any]] = []
    for sample in samples:
        rows = {item.get("scenario"): item for item in sample.get("scenarios", [])}
        w1 = rows.get("direct_write_190k_flat", {})
        w3 = rows.get("direct_write_1m_flat", {})
        record_count_ratio = None
        peak_rss_ratio = None
        peak_rss_delta_ratio = None
        if (
            isinstance(w1.get("record_count"), int)
            and w1["record_count"] > 0
            and isinstance(w3.get("record_count"), int)
        ):
            record_count_ratio = round(
                w3["record_count"] / w1["record_count"], 4
            )
        if (
            isinstance(w1.get("peak_rss_bytes"), int)
            and w1["peak_rss_bytes"] > 0
            and isinstance(w3.get("peak_rss_bytes"), int)
        ):
            peak_rss_ratio = round(w3["peak_rss_bytes"] / w1["peak_rss_bytes"], 4)
        if (
            isinstance(w1.get("peak_rss_delta_bytes"), int)
            and w1["peak_rss_delta_bytes"] > 0
            and isinstance(w3.get("peak_rss_delta_bytes"), int)
        ):
            peak_rss_delta_ratio = round(
                w3["peak_rss_delta_bytes"] / w1["peak_rss_delta_bytes"], 4
            )
        memory.append(
            {
                "replica_id": sample.get("replica_id"),
                "w1_record_count": w1.get("record_count"),
                "w1_rss_before_bytes": w1.get("rss_before_bytes"),
                "w1_peak_rss_bytes": w1.get("peak_rss_bytes"),
                "w1_peak_rss_delta_bytes": w1.get("peak_rss_delta_bytes"),
                "w1_rss_after_bytes": w1.get("rss_after_bytes"),
                "w3_record_count": w3.get("record_count"),
                "w3_rss_before_bytes": w3.get("rss_before_bytes"),
                "w3_peak_rss_bytes": w3.get("peak_rss_bytes"),
                "w3_peak_rss_delta_bytes": w3.get("peak_rss_delta_bytes"),
                "w3_rss_after_bytes": w3.get("rss_after_bytes"),
                "record_count_ratio": record_count_ratio,
                "peak_rss_ratio": peak_rss_ratio,
                "peak_rss_delta_ratio": peak_rss_delta_ratio,
                "assessment": "MEASURED_FACTS_ONLY",
            }
        )
    return memory


def _descriptive_statistics(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Descriptive min/median/max/MAD per retained metric — NO thresholds.

    Metrics are extracted from each sample's scenario rows and aggregated
    across the calibration set.  ``None``/not-applicable metrics are excluded
    from the statistics instead of producing misleading numbers.
    """
    metric_names = sorted(
        {key for sample in samples for key in _sample_metric_keys(sample)}
    )
    aggregated: dict[str, dict[str, Any]] = {}
    for metric in metric_names:
        scenario, _, metric_name = metric.rpartition(".")
        values: list[Any] = []
        for sample in samples:
            rows = {item.get("scenario"): item for item in sample.get("scenarios", [])}
            values.append(rows.get(scenario, {}).get(metric_name))
        aggregated[metric] = _descriptive(values)
    return {
        "descriptive_only": True,
        "statistics": aggregated,
    }


def _sample_metric_keys(sample: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for row in sample.get("scenarios", []):
        if row.get("scenario") == "cancellation_cleanup_smoke":
            continue
        for metric in _RETAINED_METRICS:
            if metric in row:
                keys.append(f"{row['scenario']}.{metric}")
    return keys


# ---------------------------------------------------------------------------
# public collector API
# ---------------------------------------------------------------------------


def build_calibration(
    samples: list[dict[str, Any]],
    *,
    reference_commit: str,
    workflow_run_id: str | None = None,
    runtime_recipe: str,
    provenance_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate the sample set and emit the compact calibration payload.

    ``samples`` are FULL saved ``dbfbridge-direct-write-v1`` reports, each
    already annotated with its replica identity/provenance.  The complete set
    is validated fail-closed (§11); any problem rejects the calibration.
    """
    problems = validate_sample_reports(samples)
    if problems:
        return {"accepted": False, "problems": problems}
    measured_shas = {
        sample.get("measured_code_sha") for sample in samples
    }
    reference_commit = reference_commit or next(iter(measured_shas), "")
    scenario_ids = tuple(sorted(str(item) for item in SCENARIO_IDS))
    run_ids = sorted(
        str(sample.get("run_id")) for sample in samples if sample.get("run_id")
    )
    replica_ids = sorted(
        str(sample.get("replica_id")) for sample in samples if sample.get("replica_id")
    )
    workflow_run_ids = sorted(
        {str(sample.get("workflow_run_id")) for sample in samples}
    )
    report_shas = {
        sample.get("replica_id"): sample.get("report_sha256") for sample in samples
    }
    return {
        "accepted": True,
        "problems": [],
        "calibration_contract": CALIBRATION_CONTRACT,
        "calibration_contract_version": CALIBRATION_CONTRACT_VERSION,
        "benchmark_contract": BENCHMARK_CONTRACT,
        "benchmark_contract_version": BENCHMARK_CONTRACT_VERSION,
        "reference_commit": reference_commit,
        "calibration_count": len(samples),
        "scenario_ids": list(scenario_ids),
        "workflow_run_ids": workflow_run_ids,
        "benchmark_run_ids": run_ids,
        "replica_ids": replica_ids,
        "runtime_recipe": runtime_recipe,
        "provenance": provenance_entries or [],
        "report_sha256_by_replica": report_shas,
        "samples": [
            {
                "replica_id": sample.get("replica_id"),
                "workflow_run_id": sample.get("workflow_run_id"),
                "benchmark_run_id": sample.get("run_id"),
                "measured_code_sha": sample.get("measured_code_sha"),
                "git_commit": sample.get("git_sha"),
                "python": sample.get("python_version"),
                "os": sample.get("platform"),
                "arch": sample.get("platform"),
                "runner": None,
                "runner_reason": (
                    "hosted-runner image identity is not exposed by the "
                    "benchmark artifact (NOT_AVAILABLE)"
                ),
                "dependency_versions": sample.get("dependencies"),
                "report_sha256": sample.get("report_sha256"),
                "scenarios": [
                    _compact_row(row) for row in sample.get("scenarios", [])
                ],
            }
            for sample in samples
        ],
        "descriptive_statistics": _descriptive_statistics(samples),
        "ratio_facts": _ratio_facts(samples),
        "memory_facts": memory_facts(samples),
        "thresholds": None,
        "note": (
            "Calibration evidence only: descriptive statistics and ratio "
            "facts are DESCRIPTIVE_ONLY.  No Direct Write regression "
            "threshold is established by this artifact (F3B derives the "
            "versioned policy after architecture review)."
        ),
    }


def markdown_summary(payload: dict[str, Any]) -> str:
    """Human-readable summary rendered FROM the same payload (one source)."""
    lines = [
        "# Direct Write regression calibration (dbfbridge-direct-write-calibration-v1)",
        "",
        f"Accepted: `{payload.get('accepted')}` · samples: `{payload.get('calibration_count')}` · "
        f"reference commit: `{payload.get('reference_commit')}`",
        "",
        "| scenario | metric | min | median | max | MAD | rel MAD |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for metric, stats in (payload.get("descriptive_statistics") or {}).get(
        "statistics", {}
    ).items():
        lines.append(
            f"| {metric} | | {stats.get('min')} | {stats.get('median')} | "
            f"{stats.get('max')} | {stats.get('mad')} | {stats.get('relative_mad')} |"
        )
    for fact in payload.get("ratio_facts", []):
        lines += [
            "",
            f"Ratio candidate `{fact['label']}` — {fact['classification']}: "
            f"values {fact['values']}; median {fact.get('median')}; "
            f"relative MAD {fact.get('relative_mad')}.",
        ]
    lines += [
        "",
        "No Direct Write regression threshold is established by this "
        "calibration evidence (DBFB-PERF-006: IN PROGRESS).",
    ]
    return "\n".join(lines) + "\n"


def load_report(path: Path) -> dict[str, Any]:
    """Load one saved full Direct Write profile report."""
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report",
        action="append",
        required=True,
        metavar="REPLICA=path",
        help="saved full Direct Write profile JSON for one replica",
    )
    parser.add_argument(
        "--provenance",
        action="append",
        default=[],
        metavar="REPLICA=path.json",
        help="optional per-replica provenance JSON (python/os/deps)",
    )
    parser.add_argument("--workflow-run-id", default=None)
    parser.add_argument(
        "--runtime-recipe",
        default="windows-latest + Python 3.12 + pip install -e \".[dev]\"",
    )
    parser.add_argument("--reference-commit", default="")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, default=None)
    args = parser.parse_args(argv)

    samples: list[dict[str, Any]] = []
    provenance_entries: list[dict[str, Any]] = []
    for pair in args.report:
        replica, _, path_text = pair.partition("=")
        path = Path(path_text)
        report = json.loads(path.read_text(encoding="utf-8"))
        report.setdefault("replica_id", replica)
        report.setdefault(
            "workflow_run_id",
            args.workflow_run_id,
        )
        report.setdefault("report_sha256", _report_sha256(path))
        samples.append(report)
    for pair in args.provenance:
        replica, _, path_text = pair.partition("=")
        provenance_entries.append(
            {
                "replica_id": replica,
                **json.loads(Path(path_text).read_text(encoding="utf-8")),
            }
        )
    payload = build_calibration(
        samples,
        reference_commit=args.reference_commit,
        runtime_recipe=args.runtime_recipe,
        workflow_run_id=args.workflow_run_id,
        provenance_entries=provenance_entries,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if args.markdown:
        args.markdown.write_text(markdown_summary(payload), encoding="utf-8")
    print(
        f"accepted={payload.get('accepted')} "
        f"samples={payload.get('calibration_count')} "
        f"problems={payload.get('problems')}"
    )
    return 0 if payload.get("accepted") else 1


if __name__ == "__main__":
    raise SystemExit(main())

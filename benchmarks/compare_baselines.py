"""Compare the preserved Phase 0 BEFORE baseline with a Phase 1 AFTER baseline.

stdlib-only CLI (no third-party dependencies):

    python -m benchmarks.compare_baselines \\
        benchmarks/baselines/phase-0-full.json \\
        benchmarks/baselines/phase-1-direct-read-full.json

Rules:

- the FIRST file must be the legacy Phase 0 BEFORE baseline (it carries no
  ``benchmark_contract`` — that is how a historical snapshot is recognised);
  passing an AFTER baseline first is rejected as a swapped argument order;
- the SECOND file must carry exactly
  ``benchmark_contract == "phase-1-direct-read-v1"``;
- only scenarios with the same name — and therefore the same meaning in the
  frozen Phase 0 contract — are compared;
- a scenario that was ``NOT_IMPLEMENTED`` in BEFORE (the four Phase 1
  placeholders) and is ``MEASURED`` in AFTER is reported as ``NEWLY_MEASURED``;
  **no speedup is invented for it**;
- common ``MEASURED`` pairs report BEFORE/AFTER values, the AFTER/BEFORE ratio
  and the percentage change for median wall time, median CPU time, records/s,
  source MiB/s, peak RSS, read/write amplification, temporary bytes and output
  bytes; zero or missing values render ``NOT_AVAILABLE`` (never a division by
  zero);
- environment differences (OS, Python, CPU, architecture, memory, dependency
  versions, commit) are shown with an explicit warning: when the environments
  are not comparable, the report never labels any change an "improvement";
- the comparison can be written as JSON (``--json``) and Markdown
  (``--markdown``); the exit code is non-zero for broken, incomplete or
  swapped artifacts.

A committed comparison report is deliberately absent until the Phase 1 AFTER
baseline has been published through the full baseline gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from .contract import (
    CONTRACT_PHASE_1,
    PHASE0_PLACEHOLDER_NAMES,
    environment_comparability,
    manifest_problems,
    validate_saved_phase0_before,
    validate_saved_phase1_after,
)

#: Metric rows: (key inside ``aggregated``, human label, presentation hint).
METRIC_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("median_wall_seconds", "median wall time (s)", "lower-is-better"),
    ("median_cpu_seconds", "median CPU time (s)", "lower-is-better"),
    ("median_records_per_second", "records/s", "higher-is-better"),
    ("median_source_mib_per_second", "source MiB/s", "higher-is-better"),
    ("max_peak_rss_bytes", "peak RSS (bytes)", "lower-is-better"),
    ("median_read_amplification", "read amplification (measured)", "system ratio"),
    ("median_write_amplification", "write amplification (measured)", "system ratio"),
    ("max_output_bytes", "output bytes (max)", "informational"),
    (
        "max_temporary_bytes_written",
        "temporary bytes written (max)",
        "informational",
    ),
)

_METRIC_LABELS = {key: label for key, label, _ in METRIC_COLUMNS}

#: Environment fields that must match for a like-for-like comparison.
_ENV_FIELDS = (
    "python",
    "os",
    "arch",
    "processor",
    "cpu_count",
    "physical_memory_bytes",
)

__all__ = [
    "CONTRACT_PHASE_1",
    "ComparisonError",
    "compare_payloads",
    "main",
    "render_markdown",
]


class ComparisonError(RuntimeError):
    """A broken, incomplete or swapped artifact pair."""


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ComparisonError(f"Cannot read baseline {path}: {type(exc).__name__}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("scenarios"), list):
        raise ComparisonError(
            f"{path} is not a benchmark report (missing/invalid 'scenarios' list)."
        )
    if not isinstance(payload.get("environment"), dict):
        raise ComparisonError(f"{path} has a missing or malformed 'environment' block.")
    return payload


def _scenario_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for entry in payload["scenarios"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("scenario"), str):
            raise ComparisonError(
                "A scenario entry is malformed (missing scenario name or not a dict)."
            )
        if entry["scenario"] in result:
            raise ComparisonError(f"Duplicate scenario name {entry['scenario']!r} in the report.")
        result[entry["scenario"]] = entry
    return result


def _env(payload: dict[str, Any]) -> dict[str, Any]:
    env = payload.get("environment")
    return env if isinstance(env, dict) else {}


def _metric_row(before_agg: dict[str, Any], after_agg: dict[str, Any], key: str) -> dict[str, Any]:
    """One BEFORE/AFTER metric row (never a division by zero)."""
    before = before_agg.get(key)
    after = after_agg.get(key)

    def _number(value: Any) -> float | int | None:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, (int, float)):
            return value
        return None

    before_num = _number(before)
    after_num = _number(after)
    if before_num is None or after_num is None:
        ratio: float | str = "NOT_AVAILABLE"
        change: float | str = "NOT_AVAILABLE"
    elif before_num == 0:
        # Guard against division by zero: a zero BEFORE value can only carry a
        # percentage change when the AFTER value is zero as well.
        ratio = "NOT_AVAILABLE"
        change = 0.0 if after_num == 0 else "NOT_AVAILABLE"
    else:
        ratio = after_num / before_num
        change = (after_num - before_num) / before_num * 100.0

    return {
        "label": _METRIC_LABELS.get(key, key),
        "before": before,
        "after": after,
        "ratio": ratio,
        "change_percent": change,
        "available": ratio != "NOT_AVAILABLE",
    }


def _environment_summary(env: dict[str, Any]) -> dict[str, Any]:
    """Comparable snapshot used by both sides and the difference check."""
    system = env.get("system") or {}
    git = env.get("git") or {}
    return {
        "python": system.get("python"),
        "os": system.get("os"),
        "arch": system.get("arch"),
        "processor": system.get("processor"),
        "cpu_count": system.get("cpu_count"),
        "physical_memory_bytes": system.get("physical_memory_bytes"),
        "packages": env.get("packages"),
        "git_commit": git.get("commit"),
        "profile": env.get("profile"),
        "benchmark_contract": env.get("benchmark_contract"),
    }


def _artifact_meta(side: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"side": side, "environment": _environment_summary(_env(payload))}


def _environment_differences(
    before_summary: dict[str, Any], after_summary: dict[str, Any]
) -> list[str]:
    differences: list[str] = []
    for key in (*_ENV_FIELDS, "packages", "benchmark_contract"):
        if before_summary.get(key) != after_summary.get(key):
            differences.append(key)
    return differences


def comparability_differences(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Human-readable field list of the runtime environment differences."""
    _verdict, differences = environment_comparability(before, after)
    return differences


def _comparability_verdict(before: dict[str, Any], after: dict[str, Any]) -> str:
    verdict, _reasons = environment_comparability(before, after)
    return verdict


def compare_payloads(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Build the comparison report payload from two fully validated payloads.

    Raises :class:`ComparisonError` for swapped, broken, incomplete or
    non-conforming artifacts.  Both sides are fully validated with the shared
    saved-artifact validators BEFORE any comparison; the comparator works on
    the frozen name contract only (no unknown, duplicate, extra or missing
    scenario names; NEWLY_MEASURED is limited to the four former
    ``NOT_IMPLEMENTED`` placeholders; the remaining 20 scenarios must be
    ``MEASURED`` on both sides).
    """

    def _raw_env(payload: dict[str, Any]) -> Any:
        return payload.get("environment")

    if _env(before).get("benchmark_contract"):
        raise ComparisonError(
            "The FIRST file must be the preserved Phase 0 BEFORE baseline "
            "(carrying no benchmark_contract); an AFTER baseline was passed "
            "first — swap the argument order."
        )
    after_contract = _env(after).get("benchmark_contract")
    if after_contract != CONTRACT_PHASE_1:
        raise ComparisonError(
            f"The second file carries benchmark_contract {after_contract!r}; "
            f"the AFTER baseline must carry exactly {CONTRACT_PHASE_1!r}."
        )

    # Full saved-artifact validation of BOTH sides, host-independent.
    before_problems = validate_saved_phase0_before(before)
    if before_problems:
        raise ComparisonError(
            "The BEFORE artifact does not satisfy the frozen Phase 0 contract: "
            + "; ".join(before_problems[:8])
        )
    after_problems = validate_saved_phase1_after(after)
    if after_problems:
        raise ComparisonError(
            "The AFTER artifact does not satisfy the Phase 1 contract: "
            + "; ".join(after_problems[:8])
        )

    before_map = _scenario_map(before)
    after_map = _scenario_map(after)
    common_measured = [
        name
        for name, entry in after_map.items()
        if entry.get("status") == "MEASURED" and before_map[name].get("status") == "MEASURED"
    ]
    if len(common_measured) != len(after_map) - 4:
        raise ComparisonError(
            f"The comparison requires exactly {len(after_map) - 4} common "
            f"MEASURED scenarios (both sides), found {len(common_measured)}."
        )

    verdict, comparability_reasons = environment_comparability(before, after)
    warnings: list[str] = []
    if verdict == "NOT_COMPARABLE":
        warnings.append(
            "ENVIRONMENT MISMATCH in: "
            + ", ".join(comparability_differences(before, after))
            + ". The numbers were measured on different systems or dependency "
            "versions; this report must NOT label any change an 'improvement'."
        )
    elif verdict == "PARTIALLY_COMPARABLE":
        warnings.append(
            "PARTIALLY COMPARABLE: "
            + "; ".join(comparability_differences(before, after))
            + ". Numbers and ratios are shown, but I/O-sensitive results do not "
            "prove improvement without a shared storage provenance."
        )

    newly_measured: list[str] = []
    comparisons: list[dict[str, Any]] = []
    for name, after_entry in after_map.items():
        before_entry = before_map[name]
        after_status = after_entry.get("status")
        before_entry_status = before_entry.get("status")
        if before_entry_status == "MEASURED" and after_status == "MEASURED":
            before_agg: dict[str, Any] = before_entry.get("aggregated") or {}
            after_agg: dict[str, Any] = after_entry.get("aggregated") or {}
            metrics = {
                key: _metric_row(before_agg, after_agg, key)
                for key, _label, _direction in METRIC_COLUMNS
            }
            comparisons.append({"scenario": name, "status": "SAME_MEASURED", "metrics": metrics})
        elif after_status == "MEASURED":
            # After the full validation the only possible non-common MEASURED
            # scenario is one of the four former NOT_IMPLEMENTED placeholders.
            if name not in PHASE0_PLACEHOLDER_NAMES:
                raise ComparisonError(
                    f"Scenario {name!r} has no BEFORE counterpart; only the four "
                    "documented Phase 1 placeholders may be NEWLY_MEASURED."
                )
            newly_measured.append(name)
            comparisons.append(
                {
                    "scenario": name,
                    "status": "NEWLY_MEASURED",
                    "before_status": before_entry_status,
                    "after_status": after_status,
                    "note": (
                        "The Phase 0 BEFORE baseline listed this scenario as "
                        f"{before_entry_status!r}: there is no BEFORE measurement of the "
                        "same meaning, so NO speedup is computed for it (it is "
                        "never 'infinitely faster' than a missing feature)."
                    ),
                }
            )

    before_run_id = _env(before).get("run_id")
    after_run_id = _env(after).get("run_id")

    return {
        "comparison": "phase-0-before-vs-phase-1-after",
        "before": _artifact_meta("before", before),
        "after": _artifact_meta("after", after),
        "before_run_id": before_run_id,
        "after_run_id": after_run_id,
        "newly_measured": newly_measured,
        "environment_comparability": _comparability_verdict(before, after),
        "environment_differences": comparability_differences(before, after),
        "comparisons": comparisons,
        "warnings": warnings,
    }


def render_markdown(report: dict[str, Any]) -> str:
    """Human-readable Markdown rendering of a comparison report."""
    lines: list[str] = ["# BEFORE/AFTER comparison report", ""]

    before_env = report["before"]["environment"]
    after_env = report["after"]["environment"]
    lines.append("## Artifacts")
    lines.append("")
    for side in ("before", "after"):
        env = report[side]["environment"]
        lines.append(
            f"- **{side}** (sha256 `{report[side].get('sha256', '?')}`): "
            f"{env['os']} / Python {env['python']} / {env['arch']} / "
            f"commit {(env.get('git_commit') or '')[:12]!r} / "
            f"contract={env['benchmark_contract']!r} / profile={env['profile']!r}"
        )

    lines.append("")
    lines.append("## Environment comparability")
    verdict = report["environment_comparability"]
    differences = report["environment_differences"]
    lines.append(f"Verdict: **{verdict}**")
    if verdict == "COMPARABLE":
        lines.append(
            "The BEFORE and AFTER environments match on all summary fields and "
            "carry consistent storage provenance; the deltas below may be read "
            "as *like-for-like*."
        )
    elif verdict == "PARTIALLY_COMPARABLE":
        lines.append(
            "**PARTIALLY COMPARABLE**: " + "; ".join(differences) + ". Numbers and "
            "ratios are shown, but I/O-sensitive results do not prove improvement "
            "without a shared storage provenance."
        )
    else:
        lines.append(
            "**WARNING: the environments are NOT comparable** (differing: "
            + ", ".join(differences)
            + "). No change below may be called an 'improvement'."
        )
    lines.append("")
    lines.append(f"- BEFORE run_id: `{report.get('before_run_id') or 'N/A (legacy Phase 0)'}`")
    lines.append(f"- AFTER run_id: `{report.get('after_run_id') or 'N/A'}`")
    lines.append("")
    lines.append("| field | BEFORE | AFTER |")
    lines.append("|---|---|---|")
    for field in (*_ENV_FIELDS, "packages", "benchmark_contract"):
        before_value = before_env.get(field)
        after_value = after_env.get(field)
        lines.append(
            f"| {field} | {_format_metric(before_value)} | {_format_metric(after_value)} |"
        )

    lines.append("")
    lines.append("## Common MEASURED scenarios")
    lines.append("")
    lines.append("| scenario | metric | BEFORE | AFTER | AFTER/BEFORE | change % |")
    lines.append("|---|---|---|---|---|---|")
    for entry in report["comparisons"]:
        if entry["status"] != "SAME_MEASURED":
            continue
        for key, label, _direction in METRIC_COLUMNS:
            row = entry["metrics"][key]
            if not row["available"]:
                lines.append(
                    f"| {entry['scenario']} | {label} | "
                    f"{_format_metric(row['before'])} | {_format_metric(row['after'])} "
                    f"| NOT_AVAILABLE | NOT_AVAILABLE |"
                )
                continue
            lines.append(
                f"| {entry['scenario']} | {label} | "
                f"{_format_metric(row['before'])} | {_format_metric(row['after'])} | "
                f"{row['ratio']:.3f} | {row['change_percent']:+.1f} |"
            )

    lines.append("")
    lines.append("## Newly measured scenarios (no BEFORE measurement exists)")
    if report["newly_measured"]:
        lines.append("")
        lines.append(
            "The BEFORE baseline listed these as `NOT_IMPLEMENTED`; they are "
            "`MEASURED` in AFTER. **No speedup is claimed for them** — there is "
            "no BEFORE number to compare against, and they are never 'infinitely "
            "faster' than a missing feature."
        )
        lines.append("")
        for name in report["newly_measured"]:
            lines.append(f"- `{name}` (NEWLY_MEASURED)")
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def _format_metric(value: Any) -> str:
    if value is None:
        return "NOT_AVAILABLE"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", help="Phase 0 BEFORE baseline JSON (legacy, no contract)")
    parser.add_argument(
        "after",
        help="Phase 1 AFTER baseline JSON (requires benchmark_contract=phase-1-direct-read-v1)",
    )
    parser.add_argument("--json", type=Path, help="Also write the comparison payload as JSON")
    parser.add_argument("--markdown", type=Path, help="Also write a Markdown comparison report")
    parser.add_argument("--quiet", action="store_true", help="Do not print the report to stdout")
    args = parser.parse_args(argv)
    try:
        before_payload = _load_json(Path(args.before))
        after_payload = _load_json(Path(args.after))
        report = compare_payloads(before_payload, after_payload)
        report["before"]["sha256"] = _sha256_file(Path(args.before))
        report["after"]["sha256"] = _sha256_file(Path(args.after))
        _verify_after_manifest(Path(args.after), after_payload)
    except ComparisonError as exc:
        print(f"COMPARISON REFUSED: {exc}", file=sys.stderr)
        return 2

    if not args.quiet:
        print(render_markdown(report), end="")
    if args.json is not None:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.markdown is not None:
        args.markdown.write_text(render_markdown(report), encoding="utf-8")
    return 0


def _verify_after_manifest(after_path: Path, after_payload: dict[str, Any]) -> None:
    """Verify the publication manifest next to a versioned AFTER baseline.

    A committed Phase 1 AFTER baseline is complete only when a valid manifest
    corroborates the published JSON (names, contract, profile, run id, SHA-256).
    """
    from benchmarks import artifacts as bench_artifacts

    contract = str(_env(after_payload).get("benchmark_contract") or "")
    profile = str(_env(after_payload).get("profile"))
    if not contract:
        raise ComparisonError("The AFTER payload carries no benchmark_contract.")
    json_name, md_name, manifest_name = bench_artifacts.baseline_target_paths(contract, profile)
    if after_path.name != json_name:
        raise ComparisonError(
            f"The AFTER baseline file must be named {json_name!r}, got {after_path.name!r}."
        )
    manifest_path = after_path.with_name(manifest_name)
    if not manifest_path.is_file():
        raise ComparisonError(
            f"the publication manifest {manifest_name} is missing next to {after_path.name}"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ComparisonError(f"Cannot read the manifest {manifest_path}: {exc}") from exc
    run_id = str(_env(after_payload).get("run_id") or "")
    problems = manifest_problems(
        manifest,
        expected_json_name=json_name,
        expected_json_sha256=_sha256_file(after_path),
        expected_markdown_name=md_name,
        expected_markdown_sha256=_sha256_file(after_path.with_name(md_name)),
        expected_run_id=run_id,
        expected_contract=CONTRACT_PHASE_1,
        expected_profile=profile,
    )
    if problems:
        raise ComparisonError(
            "The AFTER baseline manifest does not corroborate the published "
            "artifacts: " + "; ".join(problems)
        )


if __name__ == "__main__":
    raise SystemExit(main())

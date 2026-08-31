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

#: Phase 1 AFTER contract (single source: benchmarks.artifacts).
CONTRACT_PHASE_1 = "phase-1-direct-read-v1"

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


def compare_payloads(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Build the comparison report payload from two validated report payloads.

    Raises :class:`ComparisonError` for swapped, broken or incomplete pairs.
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

    before_map = _scenario_map(before)
    after_map = _scenario_map(after)
    before_summary = _environment_summary(_env(before))
    after_summary = _environment_summary(_env(after))
    differences = _environment_differences(before_summary, after_summary)
    comparable = not differences
    warnings: list[str] = []
    if not comparable:
        warnings.append(
            "ENVIRONMENT MISMATCH in: "
            + ", ".join(differences)
            + ". The numbers were measured on different systems or dependency "
            "versions; this report must NOT label any change an 'improvement'."
        )

    newly_measured: list[str] = []
    comparisons: list[dict[str, Any]] = []
    for name, after_entry in after_map.items():
        before_entry = before_map.get(name)
        after_status = after_entry.get("status")
        before_entry_status = (
            before_entry.get("status") if before_entry else "NOT_PRESENT_IN_BEFORE"
        )
        if before_entry_status == "MEASURED" and after_status == "MEASURED":
            assert before_entry is not None
            before_agg: dict[str, Any] = before_entry.get("aggregated") or {}
            after_agg: dict[str, Any] = after_entry.get("aggregated") or {}
            metrics = {
                key: _metric_row(before_agg, after_agg, key)
                for key, _label, _direction in METRIC_COLUMNS
            }
            comparisons.append({"scenario": name, "status": "SAME_MEASURED", "metrics": metrics})
        elif after_status == "MEASURED":
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
        elif before_entry_status == "MEASURED" and after_status != "MEASURED":
            comparisons.append(
                {
                    "scenario": name,
                    "status": "MEASURED_NO_LONGER_IN_AFTER",
                    "before_status": before_entry_status,
                    "after_status": after_status,
                    "note": "A previously measured scenario lost its AFTER measurement.",
                }
            )

    return {
        "comparison": "phase-0-before-vs-phase-1-after",
        "before": _artifact_meta("before", before),
        "after": _artifact_meta("after", after),
        "newly_measured": newly_measured,
        "environments_comparable": comparable,
        "environment_differences": differences,
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
    if report["environments_comparable"]:
        lines.append(
            "Passed: the BEFORE and AFTER environments match on all summary "
            "fields; the deltas below may be read as *like-for-like*."
        )
    else:
        lines.append(
            "**WARNING: the environments are NOT comparable** (differing: "
            + ", ".join(report["environment_differences"])
            + "). No change below may be called an 'improvement'."
        )
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


if __name__ == "__main__":
    raise SystemExit(main())

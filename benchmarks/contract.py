"""Saved-baseline contract validators (pure, stdlib-only, host-independent).

This module is the SINGLE source of truth for what a *saved* benchmark
artifact must contain.  The validators work exclusively on the serialized
payload content — they never depend on the machine the tool runs on, the
current local worktree, the current git HEAD, or the availability of optional
dependencies such as ``psutil``.

Two frozen contracts exist:

- **Phase 0 BEFORE** (:func:`validate_saved_phase0_before`): the approved
  legacy snapshot — no ``benchmark_contract`` field, full profile, exactly the
  24 unique scenario names (20 ``MEASURED`` + the 4 documented
  ``NOT_IMPLEMENTED`` placeholders), complete samples and metadata;
- **Phase 1 AFTER** (:func:`validate_saved_phase1_after`): requires the exact
  ``phase-1-direct-read-v1`` contract, 24 unique ``MEASURED`` scenarios over
  the same frozen name set, complete per-scenario sample/warm-up counts,
  required metrics, available peak RSS, zero temporary residue, a stable
  ``run_id``, and the memo-reconstruction extras.

The comparison CLI validates both sides with these validators before any
comparison, and the active-run gate (``run_benchmark.check_baseline_gate``)
delegates the shared scenario-shape checks here while keeping its own
live-machine checks (e.g. the current ``psutil`` availability).
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
from datetime import datetime, timezone
from typing import Any

#: Versioned identity of the Phase 1 benchmark report contract.
CONTRACT_PHASE_1 = "phase-1-direct-read-v1"

#: The four scenarios that were NOT_IMPLEMENTED in the Phase 0 BEFORE
#: baseline.  Only these names may ever be reported NEWLY_MEASURED.
PHASE0_PLACEHOLDER_NAMES = frozenset(
    {"direct_read_bounded", "field_projection", "memo_lazy", "raw_mode_none"}
)

#: The 20 scenarios that are MEASURED in BOTH the frozen Phase 0 contract and
#: the Phase 1 contract (identical names and meanings).
FROZEN_PHASE0_MEASURED_NAMES = frozenset(
    {
        "jsonl_conversion_json",
        "jsonl_conversion_csv",
        "raw_record_metadata_default",
        "export_jsonl_validate_on",
        "export_jsonl_validate_off",
        "memo_skip",
        "memo_null",
        "memo_inline",
        "deleted_skip",
        "deleted_include",
        "encoding_cp1250",
        "encoding_cp852",
        "encoding_mazovia",
        "reconstruction_jsonl_to_dbf",
        "roundtrip_quality",
        "reconstruction_190k",
        "memo_heavy_190k",
        "export_1m_records",
        "reconstruction_memo_190k",
        "jsonl_conversion_xlsx",
    }
)

#: The full scenario name contract (the identical 24-name set in Phase 0 and
#: Phase 1: the 20 measured names never changed meaning, and the 4
#: placeholders became real MEASURED scenarios in Phase 1).
FROZEN_SCENARIO_NAMES = frozenset(FROZEN_PHASE0_MEASURED_NAMES | PHASE0_PLACEHOLDER_NAMES)

#: The memo rebuild scenario that must carry its per-sample extras.
MEMO_RECONSTRUCTION_SCENARIO = "reconstruction_memo_190k"

#: Fields required in every sample (measured and warm-up) of a MEASURED
#: scenario.  Presence is required; individual values may legitimately be
#: ``NOT_AVAILABLE`` (e.g. amplifications without I/O counters).
REQUIRED_SAMPLE_FIELDS: tuple[str, ...] = (
    "wall_seconds",
    "cpu_seconds",
    "records_per_second",
    "source_mib_per_second",
    "output_bytes",
    "peak_rss_bytes",
    "read_amplification",
    "write_amplification",
    "input_bytes",
    "input_records",
    "temporary_bytes_written",
    "temporary_bytes_left",
    "temporary_files_left",
)

#: Fields required in the aggregated block of a MEASURED scenario.
REQUIRED_AGGREGATED_FIELDS: tuple[str, ...] = (
    "median_wall_seconds",
    "median_cpu_seconds",
    "median_records_per_second",
    "median_source_mib_per_second",
    "max_peak_rss_bytes",
    "max_output_bytes",
    "max_temporary_bytes_written",
    "valid_baseline",
)

#: Per-sample extras that only the memo rebuild scenario must provide.
MEMO_REQUIRED_SAMPLE_FIELDS: tuple[str, ...] = (
    "output_dbf_bytes",
    "output_fpt_bytes",
    "fpt_mib_per_second",
)

_ENV_RUNTIME_KEYS: tuple[str, ...] = (
    "python",
    "os",
    "arch",
    "processor",
    "cpu_count",
    "physical_memory_bytes",
)
_MEASUREMENT_DEPENDENCIES: tuple[str, ...] = (
    "dbf",
    "dbfread",
    "orjson",
    "polars",
    "openpyxl",
    "xlsxwriter",
    "psutil",
)

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")

CONTRACT_PHASE_3 = "phase-3-performance-v1"

__all__ = [
    "CONTRACT_PHASE_3",
    "CONTRACT_PHASE_1",
    "FROZEN_PHASE0_MEASURED_NAMES",
    "FROZEN_SCENARIO_NAMES",
    "MEMO_RECONSTRUCTION_SCENARIO",
    "PHASE0_PLACEHOLDER_NAMES",
    "build_manifest",
    "environment_comparability",
    "manifest_problems",
    "validate_saved_phase0_before",
    "validate_saved_phase1_after",
]


# ---------------------------------------------------------------------------
# accessors (tolerant of malformed payloads)
# ---------------------------------------------------------------------------


def _env_of(payload: Any) -> dict[str, Any]:
    env = payload.get("environment") if isinstance(payload, dict) else None
    return env if isinstance(env, dict) else {}


def _git_of(payload: Any) -> dict[str, Any]:
    git = _env_of(payload).get("git")
    return git if isinstance(git, dict) else {}


def _system_of(payload: Any) -> dict[str, Any]:
    system = _env_of(payload).get("system")
    return system if isinstance(system, dict) else {}


def _packages_of(payload: Any) -> dict[str, Any]:
    packages = _env_of(payload).get("packages")
    return packages if isinstance(packages, dict) else {}


def _numeric(value: Any) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    return None


# ---------------------------------------------------------------------------
# shared payload checks
# ---------------------------------------------------------------------------


def _profile_problems(payload: Any) -> list[str]:
    profile = _env_of(payload).get("profile")
    if profile != "full":
        return [f"environment.profile must be 'full', got {profile!r}"]
    return []


def _shape_problems(payload: Any) -> tuple[int, int, list[str]]:
    env = _env_of(payload)
    warmup = env.get("warmup")
    repetitions = env.get("repetitions")
    problems: list[str] = []
    if not isinstance(warmup, int) or isinstance(warmup, bool) or warmup < 1:
        problems.append(f"environment.warmup must be >= 1, got {warmup!r}")
        warmup = -1
    if not isinstance(repetitions, int) or isinstance(repetitions, bool) or repetitions < 3:
        problems.append(f"environment.repetitions must be >= 3, got {repetitions!r}")
        repetitions = -1
    return warmup, repetitions, problems


def _scenario_map_problems(
    payload: Any, allowed_statuses: frozenset[str]
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    scenarios: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    raw = payload.get("scenarios") if isinstance(payload, dict) else None
    if not isinstance(raw, list):  # pragma: no cover - callers check shape first
        return scenarios, ["payload is missing a 'scenarios' list"]
    for entry in raw:
        if not isinstance(entry, dict) or not isinstance(entry.get("scenario"), str):
            problems.append("a scenario entry is malformed (missing scenario name)")
            continue
        name = entry["scenario"]
        if name in scenarios:
            problems.append(f"duplicate scenario name {name!r}")
            continue
        scenarios[name] = entry
    unknown = set(scenarios) - FROZEN_SCENARIO_NAMES
    missing = FROZEN_SCENARIO_NAMES - set(scenarios)
    for name in sorted(unknown):
        problems.append(f"unknown scenario outside the frozen name contract: {name!r}")
    for name in sorted(missing):
        problems.append(f"missing scenario from the frozen name contract: {name!r}")
    for name in sorted(scenarios):
        status = scenarios[name].get("status")
        if status not in allowed_statuses:
            problems.append(f"scenario {name!r} has status {status!r} (failed or unknown)")
    return scenarios, problems


def _measured_scenario_problems(
    name: str, entry: dict[str, Any], warmup: int, repetitions: int
) -> list[str]:
    """Check one MEASURED scenario: counts, statuses, metrics, residue."""
    problems: list[str] = []
    samples = entry.get("samples")
    warmup_samples = entry.get("warmup_samples")
    if not isinstance(samples, list) or not isinstance(warmup_samples, list):
        return [f"scenario {name!r} is missing its samples/warmup_samples lists"]
    if len(samples) != repetitions:
        problems.append(
            f"scenario {name!r} has {len(samples)} measured samples, expected exactly {repetitions}"
        )
    if len(warmup_samples) != warmup:
        problems.append(
            f"scenario {name!r} has {len(warmup_samples)} warm-up samples, expected exactly {warmup}"
        )
    for label, group, expected_warmup_flag in (
        ("measured", samples, False),
        ("warm-up", warmup_samples, True),
    ):
        for sample in group:
            if not isinstance(sample, dict):
                problems.append(f"scenario {name!r} has a malformed {label} sample")
                continue
            if sample.get("status") != "MEASURED":
                problems.append(
                    f"scenario {name!r} has a {label} sample with status {sample.get('status')!r}"
                )
            if sample.get("warmup") is not expected_warmup_flag:
                problems.append(
                    f"scenario {name!r} has a {label} sample with warmup={sample.get('warmup')!r}"
                )
            for field in REQUIRED_SAMPLE_FIELDS:
                if field not in sample:
                    problems.append(
                        f"scenario {name!r} {label} sample lacks required metric {field!r}"
                    )
            peak = sample.get("peak_rss_bytes")
            if not isinstance(peak, (int, float)) or isinstance(peak, bool):
                problems.append(f"scenario {name!r} {label} sample has no available peak RSS")
            if sample.get("temporary_bytes_left") != 0:
                problems.append(f"scenario {name!r} {label} sample left temporary bytes")
            if sample.get("temporary_files_left") != 0:
                problems.append(f"scenario {name!r} {label} sample left temporary files")

    aggregated = entry.get("aggregated")
    if not isinstance(aggregated, dict):
        problems.append(f"scenario {name!r} is missing its aggregated block")
    else:
        for field in REQUIRED_AGGREGATED_FIELDS:
            if field not in aggregated:
                problems.append(f"scenario {name!r} aggregated block lacks {field!r}")
        if aggregated.get("valid_baseline") is not True:
            problems.append(f"scenario {name!r} is not flagged valid_baseline=true")

    if name == MEMO_RECONSTRUCTION_SCENARIO:
        for sample in samples:
            if not isinstance(sample, dict):
                continue
            for field in MEMO_REQUIRED_SAMPLE_FIELDS:
                if field not in sample or not isinstance(sample.get(field), (int, float)):
                    problems.append(f"memo rebuild sample of {name!r} lacks {field!r}")
            output_total = (sample.get("output_dbf_bytes") or 0) + (
                sample.get("output_fpt_bytes") or 0
            )
            if (sample.get("output_dbf_bytes") or 0) <= 0 or (
                sample.get("output_fpt_bytes") or 0
            ) <= 0:
                problems.append(
                    f"memo rebuild sample of {name!r} must report positive "
                    "output_dbf_bytes and output_fpt_bytes"
                )
            if (sample.get("fpt_mib_per_second") or 0) <= 0:
                problems.append(
                    f"memo rebuild sample of {name!r} must report fpt_mib_per_second > 0"
                )
            if (sample.get("temporary_publish_count") or 0) < 2:
                problems.append(
                    f"memo rebuild sample of {name!r} must report temporary_publish_count >= 2"
                )
            if (sample.get("temporary_bytes_written") or 0) < output_total:
                problems.append(
                    f"memo rebuild sample of {name!r} under-reports temporary_bytes_written"
                )
    return problems


# ---------------------------------------------------------------------------
# public validators
# ---------------------------------------------------------------------------


def _repo_identity_problems(payload: Any) -> list[str]:
    """Full commit + clean worktree + complete system/package metadata."""
    problems: list[str] = []
    git = _git_of(payload)
    commit = git.get("commit")
    if not isinstance(commit, str) or not _COMMIT_RE.match(commit):
        problems.append(f"environment.git.commit must be a full 40-hex SHA, got {commit!r}")
    if git.get("worktree_dirty") is not False:
        problems.append(
            "environment.git.worktree_dirty must be false; a baseline requires a clean worktree"
        )
    system = _system_of(payload)
    for key in _ENV_RUNTIME_KEYS:
        if key not in system:
            problems.append(f"environment.system.{key} metadata is missing")
    packages = _packages_of(payload)
    if not packages:
        problems.append("environment.packages metadata is missing")
    else:
        if not isinstance(packages.get("dbfbridge"), str):
            problems.append("environment.packages.dbfbridge version metadata is missing")
        if not isinstance(packages.get("psutil"), str):
            problems.append("environment.packages.psutil version metadata is missing")
    return problems


def validate_saved_phase0_before(payload: Any) -> list[str]:
    """Validate the frozen Phase 0 BEFORE artifact (approved legacy form).

    The Phase 0 snapshot carries NO ``benchmark_contract``: that is part of
    the saved-artifact contract, not a defect to fix by modifying the artifact.
    """
    problems: list[str] = []
    if not isinstance(payload, dict) or not isinstance(payload.get("scenarios"), list):
        return ["payload is not a benchmark report (needs a 'scenarios' list)"]
    if _env_of(payload).get("benchmark_contract") is not None:
        problems.append(
            "the Phase 0 BEFORE baseline is a legacy artifact and must carry no "
            f"benchmark_contract (found {_env_of(payload).get('benchmark_contract')!r})"
        )
    problems.extend(_profile_problems(payload))
    warmup, repetitions, shape = _shape_problems(payload)
    problems.extend(shape)
    scenarios, map_problems = _scenario_map_problems(
        payload, frozenset({"MEASURED", "NOT_IMPLEMENTED"})
    )
    problems.extend(map_problems)
    measured_now = {n for n, e in scenarios.items() if e.get("status") == "MEASURED"}
    if measured_now != FROZEN_PHASE0_MEASURED_NAMES:
        for name in sorted(measured_now - FROZEN_PHASE0_MEASURED_NAMES):
            problems.append(
                f"unexpected MEASURED scenario {name!r} for the frozen Phase 0 contract"
            )
        for name in sorted(FROZEN_PHASE0_MEASURED_NAMES - measured_now):
            problems.append(f"scenario {name!r} must be MEASURED for the frozen Phase 0 contract")
    for name in sorted(set(scenarios) & PHASE0_PLACEHOLDER_NAMES):
        if scenarios[name].get("status") != "NOT_IMPLEMENTED":
            problems.append(
                f"scenario {name!r} must stay NOT_IMPLEMENTED in the frozen Phase 0 contract"
            )
    if warmup >= 1 and repetitions >= 3:
        for name in sorted(measured_now):
            entry = scenarios[name]
            assert isinstance(entry, dict)
            problems.extend(_measured_scenario_problems(name, entry, warmup, repetitions))
    problems.extend(_repo_identity_problems(payload))
    return problems


def validate_saved_phase1_after(payload: Any) -> list[str]:
    """Validate a Phase 1 AFTER baseline payload against the full contract."""
    problems: list[str] = []
    if not isinstance(payload, dict) or not isinstance(payload.get("scenarios"), list):
        return ["payload is not a benchmark report (needs a 'scenarios' list)"]
    if _env_of(payload).get("benchmark_contract") != CONTRACT_PHASE_1:
        problems.append(
            f"benchmark_contract is {_env_of(payload).get('benchmark_contract')!r}; "
            f"the Phase 1 AFTER baseline requires exactly {CONTRACT_PHASE_1!r}"
        )
    problems.extend(_run_id_problems(payload))
    problems.extend(_generated_at_problems(payload))
    problems.extend(_profile_problems(payload))
    warmup, repetitions, shape = _shape_problems(payload)
    problems.extend(shape)
    scenarios, map_problems = _scenario_map_problems(payload, frozenset({"MEASURED"}))
    problems.extend(map_problems)
    measured_now = {n for n, e in scenarios.items() if e.get("status") == "MEASURED"}
    if measured_now != FROZEN_SCENARIO_NAMES:
        for name in sorted(measured_now - FROZEN_SCENARIO_NAMES):
            problems.append(f"unexpected MEASURED scenario {name!r} for the Phase 1 contract")
        for name in sorted(FROZEN_SCENARIO_NAMES - measured_now):
            problems.append(f"scenario {name!r} must be MEASURED for the Phase 1 contract")
    if warmup >= 1 and repetitions >= 3:
        for name in sorted(measured_now & FROZEN_SCENARIO_NAMES):
            entry = scenarios[name]
            assert isinstance(entry, dict)
            problems.extend(_measured_scenario_problems(name, entry, warmup, repetitions))
    problems.extend(_repo_identity_problems(payload))
    return problems


# ---------------------------------------------------------------------------
# run identity
# ---------------------------------------------------------------------------

#: Accepted run identifier format: ``run-`` + 32 lowercase hex characters.
RUN_ID_RE = re.compile(r"^run-[0-9a-f]{32}$")

#: Accepted ``generated_at`` format: timezone-aware UTC ISO 8601 with
#: microseconds and a ``+00:00`` or ``Z`` offset — never ``+Z``, a local
#: offset, or a value without microseconds.
GENERATED_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}(?:\+00:00|Z)$")


def generate_run_id(
    *,
    commit: str = "",
    contract: str = "",
    profile: str = "",
    warmup: Any = None,
    repetitions: Any = None,
) -> str:
    """Generate the unique identifier of ONE actual benchmark run.

    Uniqueness across real runs (even with identical parameters) comes from
    the UTC timestamp with microsecond precision and a random nonce; commit,
    contract, profile and the run parameters bind the identifier to
    provenance.  The format is the stable ``run-`` + 32 hex characters
    (validated by :data:`RUN_ID_RE`).

    Called exactly once at the start of report creation — the SAME
    identifier then lands in the JSON, the Markdown, the manifest and the
    publication message.
    """
    timestamp = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    nonce = secrets.token_hex(16)
    material = "|".join(
        str(part)
        for part in (
            timestamp,
            commit,
            contract,
            profile,
            warmup,
            repetitions,
            nonce,
        )
    )
    return "run-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _run_id_problems(payload: Any) -> list[str]:
    run_id = _env_of(payload).get("run_id")
    if not isinstance(run_id, str) or not run_id:
        return ["environment.run_id must be a stable, non-empty identifier"]
    if not RUN_ID_RE.match(run_id):
        return [f"environment.run_id {run_id!r} must match the format 'run-<32 hex>'"]
    return []


def _generated_at_problems(payload: Any) -> list[str]:
    generated_at = _env_of(payload).get("generated_at")
    if not isinstance(generated_at, str) or not generated_at:
        return ["environment.generated_at must be a timezone-aware UTC ISO 8601 timestamp"]
    if not GENERATED_AT_RE.match(generated_at):
        return [
            f"environment.generated_at {generated_at!r} must be timezone-aware UTC "
            "ISO 8601 with microseconds (e.g. 2026-08-31T12:00:00.123456+00:00)"
        ]
    return []


def derive_runner_from_environment(
    environ: dict[str, str] | None = None,
) -> str:
    """Derive a safe, non-secret runner provenance string.

    On GitHub Actions, the description is built only from the non-secret
    workflow variables (``GITHUB_ACTIONS``, ``RUNNER_OS``, ``RUNNER_ARCH``,
    ``ImageOS``, ``ImageVersion``) — e.g.
    ``github-actions-windows-amd64-windows-2022``.  For any other machine a
    neutral ``local`` is returned: no hostname, no username, no user path
    and never a copy of ``os.environ``.
    """
    env = environ if environ is not None else os.environ
    if str(env.get("GITHUB_ACTIONS", "")).lower() not in {"", "false", "0", "no"}:
        parts = [
            "github-actions",
            str(env.get("RUNNER_OS") or ""),
            str(env.get("RUNNER_ARCH") or ""),
            f"{env.get('ImageOS') or ''}{env.get('ImageVersion') or ''}".strip(),
        ]
        return "-".join(part for part in parts if part).lower()
    return "local"


# ---------------------------------------------------------------------------
# publication manifest
# ---------------------------------------------------------------------------


MANIFEST_VERSION = 1


def build_manifest(
    *,
    run_id: str,
    contract: str,
    profile: str,
    git_commit: str,
    generated_at: str,
    json_name: str,
    json_sha256: str,
    markdown_name: str,
    markdown_sha256: str,
    runner: str,
    storage: str | None,
) -> dict[str, Any]:
    """The publication commit marker: a baseline is complete only when the
    JSON, the Markdown AND a valid manifest, all binding the same commit and
    generated_at, exist."""
    return {
        "manifest_version": MANIFEST_VERSION,
        "benchmark_contract": contract,
        "profile": profile,
        "run_id": run_id,
        "generated_at": generated_at,
        "git_commit": git_commit,
        "runner": runner,
        "storage": storage,
        "artifacts": {
            "json": {"name": json_name, "sha256": json_sha256},
            "markdown": {"name": markdown_name, "sha256": markdown_sha256},
        },
    }


def manifest_problems(
    manifest: Any,
    *,
    expected_json_name: str,
    expected_json_sha256: str,
    expected_markdown_name: str,
    expected_markdown_sha256: str,
    expected_run_id: str,
    expected_contract: str,
    expected_profile: str,
    expected_git_commit: str = "",
    expected_generated_at: str = "",
    expected_runner: str = "",
    expected_storage: str = "",
) -> list[str]:
    """Validate a publication manifest against the actually published bytes.

    ``expected_runner``/``expected_storage`` bind the manifest to the runner
    and storage provenance recorded in the AFTER JSON; a manifest without a
    non-empty, matching runner or storage voids the published baseline.
    """
    problems: list[str] = []
    if not isinstance(manifest, dict):
        return ["baseline manifest is missing or not a JSON object"]
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        problems.append(f"unsupported manifest_version {manifest.get('manifest_version')!r}")
    if manifest.get("benchmark_contract") != CONTRACT_PHASE_1:
        problems.append(
            f"manifest carries benchmark_contract {manifest.get('benchmark_contract')!r}"
        )
    if manifest.get("profile") != expected_profile:
        problems.append(f"manifest profile {manifest.get('profile')!r} does not match the run")
    if manifest.get("run_id") != expected_run_id:
        problems.append(
            f"manifest run_id {manifest.get('run_id')!r} does not match the run {expected_run_id!r}"
        )
    if not expected_git_commit or manifest.get("git_commit") != expected_git_commit:
        problems.append(
            f"manifest git_commit {manifest.get('git_commit')!r} does not match the "
            f"run commit {expected_git_commit!r}"
        )
    if not expected_generated_at or manifest.get("generated_at") != expected_generated_at:
        problems.append(
            f"manifest generated_at {manifest.get('generated_at')!r} does not match the "
            f"run timestamp {expected_generated_at!r}"
        )
    if not expected_runner or manifest.get("runner") != expected_runner:
        problems.append(
            f"manifest runner {manifest.get('runner')!r} does not match the "
            f"run provenance {expected_runner!r}"
        )
    if manifest.get("storage") != expected_storage:
        problems.append(
            f"manifest storage {manifest.get('storage')!r} does not match the "
            f"run provenance {expected_storage!r}"
        )
    artifacts_blob = manifest.get("artifacts")
    if not isinstance(artifacts_blob, dict):
        problems.append("manifest has no artifacts block")
        return problems
    json_entry = artifacts_blob.get("json")
    md_entry = artifacts_blob.get("markdown")
    for role, entry, expected_name, expected_sha256 in (
        ("json", json_entry, expected_json_name, expected_json_sha256),
        ("markdown", md_entry, expected_markdown_name, expected_markdown_sha256),
    ):
        if not isinstance(entry, dict):
            problems.append(f"manifest artifact entry {role!r} is missing")
            continue
        if entry.get("name") != expected_name:
            problems.append(
                f"manifest {role!r} name {entry.get('name')!r} != expected {expected_name!r}"
            )
        if entry.get("sha256") != expected_sha256:
            problems.append(
                f"manifest {role!r} sha256 {entry.get('sha256')!r} does not match "
                f"the published file ({expected_sha256})"
            )
    return problems


# ---------------------------------------------------------------------------
# environment comparability (three-state)
# ---------------------------------------------------------------------------


def _dependency_versions(payload: Any) -> dict[str, Any]:
    """Versions of the libraries the measured code paths actually run on."""
    packages = _packages_of(payload)
    return {key: packages.get(key) for key in _MEASUREMENT_DEPENDENCIES}


def _storage_descriptor(payload: Any) -> str | None:
    env = _env_of(payload)
    storage = env.get("storage")
    return storage if isinstance(storage, str) else None


def _runner_descriptor(payload: Any) -> str | None:
    env = _env_of(payload)
    runner = env.get("runner")
    return runner if isinstance(runner, str) else None


def environment_comparability(before_payload: Any, after_payload: Any) -> tuple[str, list[str]]:
    """Three-state comparability verdict of two measurement environments.

    Differences that are the *expected subject* of the comparison are never
    environment mismatches: the benchmark contract, the git commit, the
    branch, ``origin_main``, and the dbfbridge package version are ignored.

    - ``COMPARABLE`` — identical runtime environment (OS/Python/arch/processor/
      CPU/memory, all measurement dependencies) AND matching storage
      descriptors on both sides;
    - ``PARTIALLY_COMPARABLE`` — identical runtime environment, but the
      storage descriptor (or the runner descriptor) is missing on at least one
      side: numbers and ratios may be shown, but I/O-sensitive results do not
      prove improvement without a shared storage provenance;
    - ``NOT_COMPARABLE`` — any runtime/dependency mismatch (older Phase 0 or
      Phase 1 snapshots may also lack optional descriptors; that alone is not
      a runtime mismatch).
    """
    differences: list[str] = []
    before_system = _system_of(before_payload)
    after_system = _system_of(after_payload)
    for key in _ENV_RUNTIME_KEYS:
        if before_system.get(key) != after_system.get(key):
            differences.append(f"system.{key}")
    before_dependencies = _dependency_versions(before_payload)
    after_dependencies = _dependency_versions(after_payload)
    for key in _MEASUREMENT_DEPENDENCIES:
        if before_dependencies[key] != after_dependencies[key]:
            differences.append(f"packages.{key}")

    if differences:
        return "NOT_COMPARABLE", differences

    partial_reasons: list[str] = []
    before_storage = _storage_descriptor(before_payload)
    after_storage = _storage_descriptor(after_payload)
    if before_storage is None or after_storage is None or before_storage != after_storage:
        partial_reasons.append("storage descriptor missing or different")
    if _runner_descriptor(before_payload) != _runner_descriptor(after_payload):
        partial_reasons.append("runner descriptor missing or different")

    if not partial_reasons:
        return "COMPARABLE", []
    return "PARTIALLY_COMPARABLE", partial_reasons

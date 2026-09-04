"""Derived artifact names and atomic baseline publication (benchmark-harness).

This module is the SINGLE place that knows how benchmark report and baseline
artifacts are named.  Names are always derived from the explicit
``benchmark_contract`` (validated by :mod:`benchmarks.contract`) so a Phase 1
run can never collide with the preserved Phase 0 BEFORE baseline:

- runner reports: ``benchmarks/results/<contract-prefix>-<profile>[-<scenarios>].{json,md}``;
- versioned AFTER baseline: ``benchmarks/baselines/<contract-prefix>-full.{json,md}``
  plus the commit marker ``<contract-prefix>-full.manifest.json``.

``publish_baseline_pair()`` performs the publication as an **exception-safe
transaction** over the actually-read JSON source:

1. the source JSON is parsed and fully validated with the Phase 1 AFTER
   contract validator (an independently passed payload is never trusted);
2. the Markdown is verified to belong to the same run (run identifier);
3. the targets (JSON, Markdown, manifest) are staged as ``.partial`` files
   and published back-to-back with ``os.replace``; any failure rolls the
   target directory back to its previous state — never half a trio, never a
   leftover ``.partial``;
4. every published file is re-read, byte-verified and re-validated
   (JSON re-parsed and re-validated; manifest checked against the published
   bytes).

Two independent ``os.replace`` calls are not a crash-consistent transaction;
the published **manifest** is what makes the baseline complete: a baseline
counts as committed only when the JSON, the Markdown AND a valid manifest all
exist and corroborate each other (names, SHA-256, contract, profile, run id).
No force/overwrite flag exists: re-baselining an existing snapshot requires
an explicit architectural decision.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .contract import (
    CONTRACT_PHASE2_RESEARCH,
    CONTRACT_PHASE_1,
    CONTRACT_PHASE_3,
    build_manifest,
    manifest_problems,
    validate_saved_phase1_after,
    validate_saved_phase3_before,
)

#: Versioned identity of the Phase 1 benchmark report contract (direct
#: record read).  A Phase 1 AFTER baseline must carry exactly this value.
BENCHMARK_CONTRACT = CONTRACT_PHASE_1

#: The preserved BEFORE snapshot that must never be touched by Phase 1.
RESERVED_PHASE_0_BASELINE_FILES = frozenset({"phase-0-full.json", "phase-0-full.md"})

#: The frozen Phase 1 AFTER baseline trio that Phase 3 publication must
#: never overwrite (the same files the existing-baseline guard protects).
RESERVED_PHASE_1_BASELINE_FILES = frozenset(
    {
        "phase-1-direct-read-full.json",
        "phase-1-direct-read-full.md",
        "phase-1-direct-read-full.manifest.json",
    }
)

#: The complete set of frozen historical baseline names (Phase 0 BEFORE pair
#: and the Phase 1 AFTER trio) that no later phase may publish over.
RESERVED_FROZEN_BASELINE_FILES = RESERVED_PHASE_0_BASELINE_FILES | RESERVED_PHASE_1_BASELINE_FILES

#: Saved-artifact validator for each versioned contract that may be published.
_CONTRACT_VALIDATORS = {
    CONTRACT_PHASE_1: validate_saved_phase1_after,
    CONTRACT_PHASE_3: validate_saved_phase3_before,
}

__all__ = [
    "BENCHMARK_CONTRACT",
    "BaselinePublishError",
    "CONTRACT_PHASE_1",
    "CONTRACT_PHASE_3",
    "RESERVED_FROZEN_BASELINE_FILES",
    "RESERVED_PHASE_0_BASELINE_FILES",
    "RESERVED_PHASE_1_BASELINE_FILES",
    "UnknownBenchmarkContractError",
    "baseline_target_names",
    "baseline_target_paths",
    "contract_report_prefix",
    "publish_baseline_pair",
    "report_stem",
    "sha256_file",
]


class UnknownBenchmarkContractError(RuntimeError):
    """An unknown benchmark_contract cannot name an artifact."""


class BaselinePublishError(RuntimeError):
    """A baseline publication was refused or failed (nothing committed)."""


def contract_report_prefix(contract: Any) -> str:
    """Derive the report/baseline name prefix from the versioned contract.

    ``"phase-1-direct-read-v1"`` → ``"phase-1-direct-read"``.  Reports use
    ``<prefix>-<profile>[-<scenarios>].{json,md}``; the full baseline trio is
    ``<prefix>-full.{json,md}`` + ``<prefix>-full.manifest.json``.  The Phase 0
    prefix is only produced for the explicitly given legacy contract.
    """
    if contract == CONTRACT_PHASE_1:
        return "phase-1-direct-read"
    if contract == CONTRACT_PHASE_3:
        return "phase-3-performance"
    if contract == CONTRACT_PHASE2_RESEARCH:
        # RESEARCH (phase2 / Direct Write) reports are named but never
        # published as baselines (no validator exists for this contract).
        return "phase2-research"
    if contract in (None, "", "phase-0"):
        return "phase-0"
    raise UnknownBenchmarkContractError(
        f"Unknown benchmark_contract {contract!r}; expected "
        f"{CONTRACT_PHASE_1!r} or {CONTRACT_PHASE_3!r}."
    )


def report_stem(contract: Any, profile: str, scenario_suffix: str = "") -> str:
    """Derive the report stem (without extension) from the explicit contract."""
    prefix = contract_report_prefix(contract)
    suffix = f"-{scenario_suffix}" if scenario_suffix else ""
    return f"{prefix}-{profile}{suffix}"


def baseline_target_paths(contract: str, profile: str = "full") -> tuple[str, str, str]:
    """Derive the versioned baseline file names from the contract.

    Returns ``(json_name, markdown_name, manifest_name)``; the derived names
    can never collide with the preserved Phase 0 BEFORE pair
    (``phase-0-full.{json,md}``) or the frozen Phase 1 AFTER trio — see
    :data:`RESERVED_FROZEN_BASELINE_FILES`.
    """
    if contract not in _CONTRACT_VALIDATORS:
        raise UnknownBenchmarkContractError(
            f"A versioned baseline requires a versioned benchmark_contract "
            f"({CONTRACT_PHASE_1!r} or {CONTRACT_PHASE_3!r}); got {contract!r}. The "
            f"Phase 0 BEFORE files are a historical legacy artifact and are "
            f"never a publication target."
        )
    # Each versioned contract publishes exactly one profile: the Phase 1
    # AFTER baseline runs the full profile, the Phase 3 BEFORE baseline runs
    # the dedicated phase3 profile.  The baseline trio is always named
    # <prefix>-full.* ; the manifest records the run's own profile value.
    expected_profile = "phase3" if contract == CONTRACT_PHASE_3 else "full"
    if profile != expected_profile:
        raise UnknownBenchmarkContractError(
            f"A {contract!r} baseline is only published for the "
            f"{expected_profile!r} profile, got {profile!r}."
        )
    stem = f"{contract_report_prefix(contract)}-full"
    json_name = f"{stem}.json"
    md_name = f"{stem}.md"
    manifest_name = f"{stem}.manifest.json"
    _reject_reserved_names(json_name, md_name, manifest_name, contract=contract)
    return json_name, md_name, manifest_name


def baseline_target_names(contract: str, profile: str = "full") -> tuple[str, str]:
    """The versioned baseline JSON/Markdown names (manifest is separate)."""
    json_name, md_name, _manifest_name = baseline_target_paths(contract, profile)
    return json_name, md_name


def _reject_reserved_names(*names: str, contract: Any = None) -> None:
    """Refuse target names that belong to a frozen historical phase.

    The Phase 0 prefix is forbidden for every versioned publication.  The
    frozen Phase 1 AFTER trio may only ever be produced by the Phase 1
    contract itself — a Phase 3 (or later) publication can never adopt
    Phase 1's names.  An existing file is additionally protected by the
    never-overwrite guard in :func:`publish_baseline_pair`.
    """
    reserved = RESERVED_PHASE_0_BASELINE_FILES
    for name in names:
        if name in reserved:
            raise BaselinePublishError(
                f"Refusing to publish a baseline under the preserved Phase 0 BEFORE name {name!r}."
            )
        if name.lower().startswith("phase-0"):
            raise BaselinePublishError(
                f"Versioned baseline artifacts must not use the Phase 0 prefix: {name!r}."
            )
        if name in RESERVED_PHASE_1_BASELINE_FILES and contract != CONTRACT_PHASE_1:
            raise BaselinePublishError(
                f"Refusing to publish a baseline under the frozen Phase 1 "
                f"AFTER name {name!r}; only the Phase 1 contract may use it."
            )


def _read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def sha256_file(path: Path) -> str:
    """SHA-256 of a file's raw bytes (used for published artifacts)."""
    return _sha256(_read_bytes(path))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _remove_quietly(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        path.unlink()  # pragma: no cover - best effort cleanup path


def _validator_for(contract: Any):
    """Return the saved-artifact validator for *contract* (None if unknown)."""
    if not isinstance(contract, str):
        return None
    return _CONTRACT_VALIDATORS.get(contract)


def publish_baseline_pair(source_json: Path, source_md: Path, target_dir: Path) -> dict[str, Any]:
    """Publish a versioned baseline trio (Phase 1 AFTER or Phase 3 BEFORE),
    exception-safe.

    The publication validates the ACTUALLY read JSON (never an independently
    passed payload): the target names, contract, profile, and run id are
    derived from the source JSON itself and the contract's saved-artifact
    validator (Phase 1 AFTER for ``phase-1-direct-read-v1``, Phase 3 BEFORE
    for ``phase-3-performance-v1``) runs on the real bytes.  The Markdown must
    carry the same ``run_id`` — otherwise it belongs to another run and is
    refused.

    Guarantees:

    - an existing baseline artifact (JSON/Markdown/manifest) is never
      overwritten; a re-baseline requires an explicit, separate decision;
    - the trio (JSON + Markdown + manifest) is staged as ``.partial`` files
      and published back-to-back; every handled failure rolls the target
      directory back to its exact previous state — no half trio, no leftover
      ``.partial``;
    - every published file is re-read, byte-compared, re-validated (the JSON
      through the full Phase 1 contract validator again, the manifest through
      its own checks against the published bytes), and only then considered
      committed; a post-publish verification failure removes all three files.

    Note: two independent ``os.replace`` calls are not crash-consistent
    between themselves — a hard process kill between them cannot be fully
    rolled back, which is exactly why the manifest is published LAST and
    completeness is defined as JSON + Markdown + a manifest that
    corroborates them (a directory missing or mismatching the manifest is
    never a complete baseline).
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        data_json = _read_bytes(source_json)
    except OSError as exc:
        raise BaselinePublishError(f"Cannot read the source report JSON: {source_json}") from exc
    try:
        payload = json.loads(data_json)
    except json.JSONDecodeError as exc:
        raise BaselinePublishError(
            f"The source report JSON is not valid JSON: {source_json} ({exc})"
        ) from exc

    # Never trust a separately passed payload: the ACTUAL source bytes decide.
    env = payload.get("environment")
    env = env if isinstance(env, dict) else {}
    validator = _validator_for(env.get("benchmark_contract"))
    if validator is None:
        raise BaselinePublishError(
            "The report JSON carries no publishable benchmark_contract "
            f"(expected {CONTRACT_PHASE_1!r} or {CONTRACT_PHASE_3!r})."
        )
    problems = validator(payload)
    if problems:
        summary = "; ".join(problems[:6]) + ("..." if len(problems) > 6 else "")
        raise BaselinePublishError(
            f"The report JSON does not satisfy its saved-baseline contract ({summary})."
        )

    run_id = env["run_id"]
    contract = env["benchmark_contract"]
    profile = env["profile"]
    generated_at = env["generated_at"]
    git_commit = (env.get("git") or {}).get("commit") or ""
    runner = env.get("runner")
    storage = env.get("storage")
    if not isinstance(runner, str) or not runner:
        raise BaselinePublishError(
            "A baseline requires environment.runner provenance (e.g. --runner-label)."
        )
    if not isinstance(storage, str) or not storage:
        raise BaselinePublishError(
            "A baseline requires environment.storage provenance (e.g. --storage-label)."
        )

    json_name, md_name, manifest_name = baseline_target_paths(contract, profile)
    target_json = target_dir / json_name
    target_md = target_dir / md_name
    target_manifest = target_dir / manifest_name

    # The Markdown must belong to the same run as the JSON: run_id, contract,
    # profile, generated_at, commit and the runner/storage provenance must all
    # appear in it.  This is the pre-publication part of the shared trio
    # validation — the failure observed in workflow run 33400522593 (Markdown
    # without the runner/storage provenance) is caught HERE, before anything
    # is staged or published, and not only by the comparator.
    data_md = _read_bytes(source_md)
    md_text = data_md.decode("utf-8", errors="replace")
    identity_problems = [
        (part, present)
        for part, present in (
            ("run_id", run_id not in md_text),
            ("benchmark contract", str(contract) not in md_text),
            ("profile", str(profile) not in md_text),
            ("git commit", bool(git_commit) and git_commit not in md_text),
            ("generated_at", bool(generated_at) and generated_at not in md_text),
            ("runner", bool(runner) and runner not in md_text),
            ("storage", bool(storage) and storage not in md_text),
        )
        if present
    ]
    if identity_problems:
        raise BaselinePublishError(
            "The Markdown report does not match the JSON run: "
            + "; ".join(f"missing {name}" for name, _ok in identity_problems)
            + ".  The published Markdown must render the full run identity "
            "(run_benchmark.render_markdown carries run_id, benchmark_contract, "
            "profile, commit, generated_at, runner and storage)."
        )

    # An existing baseline is protected: re-baselining is an explicit
    # architectural decision and is never automatic.
    for target in (target_json, target_md, target_manifest):
        if target.exists():
            raise BaselinePublishError(
                f"Refusing to overwrite the existing baseline artifact {target}; "
                "removing or replacing a versioned baseline requires an explicit, "
                "separate decision."
            )

    json_sha = _sha256(data_json)
    md_sha = _sha256(data_md)
    manifest_payload = build_manifest(
        run_id=run_id,
        contract=contract,
        profile=profile,
        git_commit=git_commit,
        generated_at=generated_at,
        json_name=json_name,
        json_sha256=json_sha,
        markdown_name=md_name,
        markdown_sha256=md_sha,
        runner=runner,
        storage=storage,
    )
    data_manifest = json.dumps(manifest_payload, ensure_ascii=False, indent=2).encode("utf-8")

    json_partial = target_dir / f"{json_name}.partial"
    md_partial = target_dir / f"{md_name}.partial"
    manifest_partial = target_dir / f"{manifest_name}.partial"
    published: list[Path] = []
    partials = (json_partial, md_partial, manifest_partial)
    targets = (target_json, target_md, target_manifest)
    try:
        for partial, data in (
            (json_partial, data_json),
            (md_partial, data_md),
            (manifest_partial, data_manifest),
        ):
            partial.write_bytes(data)
        # Every staged payload must round-trip byte-for-byte before anything
        # is published.
        for partial, data in (
            (json_partial, data_json),
            (md_partial, data_md),
            (manifest_partial, data_manifest),
        ):
            if partial.read_bytes() != data:
                raise BaselinePublishError(
                    f"The staged file {partial.name} failed its round-trip verification."
                )
        for partial, target in zip(partials, targets, strict=True):
            os.replace(partial, target)
            published.append(target)
    except Exception as exc:
        # Exception-safe transaction: the target directory must look exactly
        # as before the call.
        for published_file in reversed(published):
            _remove_quietly(published_file)
        for partial in partials:
            _remove_quietly(partial)
        if isinstance(exc, BaselinePublishError):
            raise
        raise BaselinePublishError(f"Baseline publication failed: {exc}") from exc

    # Post-publish verification: re-read all three artifacts and re-check
    # bytes, the manifest, and a full re-validation of the published JSON.
    try:
        if (_read_bytes(target_json), _read_bytes(target_md), _read_bytes(target_manifest)) != (
            data_json,
            data_md,
            data_manifest,
        ):
            raise BaselinePublishError(
                "Post-publish verification failed: files do not match the staged payloads."
            )
        published_problems = manifest_problems(
            json.loads(_read_bytes(target_manifest).decode("utf-8")),
            expected_json_name=json_name,
            expected_json_sha256=json_sha,
            expected_markdown_name=md_name,
            expected_markdown_sha256=md_sha,
            expected_run_id=run_id,
            expected_contract=contract,
            expected_profile=profile,
            expected_git_commit=git_commit,
            expected_generated_at=generated_at,
            expected_runner=runner,
            expected_storage=storage if isinstance(storage, str) else "",
        )
        if published_problems:
            raise BaselinePublishError(
                "Post-publish manifest verification failed: " + "; ".join(published_problems)
            )
        revalidated = json.loads(_read_bytes(target_json).decode("utf-8"))
        republished_problems = validator(revalidated)
        if republished_problems:
            raise BaselinePublishError(
                "Post-publish re-validation of the published JSON failed: "
                + "; ".join(republished_problems[:6])
            )
        if run_id not in _read_bytes(target_md).decode("utf-8"):
            raise BaselinePublishError(
                "Post-publish Markdown verification failed: the run_id is missing."
            )
    except BaselinePublishError:
        for published_file in targets:
            _remove_quietly(published_file)
        for partial in partials:
            _remove_quietly(partial)
        raise

    return {
        "run_id": run_id,
        "generated_at": generated_at,
        "git_commit": git_commit,
        "runner": runner,
        "storage": storage,
        "json": target_json,
        "markdown": target_md,
        "manifest": target_manifest,
        "manifest_name": manifest_name,
        "manifest_sha256": _sha256(data_manifest),
        "json_sha256": json_sha,
        "markdown_sha256": md_sha,
    }

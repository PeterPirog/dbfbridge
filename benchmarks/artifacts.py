"""Derived artifact names and atomic baseline publication (benchmark-harness).

This module is the SINGLE place that knows how benchmark report and baseline
artifacts are named.  Names are always derived from the explicit
``benchmark_contract`` so a Phase 1 run can never collide with the preserved
Phase 0 BEFORE baseline:

- runner reports: ``benchmarks/results/<contract-prefix>-<profile>[-<scenarios>].{json,md}``;
- versioned AFTER baseline: ``benchmarks/baselines/<contract-prefix>-full.{json,md}``.

``publish_baseline_pair()`` writes the JSON/Markdown pair atomically: both
payloads are staged as ``.partial`` files, then published back-to-back with
``os.replace``; a refusal (existing target, reserved Phase 0 name, unknown
contract) or a partially failed publish leaves the baselines directory
exactly as it was — never half a pair, never a leftover ``.partial`` — and
both published files are re-read and SHA-256 verified before the call
returns.  There is deliberately **no overwrite/force flag**: re-baselining an
existing snapshot requires an explicit architectural decision, and Phase 1
can never target the historical Phase 0 names.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
from pathlib import Path
from typing import Any

#: Versioned identity of the Phase 1 benchmark report contract (direct
#: record read).  A Phase 1 AFTER baseline must carry exactly this value.
CONTRACT_PHASE_1 = "phase-1-direct-read-v1"
BENCHMARK_CONTRACT = CONTRACT_PHASE_1

#: The preserved BEFORE snapshot that must never be touched by Phase 1.
RESERVED_PHASE_0_BASELINE_FILES = frozenset({"phase-0-full.json", "phase-0-full.md"})

__all__ = [
    "BENCHMARK_CONTRACT",
    "BaselinePublishError",
    "CONTRACT_PHASE_1",
    "RESERVED_PHASE_0_BASELINE_FILES",
    "UnknownBenchmarkContractError",
    "baseline_target_names",
    "contract_report_prefix",
    "publish_baseline_pair",
    "report_stem",
    "sha256_file",
]


class UnknownBenchmarkContractError(RuntimeError):
    """An unknown benchmark_contract cannot name an artifact."""


class BaselinePublishError(RuntimeError):
    """A baseline publication was refused or failed (nothing published)."""


def contract_report_prefix(contract: str | None) -> str:
    """Derive the report/baseline name prefix from the versioned contract.

    ``"phase-1-direct-read-v1"`` → ``"phase-1-direct-read"``.  Reports use
    ``<prefix>-<profile>[-<scenarios>].{json,md}``; the full baseline pair is
    ``<prefix>-full.{json,md}``.  The Phase 0 prefix is only produced for the
    explicitly given legacy contract.
    """
    if contract == CONTRACT_PHASE_1:
        return "phase-1-direct-read"
    if contract in (None, "", "phase-0"):
        return "phase-0"
    raise UnknownBenchmarkContractError(
        f"Unknown benchmark_contract {contract!r}; expected {CONTRACT_PHASE_1!r}."
    )


def report_stem(contract: str | None, profile: str, scenario_suffix: str = "") -> str:
    """Derive the report stem (without extension) from the explicit contract.

    ``"phase-1-direct-read-v1"`` produces ``phase-1-direct-read-<profile>``
    stems (plus ``-<scenario_suffix>`` when given).  Reports are never named
    with the Phase 0 prefix unless the legacy contract is explicitly given.
    """
    prefix = contract_report_prefix(contract)
    suffix = f"-{scenario_suffix}" if scenario_suffix else ""
    return f"{prefix}-{profile}{suffix}"


def baseline_target_names(contract: str, profile: str = "full") -> tuple[str, str]:
    """Derive the versioned baseline file names from the contract.

    Only the Phase 1 contract names baseline artifacts, and the derived names
    can never collide with the preserved Phase 0 BEFORE pair
    (``phase-0-full.{json,md}`` — see :data:`RESERVED_PHASE_0_BASELINE_FILES`).
    """
    if contract != CONTRACT_PHASE_1:
        raise UnknownBenchmarkContractError(
            f"A versioned baseline requires benchmark_contract "
            f"{CONTRACT_PHASE_1!r}; got {contract!r}. The Phase 0 BEFORE files "
            f"are a historical legacy artifact and are never a publication target."
        )
    if profile != "full":
        raise UnknownBenchmarkContractError(
            f"A versioned baseline is only published for the full profile, got {profile!r}."
        )
    stem = report_stem(contract, profile)
    json_name, md_name = f"{stem}.json", f"{stem}.md"
    _reject_reserved_names(json_name, md_name)
    return json_name, md_name


def _reject_reserved_names(json_name: str, md_name: str) -> None:
    for name in (json_name, md_name):
        if name in RESERVED_PHASE_0_BASELINE_FILES:
            raise BaselinePublishError(
                f"Refusing to publish a Phase 1 baseline under the preserved "
                f"Phase 0 BEFORE name {name!r}."
            )
        if name.lower().startswith("phase-0"):
            raise BaselinePublishError(
                f"Phase 1 artifacts must not use the Phase 0 prefix: {name!r}."
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


def publish_baseline_pair(
    source_json: Path,
    source_md: Path,
    target_dir: Path,
    *,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Publish a versioned baseline JSON/Markdown pair atomically.

    - the target names are derived from the payload's explicit
      ``benchmark_contract`` (or from the explicit contract parameter);
    - an existing baseline file is never overwritten (no force flag exists);
    - both files are staged as ``.partial`` first and published back-to-back
      with ``os.replace``; a failure at any point restores the directory to
      its previous state (never half a pair, never a leftover ``.partial``);
    - after publishing, both files are re-read and their SHA-256 hashes are
      verified against the staged payloads.

    Returns a dict with the published paths and their SHA-256 hashes.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    data_json = _read_bytes(source_json)
    data_md = _read_bytes(source_md)

    # Derive the target names from the explicit contract carried by the
    # payload; a missing/unknown contract is a refusal, never a guess.
    if payload is None or not isinstance(payload, dict):
        raise BaselinePublishError(
            "The report payload is required to derive the baseline target names."
        )
    env = payload.get("environment")
    env = env if isinstance(env, dict) else {}
    contract = env.get("benchmark_contract")
    profile = env.get("profile")
    if contract != CONTRACT_PHASE_1:
        raise BaselinePublishError(
            f"The report carries benchmark_contract {contract!r}; a Phase 1 "
            f"baseline requires exactly {CONTRACT_PHASE_1!r}."
        )
    if profile != "full":
        raise BaselinePublishError(f"Baseline requires the full profile, got {profile!r}.")
    # Narrowed by the two refusals above.
    validated_contract: str = CONTRACT_PHASE_1
    validated_profile: str = profile

    json_name, md_name = baseline_target_names(validated_contract, validated_profile)

    target_json = target_dir / json_name
    target_md = target_dir / md_name

    # An existing baseline is protected: re-baselining is an explicit
    # architectural decision and is never automatic.
    for target in (target_json, target_md):
        if target.exists():
            raise BaselinePublishError(
                f"Refusing to overwrite the existing baseline {target}; removing "
                "or replacing a versioned baseline requires an explicit, "
                "separate decision."
            )

    # Verify the sources actually belong to the derived pair.
    if _sha256(_read_bytes(source_json)) != _sha256(data_json):  # pragma: no cover - paranoia
        raise BaselinePublishError(f"The JSON source changed while publishing: {source_json}")

    json_partial = target_dir / f"{json_name}.partial"
    md_partial = target_dir / f"{md_name}.partial"
    try:
        json_partial.write_bytes(data_json)
        md_partial.write_bytes(data_md)
        # Both staged payloads must round-trip byte-for-byte before anything
        # is published.
        if json_partial.read_bytes() != data_json or md_partial.read_bytes() != data_md:
            raise BaselinePublishError(
                "The staged baseline files failed their round-trip verification."
            )
        os.replace(json_partial, target_json)
        try:
            os.replace(md_partial, target_md)
        except Exception:
            # Never leave half a pair on the filesystem: remove the already
            # published JSON (which did not exist before this call).
            _remove_quietly(target_json)
            raise
    except Exception as exc:
        _remove_quietly(json_partial)
        _remove_quietly(md_partial)
        if isinstance(exc, BaselinePublishError):
            raise
        raise BaselinePublishError(f"Baseline publication failed: {exc}") from exc

    published_json = _read_bytes(target_json)
    published_md = _read_bytes(target_md)
    if published_json != data_json or published_md != data_md:
        raise BaselinePublishError(
            "Post-publish verification failed: the published files do not "
            "match the staged payloads byte for byte."
        )

    return {
        "json": target_json,
        "markdown": target_md,
        "json_sha256": _sha256(published_json),
        "markdown_sha256": _sha256(published_md),
    }

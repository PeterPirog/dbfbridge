"""Deterministic per-run provenance document generator (F3A-BLK-01/09/10).

Writes a REAL JSON run-provenance document (strict whitelist contract
``dbfbridge-direct-write-run-provenance-v1``) for one calibration replica.
Called by the `direct-write-calibration.yml` workflow with GitHub contexts
passed through explicit environment variables / arguments; it never dumps
arbitrary environment variables, secrets, user paths or measurement payloads.

Source-context semantics (F3A-BLK-09):

- ``pull_request_merge_ref``: GitHub checked out the synthetic PR merge
  commit; ``branch_head_sha`` (the PR head) and ``base_sha`` are REQUIRED and
  MUST be valid 40-hex SHAs — the measured SHA is the merge ref, NOT the
  branch head.
- ``main_push``: the checked-out SHA IS the main branch head; therefore
  ``branch_head_sha`` MUST equal ``github_sha`` and ``base_sha`` MUST be
  omitted (None).
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from importlib.metadata import version as _dependency_version
from pathlib import Path
from typing import Any

INSTALL_RECIPE = 'pip install -e ".[dev]"'
_SOURCE_CONTEXTS = ("pull_request_merge_ref", "main_push")


def build_run_provenance(
    *,
    workflow_run_id: str,
    replica_id: str,
    github_sha: str,
    source_context: str,
    branch_head_sha: str | None,
    base_sha: str | None,
    runner_os: str,
    runner_arch: str,
) -> dict[str, Any]:
    """Deterministic run-provenance payload (F3A-BLK-10: runner contexts are
    explicit required inputs — GitHub's `runner.os`/`runner.arch` — while
    `sys_platform`/`machine` remain Python-observed facts)."""
    return {
        "provenance_contract": "dbfbridge-direct-write-run-provenance-v1",
        "provenance_contract_version": 1,
        "workflow_run_id": workflow_run_id,
        "replica_id": replica_id,
        "github_sha": github_sha,
        "source_context": source_context,
        "branch_head_sha": branch_head_sha,
        "base_sha": base_sha,
        "runner_os": runner_os,
        "runner_arch": runner_arch,
        "python_version": sys.version.split()[0],
        "python_implementation": platform.python_implementation(),
        "sys_platform": sys.platform,
        "machine": platform.machine(),
        "install_recipe": INSTALL_RECIPE,
        "dependencies": {
            "dbf": _dependency_version("dbf"),
            "dbfread": _dependency_version("dbfread"),
            "psutil": _dependency_version("psutil"),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--replica-id", required=True)
    parser.add_argument("--github-sha", required=True)
    parser.add_argument(
        "--source-context",
        required=True,
        choices=["pull_request_merge_ref", "main_push"],
    )
    parser.add_argument(
        "--branch-head-sha",
        required=True,
        help="PR head SHA for pull_request_merge_ref; github.sha for main_push",
    )
    parser.add_argument(
        "--base-sha",
        default=None,
        help="PR base SHA for pull_request_merge_ref; omitted for main_push",
    )
    parser.add_argument("--runner-os", required=True, help="GitHub runner.os context")
    parser.add_argument("--runner-arch", required=True, help="GitHub runner.arch context")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.source_context == "main_push":
        # F3A-BLK-06: main_push has NO PR head/base — the checked-out SHA is
        # the branch head; base_sha must be absent.
        if args.branch_head_sha != args.github_sha:
            parser.error(
                "main_push requires branch_head_sha to equal github_sha "
                "(pass github.sha as --branch-head-sha)"
            )
        if args.base_sha:
            parser.error("main_push must not carry a PR base_sha")
        base_sha = None
    else:
        # pull_request_merge_ref: both PR SHAs are REQUIRED.
        if not args.branch_head_sha or not args.base_sha:
            parser.error(
                "pull_request_merge_ref requires non-empty --branch-head-sha "
                "and --base-sha"
            )
        base_sha = args.base_sha
    payload = build_run_provenance(
        workflow_run_id=args.workflow_run_id,
        replica_id=args.replica_id,
        github_sha=args.github_sha,
        source_context=args.source_context,
        branch_head_sha=args.branch_head_sha,
        base_sha=base_sha,
        runner_os=args.runner_os,
        runner_arch=args.runner_arch,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print("run provenance written to " + str(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

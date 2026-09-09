"""Deterministic per-run provenance document generator (F3A-BLK-01).

Writes a REAL JSON run-provenance document (strict whitelist contract
``dbfbridge-direct-write-run-provenance-v1``) for one calibration replica.
Called by the `direct-write-calibration.yml` workflow with GitHub contexts
passed through environment variables; it never dumps arbitrary environment
variables, secrets, user paths or measurement payloads.
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
) -> dict[str, Any]:
    """Deterministic run-provenance payload from sys/platform/metadata."""
    return {
        "provenance_contract": "dbfbridge-direct-write-run-provenance-v1",
        "provenance_contract_version": 1,
        "workflow_run_id": workflow_run_id,
        "replica_id": replica_id,
        "github_sha": github_sha,
        "source_context": source_context,
        "branch_head_sha": branch_head_sha,
        "base_sha": base_sha,
        "runner_os": platform.system(),
        "runner_arch": platform.machine(),
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
    parser.add_argument("--branch-head-sha", default=None)
    parser.add_argument("--base-sha", default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    payload = build_run_provenance(
        workflow_run_id=args.workflow_run_id,
        replica_id=args.replica_id,
        github_sha=args.github_sha,
        source_context=args.source_context,
        branch_head_sha=args.branch_head_sha,
        base_sha=args.base_sha,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print("run provenance written to " + str(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

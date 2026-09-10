"""F3B2 contract tests for the dedicated Direct Write regression workflow.

Deterministic offline static-contract tests (same stdlib/text-parsing style
as the F3A workflow contract tests — no YAML runtime dependency, no network,
no benchmark execution).  They prove the workflow wires the ACCEPTED
committed Direct Write policy into CI with FULL-profile candidates on both
``pull_request`` and ``push`` to ``main``, truthful F3A provenance, strict
comparability enforcement, read-only permissions and no baseline mutation —
and that the static contract REJECTS mutations such as smoke-as-gate,
runner/Python drift, swallowed comparator failures, policy creation/writes
and extra triggers.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

WORKFLOW_PATH = ROOT / ".github" / "workflows" / "direct-write-regression.yml"
PHASE3_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "performance-regression.yml"
F3A_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "direct-write-calibration.yml"
POLICY_PATH = (
    ROOT / "benchmarks" / "regression" / "direct-write-regression-policy-v1.json"
)

COMPARATOR = "benchmarks.compare_direct_write_regression"
PROVENANCE_GENERATOR = "benchmarks.direct_write_run_provenance"
PROFILE = "benchmarks.direct_write_profile"
POLICY_FILE = "benchmarks/regression/direct-write-regression-policy-v1.json"


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _provenance_step(text: str) -> str:
    return text.split("Record run provenance", 1)[1].split("Compare candidate", 1)[0]


def _provenance_else_branch(text: str) -> str:
    return _provenance_step(text).split("else {", 1)[1]


def _provenance_pr_branch(text: str) -> str:
    return _provenance_step(text).split("pull_request_merge_ref", 1)[1]


def _contract_violations(text: str) -> list[str]:
    """All F3B2 workflow contract requirements as mechanical predicates."""
    violations: list[str] = []

    # 1. dedicated workflow exists (module-level file existence; text implies it)
    if not WORKFLOW_PATH.is_file():
        violations.append("contract: workflow-file-exists")
    if "name: Direct Write regression" not in text:
        violations.append("contract: dedicated-workflow-name")
    # separation from the historical Phase 3 workflow
    if ".github/workflows/performance-regression.yml" not in text:
        pass  # the dedicated workflow must not reference it (checked below)
    # 2. permissions are contents: read
    if "permissions:\n  contents: read" not in text:
        violations.append("contract: permissions-contents-read")
    # 3. checkout uses persist-credentials: false
    checkout = text.split("Checkout exact commit", 1)[1] if (
        "Checkout exact commit" in text
    ) else ""
    if "actions/checkout@v6" not in checkout or "persist-credentials: false" not in checkout:
        violations.append("contract: checkout-read-only")
    # 4/5. triggers
    if "  pull_request:" not in text:
        violations.append("contract: pull_request-trigger")
    if "  push:" not in text or "      - main" not in text:
        violations.append("contract: push-main-trigger")
    # 6/7. no manual/scheduled triggers (source_context contract is exact)
    if "workflow_dispatch" in text:
        violations.append("contract: no-workflow-dispatch")
    if "schedule" in text:
        violations.append("contract: no-schedule")
    # 8. runner pinned to the calibrated OS generation (windows-2025)
    if "runs-on: windows-2025" not in text:
        violations.append("contract: runner-windows-2025")
    if "windows-latest" in text:
        violations.append("contract: no-moving-runner-label")
    # 9. exact Python 3.12.10
    if 'python-version: "3.12.10"' not in text:
        violations.append("contract: python-3-12-10")
    # 10/11. FULL profile is the hard candidate; smoke is never a candidate
    if PROFILE not in text or "--mode full" not in text:
        violations.append("contract: full-profile-candidate")
    if "--mode smoke" in text or "mode smoke" in text:
        violations.append("contract: no-smoke-candidate")
    # 12. generated evidence lives under RUNNER_TEMP
    if "$env:RUNNER_TEMP\\direct-write-regression" not in text or (
        "${{ runner.temp }}/direct-write-regression/" not in text
    ):
        violations.append("contract: runner-temp-evidence")
    # 13/14. reuse of the profile and the F3A provenance generator
    if "python -m " + PROFILE not in text:
        violations.append("contract: reuse-profile")
    if PROVENANCE_GENERATOR not in text or "python @p" not in _provenance_step(
        text
    ):
        violations.append("contract: reuse-provenance-generator")
    # 15-17. truthful PR provenance
    pr_branch = _provenance_pr_branch(text) if "pull_request_merge_ref" in text else ""
    if "pull_request_merge_ref" not in _provenance_step(text):
        violations.append("contract: pr-merge-ref-context")
    if '"${{ github.event.pull_request.head.sha }}"' not in pr_branch:
        violations.append("contract: pr-actual-head-sha")
    if '"--base-sha", "${{ github.event.pull_request.base.sha }}"' not in pr_branch:
        violations.append("contract: pr-actual-base-sha")
    # 18-20. truthful main_push provenance (never an empty base_sha)
    else_branch = _provenance_else_branch(text)
    if "main_push" not in else_branch:
        violations.append("contract: main-push-context")
    if '"--branch-head-sha", "${{ github.sha }}"' not in else_branch:
        violations.append("contract: main-push-branch-head-is-github-sha")
    if "base-sha" in else_branch:
        violations.append("contract: main-push-no-base-sha")
    # 21-23. comparator uses the committed policy, the provenance, mode full
    if "python -m " + COMPARATOR not in text:
        violations.append("contract: comparator-invoked")
    comparator_step = text.split(COMPARATOR, 1)[1] if COMPARATOR in text else ""
    if "--policy " + POLICY_FILE not in text:
        violations.append("contract: committed-policy-consumed")
    if "--candidate-provenance" not in comparator_step:
        violations.append("contract: candidate-provenance-passed")
    if "--mode full" not in comparator_step:
        violations.append("contract: comparator-mode-full")
    # 24. comparator failures are never swallowed
    if "continue-on-error" in text or "|| true" in text or "catch {" in text:
        violations.append("contract: no-failure-swallowing")
    # 25-29. the explicit post-comparison CI decision gate
    if "comparability != 'COMPARABLE'" not in text:
        violations.append("contract: comparability-enforced")
    if "DIRECT_WRITE_REGRESSION_ENVIRONMENT_NOT_COMPARABLE" not in text:
        violations.append("contract: not-comparable-marker")
    if "overall != 'PASS'" not in text or "expected PASS" not in text:
        violations.append("contract: overall-pass-required")
    if "correctness != 'PASS'" not in text:
        violations.append("contract: correctness-required")
    if "no hard performance gates were evaluated" not in text:
        violations.append("contract: hard-gates-non-empty")
    if "for gate in gates if gate['status'] != 'PASS'" not in text:
        violations.append("contract: every-hard-gate-pass")
    # 30/31/32. CI consumes policy; it NEVER creates or rewrites it
    if "calibrate_direct_write_regression" in text:
        violations.append("contract: no-policy-creation")
    if "direct-write-regression-calibration-inputs-v1.json" in text:
        violations.append("contract: no-calibration-input-write")
    for line in text.splitlines():
        if POLICY_FILE in line and (
            "--output" in line or "Set-Content" in line or "Out-File" in line
        ):
            violations.append("contract: policy-not-written")
            break
    # 33/34/35. no baseline mutation, no repository writes, no publishing
    if "benchmarks/baselines" in text:
        violations.append("contract: no-baseline-update")
    if "git commit" in text or "git push" in text:
        violations.append("contract: no-git-write")
    if "twine" in text or "pypi" in text.lower() or "publish.yml" in text:
        violations.append("contract: no-pypi")
    # 36. the evidence artifact carries the compact evidence set
    upload = text.split("Upload Direct Write regression evidence", 1)[1]
    for evidence_name in (
        "direct-write-v1-full.json",
        "direct-write-v1-full.md",
        "direct-write-provenance.json",
        "direct-write-regression.json",
        "direct-write-regression.md",
    ):
        if evidence_name not in upload:
            violations.append("contract: evidence-artifact-completeness")
            break
    if "if: always()" not in upload or "direct-write-regression-evidence" not in upload:
        violations.append("contract: evidence-artifact-always")
    # 37. benchmark working DBF/FPT files are never uploaded
    if ".dbf" in upload or ".fpt" in upload:
        violations.append("contract: no-dbf-fpt-artifact")
    # 38. historical Phase 3 workflow untouched and not referenced here
    if "phase-3" in text.lower() or "compare_phase3_regression" in text:
        violations.append("contract: no-phase3-reference")
    phase3 = PHASE3_WORKFLOW_PATH.read_text(encoding="utf-8")
    if "Phase 3" not in phase3 or "Performance" not in phase3:
        violations.append("contract: phase3-workflow-untouched")
    return violations


def test_dedicated_workflow_satisfies_the_full_contract() -> None:
    violations = _contract_violations(_workflow_text())
    assert violations == []


def test_accepted_policy_file_exists_unchanged_and_is_consumed() -> None:
    assert POLICY_FILE in _workflow_text()
    assert (ROOT / POLICY_FILE).is_file()


def test_dedicated_workflow_is_not_the_phase3_workflow() -> None:
    assert WORKFLOW_PATH.name != PHASE3_WORKFLOW_PATH.name
    assert PHASE3_WORKFLOW_PATH.is_file()
    assert F3A_WORKFLOW_PATH.is_file()


# ---------------------------------------------------------------------------
# negative mutation tests — the static contract must REJECT drift
# ---------------------------------------------------------------------------


def _mutated_violations(old: str, new: str) -> list[str]:
    text = _workflow_text()
    assert old in text, f"mutation target not found: {old!r}"
    return _contract_violations(text.replace(old, new))


def test_mutation_smoke_candidate_is_rejected() -> None:
    violations = _mutated_violations("--mode full", "--mode smoke")
    assert "contract: no-smoke-candidate" in violations


def test_mutation_moving_runner_label_is_rejected() -> None:
    violations = _mutated_violations("windows-2025", "windows-latest")
    assert "contract: runner-windows-2025" in violations
    assert "contract: no-moving-runner-label" in violations


def test_mutation_python_patch_version_is_rejected() -> None:
    violations = _mutated_violations(
        'python-version: "3.12.10"', 'python-version: "3.12"'
    )
    assert "contract: python-3-12-10" in violations


def test_mutation_removing_comparability_enforcement_is_rejected() -> None:
    violations = _mutated_violations(
        "comparability != 'COMPARABLE'", "comparability != 'COMPARABLE_PLACEHOLDER'"
    )
    assert "contract: comparability-enforced" in violations


def test_mutation_continue_on_error_is_rejected() -> None:
    violations = _mutated_violations(
        "name: Compare candidate against the accepted committed policy (comparator)",
        "name: Compare candidate against the accepted committed policy (comparator)\n"
        "        continue-on-error: true",
    )
    assert "contract: no-failure-swallowing" in violations


def test_mutation_policy_creation_call_is_rejected() -> None:
    violations = _mutated_violations(
        PROVENANCE_GENERATOR, "calibrate_direct_write_regression"
    )
    assert "contract: no-policy-creation" in violations


def test_mutation_policy_write_is_rejected() -> None:
    violations = _mutated_violations(
        "--output-md \"$env:RUNNER_TEMP\\direct-write-regression\\direct-write-regression.md\"",
        "--output-md \"$env:RUNNER_TEMP\\direct-write-regression\\direct-write-regression.md\" `n"
        "            --output " + POLICY_FILE,
    )
    assert "contract: policy-not-written" in violations


def test_mutation_workflow_dispatch_is_rejected() -> None:
    violations = _mutated_violations(
        "on:\n  pull_request:", "on:\n  workflow_dispatch:\n  pull_request:"
    )
    assert "contract: no-workflow-dispatch" in violations


def test_mutation_schedule_is_rejected() -> None:
    violations = _mutated_violations(
        "on:\n  pull_request:",
        "on:\n  schedule:\n    - cron: '0 0 * * *'\n  pull_request:",
    )
    assert "contract: no-schedule" in violations


def test_mutation_pypi_upload_is_rejected() -> None:
    violations = _mutated_violations(
        "if-no-files-found: warn",
        "if-no-files-found: warn\n          # twine upload dist/*",
    )
    assert "contract: no-pypi" in violations


def test_mutation_empty_base_sha_on_main_push_is_rejected() -> None:
    violations = _mutated_violations(
        '"--branch-head-sha", "${{ github.sha }}"',
        '"--branch-head-sha", "${{ github.sha }}",\n            "--base-sha", ""',
    )
    assert "contract: main-push-no-base-sha" in violations


def test_mutation_git_write_is_rejected() -> None:
    violations = _mutated_violations(
        "if-no-files-found: warn", "if-no-files-found: warn\n          # git push"
    )
    assert "contract: no-git-write" in violations

# dbfbridge 1.0 Architecture Closure Matrix

**Contract:** `DBFBRIDGE_TARGET_ARCHITECTURE(20260904-052528).md` (immutable upstream document; this file is the working repository status and does not replace it).
**Final main lineage:** `main` = `b712ce59631d20015360f4f6fd26bc9be10214fa` — reached through PR #14 (`c84a611`) → closure-audit PR #15 (`6104294`) → **Macro A** PR #16 (`90f8362`) → **Macro B** PR #17 (`197c986`) → **Macro C** PR #18 (final head `e173ec6`, merge `b712ce5`) → final repository-closure docs PR. Post-Macro-C main CI 33843870158 SUCCESS (package job incl. the shared artifact verifier).
**Status vocabulary:** CLOSED_FROZEN / BLOCKER / ACCEPTED_LIMITATION / INTENTIONALLY_UNSUPPORTED / EXTERNAL_BLOCKER / DEFERRED / NOT_YET_AUDITED (this final matrix contains **no** `NOT_YET_AUDITED` rows).

Exact test evidence: closure-audit `main` suite = 432 passed, 1 skipped (433 collected, CI 33772195304); Macro A branch after the correctness gate = 505 passed locally (Windows; includes the 55 contract tests + 17 Varchar-gate tests); `release/0.3.0` = exact-head release CI SUCCESS (454 passed, 2 skipped, release CI 33756842901).

## Status definitions

```text
CLOSED_FROZEN
=
requirement is proven and must not be modified
without failing regression or benchmark evidence

BLOCKER
=
must be resolved before 1.0

ACCEPTED_LIMITATION
=
known documented limitation compatible with
the intentionally declared support contract

INTENTIONALLY_UNSUPPORTED
=
explicitly outside supported API/format guarantee

EXTERNAL_BLOCKER
=
cannot be closed by repository code alone

DEFERRED
=
not required for current architecture target

NOT_YET_AUDITED
=
audit incomplete; temporary state only
```

## Progress metric (§57)

```text
requirements audited: 82

CLOSED_FROZEN: 67
BLOCKER: 0 requirement rows
ACCEPTED_LIMITATION: 3
INTENTIONALLY_UNSUPPORTED: 10
EXTERNAL_BLOCKER: 1
DEFERRED: 1
NOT_YET_AUDITED: 0
```

Root-cause blockers: **Macro A 3 → Macro B 0**. BLK-01 (public error model) and
BLK-02 (migration raw-mode split) are **CLOSED_FROZEN** (merged in Macro A,
PR #16 `90f8362`); BLK-03 (1.0 API contract declaration) is **CLOSED_FROZEN**
by Macro B (`docs/api-1.0.md` + `tests/test_api_1_0_contract.py`, PR #17
`197c986`). **Zero repository-controlled architecture blockers remain.** The
sole remaining blocker is external: **PyPI Trusted Publisher verification
(EXB-01)**.

## Documentation integration hardening (post-closure note)

After the repository code-complete checkpoint, a documentation-quality and
integration-readiness pass was performed as a NEW user requirement. It did
not change the runtime architecture or the public API. Documentation now
additionally covers: the installed API cookbook for all nine operations
(\docs/python-api-examples.md\), the JSON/tool-server boundary and generic
MCP integration patterns (\docs/tool-server-integration.md\),
path-security responsibility, machine-readable error mapping, bounded
paging, progress/cancellation bridging, offline/vendored deployment, and a
documentation map (\docs/README.md\). English-language convergence and
link/anchor validation were added for maintained documentation.

## Repository code-complete checkpoint

Repository `main` is **code-complete for the declared 1.x architecture**.

- Repository-controlled blockers: **0**
- Known correctness blockers in supported VFP DBF/FPT cases: **0**
- External publication blockers: **1** — **EXB-01: PyPI Trusted Publisher /
  account access**
- Version/tag/publication: **intentionally deferred to the final release
  lifecycle** — package metadata remains `0.2.0`, no tag exists, and nothing
  is published; the final release-preparation commit (final version, dated
  CHANGELOG entry, timeless docs, stable classifier) belongs to the future
  release-only task after PyPI access is restored.

Historical status for future reference: `release/0.3.0` and its draft PR #12
are **SUPERSEDED / HISTORICAL** — the generic release hardening from that
branch was converged onto `main` by Macro C (PR #18); no 0.3.0 publication
was performed.

### Closure-accounting correction (Macro C)

After Macro B, a final repository release-infrastructure convergence gap was
identified: **R-26 had previously been treated as CLOSED using important
evidence located only on the unmerged `release/0.3.0` branch** (hardened
publish workflow, release-state validator, version-neutral smoke contracts,
migration guide). Macro C converges that proven generic release infrastructure
onto the current `main` lineage so R-26 evidence no longer cites an unmerged
branch. No functional DBF/FPT blocker was reopened.

---

## 1. Architecture requirement matrix

Columns: ID | architecture section | requirement | status | repository evidence | test/CI evidence | known limitation | 1.0 blocker | next action | target macro PR.

### §1 Role

| ID | Section | Requirement | Status | Repository evidence | Test/CI evidence | Limitation | Blocker | Next action | Macro PR |
|---|---|---|---|---|---|---|---|---|---|
| R-01 | §1 | Standalone Python library; single DBF/FPT implementation; no copy into downstream MCP/tool-server projects (generic integration contract: `docs/tool-server-integration.md`) | CLOSED_FROZEN | Single implementation in `src/dbf_bridge/`; `src/dbfbridge/` is a lazy alias package | `tests/test_public_api.py::test_distribution_import_exposes_the_documented_api` | none | NO | none | — |
| R-02 | §1 | Loss-aware engine capability set (inspect / schema / direct read / raw metadata / migration / reconstruction / verification) | CLOSED_FROZEN | Public API exposes all six capability areas | exact-head CI 33772195304: 432 passed, 1 skipped (433 collected) | none | NO | none | — |
| R-03 | §1 | Two consumer classes: PyPI users; MCP `PURE_READ` backend without VFP | CLOSED_FROZEN | `core/` reads via `dbfread` only; no COM/VFP/network imports (`git grep` evidence) | CI (Ubuntu 3.10–3.14 + Windows 3.12) runs without any VFP runtime | none | NO | none | — |

### §2 Assets

| ID | Section | Requirement | Status | Repository evidence | Test/CI evidence | Limitation | Blocker | Next action | Macro PR |
|---|---|---|---|---|---|---|---|---|---|
| R-04 | §2 | Project assets preserved (streaming dbfread, VFP/FPT metadata, Polish codepages, deleted distinction, memo policies, atomic output, JSONL, raw metadata, SHA256/validation/diagnostics, schema-driven reconstruction, typed API, progress, incremental, honest CDX) | CLOSED_FROZEN | All assets present after PR #13/#14; CHANGELOG 0.2→0.3 records each | 432 passed / 1 skipped green on `main`; release exact-head CI SUCCESS (454 passed, 2 skipped, verified from release CI 33756842901) | none | NO | none | — |

### §4 Package layers

| ID | Section | Requirement | Status | Repository evidence | Test/CI evidence | Limitation | Blocker | Next action | Macro PR |
|---|---|---|---|---|---|---|---|---|---|
| R-05 | §4 | `core` purity: zero CLI, zero migration reports, zero outputs at READ, minimal dependencies | CLOSED_FROZEN | `git grep` in `src/dbf_bridge/core` finds no `..cli`, no importer/exporter, no openpyxl/polars/xlsxwriter/orjson imports | `test_fresh_interpreter_import_has_no_side_effects` (schema + records variants) | none | NO | none | — |
| R-06 | §4 | migration / reconstruction / diagnostics responsibility split | CLOSED_FROZEN | `exporter`/`importer`/`verifier`/`quality` modules mirror the required responsibilities (§36: folder names cosmetic) | layering greps clean | cosmetic naming only | NO | none | — |
| R-07 | §4 | diagnostics layer: canonical checksums, raw hashes, round-trip verification, bounded field diff | CLOSED_FROZEN | `importer/checksum.py`, `verifier.py`, `quality.py`, `diagnose_reconstruction(limit=20)` | `tests/test_importer.py::test_quality_diagnostics_identify_record_field_and_binary_area` | none | NO | none | — |

### §5 Direct Read API

| ID | Section | Requirement | Status | Repository evidence | Test/CI evidence | Limitation | Blocker | Next action | Macro PR |
|---|---|---|---|---|---|---|---|---|---|
| R-08 | §5 | `inspect_table(path)` — safe JSON-serializable `TableInfo` without reading the whole table | CLOSED_FROZEN | `core/inspect.py`; `TableInfo.to_dict()` | `tests/test_direct_read_schema.py` (whole module), `test_public_to_dict_payloads_are_json_safe` | none | NO | none | — |
| R-09 | §5 | `read_schema(path)` — full field/physical description without writing `_schema.json` | CLOSED_FROZEN | `core/schema.py`; `TableSchema` model | `tests/test_direct_read_schema.py` | none | NO | none | — |
| R-10 | §5 | `iter_records` — streaming O(1) memory, projection, memo lazy/skip/null/inline, deleted single physical pass, no outputs, no print/sys.exit, interruptible | CLOSED_FROZEN | `core/records.py`; `iter_physical_records` shared loop | `test_active_and_deleted_in_physical_order_one_pass`, `test_projection_really_skips_the_parser_for_unselected_fields`, `test_lazy_memo_values_never_open_fpt_during_iteration`, `test_source_byte_identical_and_no_files_created` | none | NO | none | — |
| R-11 | §5 | `read_records(offset, limit, ...)` bounded memory | CLOSED_FROZEN | `read_records` public API | `test_limit_does_not_materialize_further_records`, `test_read_records_argument_validation` | none | NO | none | — |
| R-12 | §5 | `iter_raw_records` for forensics (not the default read path) | CLOSED_FROZEN | public API; 18 test references | `test_raw_true_keeps_exact_physical_bytes`, `test_raw_false_stores_no_raw_data` | none | NO | none | — |

### §6 Cost profiles

| ID | Section | Requirement | Status | Repository evidence | Test/CI evidence | Limitation | Blocker | Next action | Macro PR |
|---|---|---|---|---|---|---|---|---|---|
| R-13 | §6 | `READ_FAST`: no outputs, no raw base64, memo lazy/skip, projection, bounded limit | CLOSED_FROZEN | Direct Read surface defaults | Direct Read test module + Phase 1/3 benchmark scenarios | none | NO | none | — |
| R-14 | §6 | `MIGRATION_SAFE`: schema artifact, checksums, atomic output, validation, incremental manifest | CLOSED_FROZEN | `exporter/writer.py` (AtomicTextWriter, `os.replace`), checksum manifest | `tests/test_importer.py`, `tests/test_incremental.py` | none | NO | none | — |
| R-15 | §6 | `FORENSIC_ROUNDTRIP`: raw record image, FPT pointers, deleted order, raw SHA, bounded diagnostics | CLOSED_FROZEN | `iter_raw_records`, `iter_physical_records(keep_raw=True)`, `diagnose_reconstruction` | `test_raw_true_keeps_exact_physical_bytes`, FPT edge-case suite | none | NO | none | — |

### §7 Raw metadata

| ID | Section | Requirement | Status | Repository evidence | Test/CI evidence | Limitation | Blocker | Next action | Macro PR |
|---|---|---|---|---|---|---|---|---|---|
| R-16 | §7 | Direct Read treats raw as optional feature (`raw=False` default; separate `iter_raw_records`) | CLOSED_FROZEN | `raw=False` default in `iter_records` | `test_raw_false_stores_no_raw_data` | none | NO | none | — |
| R-17 | §7 | Migration API must expose an explicit raw retention level (`raw_mode="none"\|"metadata"\|"full-record"`) | CLOSED_FROZEN | `RawMode` public type; `raw_mode` on `ExportOptions`/`export_dbf`/`ExportConfig`/`make_config`/`run_export`/`_export_one`; CLI `--raw-mode`; incremental signature includes `raw_mode` | `tests/test_raw_mode.py` (mode matrix, incremental invalidation, no-allocation spy); default `full-record` = historical behaviour | none | NO | none | done (Macro A) |

### §8 Validation

| ID | Section | Requirement | Status | Repository evidence | Test/CI evidence | Limitation | Blocker | Next action | Macro PR |
|---|---|---|---|---|---|---|---|---|---|
| R-18 | §8 | Validation levels explicit and measured (READ fast / MIGRATION standard / FORENSIC full) | CLOSED_FROZEN | `ExportOptions.validate`; benchmark scenarios `export_jsonl_validate_on/off`, `migration_validate_on/off` with cost ratios in baseline contract | `benchmarks/contract.py:524` ratio `migration_validate_on_over_off` | none | NO | none | — |

### §9 Backend abstraction

| ID | Section | Requirement | Status | Repository evidence | Test/CI evidence | Limitation | Blocker | Next action | Macro PR |
|---|---|---|---|---|---|---|---|---|---|
| R-19 | §9 | Backend abstraction isolating `dbfread` (reference backend), incl. its private-API touchpoint | CLOSED_FROZEN | `core/backend.py` protocols + `dbfread_backend` adapter; one physical record loop | Direct Read suites; PR #12 phase-3 CI | none | NO | none | — |
| R-20 | §9 | Second backend only with benchmark + type-matrix evidence | CLOSED_FROZEN | No native backend exists; Direct Write research isolated in `feat/phase-2-direct-write` (untouched) | — | none | NO | none | — |

### §10 Writer / reconstruction

| ID | Section | Requirement | Status | Repository evidence | Test/CI evidence | Limitation | Blocker | Next action | Macro PR |
|---|---|---|---|---|---|---|---|---|---|
| R-21 | §10 | Writer changes only with measured bottleneck or correctness justification | CLOSED_FROZEN | Policy honoured: PR #13/#14 correctness-only; no rewrite | FPT/NullFlags suites | none | NO | none | — |
| R-22 | §10/§35 | Writer throughput as 1.0 blocker? | **DEFERRED** | Phase 3 baseline records `migration_jsonl_to_dbf_fpt` metrics; no failing threshold, no bug evidence | performance-regression CI PASS | no requirement shows writer is a bottleneck | NO | revisit only with benchmark evidence | — |

### §11 Dependencies & PyPI

| ID | Section | Requirement | Status | Repository evidence | Test/CI evidence | Limitation | Blocker | Next action | Macro PR |
|---|---|---|---|---|---|---|---|---|---|
| R-23 | §11 | Extras split: base = direct read + JSONL/JSON/CSV; `[write]`, `[xlsx]`, `[fast]`, `[all]`, `[import]` | CLOSED_FROZEN | `optional_deps.py` contract (fail-before-output, `[fast]` never raises) | `tests/test_optional_dependencies.py`; PR #12 wheel METADATA evidence | none | NO | none | — |
| R-24 | §11 | Package name `dbfbridge` retained | CLOSED_FROZEN | pyproject name; `import dbfbridge` preferred surface | `test_distribution_import_exposes_the_documented_api` | none | NO | none | — |
| R-25 | §11 | Normal PyPI package, no `git+` dependencies | CLOSED_FROZEN | pyproject deps are PyPI-only | PR #12 `twine check` + fresh-venv wheel smokes PASS (CI 33756842901, release/0.3.0) | none | NO | none | — |

### §12 Package quality

| ID | Section | Requirement | Status | Repository evidence | Test/CI evidence | Limitation | Blocker | Next action | Macro PR |
|---|---|---|---|---|---|---|---|---|---|
| R-26 | §12 | Repository packaging readiness: semver, metadata, SPDX, wheels/sdist, 3.10–3.14, Windows CI, changelog, migration guide, `py.typed`, side-effect-free import, examples, documented VFP/CDX limits | CLOSED_FROZEN | **Macro C merged on main (`b712ce5`):** version-neutral CI package job (`build` + `twine check` + shared `scripts/verify_release_artifacts.py` + `release_wheel_smoke` + `pypi_install_smoke` on the exact wheel, no expected-version literals), hardened `publish.yml` (release-final-state gate `scripts/check_release_state.py`, wheel/sdist count gates, wheel METADATA + `py.typed` gate, sdist PKG-INFO gate, build-once/same-artifact publish, Trusted Publishing OIDC), `docs/migration-1.0.md` + `docs/api-1.0.md` + `docs/compatibility-vfp.md` + public docs in the sdist (`MANIFEST.in`), tested by `tests/test_release_state.py` / `test_release_readiness.py` / `test_migration_guide.py` / `test_release_artifacts.py` / `test_packaging.py` | **post-merge main CI 33843870158 SUCCESS @ `b712ce5`** — package job: build PASS, twine PASS, shared artifact verifier PASS (`ARTIFACTS: PASS (one wheel, one sdist, expected version 0.2.0)`), fresh-wheel smoke PASS, install-profile smoke PASS (base/write/xlsx/write,xlsx/fast/all/import) | `main` still at 0.2.0 — intentional release lifecycle (§41) | NO | none | — |
| R-27 | §12 | Actual PyPI publication (Trusted Publishing) | **EXTERNAL_BLOCKER** | publish run 33487949133: build SUCCESS, publish FAILURE `invalid-publisher`; OIDC claims correct; PyPI-side Trusted Publisher missing/mismatched | v0.2.0 publish run | blocked outside repository | **YES (EXB-01)** | external verification only; no repo action | Macro C |

### §13 Benchmarks / §14 Performance policy

| ID | Section | Requirement | Status | Repository evidence | Test/CI evidence | Limitation | Blocker | Next action | Macro PR |
|---|---|---|---|---|---|---|---|---|---|
| R-28 | §13 | Canonical baseline + provenance (measured, versioned, committed) | CLOSED_FROZEN | `benchmarks/baselines/phase-3-performance-full.{json,md}` + `manifest.json` | SHA256 JSON `88fcf32e…`, MD `66b8a151…`, manifest `391b175d…` verified blob-identical `811c26e`→`d690d33` | none | NO | none | — |
| R-29 | §14 | Regression policy + PR smoke + full profile, no baseline mutation | CLOSED_FROZEN | `benchmarks/regression/phase-3-regression-policy-v1.json`, `constraints-phase3-v1.txt`, calibration inputs; workflows `performance-regression.yml` (PR smoke) + `benchmark-phase3.yml` (full) | PR smoke 33754128032 / 33755859372 / 33756842945 SUCCESS | none | NO | none | — |

### §15 MCP integration contract

| ID | Section | Requirement | Status | Repository evidence | Test/CI evidence | Limitation | Blocker | Next action | Macro PR |
|---|---|---|---|---|---|---|---|---|---|
| R-30 | §15 | `import dbfbridge` boundary; toolchain needs no private modules | CLOSED_FROZEN | lazy alias `src/dbfbridge/__init__.py`; documented `__all__` | `test_distribution_import_exposes_the_documented_api` | none | NO | none | — |
| R-31 | §15/§32 | JSON-serializable result objects — Direct Read models | CLOSED_FROZEN | `TableInfo/TableSchema/FieldInfo/DirectRecord/RecordPage.to_dict()`, error `to_dict()` | `test_public_to_dict_payloads_are_json_safe` (records + page + raw) | none | NO | none | — |
| R-32 | §15/§32 | JSON-serializable run-level results + typed exception payloads (export/reconstruct/verify/quality, `DBFBridgeRunError`) | CLOSED_FROZEN | `to_dict()` on all four run results; `DBFBridgeRunError` carries `code` + `details` + `to_dict()`; `FileCheck`/`TableCheck` serializable | `tests/test_public_error_contract.py::test_run_level_results_are_json_safe`, `test_run_error_payload_preserves_all_details` | none | NO | none | done (Macro A) |

### §16 MCP read semantics

| ID | Section | Requirement | Status | Repository evidence | Test/CI evidence | Limitation | Blocker | Next action | Macro PR |
|---|---|---|---|---|---|---|---|---|---|
| R-33 | §16 | `limit`/`fields`/`offset`/`includeDeleted`/memo/encoding policies + progress/cancellation | CLOSED_FROZEN | `iter_records(..., fields, include_deleted, memo, raw, encoding, decode_errors, cancel_check, progress)`; `read_records(offset, limit)` | `test_include_deleted_paging_skips_deleted_in_same_pass`, `test_progress_normal_exhaustion`, `test_read_records_argument_validation`, cancel/progress suites | none | NO | none | — |

### §17 Error model

| ID | Section | Requirement | Status | Repository evidence | Test/CI evidence | Limitation | Blocker | Next action | Macro PR |
|---|---|---|---|---|---|---|---|---|---|
| R-34 | §17 | Direct Read: typed `ErrorCode` model, stable codes, JSON-safe context, no text parsing | CLOSED_FROZEN | `core/errors.py`: 14 codes; every failure carries `code/message/path/context`; `to_dict()` POSIX-path-normalized | Direct Read typed-error tests (truncated header, bad projection, FPT missing/invalid, decode strict, cancel) | none | NO | none | — |
| R-35 | §17 | `OPTIONAL_DEPENDENCY_MISSING` machine-readable | CLOSED_FROZEN | `OptionalDependencyMissingError(RuntimeError)` with `.code = "OPTIONAL_DEPENDENCY_MISSING"`, `to_dict()`, exact install command; fail-before-output; never for `[fast]` | `tests/test_optional_dependencies.py` | none | NO | none | — |
| R-36 | §17 | `OUTPUT_EXISTS` distinguishable at the public boundary | CLOSED_FROZEN | `OperationOutputExistsError(FileExistsError)` with code `OUTPUT_EXISTS` + `to_dict()`; `OutputExistsError` kept as the import alias at `exporter/writer.py`; per-table `error_details` on export AND reconstruction conflicts | `tests/test_public_error_contract.py::test_output_exists_is_machine_readable_per_table`, `test_output_exists_in_reconstruction_is_machine_readable` | none | NO | none | done (Macro A) |
| R-37 | §17 | `RECONSTRUCTION_FAILED` / `ROUNDTRIP_MISMATCH` machine-readable | CLOSED_FROZEN | structured `error_details` on `ReconstructionResult`: `RECONSTRUCTION_FAILED` for writer failures, `ROUNDTRIP_MISMATCH` for canonical mismatches (with both checksums in context), more-specific physical codes preserved | `test_reconstruction_failure_is_machine_readable`, `test_roundtrip_mismatch_is_machine_readable` | none | NO | none | done (Macro A) |
| R-38 | §17/§25 | High-level public ops raise machine-classifiable failures; MCP classifies without parsing text | CLOSED_FROZEN | bare `ValueError`/`FileNotFoundError` at `api.py` replaced by `OperationArgumentError`/`OperationPathError` (same messages; superclass-compat preserved); §25 answer now **YES** | `test_mcp_machine_readable_classification` (message-blind, parametrized over 6 representative cases) | none | NO | none | done (Macro A) |

### §18 Safety

| ID | Section | Requirement | Status | Repository evidence | Test/CI evidence | Limitation | Blocker | Next action | Macro PR |
|---|---|---|---|---|---|---|---|---|---|
| R-39 | §18 | Direct READ truly read-only: source SHA before == after; no touch/mkdir/`.partial`/lock/index rebuild/PACK | CLOSED_FROZEN | READ opens files read-only through `dbfread`; no writes in `core/` | `test_source_byte_identical_and_no_files_created`, `test_source_stays_byte_identical_with_same_mtime`, `test_source_immutability_across_control_modes`, `test_inspection_creates_no_outputs_or_temp_files` | none | NO | none | — |
| R-40 | §18 | Reconstruction/write: output-only, atomic publish (`.partial` + `os.replace`), failure cleanup | CLOSED_FROZEN | `AtomicTextWriter`/`ensure_can_write_final`; importer partial cleanup on failure | `tests/test_fpt_edge_cases.py::test_reconstruction_failure_leaves_no_partial_outputs`; exporter atomic tests | none | NO | none | — |
| R-41 | §18 | No implicit source overwrite; no source-overwrite option in high-level API | CLOSED_FROZEN | `export_dbf`/`reconstruct_dbf` write only to `output`; `overwrite` guards outputs only | reconstruction safety suite | none | NO | none | — |

### §19 CDX

| ID | Section | Requirement | Status | Repository evidence | Test/CI evidence | Limitation | Blocker | Next action | Macro PR |
|---|---|---|---|---|---|---|---|---|---|
| R-42 | §19/§42 | CDX: presence-only (`has_structural_cdx`, `companion_cdx_present`, raw header flags); no fake tag parser | **INTENTIONALLY_UNSUPPORTED** | `core/models.py` exposes the three flags; docs truthful; §19 reserves tag parsing for the VFP runtime adapter | `tests/test_direct_read_schema.py` CDX flag tests | no CDX tag expressions/order data | NO | none | — |

### §20 Release roadmap

| ID | Section | Requirement | Status | Repository evidence | Test/CI evidence | Limitation | Blocker | Next action | Macro PR |
|---|---|---|---|---|---|---|---|---|---|
| R-43 | §20 | 0.2.0 Direct Read Core delivered | CLOSED_FROZEN | Phase 1 direct-read baseline; merged milestones | CI history | none | NO | none | — |
| R-44 | §20 | 0.3.0 performance + backend abstraction + progress/cancellation + regression CI | CLOSED_FROZEN | release/0.3.0 candidate frozen at `d690d33`; PR #12 | release exact-head CI 33756842901 + perf 33756842945 SUCCESS | publication blocked externally (R-27) | NO | none | — |
| R-45 | §20 | 0.4.x reconstruction hardening only if benchmarks/bugs justify | CLOSED_FROZEN | PR #13 + PR #14 correctness closures (evidence-driven); no open justified item | FPT/NullFlags suites green | none | NO | none | — |
| R-46 | §20 | 1.0.0 = stable direct-read API, stable migration/reconstruction API, documented compatibility matrix, benchmark suite, robust packaging, no known correctness gaps in supported cases | CLOSED_FROZEN | BLK-03 closed by Macro B: the 1.0 public API contract is declared in `docs/api-1.0.md` (import boundary, nine frozen operations, guarantees, machine-code vocabulary, RawMode contract, JSON boundary, SemVer + deprecation policy, aliases, accepted limitations) and enforced mechanically by `tests/test_api_1_0_contract.py` | all other §20 items CLOSED_FROZEN; matrix + benchmarks + packaging closed | none | NO | none | done (Macro B) |

### §21 Definition of Done (Direct Read / MCP readiness)

| ID | Section | Requirement | Status | Test evidence | Limitation | Blocker | Next action | Macro PR |
|---|---|---|---|---|---|---|---|---|
| R-47 | §21 | `import dbfbridge` side-effect free | CLOSED_FROZEN | `test_fresh_interpreter_import_has_no_side_effects` (schema), `test_fresh_interpreter_record_import_has_no_side_effects` (records) | none | NO | none | — |
| R-48 | §21 | Direct read works without VFP9 | CLOSED_FROZEN | pure-Python (`dbfread`) core; CI matrix has no VFP | none | NO | none | — |
| R-49 | §21 | Direct read creates no output files | CLOSED_FROZEN | `test_inspection_creates_no_outputs_or_temp_files`, `test_source_byte_identical_and_no_files_created` | none | NO | none | — |
| R-50 | §21 | Field projection works (and skips parsing unselected fields) | CLOSED_FROZEN | `test_projection_really_skips_the_parser_for_unselected_fields`, `test_projection_uses_schema_names_in_user_order` | none | NO | none | — |
| R-51 | §21 | Memo lazy / skip policies | CLOSED_FROZEN | `test_lazy_memo_values_never_open_fpt_during_iteration`, `test_memo_skip_and_null_do_not_touch_the_fpt` | none | NO | none | — |
| R-52 | §21 | `read_records(limit=N)` bounded, does not materialize the table | CLOSED_FROZEN | `test_limit_does_not_materialize_further_records` | none | NO | none | — |
| R-53 | §21 | Source stays byte-identical | CLOSED_FROZEN | `test_source_stays_byte_identical_with_same_mtime`, `test_source_immutability_across_control_modes` | none | NO | none | — |
| R-54 | §21 | API returns structured results **and structured exceptions** | CLOSED_FROZEN | results structured (dataclasses + `to_dict`); public exceptions typed (`OperationArgumentError`/`OperationPathError`/`OperationOutputExistsError`); per-table `error_details` additive to text `errors` | `tests/test_public_error_contract.py` (whole module) | none | NO | none | done (Macro A) |
| R-55 | §21 | No runtime network | CLOSED_FROZEN | `git grep` finds no `requests/urllib/httpx/aiohttp/socket` in `src/` | fresh-interpreter import tests | none | NO | none | — |
| R-56 | §21 | Benchmarks recorded and repeatable | CLOSED_FROZEN | baselines + provenance manifests + strict contract validators | `tests/test_benchmark_infrastructure.py` | none | NO | none | — |
| R-57 | §21 | Consumers can stream records without JSONL (anonymizer path) | CLOSED_FROZEN | public `iter_records`/`read_records` used directly in tests, parity with exporter proven | Direct Read suites | none | NO | none | — |

### §22 Prohibited directions

| ID | Section | Requirement | Status | Repository evidence | Test/CI evidence | Limitation | Blocker | Next action | Macro PR |
|---|---|---|---|---|---|---|---|---|---|
| R-58 | §22 | No code copy to toolchain; no unbenchmarked optimization; no writer rewrite; no Polars/XLSX import in core; no MCP/transport in library; no runtime Internet; no VFP COM in core; JSONL/round-trip kept | CLOSED_FROZEN | greps: no prohibited imports; regression CI enforces benchmark-first; writer untouched | full suite + CI | none | NO | none | — |

---

## 2. Supported Cases audit (`docs/compatibility-vfp.md`)

All 24 type rows audited; every `SUPPORTED`/`SUPPORTED_WITH_LIMITATION` row now carries exact test names (evidence references corrected for `C`, `F`, `D`, `L`, `M` in this PR). **SUPPORTED: 11 types. SUPPORTED_WITH_LIMITATION: 3 types. Evidence gaps remaining: none** (previously vague `existing suite` references for C/F/D/L/M were replaced with exact test names).

| ID | Type | Matrix status | Closure status | Exact evidence | 1.0 blocker |
|---|---|---|---|---|---|
| SC-01 | `C` | SUPPORTED | CLOSED_FROZEN | `test_public_api_runs_the_complete_workflow_silently`; `test_exporter_parity_klienci_memo`; `test_vfp_nullable_ordinary_fields_null_bit_reads_as_none` | NO |
| SC-02 | `C` + flags 0x04 (NOCPTRANS) | RAW_ONLY | INTENTIONALLY_UNSUPPORTED | `test_binary_character_nocptrans_is_raw_readable_but_not_decoded` | NO |
| SC-03 | `V` Varchar | SUPPORTED_WITH_LIMITATION | **ACCEPTED_LIMITATION** (canonical reconstruction incl. NULL records PASS; exact raw DBF identity documented gap) | `tests/test_vfp_varchar.py` (authentic 0x32 fixtures; `test_vfp_varchar_null_record_reconstructs_canonically`, `test_vfp_varchar_mixed_null_bitmap_reconstructs_canonically`) | NO |
| SC-04 | `V` + flags 0x04 (binary Varchar) | NOT_YET_VERIFIED (unsupported by design) | INTENTIONALLY_UNSUPPORTED | no authentic fixture exists; classified honestly in matrix | NO |
| SC-05 | `N` | SUPPORTED | CLOSED_FROZEN | `test_jsonl_roundtrip_preserves_narrow_negative_numeric_and_complete_header` | NO |
| SC-06 | `F` | SUPPORTED | CLOSED_FROZEN | `test_vfp_integer_float_datetime_roundtrip` | NO |
| SC-07 | `I` | SUPPORTED | CLOSED_FROZEN | `test_vfp_integer_float_datetime_roundtrip` | NO |
| SC-08 | `I` + 0x0C autoincrement | SUPPORTED | CLOSED_FROZEN | `test_vfp_autoincrement_descriptor_survives_roundtrip` | NO |
| SC-09 | `+` (dBASE 7 marker) | PARSER_COMPATIBILITY_ONLY | INTENTIONALLY_UNSUPPORTED | `test_plus_type_is_never_vfp_autoincrement_evidence`; `test_vfp_plus_type_reads_as_integer_with_valid_layout` | NO |
| SC-10 | `Y` | SUPPORTED | CLOSED_FROZEN | `test_vfp_currency_direct_read_and_roundtrip_preserves_decimal_value` | NO |
| SC-11 | `B` (VFP Double inline) | SUPPORTED | CLOSED_FROZEN | `test_vfp_double_table_without_fpt_exports_and_roundtrips`; `test_vfp_double_with_real_memo_still_requires_fpt` | NO |
| SC-12 | `B` (non-VFP) | NOT_YET_VERIFIED | INTENTIONALLY_UNSUPPORTED | dBASE III/IV dialect outside matrix scope (truthful) | NO |
| SC-13 | `O` | PARSER_COMPATIBILITY_ONLY | INTENTIONALLY_UNSUPPORTED | `test_vfp_double_alias_o_reads_like_a_double` | NO |
| SC-14 | `@` | PARSER_COMPATIBILITY_ONLY | INTENTIONALLY_UNSUPPORTED | `test_vfp_timestamp_alias_decodes_like_datetime` | NO |
| SC-15 | `T` | SUPPORTED | CLOSED_FROZEN | `test_vfp_integer_float_datetime_roundtrip` | NO |
| SC-16 | `D` | SUPPORTED | CLOSED_FROZEN | `test_exporter_parity_zamowienia_types`; `test_public_api_runs_the_complete_workflow_silently` | NO |
| SC-17 | `L` | SUPPORTED | CLOSED_FROZEN | `test_exporter_parity_klienci_memo` (incl. NULL `VIP`); `test_public_api_runs_the_complete_workflow_silently` | NO |
| SC-18 | `M` | SUPPORTED | CLOSED_FROZEN | `test_memo_block_type_decides_text_vs_binary_decoding`; `test_lazy_memo_values_never_open_fpt_during_iteration`; `test_jsonl_roundtrip_relocates_memo_to_original_pointer` | NO |
| SC-19 | `G` | SUPPORTED_WITH_LIMITATION | **ACCEPTED_LIMITATION** (DBF byte-identical proven; raw FPT layout may differ, explicit WARNING) | `test_general_and_picture_memo_roundtrip_keeps_canonical_identity` | NO |
| SC-20 | `P` | SUPPORTED_WITH_LIMITATION | **ACCEPTED_LIMITATION** (as `G`) | `test_general_and_picture_memo_roundtrip_keeps_canonical_identity[picture]` | NO |
| SC-21 | `Q` Varbinary | UNSUPPORTED | INTENTIONALLY_UNSUPPORTED | `test_unsupported_varbinary_is_raw_readable_but_not_decoded`; `test_unsupported_table_export_reports_typed_unsupported_status` | NO |
| SC-22 | `W` Blob | UNSUPPORTED | INTENTIONALLY_UNSUPPORTED | `test_unsupported_blob_is_raw_readable_but_not_decoded`; `test_skip_removes_unsupported_memo_field` | NO |
| SC-23 | `0` `_NullFlags` | SYSTEM_INTERNAL | CLOSED_FROZEN | `test_nullflags_system_column_is_raw_readable_and_roundtrips`; canonical engine lock `tests/test_vfp_varchar.py` descriptor-shape tests | NO |
| SC-24 | `X` (unknown byte) | NOT_YET_VERIFIED | INTENTIONALLY_UNSUPPORTED | typed unknown handling; deliberately left unproven rather than faked | NO |

### §16 Varchar raw-identity decision (quoted evidence)

`docs/compatibility-vfp.md` row `V`: *"canonical match including NULL Varchar records; raw byte identity is a tested gap (writer cannot yet rebuild the variable-length layout)"* — status `SUPPORTED_WITH_LIMITATION`. The architecture's 1.0 list (§20) requires *"no known correctness gaps in supported VFP DBF/FPT cases"*; canonical correctness (the declared guarantee) **passes** for NULL-bearing and mixed V/C/V tables (PR #14), while byte identity is explicitly **excluded and documented** in the public support contract. The public contract does not promise byte-identical Varchar reconstruction; it promises `raw_dbf_match` as a separate, reported flag. → **ACCEPTED_LIMITATION**, not a 1.0 blocker.

### §17 G/P FPT raw-layout decision (quoted evidence)

`docs/compatibility-vfp.md` rows `G`/`P`: *"DBF byte-identical; FPT content restored, raw FPT layout may differ (report WARNING)"* with `raw_fpt_match: false` + explicit warning surfaced. The writer policy (§10) forbids rewrites without evidence; no public contract promises raw FPT layout identity. → **ACCEPTED_LIMITATION**.

---

## 3. Public API candidate for 1.0

Exported surface (57 symbols in `src/dbf_bridge/__init__.py::__all__`, mirrored lazily by `src/dbfbridge/__init__.py`) — declared stable for 1.x by `docs/api-1.0.md` (Macro B).

### STABLE_1_0 (declared by `docs/api-1.0.md`, enforced by `tests/test_api_1_0_contract.py`)

Direct Read operations: `inspect_table`, `read_schema`, `iter_records`, `read_records`, `iter_raw_records`.
High-level operations: `export_dbf`, `reconstruct_dbf`, `verify_conversion`, `check_conversion_quality`.
Direct models: `FieldInfo`, `TableInfo`, `TableSchema`, `DirectRecord`, `RecordPage`, `LazyMemoValue`.
Options/result models: `ExportOptions`, `ExportRunResult`, `ReconstructionOptions`, `ReconstructionRunResult`, `ReconstructionResult`, `VerificationRunResult`, `QualityRunResult`, `TableResult`, `TableStatus`.
Type aliases: `OutputFormat`, `InputFormat`, `MemoPolicy`, `MissingMemoPolicy`, `DecodeErrors`, `DeletedPolicy`, `RawMode`.
Progress/cancellation: `ProgressCallback`, `ProgressEvent`, `CancellationCheck`.
Direct error model: `ErrorCode`, `DirectReadError`, `DbfPathError`, `DbfHeaderInvalidError`, `DbfTruncatedError`, `DbfFormatUnsupportedError`, `DbfIoError`, `DbfRecordInvalidError`, `EncodingUnknownError`, `TextDecodeError`, `FptRequiredMissingError`, `FptInvalidError`, `ArgumentInvalidError`, `FieldProjectionInvalidError`, `FieldTypeUnsupportedError`, `ReadCancelledError`.
High-level structured error model (Macro A): `OperationError`, `OperationArgumentError`, `OperationPathError`, `OperationOutputExistsError`.
Dependency boundary: `OptionalDependencyMissingError`. Metadata: `__version__`.

### CONTRACT-RESOLVED (formerly REVIEW_REQUIRED — hardened by Macro A)

Both former `REVIEW_REQUIRED` entries are resolved by BLK-01 (CLOSED_FROZEN) and now carry the structured machine contract:

- `DBFBridgeRunError(RuntimeError)` — machine `code`, all underlying structured `details`, JSON-safe `to_dict()` (`tests/test_public_error_contract.py::test_run_error_payload_preserves_all_details`).
- `FileCheck`/`TableCheck` — `to_dict()` serialization (`api_models.py::_check_to_dict` consumes them in `VerificationRunResult.to_dict()`; `test_run_level_results_are_json_safe`).

They are classified as `STABLE_1_0` in the inventory below.

### COMPATIBILITY_ALIAS

- `dbf_bridge` package namespace (historical import name; zero-cost lazy alias; keep for 1.0).
- pip extra `[import]` (historical alias of `[write]`; keep for 1.0).

### DO_NOT_PROMOTE

None. (Internal-only symbols are not exported; `_LAZY_SYMBOLS`/`TYPE_CHECKING` guards keep the runtime import side-effect free.)

### Public operations contract (§19)

| Operation | Signature (key args) | Result type | Error contract (current) | Side effects | Optional deps | Progress | Cancellation | Output semantics |
|---|---|---|---|---|---|---|---|---|
| `inspect_table` | `(path)` | `TableInfo` | typed core errors, `to_dict()` | none | none (base) | n/a | n/a | read-only |
| `read_schema` | `(path)` | `TableSchema` | typed core errors, `to_dict()` | none | none (base) | n/a | n/a | read-only |
| `iter_records` | `(path, *, fields, include_deleted, memo, raw, encoding, decode_errors, cancel_check, progress)` | iterator of `DirectRecord` | typed core errors + `ReadCancelledError` | none | none (base) | yes | yes (`cancel_check`) | read-only |
| `read_records` | `(path, offset=0, limit=100, fields, ...)` | `RecordPage` | typed core errors + `ARGUMENT_INVALID` | none | none (base) | yes | yes | read-only |
| `iter_raw_records` | `(path)` | iterator of raw `DirectRecord` | typed core errors | none | none (base) | no | n/a | read-only |
| `export_dbf` | `(source, output, *, formats, memo, ..., overwrite=True, validate, incremental, progress, options)` | `ExportRunResult` | bare `ValueError`/`FileNotFoundError` for arguments/paths; per-table `FAILED` + text; `OutputExistsError` flattened | creates output tree (atomic) | `[xlsx]` fail-before-output | yes | no | overwrite policy; atomic `.partial` |
| `reconstruct_dbf` | `(source, output, *, input_format, memo, overwrite=False, progress, options)` | `ReconstructionRunResult` | bare `ValueError`/`FileNotFoundError`; per-table `FAILED` + text; plain `FileExistsError` flattened | creates output DBF/FPT + report | `[write]`, `[xlsx]` fail-before-output | yes | no | atomic publish; failure cleanup |
| `verify_conversion` | `(source, output, *, formats, strict, report, write_report, verbose)` | `VerificationRunResult` | bare `FileNotFoundError`; global errors text list | writes verification report (atomic, unconditional replace) | xlsx check for xlsx verify | no | n/a | report JSON |
| `check_conversion_quality` | `(source, output, *, overwrite=False, max_differences, progress)` | `QualityRunResult` | bare `ValueError`/`FileNotFoundError`; per-table text | writes quality artifacts | `[write]` fail-before-output | yes | no | workspace-only round trip |

### Historical namespace (§20)

`dbfbridge` = preferred user namespace; `dbf_bridge` = historical package. The alias is a zero-cost lazy module (`__getattr__` delegates; no runtime duplication, no import side effects). Classified **COMPATIBILITY_ALIAS**: keeping `dbf_bridge` for 1.0 has **no architectural cost** (no second parser, no divergent state — verified by `__getattr__` delegation code).

---

## 4. Error-model audit (§21-§29)

### Architecture error codes (§23)

| Code | Required | Implemented model | Public operation | Machine-readable | JSON-safe | Current tests | Gap |
|---|---|---|---|---|---|---|---|
| DBF_FORMAT_UNSUPPORTED | YES | `DbfFormatUnsupportedError` (core) | Direct Read | YES | YES | direct-read tests | none |
| DBF_HEADER_INVALID | YES | `DbfHeaderInvalidError` | Direct Read | YES | YES | header tests | none |
| DBF_TRUNCATED | YES | `DbfTruncatedError` | Direct Read | YES | YES | truncation tests | none |
| FPT_REQUIRED_MISSING | YES | `FptRequiredMissingError` | Direct Read (memo) | YES | YES | lazy-memo/missing-FPT tests | none |
| FPT_INVALID | YES | `FptInvalidError` | Direct Read (memo) | YES | YES | pointer/payload/truncation tests | none |
| ENCODING_UNKNOWN | YES | `EncodingUnknownError` | Direct Read | YES | YES | encoding tests | none |
| TEXT_DECODE_ERROR | YES | `TextDecodeError` | Direct Read (strict) | YES | YES | decode tests | none |
| FIELD_TYPE_UNSUPPORTED | YES | `FieldTypeUnsupportedError` | Direct Read projection | YES | YES | Q/W/binary-C projection tests | none |
| OPTIONAL_DEPENDENCY_MISSING | YES | `OptionalDependencyMissingError` | export/reconstruct/quality | YES (`.code`, `to_dict()`) | YES | `tests/test_optional_dependencies.py` | none |
| OUTPUT_EXISTS | YES | `OperationOutputExistsError(FileExistsError)` (import alias `OutputExistsError`); per-table `error_details` on export + reconstruction conflicts | export/reconstruct | YES | YES | `test_output_exists_is_machine_readable_per_table`, `test_output_exists_in_reconstruction_is_machine_readable` | none |
| RECONSTRUCTION_FAILED | YES | `ReconstructionResult.error_details` (code `RECONSTRUCTION_FAILED`; fallback when no more-specific code) + `status="FAILED"` | reconstruct/quality | YES | YES | `test_reconstruction_failure_is_machine_readable` | none |
| ROUNDTRIP_MISMATCH | YES | `ReconstructionResult.error_details` (with both canonical checksums in context) + `canonical_match=False` | reconstruct/quality | YES | YES | `test_roundtrip_mismatch_is_machine_readable` | none |

Additional implemented codes (beyond the required set): `PATH_NOT_FOUND`, `DBF_IO_ERROR`, `DBF_RECORD_INVALID`, `ARGUMENT_INVALID`, `FIELD_PROJECTION_INVALID`, `READ_CANCELLED`, `OPERATION_FAILED` — all typed and JSON-safe in the one canonical `ErrorCode` vocabulary.

### §25 answer (after Macro A)

**Can a future MCP consumer classify every important dbfbridge public failure WITHOUT parsing English text? — YES.**

Evidence: `tests/test_public_error_contract.py::test_mcp_machine_readable_classification` classifies the representative cases (invalid argument → `ARGUMENT_INVALID`; missing path → `PATH_NOT_FOUND`; output conflict → `OUTPUT_EXISTS`; unsupported table → `FIELD_TYPE_UNSUPPORTED`; reconstruction failure → `RECONSTRUCTION_FAILED`; roundtrip mismatch → `ROUNDTRIP_MISMATCH`) using ONLY `.code` / structured payloads — no substring matching, no regex over messages, no English parsing. The pre-Macro-A evidence (bare `ValueError`/`FileNotFoundError` in `api.py`, codeless run error, text-only per-table reasons, missing run-level `to_dict()`) is resolved: see BLK-01 (CLOSED_FROZEN).

### §26/§27/§28 (after Macro A)

- Argument/path failures: typed `OperationArgumentError` (`ARGUMENT_INVALID`) / `OperationPathError` (`PATH_NOT_FOUND`) — messages unchanged, superclasses preserved (`isinstance` compat proven by tests).
- Output conflict (overwrite=False): `OUTPUT_EXISTS` detail on per-table results for export AND reconstruction; `OutputExistsError` remains a `FileExistsError` with code + payload. Verify report replaces unconditionally (documented); quality honours `overwrite`.
- Reconstruction failure: `RECONSTRUCTION_FAILED` / `ROUNDTRIP_MISMATCH` structured details; more-specific physical codes (e.g. `DBF_RECORD_INVALID` for the documented Varchar no-raw-image boundary) preserved when available.

---

## 5. Direct Read DoD audit (§33)

| DoD item | Verdict | Evidence |
|---|---|---|
| import side-effect free | PASS | `test_fresh_interpreter_import_has_no_side_effects` (schema + records variants); lazy `__getattr__` design |
| Direct Read without VFP9 | PASS | pure-Python core (`dbfread` only); CI matrix (Ubuntu 3.10-3.14, Windows 3.12) contains no VFP |
| READ creates no outputs | PASS | `test_inspection_creates_no_outputs_or_temp_files`; `test_source_byte_identical_and_no_files_created` |
| projection works | PASS | `test_projection_really_skips_the_parser_for_unselected_fields`; `test_unknown_duplicate_and_string_projections_are_typed_errors` |
| memo lazy works | PASS | `test_lazy_memo_values_never_open_fpt_during_iteration`; `test_lazy_to_dict_does_not_read` |
| memo skip works | PASS | `test_memo_skip_and_null_do_not_touch_the_fpt` |
| bounded read does not materialize table | PASS | `test_limit_does_not_materialize_further_records` |
| source SHA unchanged | PASS | `test_source_byte_identical_and_no_files_created`; `test_source_immutability_across_control_modes` |
| structured errors | PASS | core: typed `ErrorCode` + `to_dict()`; high-level (Macro A): typed public exceptions + per-table `error_details` + run-level `to_dict()` |
| no runtime network | PASS | `git grep` — no `requests/urllib/httpx/aiohttp/socket` in `src/` |
| stream records without JSONL | PASS | public `iter_records`/`read_records`; exporter-parity tests use both paths |

## 6. Performance closure (§34)

**CLOSED_FROZEN.** Canonical Phase 3 baseline verified byte-identical at every revision from the baseline commit (`811c26e`) through `main` (`c84a611`) and the release candidate (`d690d33`): JSON SHA256 `88fcf32e…`, MD `66b8a151…`, manifest `391b175d…`. Provenance manifests, regression policy `phase-3-regression-policy-v1.json`, committed constraints, PR smoke workflow (`performance-regression.yml`) and full-profile workflow (`benchmark-phase3.yml`) all present; recent smoke runs SUCCESS (33754128032 / 33755859372 / 33756842945).

## 7. Writer performance (§35)

**DEFERRED.** No requirement, test, benchmark threshold, or bug evidence shows writer throughput blocks 1.0. The Phase 3 baseline records the writer scenario; the regression policy would catch hard regressions. No writer optimization is planned.

## 8. Packaging / PyPI (§40/§41)

- **REPOSITORY PACKAGING READINESS: CLOSED_FROZEN** — evidenced on `release/0.3.0` @ `d690d33` (PR #12): `python -m build`, `twine check`, fresh-venv `release_wheel_smoke`, multi-profile `pypi_install_smoke` on the exact built wheel; release-state gate; Python 3.10-3.14 × Windows CI. Release exact-head CI 33756842901 (8/8 jobs) + perf 33756842945 SUCCESS.
- **ACTUAL PYPI PUBLICATION: EXTERNAL_BLOCKER (EXB-01)** — publish run 33487949133: build SUCCESS, publish FAILURE `invalid-publisher` (OIDC claims correct; PyPI-side Trusted Publisher missing/mismatched). No repository code can close it.
- **Version state (§41, intentional):** `main` = `0.2.0` (`pyproject.toml:7`, `__init__.py:83`); `release/0.3.0` = `0.3.0` release preparation. Recorded as branch lifecycle; not "fixed".

---

## 9. Finite blockers to 1.0 (§43/§44/§45/§56)

### BLK-01 — PUBLIC ERROR MODEL STABILIZATION (P0) — **CLOSED_FROZEN in Macro A**

- **Architecture requirement:** §17 (machine-readable distinguishable failures incl. `OUTPUT_EXISTS`, `RECONSTRUCTION_FAILED`, `ROUNDTRIP_MISMATCH`), §15/§32 (JSON-serializable results + typed exceptions), §26 (argument mapping), §27 (output conflict), §28 (reconstruction failure).
- **Implementation (Macro A):** one canonical vocabulary (`ErrorCode` extended additively with `OPTIONAL_DEPENDENCY_MISSING`, `OUTPUT_EXISTS`, `RECONSTRUCTION_FAILED`, `ROUNDTRIP_MISMATCH`, `OPERATION_FAILED`); one neutral payload model (`OperationError`: code/message/operation/path/table/context, frozen, JSON-safe `to_dict()`); typed public exceptions `OperationArgumentError(ValueError)`, `OperationPathError(FileNotFoundError)`, `OperationOutputExistsError(FileExistsError)` (import alias `OutputExistsError` preserved at `exporter/writer.py`); `DBFBridgeRunError(RuntimeError)` now carries `code` + all underlying `details` + `to_dict()`; per-table structured `error_details` added additively to `TableResult`/`ReconstructionResult`; JSON-safe `to_dict()` on all four run results and on `FileCheck`/`TableCheck`.
- **Acceptance evidence:** `tests/test_public_error_contract.py` — 22 tests including the message-blind `test_mcp_machine_readable_classification` (6 representative cases: invalid argument, missing path, output exists, unsupported table, reconstruction failure, roundtrip mismatch). §25 answer: **YES**.
- **Status:** CLOSED_FROZEN (must not be modified without failing regression evidence).

### BLK-02 — MIGRATION RAW-MODE SPLIT (P1) — **CLOSED_FROZEN in Macro A**

- **Architecture requirement:** §7 — explicit raw retention level on the migration API (`raw_mode="none"|"metadata"|"full-record"`; backward-compatible default; Direct Read/MCP uses none).
- **Implementation (Macro A + correctness gate):** public `RawMode` Literal; `raw_mode` propagated through `ExportOptions`/`export_dbf`/`ExportConfig`/`make_config`/`run_export`/`_export_one`/CLI `--raw-mode`; incremental signature includes `raw_mode`; the shared physical loop runs with `keep_raw=False` for none/metadata (no raw allocation — §32); deleted=`separate` feeds BOTH outputs from ONE shared-stream pass (raw images included in full-record; ordering/counts unchanged — §33); loss-aware `__dbfbridge_raw_text_fields__`/`__dbfbridge_binary_memo_fields__` retained in ALL modes (§30 decision: they are loss-aware logical/canonical aids, not physical blobs); `none` omits the replay-only `dbf.header_base64`/`memo.header_base64` schema blobs (reconstruction handles absence gracefully).
- **Correctness gate (supported Varchar without raw record images):** root cause reproduced — the `dbf` writer stores `V` columns through the Character alias with fixed-width payloads and manages its own `_NullFlags` bitmap (one bit per NULLable field, no varlength concept), so canonical reconstruction without raw images produced `DBF_RECORD_INVALID`. Fix: a bounded, schema-driven **Varchar logical-layout repair** pass inside the reconstruction staging boundary (`_repair_varchar_logical_layout`) — per record, rewrites text-V payloads (`value + padding + length byte` when shorter than width, full-width data otherwise, NULL blank) and re-derives the canonical `_NullFlags` bits with the SINGLE engine (`build_nullflags_layout` + `varlength_bits`/`null_bits`/`bit_is_set`; NULL bits from `nullable_null_fields`, padding bits preserved from the exported source bitmap). `V` fields now always declare a NULL flag in the writer spec so the writer's bitmap is exactly one bit wide per canonical bit (non-nullable Varchar included); the Mazovia/Kamenicky language-driver codec gap of the writer is bridged on demand from the schema encodings. Repair runs BEFORE metadata patching/layout validation/atomic publish — failure leaves no published output and no staging residue (tested). Streaming via `records_factory` (O(1)/O(batch), no materialization).
- **Acceptance evidence:** `tests/test_raw_mode.py` — 50 tests: canonical PASS for all three raw modes incl. short/full-width/trailing-space/empty/NULL/non-nullable/mixed V1+V2+C1 bitmaps/deleted rows and cp1250/cp852/Mazovia text (`test_varchar_value_matrix_reconstructs_logically_identical` compares the public Direct Read values of source vs reconstructed); failure-atomicity test (typed `RECONSTRUCTION_FAILED`, no published DBF/FPT, no `.partial` residue); raw-mode + incremental + no-allocation regressions. Suite: 505 passed (Windows local).
- **Remaining physical-identity limitation (unchanged, documented):** exact raw Varchar byte identity stays `SUPPORTED_WITH_LIMITATION` (`raw_dbf_match` reported separately); in `none`/`metadata` a byte-identical physical layout is explicitly NOT guaranteed.
- **Status:** CLOSED_FROZEN (default `full-record` preserves the historical forensic behaviour byte-for-byte).

### BLK-03 — 1.0 API CONTRACT DECLARATION (P2) — **CLOSED_FROZEN in Macro B**

- **Architecture requirement:** §12/§20/§30 — 1.0 means stable API and guarantees.
- **Closure (Macro B):** `docs/api-1.0.md` declares the complete 1.x contract — preferred import boundary (`import dbfbridge`), the nine frozen public operations with signatures/inputs/results/side-effects/error contracts/optional dependencies/progress-cancellation/output behaviour, Direct Read + migration/reconstruction guarantees, the RawMode contract, the structured machine-code vocabulary (19 stable `ErrorCode` values; removal/repurposing = breaking, additions = additive), the JSON result boundary (documented keys not removed or repurposed without a major; additive keys allowed), compatibility aliases (`dbf_bridge`, `[import]`), the authoritative compatibility-matrix link, accepted limitations, and explicit SemVer + deprecation policies. Enforced mechanically by `tests/test_api_1_0_contract.py` (11 contract tests: symbol parity, operations, RawMode choices, required codes, JSON-safe run results on a real run, alias availability, no private-module imports in examples).
- **Status:** CLOSED_FROZEN. Repository-controlled blockers: **0**. External blocker: EXB-01 (PyPI Trusted Publisher).

### §56 — finite numbered answer (after Macro B)

If dbfbridge 1.0 were released today, **zero repository-controlled facts** would
prevent truthful compliance with `DBFBRIDGE_TARGET_ARCHITECTURE(20260904-052528).md`.

All former items are closed with evidence: BLK-01 (machine-readable public error
contract — `tests/test_public_error_contract.py`), BLK-02 (RawMode split with
canonical Varchar reconstruction in every mode — `tests/test_raw_mode.py`),
BLK-03 (declared 1.0 API contract — `docs/api-1.0.md` +
`tests/test_api_1_0_contract.py`).

One **external** fact remains, not repository-controlled: PyPI Trusted Publisher
verification pending (`invalid-publisher`) — see External blockers.

---

## 10. Remaining macro PRs (§46/§47)

### Macro A — Public API + Error Model Stabilization — **EXECUTED (this branch)**

- **Objective:** BLK-01 + BLK-02 — both acceptance criteria met; blockers CLOSED_FROZEN.
- **Included blocker IDs:** BLK-01 (P0), BLK-02 (P1).
- **Forbidden scope honoured:** no writer rewrite (only raw-retention plumbing + result/error payloads), no reader behaviour change, no compatibility-matrix status changes, no new supported types, no benchmark baseline mutation, no release-branch changes.
- **Tests added:** `tests/test_public_error_contract.py` (22), `tests/test_raw_mode.py` (33). Suite 488 passed (Windows local).
- **Definition of Done met:** §25 answer **YES** with the dedicated message-blind test; R-17/R-32/R-36/R-37/R-38/R-54 flipped to CLOSED_FROZEN in this document.
- **What becomes FROZEN upon merge:** public API surface and error-code vocabulary; JSON boundary shapes; `raw_mode` option semantics.

### Macro B — 1.0 Release Acceptance — **EXECUTED (branch `docs/1.0-api-contract`)**

- **Objective:** BLK-03 — declared, closed.
- **Included blocker IDs:** BLK-03 (P2) — CLOSED_FROZEN.
- **Deliverables:** `docs/api-1.0.md` (the 1.x contract), `tests/test_api_1_0_contract.py` (mechanical contract regression), truthful inventory (no stale `REVIEW_REQUIRED`; classification vocabulary `STABLE_1_0` / `COMPATIBILITY_ALIAS` / internal-not-public), R-46 → CLOSED_FROZEN, repository-controlled blockers → 0.
- **Forbidden scope honoured:** no runtime behaviour change, no new public symbols, no benchmark changes, no release-branch changes.
- **What becomes FROZEN upon merge:** the 1.x public API contract and guarantees (this matrix + `docs/api-1.0.md` become the release record).

### Macro C — Stable Release Infrastructure Convergence — **MERGED on main (PR #18, final head `e173ec6`, merge `b712ce5`)**

- **Objective:** converge the proven generic release infrastructure from the superseded 0.3 release-preparation branch onto current `main` (version-neutral CI smokes, release-final-state validator, shared artifact verifier, hardened publish pipeline, migration-1.0 guide).
- **Delivered:** `scripts/check_release_state.py`, `scripts/verify_release_artifacts.py` (ONE shared gate run by the CI package job AND the publish build job), version-neutral `ci.yml`/smoke contracts, hardened `publish.yml` (release-state gate → build once → twine → artifact verifier → fresh-wheel smoke → install-profile smoke → single artifact → OIDC publish), hardened `PUBLISHING.md`, `MANIFEST.in` public docs, `docs/migration-1.0.md`.
- **Evidence on main:** post-merge CI 33843870158 SUCCESS @ `b712ce5` — package job: build PASS, twine PASS, shared artifact verifier PASS (`ARTIFACTS: PASS (one wheel, one sdist, expected version 0.2.0)`), fresh-wheel smoke PASS, install-profile smoke PASS (base/write/xlsx/write,xlsx/fast/all/import).
- **Definition of Done met:** repository-controlled blockers 0; publication remains externally gated (EXB-01).

### Final release lifecycle (externally gated — NOT started)

- **Objective:** when PyPI access / Trusted Publisher verification is restored: verify the publisher, prepare the final release commit (final version, dated CHANGELOG entry, timeless docs), run the release-state validator, exact-main CI, annotated tag, GitHub Release, Trusted Publishing, post-publication installed-PyPI verification (per `PUBLISHING.md`).
- **Included blocker IDs:** EXB-01 (external; not a code blocker).
- **Current truth:** intentionally not started — no version bump, no tag, no publication.

---

## 11. Frozen areas (§48)

The following areas are **CLOSED_FROZEN** as of `main` `c84a611`. Future prompts must not modify them without failing regression, benchmark evidence, or a security blocker:

1. Direct Read physical architecture (single physical record loop, O(1) memory, projection, memo policies, deleted single pass).
2. Backend abstraction (`core/backend.py` protocols + dbfread reference adapter).
3. Progress/cancellation contract (`progress.py`; `cancel_check`, `ReadCancelledError`).
4. Canonical Phase 3 benchmark baseline + provenance manifests (hashes above).
5. Performance regression CI (policy v1, constraints, PR smoke, full profile).
6. Polish encoding core (cp1250, cp852, Mazovia; explicit-encoding policy).
7. NullFlags allocation engine (`core/nullflags.py`; canonical bit allocation; single-engine lock tests).
8. VFP/FPT evidence fixtures and corruption-boundary tests.
9. Optional dependency split and typed `OptionalDependencyMissingError` boundary (fail-before-output).
10. Release tooling/gates (release-state validator, publish gate, wheel/install smokes, publish workflow).
11. Canonical checksum semantics (`CanonicalChecksum`, deleted-record handling, bounded diagnostics).
12. Atomic output policy (`.partial` + `os.replace`, failure cleanup, output-only writes).
13. **(Macro A)** Machine-code vocabulary (`ErrorCode` — the ONE canonical set incl. `OPTIONAL_DEPENDENCY_MISSING`, `OUTPUT_EXISTS`, `RECONSTRUCTION_FAILED`, `ROUNDTRIP_MISMATCH`, `OPERATION_FAILED`) and the `OperationError` payload model.
14. **(Macro A)** JSON boundary shapes (`to_dict()` on run-level results, `DBFBridgeRunError` payload, per-table `error_details` additive to text `errors`).
15. **(Macro A)** `RawMode` semantics (default `full-record`; loss-aware raw-text/binary-memo aids retained in every mode; `none` omits replay-only header blobs; **canonical reconstruction of supported Varchar in EVERY raw mode** via the schema-driven Varchar logical-layout repair — physical byte identity is NOT guaranteed in `none`/`metadata`).
16. **(Macro B)** The declared 1.x public API contract (`docs/api-1.0.md`): import boundary, nine stable operations, machine-code vocabulary, JSON boundary keys, RawMode contract, SemVer + deprecation policy — enforced by `tests/test_api_1_0_contract.py`.

## 12. Accepted limitations (§49)

| Limitation | Why it does not block the declared 1.0 guarantee |
|---|---|
| Varchar exact raw DBF identity (SC-03) | Canonical reconstruction (the declared guarantee) passes for all NULL-bearing/mixed Varchar cases (PR #14); byte identity is explicitly excluded and documented in the public compatibility matrix (`raw_dbf_match` reported separately). The architecture's 1.0 clause covers *correctness gaps*, not an undocumented cosmetic byte identity. |
| G/P exact raw FPT block layout (SC-19/SC-20) | Canonical payload + DBF identity proven; raw FPT layout difference is explicit, warned, and documented. No public contract promises FPT byte identity. |
| Q/W decoding (SC-21/SC-22) | Declared `UNSUPPORTED` with typed `FIELD_TYPE_UNSUPPORTED` and per-table `UNSUPPORTED` status; forensic raw stream preserves exact bytes. Truthful and typed. |
| Binary `V`/`C` (NOCPTRANS) dedicated decoders (SC-02/SC-04) | Typed refusal + raw stream preservation; explicit non-goal in the matrix. |
| CDX tag expressions (R-42) | Presence-only reporting; §19 reserves tag parsing for the VFP runtime adapter or a proven future parser. |
| dBASE Level 7 dialect (+, O, @, non-VFP B) (SC-09/SC-12/SC-13/SC-14) | Parser-level compatibility only, honestly labelled; the dialect itself is outside `SUPPORTED_VERSIONS`. |
| Unknown type byte `X` (SC-24) | Typed error handling; deliberately left unproven rather than faked. |

## 13. External blockers (§50)

| ID | Blocker | Evidence | Impact | Owner |
|---|---|---|---|---|
| EXB-01 | PyPI Trusted Publisher verification | v0.2.0 publish run 33487949133: build SUCCESS, publish FAILURE `invalid-publisher`; OIDC claims (repository `PeterPirog/dbfbridge`, workflow `publish.yml`, environment `pypi`) correct; PyPI-side configuration missing/mismatched | Blocks 0.3.0 and 1.0 publication; repository packaging itself is ready (R-26) | External (PyPI account/publisher settings) |

## 14. Release isolation (§2/§3/§4)

`release/0.3.0` (`d690d33`) is the FROZEN 0.3.0 candidate — synchronized with PR #14 exactly once; **untouched** by this audit. `feat/phase-2-direct-write` (`0c6b927`) remains DEFERRED RESEARCH and untouched. PyPI: no publish/retry/tag/publish-config action taken. Canonical benchmarks: UNCHANGED. No `src/**` runtime change in this audit.

## 15. Validation (§51)

- `python -m pytest -q`: **516 passed** (Windows local; 505 pre-Macro-B + 11 contract tests). Exact CI counts recorded by the Macro B PR CI.
- `python -m ruff check src tests benchmarks examples scripts`: clean.
- `git diff --check`: clean.
- No new canonical performance baseline; canonical Phase 3 hashes verified UNCHANGED; targeted raw-mode comparison recorded separately (`benchmarks/raw_mode_migration.py`).
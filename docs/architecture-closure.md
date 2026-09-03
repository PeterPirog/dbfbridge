# dbfbridge 1.0 Architecture Closure Matrix

**Contract:** `DBFBRIDGE_TARGET_ARCHITECTURE(20260903-120019).md` (immutable upstream document; this file is the working repository status and does not replace it).
**Audit baseline:** `main` = `c84a611aac6e7beb4a48f5044e62c6a7d10aefeb` (PR #14 merged), branch `audit/1.0-architecture-closure`.
**Audit date:** 2026-09-03. **Status vocabulary:** CLOSED_FROZEN / BLOCKER / ACCEPTED_LIMITATION / INTENTIONALLY_UNSUPPORTED / EXTERNAL_BLOCKER / DEFERRED / NOT_YET_AUDITED (this final matrix contains **no** `NOT_YET_AUDITED` rows).

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

CLOSED_FROZEN: 60
BLOCKER: 7 requirement rows → consolidated into 3 root-cause blockers (BLK-01, BLK-02, BLK-03)
ACCEPTED_LIMITATION: 3
INTENTIONALLY_UNSUPPORTED: 10
EXTERNAL_BLOCKER: 1
DEFERRED: 1
NOT_YET_AUDITED: 0
```

External blocker (not counted above as a requirement row failure): **PyPI Trusted Publisher verification (EXB-01)**.

---

## 1. Architecture requirement matrix

Columns: ID | architecture section | requirement | status | repository evidence | test/CI evidence | known limitation | 1.0 blocker | next action | target macro PR.

### §1 Role

| ID | Section | Requirement | Status | Repository evidence | Test/CI evidence | Limitation | Blocker | Next action | Macro PR |
|---|---|---|---|---|---|---|---|---|---|
| R-01 | §1 | Standalone Python library; single DBF/FPT implementation; no copy into `mcp-vfp9sp2-toolchain` | CLOSED_FROZEN | Single implementation in `src/dbf_bridge/`; `src/dbfbridge/` is a lazy alias package | `tests/test_public_api.py::test_distribution_import_exposes_the_documented_api` | none | NO | none | — |
| R-02 | §1 | Loss-aware engine capability set (inspect / schema / direct read / raw metadata / migration / reconstruction / verification) | CLOSED_FROZEN | Public API exposes all six capability areas | 456-test suite green on `main`/release | none | NO | none | — |
| R-03 | §1 | Two consumer classes: PyPI users; MCP `PURE_READ` backend without VFP | CLOSED_FROZEN | `core/` reads via `dbfread` only; no COM/VFP/network imports (`git grep` evidence) | CI (Ubuntu 3.10–3.14 + Windows 3.12) runs without any VFP runtime | none | NO | none | — |

### §2 Assets

| ID | Section | Requirement | Status | Repository evidence | Test/CI evidence | Limitation | Blocker | Next action | Macro PR |
|---|---|---|---|---|---|---|---|---|---|
| R-04 | §2 | Project assets preserved (streaming dbfread, VFP/FPT metadata, Polish codepages, deleted distinction, memo policies, atomic output, JSONL, raw metadata, SHA256/validation/diagnostics, schema-driven reconstruction, typed API, progress, incremental, honest CDX) | CLOSED_FROZEN | All assets present after PR #13/#14; CHANGELOG 0.2→0.3 records each | 456 tests green; `docs/compatibility-vfp.md` evidence matrix | none | NO | none | — |

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
| R-17 | §7 | Migration API must expose an explicit raw retention level (`raw_mode="none"\|"metadata"\|"full-record"`) | **BLOCKER** | `exporter/config.py` has **no** raw option; `reader.py:235` hardcodes `keep_raw=True`; `writer.py:184,208` always embed `__dbfbridge_raw_record__` Base64 | benchmark scenarios `raw_mode_none` / `raw_record_metadata_default` exist (Phase 1 contract), but the public API level does not | JSONL exports always carry full raw records | **YES (BLK-02)** | implement `raw_mode` option, backward-compatible default | Macro A |

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
| R-26 | §12 | Repository packaging readiness: semver, metadata, SPDX, wheels/sdist, 3.10–3.14, Windows CI, changelog, migration guide, `py.typed`, side-effect-free import, examples, documented VFP/CDX limits | CLOSED_FROZEN | PR #12 (release/0.3.0): package job runs `python -m build` + `twine check` + `release_wheel_smoke` + `pypi_install_smoke` on the exact wheel; release-state gate; `docs/migration-0.3.md` | CI 33756842901 SUCCESS @ d690d33 (all 8 jobs) | `main` still at 0.2.0 — intentional release lifecycle (§41) | NO | none | — |
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
| R-32 | §15/§32 | JSON-serializable run-level results + typed exception payloads (export/reconstruct/verify/quality, `DBFBridgeRunError`) | **BLOCKER** | `ExportRunResult`/`VerificationRunResult`/`QualityRunResult`/`ReconstructionRunResult` lack `to_dict()`; `VerificationRunResult.checks` are dataclasses (`FileCheck`/`TableCheck`) without serialization; `DBFBridgeRunError(RuntimeError)` has no machine code/payload | no test proves run-level JSON safety (only per-table `to_dict`/`to_report_dict`) | MCP cannot transport run results without reaching into dataclass fields | **YES (BLK-01)** | add JSON-safe serialization at the public boundary | Macro A |

### §16 MCP read semantics

| ID | Section | Requirement | Status | Repository evidence | Test/CI evidence | Limitation | Blocker | Next action | Macro PR |
|---|---|---|---|---|---|---|---|---|---|
| R-33 | §16 | `limit`/`fields`/`offset`/`includeDeleted`/memo/encoding policies + progress/cancellation | CLOSED_FROZEN | `iter_records(..., fields, include_deleted, memo, raw, encoding, decode_errors, cancel_check, progress)`; `read_records(offset, limit)` | `test_include_deleted_paging_skips_deleted_in_same_pass`, `test_progress_normal_exhaustion`, `test_read_records_argument_validation`, cancel/progress suites | none | NO | none | — |

### §17 Error model

| ID | Section | Requirement | Status | Repository evidence | Test/CI evidence | Limitation | Blocker | Next action | Macro PR |
|---|---|---|---|---|---|---|---|---|---|
| R-34 | §17 | Direct Read: typed `ErrorCode` model, stable codes, JSON-safe context, no text parsing | CLOSED_FROZEN | `core/errors.py`: 14 codes; every failure carries `code/message/path/context`; `to_dict()` POSIX-path-normalized | Direct Read typed-error tests (truncated header, bad projection, FPT missing/invalid, decode strict, cancel) | none | NO | none | — |
| R-35 | §17 | `OPTIONAL_DEPENDENCY_MISSING` machine-readable | CLOSED_FROZEN | `OptionalDependencyMissingError(RuntimeError)` with `.code = "OPTIONAL_DEPENDENCY_MISSING"`, `to_dict()`, exact install command; fail-before-output; never for `[fast]` | `tests/test_optional_dependencies.py` | none | NO | none | — |
| R-36 | §17 | `OUTPUT_EXISTS` distinguishable at the public boundary | **BLOCKER** | exporter: `OutputExistsError(FileExistsError)` (writer.py:20) — typed subclass but **no machine code, no `to_dict`**, flattened into `TableResult.errors` text (writer.py:118-125); importer: plain `FileExistsError` (importer/writer.py:86,88) caught → `status="FAILED"` + text | no test asserts a machine code for output conflict | text-only classification at run level | **YES (BLK-01)** | typed code + structured payload | Macro A |
| R-37 | §17 | `RECONSTRUCTION_FAILED` / `ROUNDTRIP_MISMATCH` machine-readable | **BLOCKER** | `ReconstructionResult.status: str` + `errors: list[str]`; machine outcome flags exist (`canonical_match`, `raw_dbf_match`, `raw_fpt_match`) but the failure **reason** is text-only; `DBFBridgeRunError` message is English text | reconstruction failure tests assert statuses, not codes | reason classification requires text parsing | **YES (BLK-01)** | structured code + payload | Macro A |
| R-38 | §17/§25 | High-level public ops raise machine-classifiable failures (no bare `ValueError`/`FileNotFoundError` at the boundary); MCP can classify every important failure without parsing text | **BLOCKER** | `api.py` raises bare `ValueError` ×17 (argument validation: api.py:77,101-109,157-168,294) and `FileNotFoundError` (api.py:99,172,225,252-254,298); per-table errors are `list[str]` (`TableResult`, `ReconstructionResult`, `FileCheck`, `TableCheck`) | §25 answer: **NO** — evidence above | MCP must parse English text today | **YES (BLK-01)** | map to `ARGUMENT_INVALID` / `PATH_NOT_FOUND` codes; structured per-table error entries | Macro A |

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
| R-46 | §20 | 1.0.0 = stable direct-read API, stable migration/reconstruction API, documented compatibility matrix, benchmark suite, robust packaging, no known correctness gaps in supported cases | **BLOCKER** | Blocked by BLK-01 (error model), BLK-02 (raw_mode), BLK-03 (stability declaration); matrix + benchmarks + packaging already closed | — | — | **YES** | execute Macro A then Macro B | Macro A/B |

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
| R-54 | §21 | API returns structured results **and structured exceptions** | **BLOCKER** | results structured (dataclasses + `to_dict`) and core exceptions typed; **high-level exceptions/per-table failure reasons are text-only** (see R-36/R-37/R-38) | high-level leg fails §17 | **YES (BLK-01)** | Macro A | Macro A |
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

Exported surface (51 symbols in `src/dbf_bridge/__init__.py::__all__`, mirrored lazily by `src/dbfbridge/__init__.py`).

### STABLE_1_0_CANDIDATE

Direct Read operations: `inspect_table`, `read_schema`, `iter_records`, `read_records`, `iter_raw_records`.
High-level operations: `export_dbf`, `reconstruct_dbf`, `verify_conversion`, `check_conversion_quality`.
Direct models: `FieldInfo`, `TableInfo`, `TableSchema`, `DirectRecord`, `RecordPage`, `LazyMemoValue`.
Options/result models: `ExportOptions`, `ExportRunResult`, `ReconstructionOptions`, `ReconstructionRunResult`, `ReconstructionResult`, `VerificationRunResult`, `QualityRunResult`, `TableResult`, `TableStatus`.
Type aliases: `OutputFormat`, `InputFormat`, `MemoPolicy`, `MissingMemoPolicy`, `DecodeErrors`, `DeletedPolicy`.
Progress/cancellation: `ProgressCallback`, `ProgressEvent`, `CancellationCheck`.
Direct error model: `ErrorCode`, `DirectReadError`, `DbfPathError`, `DbfHeaderInvalidError`, `DbfTruncatedError`, `DbfFormatUnsupportedError`, `DbfIoError`, `DbfRecordInvalidError`, `EncodingUnknownError`, `TextDecodeError`, `FptRequiredMissingError`, `FptInvalidError`, `ArgumentInvalidError`, `FieldProjectionInvalidError`, `FieldTypeUnsupportedError`, `ReadCancelledError`.
Dependency boundary: `OptionalDependencyMissingError`. Metadata: `__version__`.

### REVIEW_REQUIRED (contract hardening inside BLK-01, same root cause)

`DBFBridgeRunError` (needs machine code + JSON-safe payload), and the un-exported-but-public check dataclasses `FileCheck`/`TableCheck` returned inside `VerificationRunResult.checks` (need serialization or structured replacement).

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
| OUTPUT_EXISTS | YES | `OutputExistsError(FileExistsError)` exporter / plain `FileExistsError` importer | export/reconstruct | **NO at boundary** (flattened to `errors: list[str]`) | **NO** | overwrite-refusal tests (status-level only) | machine code + payload (BLK-01) |
| RECONSTRUCTION_FAILED | YES | `ReconstructionResult.status="FAILED"` + `errors: list[str]` | reconstruct/quality | **NO** (status literal only) | flags only (`canonical_match`) | failure tests assert status, not code | machine code + reason (BLK-01) |
| ROUNDTRIP_MISMATCH | YES | `canonical_match=False` + `differences: list[dict]` | reconstruct/quality | **NO code** (outcome flag exists; reason text-only) | differences are dicts but reason is text | reconstruction mismatch tests | typed ROUNDTRIP_MISMATCH reason (BLK-01) |

Additional implemented codes (beyond the required set): `PATH_NOT_FOUND`, `DBF_IO_ERROR`, `DBF_RECORD_INVALID`, `ARGUMENT_INVALID`, `FIELD_PROJECTION_INVALID`, `READ_CANCELLED` — all typed and JSON-safe in core.

### §25 answer

**Can a future MCP consumer classify every important dbfbridge public failure WITHOUT parsing English text? — NO.**

Exact evidence:
1. `src/dbf_bridge/api.py` raises bare `ValueError` (17 call sites: 77, 101-109, 157-168, 294) and `FileNotFoundError` (99, 172, 225, 252-254, 298) — no code, no `to_dict`.
2. `DBFBridgeRunError(RuntimeError)` (api_models.py:40) carries `result` but no machine code and no JSON-safe payload.
3. `TableResult.errors: list[str]` / `ReconstructionResult.errors: list[str]` / `FileCheck.errors` / `TableCheck.errors` — failure reasons are English strings.
4. `OutputExistsError` (exporter/writer.py:20) and importer `FileExistsError` (importer/writer.py:86,88) carry no machine code and are flattened to text at run level.
5. Run-level results (`ExportRunResult`, `VerificationRunResult`, `QualityRunResult`, `ReconstructionRunResult`) have no `to_dict()`; `VerificationRunResult.checks` are dataclasses without serialization.

**Consequence:** BLK-01 (P0). Minimum future contract (§24 — not implemented here): reuse the existing `ErrorCode` vocabulary at the high-level boundary; one structured public error carrying `code/message/path/context`; structured per-table error entries alongside (not replacing) the existing text lists; `to_dict()` on all run-level results and on the run error.

### §26/§27/§28

- Argument/path failures: map to already-existing core codes `ARGUMENT_INVALID` / `PATH_NOT_FOUND` (acceptance criteria recorded in BLK-01). Do not implement now.
- Output conflict (overwrite=False): exporter = typed-but-codeless `OutputExistsError` flattened to text; importer = plain `FileExistsError`; verify report replaces unconditionally; quality honours `overwrite`. Currently **typed at class level, text-only at the public boundary** → BLK-01.
- Reconstruction failure: `status` + `canonical_match` flags are machine-readable *outcomes*; the failure *reason* is not a code → BLK-01.

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
| structured errors | PASS (core) / GAP (high-level → BLK-01) | core: typed `ErrorCode` + `to_dict()`; high-level: text-only (R-38) |
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

### BLK-01 — PUBLIC ERROR MODEL STABILIZATION (P0)

- **Architecture requirement:** §17 (machine-readable distinguishable failures incl. `OUTPUT_EXISTS`, `RECONSTRUCTION_FAILED`, `ROUNDTRIP_MISMATCH`), §15/§32 (JSON-serializable results + typed exceptions), §26 (argument mapping), §27 (output conflict), §28 (reconstruction failure).
- **Current repository evidence:** §25 answer **NO** (five evidence points above); `api.py` bare `ValueError`/`FileNotFoundError`; `DBFBridgeRunError` without code/`to_dict`; text-only per-table error lists; codeless output-conflict errors; run-level models without `to_dict()`.
- **Why this blocks 1.0:** §20 defines 1.0 as *stable API*; the MCP consumer contract (§17: *"Toolchain mapuje te kody na własny `OperationResult` bez parsowania tekstu błędu"*) is not met for every high-level failure.
- **Acceptance criteria:** (1) every §17-required failure is distinguishable from the public boundary by a machine code without parsing the message; (2) argument/path failures map to `ARGUMENT_INVALID`/`PATH_NOT_FOUND`; (3) output conflicts expose `OUTPUT_EXISTS`; (4) reconstruction/round-trip failures expose `RECONSTRUCTION_FAILED`/`ROUNDTRIP_MISMATCH` reasons; (5) all run-level results and the public run error expose JSON-safe serialization; (6) existing behaviour (statuses, text errors, exception types) stays backward-compatible — structured fields are additive; (7) a dedicated test classifies a representative set of failures from structured payloads alone (no message text).
- **Files likely involved:** `src/dbf_bridge/api.py`, `api_models.py`, `core/errors.py` (vocabulary only), `exporter/writer.py`, `exporter/models.py`, `importer/reconstruct.py`, `importer/models.py`, `importer/writer.py`, `verifier.py`, `quality.py`.
- **Tests required:** typed-error unit tests per public operation; machine-classification test; run-result `to_dict` JSON round-trip tests; backward-compat regression suite (456 tests stay green).
- **Target macro PR:** Macro A.

### BLK-02 — MIGRATION RAW-MODE SPLIT (P1)

- **Architecture requirement:** §7 — explicit raw retention level on the migration API (`raw_mode="none"|"metadata"|"full-record"`; backward-compatible default allowed; Direct Read/MCP uses none).
- **Current repository evidence:** `exporter/config.py` has no raw option; `reader.py:235` hardcodes `keep_raw=True`; `writer.py:184,208` always embed `__dbfbridge_raw_record__`; benchmark scenario `raw_mode_none` already exists in the Phase 1 contract.
- **Why this blocks 1.0:** §20 places the raw-mode split in the delivered roadmap and §7 states it as a target API requirement; 1.0 freezes the migration API — adding the option after 1.0 would be a post-stability API addition.
- **Acceptance criteria:** `raw_mode` option on `export_dbf`/`ExportOptions` with backward-compatible default; `none`-mode JSONL reconstructs canonically without raw fields (schema-driven path); benchmark scenario L comparison recorded; no logical result change for existing defaults.
- **Files likely involved:** `api.py`, `api_models.py`, `exporter/config.py`, `exporter/reader.py`, `exporter/writer.py`.
- **Tests required:** raw-mode matrix tests (none/metadata/full-record), canonical reconstruction from raw-less JSONL, benchmark contract validation.
- **Target macro PR:** Macro A.

### BLK-03 — 1.0 API CONTRACT DECLARATION (P2)

- **Architecture requirement:** §12/§20/§30 — 1.0 means stable API and guarantees; §30 found the documented import surface, typed models and public/private boundary present, but **no explicit backward-compatibility/deprecation policy statement** for the frozen 1.0 surface.
- **Current repository evidence:** `docs/migration-0.3.md` exists (0.2→0.3); no documented 1.0 stability/deprecation policy; the 1.0 public surface is declared only by `__all__` + tests.
- **Why this blocks 1.0:** a stability claim without a published compatibility policy is not a truthful guarantee.
- **Acceptance criteria:** repository documentation declares the frozen 1.0 public surface (this document's inventory), the compatibility promise (semver), and the deprecation policy; final compatibility-matrix confirmation.
- **Files likely involved:** `docs/architecture-closure.md`, README/docs, release branch flow (version/CHANGELOG only during the release macro).
- **Tests required:** `test_distribution_import_exposes_the_documented_api` extended to the frozen surface.
- **Target macro PR:** Macro B.

### §56 — finite numbered answer

If dbfbridge 1.0 were released today, these **repository-controlled** facts would prevent truthful compliance with `DBFBRIDGE_TARGET_ARCHITECTURE(20260903-120019).md`:

1. High-level public operations raise bare `ValueError`/`FileNotFoundError` and expose per-table failure reasons only as English text — §17 machine-readable classification is not met (BLK-01).
2. `OUTPUT_EXISTS` / `RECONSTRUCTION_FAILED` / `ROUNDTRIP_MISMATCH` have no machine-readable code at the public boundary (BLK-01).
3. Run-level result models and `DBFBridgeRunError` lack JSON-safe serialization required by the MCP boundary (§15/§32) (BLK-01).
4. The migration export API lacks the explicit `raw_mode` retention level required by §7 (BLK-02).
5. The 1.0 API stability/deprecation policy is not yet documented (§30) (BLK-03).

Plus one **external** fact, not repository-controlled: PyPI Trusted Publisher verification pending (`invalid-publisher`) — see External blockers.

---

## 10. Remaining macro PRs (§46/§47)

### Macro A — Public API + Error Model Stabilization

- **Objective:** BLK-01 + BLK-02.
- **Included blocker IDs:** BLK-01 (P0), BLK-02 (P1).
- **Forbidden scope:** no writer rewrite, no reader behaviour change, no compatibility-matrix status changes, no new supported types, no benchmark baseline mutation, no release-branch changes.
- **Tests:** typed-error per-operation unit tests; machine-classification test (no text parsing); run-result `to_dict` JSON round trips; raw-mode matrix; full suite + CI green.
- **CI:** full CI matrix (lint, Ubuntu 3.10-3.14, Windows 3.12, package) + performance PR smoke.
- **Definition of Done:** §25 answer becomes **YES** with a dedicated test; R-32/R-36/R-37/R-38/R-54/R-17 flip to CLOSED_FROZEN in this document.
- **What becomes FROZEN afterward:** public API surface and error-code vocabulary; JSON boundary shapes; `raw_mode` option semantics.

### Macro B — 1.0 Release Acceptance

- **Objective:** BLK-03 + final guarantee freeze.
- **Included blocker IDs:** BLK-03 (P2).
- **Forbidden scope:** no runtime behaviour change; no new public symbols beyond Macro A outcomes; no benchmark changes.
- **Tests:** documented-surface test updated to the frozen `__all__`; compatibility matrix final check; release-state gate for 1.0 (mirroring the 0.3 gate).
- **CI:** full matrix green on the 1.0 release branch.
- **Definition of Done:** this document has zero BLOCKER rows; status counts updated; 1.0 compatibility/deprecation policy published.
- **What becomes FROZEN afterward:** the 1.0 API contract and guarantees (this matrix becomes the release record).

### Macro C — Publication + post-publish verification (externally gated)

- **Objective:** execute the 1.0 publication sequence once EXB-01 is resolved externally.
- **Included blocker IDs:** EXB-01 (external; not a code blocker).
- **Forbidden scope:** no code changes; publishing actions only per `PUBLISHING.md` after the PyPI-side Trusted Publisher is verified.
- **Tests/CI:** post-publication install-profile smokes on the exact published wheel.
- **Definition of Done:** 1.0 published + provenance verified; EXTERNAL_BLOCKER cleared.
- **What becomes FROZEN afterward:** 1.0 release artifact.

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

- `python -m pytest -q`: **456 passed** (docs-only branch; suite unchanged).
- `python -m ruff check src tests benchmarks examples scripts`: clean.
- `git diff --check`: clean.
- No new performance baseline created; canonical hashes verified unchanged.
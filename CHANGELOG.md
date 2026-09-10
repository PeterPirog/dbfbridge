# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.1.0] - 2026-09-10

### Added
- Public Direct Write API (additive v1.1 contract, `docs/api-1.1.md`):
  `write_table()` writes a typed `TableSchema` + record stream as a fresh
  DBF/FPT pair through the single shared physical writer — `overwrite`
  defaults to `False` (stable `OUTPUT_EXISTS` code), the caller iterable is
  consumed exactly once (bounded streaming; private bounded spool for the
  Varchar/`_NullFlags` second pass), `progress=` uses the canonical
  `ProgressEvent` (`operation="write"`), `cancel_check=` is honoured at
  record boundaries and before final publication, and the DBF+FPT pair is
  published as one transaction (staging + fsync + atomic `os.replace`,
  previous pair restored after handled failures). The result is the
  JSON-safe `WriteResult`; failures are typed in the separate
  `DirectWriteError` family with additive structured codes
  (`WRITE_*`/`DESTINATION_IO_ERROR`). Structural CDX is never fabricated
  (`index_rebuild_required=True`); DBC semantics are not restored. The
  package version stays governed by the controlled release lifecycle.
- Optional dependency split: the base wheel installs with exactly one
  mandatory runtime dependency (`dbfread`) and covers `import dbfbridge`,
  the complete read-only Direct Read surface, and DBF → JSONL/JSON/CSV
  migration. Heavy capabilities became opt-in extras:
  - `[write]` — the additive v1.1 Direct Write operation (`write_table`),
    DBF/FPT reconstruction (`reconstruct_dbf`, `check_conversion_quality`);
  - `[xlsx]` — XLSX export and XLSX-format reading/verification support;
  - `[fast]` — optional `orjson`/`polars` accelerators (pure speed:
    identical logical results, absence never raises);
  - `[write,xlsx]` — XLSX → DBF/FPT reconstruction (both extras together);
  - `[all]` — the complete user-facing feature set;
  - `[import]` — historical compatibility alias resolving to `[write]`.
- Direct Read progress and cooperative cancellation: optional keyword-only
  `progress=` and `cancel_check=` parameters on `iter_records()`,
  `read_records()`, and `iter_raw_records()`; existing calls stay valid.
- `READ_CANCELLED` structured error (`ReadCancelledError`) carrying a
  JSON-safe progress context, with guaranteed DBF/FPT handle cleanup.
- Shared canonical progress contract: `ProgressEvent`, `ProgressCallback`,
  and `CancellationCheck` are public exports shared by Direct Read and
  migration operations.
- Explicit Polish encoding hardening: `mazovia`, `piast`, and `pki`
  overrides (plus `cp1250`/`cp852`) are handled at operation time — no
  manual codec registration and no private-module import; an unknown codec
  raises the typed `EncodingUnknownError` (`ENCODING_UNKNOWN`).
- Missing optional extras fail typed and early:
  `OptionalDependencyMissingError` (`OPTIONAL_DEPENDENCY_MISSING`) is raised
  before any output is created, never auto-installs, and never accesses the
  Internet.
- Fixed canonical reconstruction of NULL-bearing Visual FoxPro Varchar
  tables: a set `_NullFlags` NULL bit now resolves to `None` on both sides
  of the round trip, so `reconstruct_dbf` verification matches the source
  instead of reporting a false blank-vs-NULL mismatch.
- Unified `_NullFlags` semantics across Direct Read, reconstruction
  checksum, writer NULL detection, and diagnostics: one bit-allocation
  engine (varlength bits of `V`/`Q` fields included) instead of parallel
  importer arithmetic; verification re-reads the rebuilt table through the
  shared physical record loop with the configured loss-aware parser.
- Mixed nullable Varchar/ordinary fields now reconstruct canonically; the
  Varchar logical-layout repair keeps canonical reconstruction in every
  raw mode without requiring per-record raw images.
- Raw retention modes for the migration export: `raw_mode="none" |
  "metadata" | "full-record"` on `export_dbf`/`ExportOptions` and the
  `--raw-mode` CLI option (default `full-record` keeps the historical
  forensic behaviour); the loss-aware raw-text fallback and binary-memo
  markers are retained in every mode.
- Machine-readable public error contract: `OperationError` payload model,
  typed public-boundary exceptions (`OperationArgumentError` remaining a
  `ValueError`, `OperationPathError` remaining a `FileNotFoundError`,
  `OperationOutputExistsError` remaining a `FileExistsError`), and the
  shared `ErrorCode` vocabulary extended with
  `OPTIONAL_DEPENDENCY_MISSING`, `OUTPUT_EXISTS`, `RECONSTRUCTION_FAILED`,
  `ROUNDTRIP_MISMATCH`, `OPERATION_FAILED`.
- Structured `error_details` on per-table results (`TableResult`,
  `ReconstructionResult`) alongside the existing human-readable `errors`
  strings.
- JSON-safe `to_dict()` on `ExportRunResult`, `ReconstructionRunResult`,
  `VerificationRunResult`, `QualityRunResult`, and `DBFBridgeRunError`
  (now carrying a machine `code` plus all underlying structured details).
- FPT corruption boundary hardening: trailing payload beyond EOF, declared
  payload length beyond EOF, truncated block header (lazy read fails only
  at `load()`), empty payload, non-default block sizes, multiple memo
  fields per record, deleted records with memos, per-block text/binary
  typing, and atomic reconstruction failures that publish nothing and
  leave no `.partial` residue.

### Fixed
- Direct Write regression comparator: `compare_direct_write_regression
  --output-json` emitted a literal backslash+`n` suffix after the JSON
  document (uncaught until the first real CI consumer); it now terminates
  the document with one real newline, and a CLI-path regression test proves
  strict `json.loads`/`json.load` consumption with exactly one trailing LF.

### Developer / infrastructure
- Performance regression CI: canonical Phase 3 BEFORE baseline, measured
  regression policy with calibration provenance, strict workflow-ID and
  policy-parameter integrity validation.
- Direct Write performance lifecycle (DBFB-PERF-006, additive): the
  W1-W12 Direct Write benchmark; the F3A multi-sample main-push calibration
  (five FULL replicas, descriptive statistics only, no threshold); the F3B1
  committed versioned regression policy plus self-verifying offline
  comparator (canonical per-record wall ratios and the raw W3/W1
  peak-RSS-delta ratio; mechanically derived statistics; fail-closed policy
  and candidate validation); and the F3B2 dedicated regression workflow
  (`.github/workflows/direct-write-regression.yml`) running one FULL
  candidate on `pull_request` and `push` to `main` against the committed
  policy with an explicit COMPARABLE/PASS gate — the policy is never
  rewritten automatically and historical Phase 3 remains separate.
- PyPI install-profile wheel smokes: fresh-venv verification of every
  install profile (base, `[write]`, `[xlsx]`, `[write,xlsx]`, `[fast]`,
  `[all]`, `[import]`) outside the repository checkout.
- Release workflow hardening: wheel smokes read the expected version from
  `pyproject.toml` (no hardcoded historical version), the publish build
  job runs both wheel smokes on the exact artifact, and the same artifact
  is uploaded and published (build once → smoke → publish the same files).

### Changed
- Base-wheel dependency contract: installing `dbfbridge` no longer pulls
  `dbf`, `openpyxl`, `xlsxwriter`, `orjson`, or `polars`. Reconstruction
  requires `pip install "dbfbridge[write]"`, XLSX support requires `[xlsx]`,
  and `pip install "dbfbridge[all]"` restores the full capability set.
  Missing extras fail with the typed error above — they never change the
  read/export behavior.
- Base-wheel explicit Polish encoding correctness: cp1250 → cp852 → Mazovia
  decoding works in the minimal base install; explicit overrides no longer
  depend on caller-side codec registration order.
- Documentation convergence: maintained user-facing documentation, examples
  and the tool-server/MCP integration guide were brought to release-truth
  agreement with the implemented v1.1 architecture (Direct Write install
  profiles, nine-plus-one public surface terminology, current PyPI/release
  truth, the implemented Direct Write regression CI lifecycle, and the
  production host-security checklist).

## [0.2.0] - 2026-09-01

### Added
- Phase 1A Direct Read Core: `inspect_table()` and `read_schema()` — read-only
  inspection of one DBF table with no output files and bounded source reads
- Phase 1B streaming Direct Read: `iter_records()`, `read_records()`, and
  `iter_raw_records()` — typed streaming access with O(1) memory
- Field projection (case-insensitive, caller-selected order, unselected fields
  not parsed)
- Memo policies: `skip`, `null`, `lazy`, `inline` with explicit contract
- Raw forensic stream (`iter_raw_records`) preserving physical record bytes
- Typed, JSON-safe error model with stable machine codes
- Polish codepages cp1250 → cp852 → Mazovia fallback
- Phase 1 AFTER benchmark baseline recorded from GitHub Actions (24 MEASURED,
  PARTIALLY_COMPARABLE — legacy Phase 0 lacks storage/runner provenance)
- Structural CDX flag and DBC backlink metadata preserved

### Fixed
- VFP autoincrement semantics (field-flags mask 0x0C on Integer)
- FPT header validation (512-byte header record, nonzero block size)
- DBC backlink decoding with resolved encoding
- Companion discovery I/O error boundary

### Changed
- Development Status: Alpha (Direct Read Core stable, broader API stabilization continues)
- Python 3.14 CI support added

[Unreleased]: https://github.com/PeterPirog/dbfbridge/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/PeterPirog/dbfbridge/compare/v0.2.0...v1.1.0
[0.2.0]: https://github.com/PeterPirog/dbfbridge/tree/v0.2.0
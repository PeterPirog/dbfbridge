# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Raw retention modes for the migration export: `raw_mode="none" | "metadata" |
  "full-record"` on `export_dbf`/`ExportOptions` and the `--raw-mode` CLI option
  (default `full-record` keeps the historical forensic behaviour); the loss-aware
  raw-text fallback and binary-memo markers are retained in every mode
- Machine-readable public error contract: `OperationError` payload model, typed
  public-boundary exceptions (`OperationArgumentError` remaining a `ValueError`,
  `OperationPathError` remaining a `FileNotFoundError`, `OperationOutputExistsError`
  remaining a `FileExistsError`), and the shared `ErrorCode` vocabulary extended with
  `OPTIONAL_DEPENDENCY_MISSING`, `OUTPUT_EXISTS`, `RECONSTRUCTION_FAILED`,
  `ROUNDTRIP_MISMATCH`, `OPERATION_FAILED`
- Structured `error_details` on per-table results (`TableResult`,
  `ReconstructionResult`) alongside the existing human-readable `errors` strings
- JSON-safe `to_dict()` on `ExportRunResult`, `ReconstructionRunResult`,
  `VerificationRunResult`, `QualityRunResult`, and `DBFBridgeRunError`
  (now carrying a machine `code` plus all underlying structured details)

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

[0.2.0]: https://github.com/PeterPirog/dbfbridge/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/PeterPirog/dbfbridge/releases/tag/v0.1.0
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Phase 1A direct read core: `inspect_table()` and `read_schema()` — read-only,
  O(header) inspection of one DBF table with no output files, no record
  iteration, and a byte-identical source
- Stable, immutable, JSON-safe public models `FieldInfo`, `TableInfo`, and
  `TableSchema` (explicit `to_dict()`, no bytes/Path in the payload, no raw
  header Base64)
- Typed direct-read error model: `ErrorCode` machine codes
  (`DBF_HEADER_INVALID`, `DBF_TRUNCATED`, `DBF_FORMAT_UNSUPPORTED`,
  `ENCODING_UNKNOWN`, `PATH_NOT_FOUND`) and `DirectReadError` subclasses with
  JSON-safe `to_dict()`
- New `dbf_bridge.core` package holding the single shared implementation of
  the DBF header parser, field classification, and the Mazovia/PIAST
  codepage tables; the migration exporter now delegates to it instead of
  keeping a second copy
- Side-effect-free public import: `import dbfbridge` / `import dbf_bridge`
  registers no codepage, creates no files, and loads no CLI/reporting or
  optional heavy dependency (Polars, OpenPyXL, XlsxWriter, orjson, `dbf`);
  the Mazovia/PIAST codec is registered explicitly by the code paths that
  need it
- Direct read support for the Mazovia language driver byte (0x69) via the
  custom Polish codec
- `examples/inspect_table.py` — executable direct read example
- `docs/architecture/phase-1-direct-read.md` — Phase 1A architecture contract

### Notes
- Record reading (`iter_records`/`read_records`), field projection, and lazy
  memo reading remain the next phase; the benchmark scenarios
  `direct_read_bounded`, `field_projection`, `memo_lazy`, and `raw_mode_none`
  stay `NOT_IMPLEMENTED`
- The Phase 0 benchmark baselines remain the BEFORE reference

## [0.1.0] - 2026-08-09

### Added
- Initial project structure extracted from a legacy DBF migration toolkit.
- `dbf_bridge.exporter` — streaming, atomic DBF → CSV/JSON/JSONL export with:
  - SHA-256 validation and round-trip verification
  - Migration reports (`migration_report.jsonl` + `.csv`)
  - Schema files (`<table>_schema.json`) preserving complete DBF/VFP field,
    header, codepage, and FPT memo reconstruction metadata
  - Memo field policies: `skip` (null in CSV), `inline` (full text in JSON/JSONL), `null`
  - Deleted record policies: `skip`, `separate`, `include`
- `dbf_bridge.exporter.polish_codecs` — Mazovia/PIAST Polish OEM codepage registration
  with automatic fallback chain: cp1250 → cp852 → Mazovia
- `dbf_bridge.cli` — `dbf-bridge` CLI entry point (export)
- `dbf_bridge.verifier` — `dbf-bridge-verify` CLI entry point (verification)
- `tests/fixtures/generate_sample_dbf.py` — synthetic DBF generator for testing
- Streaming JSONL conversion API for JSON, CSV, and XLSX
- Polars `scan_ndjson`/`sink_csv` CSV fast path with bounded schema inference
- XlsxWriter constant-memory output with automatic Excel sheet splitting
- Strict line-numbered JSONL errors, cancellation/progress callbacks, and atomic outputs
- Synthetic large-file benchmark with time, throughput, record count, output size, and peak RSS
- Schema-driven JSONL/JSON/CSV/XLSX → Visual FoxPro DBF/FPT reconstruction
- Diagnostic DBF → JSONL → DBF quality checker with raw and canonical checksums,
  field-level differences, binary offsets, and retained intermediate artifacts
- Raw DBF/FPT headers, field descriptors, and source checksums in schema files for
  reproducible reconstruction
- Atomic `conversion_checksums.json` manifests and `--incremental` export that skips
  unchanged tables only after source, configuration, schema, and output verification
- Reproducible test fixtures generated automatically in pytest temporary storage
- Consistent user/developer documentation for all four CLIs, default dependencies,
  reports, reconstruction guarantees, and PowerShell examples
- Source-distribution manifest containing tests, fixture generator, examples, and benchmarks
- Typed high-level Python API importable through `from dbfbridge import ...` and the
  compatible `dbf_bridge` namespace
- Programmatic export, reconstruction, verification, and quality-check functions with
  reusable option objects, structured progress events, rich run results, and
  `raise_for_errors()` helpers
- Shared execution paths for CLI adapters and public operations instead of CLI-only logic
- Executable `examples/python_api.py` integration example and complete API documentation
- GitHub Actions CI for Python 3.10–3.13 on Linux and Python 3.12 on Windows
- Secretless PyPI Trusted Publishing workflow with release-tag validation
- PyPI release checklist, explicit SPDX license files, and packaging consistency tests

### Fixed
- Closed the generated FPT read handle before raw-layout replacement, preventing
  Windows `WinError 5` failures during memo-table reconstruction
- Preserved complete VFP DBF header regions, structural-index flags, fallback-codepage
  bytes, and per-record binary/text memo types during JSON/JSONL reconstruction
- Preserved the physical order of active and deleted records with `--deleted include`
- Retained source record images in JSON/JSONL, restored original memo pointers, and
  relocated generated FPT blocks before raw SHA-256 verification
- Reconstructed scientific notation used by Visual FoxPro in narrow N/F fields
- Classified DBF binary differences using the actual header and record lengths
- Accepted FoxPro's compact negative fractional representation in narrow numeric fields
- Added field-level diagnostics and explicit unverifiable counts to reconstruction reports
- Preserved memo values longer than Excel's 32,767-character cell limit in
  reconstructable `Dlugie_teksty_*` overflow sheets and reported their counts
- Recorded CSV, JSON, JSONL, and XLSX successes and failures in migration reports,
  including per-format summaries, hashes, schema references, XLSX sheet counts,
  run configuration, and the final exit code
- Added bounded-memory XLSX record verification across split worksheets
- Made XlsxWriter a default dependency so a standard install supports XLSX output
- Fixed Windows `Bad file descriptor` failures when atomically committing JSON and CSV files
- Removed the invalid CLI marker and undefined `jsonl_to_*` calls that prevented startup
- Avoided `json.load()` during validation of large JSON arrays generated by the exporter
- Converted only table JSONL outputs instead of schema and migration-report JSONL files
- Preserved separately exported deleted records in CSV, JSON, and XLSX conversions
- Required explicit, portable `--source` and `--output` paths for verification instead of
  defaults pointing to development fixtures that are absent from an installed wheel
- Clarified API defaults, warning handling, format selection, diagnostics, and safe
  single-owner Trusted Publishing setup in the user and release documentation

### Pending (planned for 0.2.0+)
- Index-aware CDX reconstruction when reliable tag definitions are available

[Unreleased]: https://github.com/PeterPirog/dbfbridge/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/PeterPirog/dbfbridge/releases/tag/v0.1.0

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-08-31

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
- Phase 1 AFTER benchmark baseline recorded from GitHub Actions (24 MEASURED)
- Structural CDX flag and DBC backlink metadata preserved

### Fixed
- VFP autoincrement semantics (field-flags mask 0x0C on Integer)
- FPT header validation (512-byte header record, nonzero block size)
- DBC backlink decoding with resolved encoding
- Companion discovery I/O error boundary

## [Unreleased]

### Added
- Phase 1 AFTER baseline recorded from GitHub Actions
  ([run 33405475850](https://github.com/PeterPirog/dbfbridge/actions/runs/33405475850),
  SUCCESS, measured at commit `df035de662f2d78a7a8d9d9a141a8235e1161382`):
  Windows Server 2025, Python 3.12.10, runner
  `github-actions-windows-2025-python-3.12.10`, storage
  `github-actions-windows-temp`, warm-up 1 + repetitions 3 per scenario,
  `run_id run-ceaf809d8b52a2b6873d7594dff4a769`; exactly **24 MEASURED /
  0 FAILED / 0 NOT_IMPLEMENTED**, `valid_baseline=true` everywhere, available
  peak RSS and zero temporary residue. Versioned as
  `benchmarks/baselines/phase-1-direct-read-full.{json,md,manifest.json}`
  with the comparison pair `phase-0-vs-phase-1.{json,md}`; the BEFORE/AFTER
  verdict is **PARTIALLY_COMPARABLE** (system/Python/dependency versions
  match; the legacy Phase 0 BEFORE carries no runner/storage descriptors and
  is never retro-fitted, so numbers and ratios are diagnostic only — they do
  not prove a performance improvement; the four Direct Read scenarios are
  NEWLY_MEASURED against a `NOT_IMPLEMENTED` BEFORE row, so no speedup is
  claimed for them and no blanket claim is made for pre-existing scenarios)
- Phase 1A direct read core: `inspect_table()` and `read_schema()` — read-only
  inspection of one DBF table (DBF read bounded by the declared header
  length, independent of the record count, plus a companion-file lookup) with
  no output files, no record iteration, and a byte-identical source
- Stable, immutable, JSON-safe public models `FieldInfo`, `TableInfo`, and
  `TableSchema` (explicit `to_dict()`, no bytes/Path in the payload, no raw
  header Base64)
- `FieldInfo` now exposes the full VFP descriptor surface: `nocptrans`
  (binary flag), `index_field_flag`, `is_autoincrement` plus
  `autoincrement_next_value`/`autoincrement_step`, and the semantic
  `is_binary` classification (binary C/V, G/P/binary memo)
- `TableInfo`/`TableSchema` expose the raw header table-flags byte
  (`table_flags` as int plus `table_flags_hex`) alongside the bit-mask
  booleans (`has_structural_cdx`, `has_memo_flag`,
  `is_database_container`), and `TableSchema` adds `dbc_backlink_path`
  (decoded relative DBC path)
- Typed direct-read error model: `ErrorCode` machine codes
  (`DBF_HEADER_INVALID`, `DBF_TRUNCATED`, `DBF_FORMAT_UNSUPPORTED`,
  `ENCODING_UNKNOWN`, `PATH_NOT_FOUND`, `DBF_IO_ERROR`) and
  `DirectReadError` subclasses with JSON-safe `to_dict()` (guaranteed
  `json.dumps`-able even with Path/bytes/enum/tuple contexts)
- New `dbf_bridge.core` package holding the single shared implementation of
  the DBF header parser, field classification, and the Mazovia/PIAST
  codepage tables; the migration exporter now delegates to it instead of
  keeping a second copy
- Side-effect-free public import: `import dbfbridge` / `import dbf_bridge`
  registers no codepage, creates no files, and loads no CLI/reporting or
  optional heavy dependency (Polars, OpenPyXL, XlsxWriter, orjson, `dbf`);
  the Mazovia/PIAST codec is registered explicitly by the code paths that
  need it; static type checkers resolve the full typed public surface via
  `TYPE_CHECKING` declarations
- Direct read support for the Mazovia language driver byte (0x69) via the
  custom Polish codec, including end-to-end export of LDID 0x69 tables with
  `encoding=auto`
- `examples/inspect_table.py` — executable direct read example
- `docs/architecture/phase-1-direct-read.md` — Phase 1A architecture contract

### Added
- Phase 1B streaming direct record read: read-only `iter_records()` /
  `read_records()` / `iter_raw_records()` with immutable, JSON-safe models
  `DirectRecord` (zero-based `physical_index`, `deleted`, `values`, optional
  `raw_record` only with `raw=True`), `RecordPage` (`offset`, `limit`,
  `records`, `scanned`, `next_offset`, `exhausted`) and `LazyMemoValue`
  (table/field/physical memo block; creation and `to_dict()` read no memo
  payload, explicit `load()` goes through the backend)
- Internal backend boundary in `dbf_bridge.core`: capability protocols
  (header inspection, physical record streaming, memo payloads) with the
  **dbfread reference backend** as the only adapter allowed to touch private
  `dbfread` API; one shared physical/decoded record loop (the migration
  exporter delegates its `iter_physical_records` to it — no second loop, no
  second header/type parser)
- Streaming semantics: `physical_index`/`offset`/`next_offset` are zero-based
  physical record indices resolved by seek; `iter_records` is O(1),
  `read_records` is O(limit) with positive `limit`/non-negative `offset`
  (`ARGUMENT_INVALID` otherwise); `include_deleted=False` skips deleted
  records in the same pass; `iter_raw_records` returns every record (deleted
  included) in physical order without opening the FPT; `raw=False` keeps no
  raw bytes anywhere
- Memo policies `skip`/`null`/`lazy`/`inline`: only `inline` reads the FPT
  (missing → `FPT_REQUIRED_MISSING`, broken → `FPT_INVALID`); `lazy` returns
  `LazyMemoValue` without any FPT I/O during iteration; `load()`/`read()`
  raises the same typed errors; `skip`/`null`/`lazy` never open or read the
  FPT payload
- Field projection: validated case-insensitively, result uses schema names in
  the caller's order, unselected fields are never parsed; unknown/duplicate
  names → `FIELD_PROJECTION_INVALID`, selected unsupported types →
  `FIELD_TYPE_UNSUPPORTED` (an unsupported unselected field never blocks the
  read)
- New machine codes (previous codes kept): `DBF_RECORD_INVALID`,
  `TEXT_DECODE_ERROR` (strict failures never leak a raw
  `UnicodeDecodeError`), `ARGUMENT_INVALID`, `FIELD_PROJECTION_INVALID`,
  `FIELD_TYPE_UNSUPPORTED`
- Real Phase 1 benchmark scenarios replacing the four `NOT_IMPLEMENTED`
  placeholders: `direct_read_bounded` (seek + `limit=100` over the 190k
  fixture, read amplification far below 1, zero output/temporary bytes),
  `field_projection` (same logical result as the unprojected stream),
  `memo_lazy` (zero FPT payload reads enforced by an open-guard),
  `raw_mode_none` (no raw bytes in any record); fast profile now reports
  **19 MEASURED / 0 NOT_IMPLEMENTED / 0 FAILED**, full contract 24 MEASURED
- `examples/read_records.py` — executable streaming record-read example
- Versioned benchmark report identity `benchmark_contract:
  "phase-1-direct-read-v1"` in the environment payload; the `--baseline` gate
  refuses a future Phase 1 AFTER snapshot without exactly this value
  (documents Phase 0 as the preserved BEFORE and forbids any
  performance-improvement claim before the AFTER baseline is saved)
- Contract-derived benchmark artifact naming with the Phase 0 pair reserved:
  local reports are now
  `benchmarks/results/phase-1-direct-read-<profile>[-<scenarios>].{json,md}`
  (and the versioned AFTER baseline `phase-1-direct-read-full.{json,md}`),
  so a Phase 1 `--baseline` can never overwrite the preserved Phase 0
  `phase-0-full.{json,md}` BEFORE pair; the legacy names stay reserved for
  the historical BEFORE artifact
- Pure, host-independent saved-baseline validators (`benchmarks/contract.py`):
  the frozen Phase 0 BEFORE contract (approved legacy form without
  `benchmark_contract`, 20 `MEASURED` + exactly the 4 documented
  `NOT_IMPLEMENTED` names) and the full Phase 1 AFTER contract (exact
  contract, stable `run_id`, 24 unique `MEASURED` scenarios, complete
  sample/warm-up counts, required metrics, peak RSS, zero temporary residue,
  repo identity, memo-reconstruction extras). `check_baseline_gate` now
  delegates these shared checks while keeping its live `psutil` check
- Publication derives everything from the ACTUAL source JSON: the full Phase
  1 AFTER validator runs on the really-read bytes (an independently passed
  payload is never trusted, so a modified-after-gate JSON is refused), the
  Markdown must carry the same `run_id`/contract/profile, and the
  publication is an **exception-safe transaction** with a crash-consistency
  manifest (`phase-1-direct-read-full.manifest.json`, published last):
  committed = JSON + Markdown + valid manifest with identical `run_id`
  (two independent `os.replace` calls are not crash-atomic between
  themselves; any handled failure rolls the directory back exactly)
- Comparison honesty hardened: `benchmarks/compare_baselines.py` runs the
  full validators on BOTH sides before comparing, verifies the AFTER
  manifest and file naming, compares only the 20 common `MEASURED`
  scenarios, restricts NEWLY_MEASURED to the four documented Phase 1
  placeholders (no invented speedup for anything else), and replaces the
  boolean environment flag with a three-state verdict — `COMPARABLE` /
  `PARTIALLY_COMPARABLE` (identical runtime environment, missing storage
  provenance) / `NOT_COMPARABLE` (any OS/Python/CPU/architecture/dependency
  difference) — while contract, commit, branch and the dbfbridge package
  version are never treated as environment mismatches
- Baseline provenance finalized: the `run_id` is UNIQUE per actual run
  (`run-<32 hex>` over a UTC microsecond timestamp, commit, contract,
  profile, parameters and a random nonce — two real runs with identical
  parameters never share one) and `generated_at` is timezone-aware UTC ISO
  8601 with microseconds; both are generated exactly once and carried
  identically in the JSON, the Markdown, the manifest and the publication
  message. The publication manifest binds generated_at, the full git commit
  and the runner/storage provenance to the published bytes (`manifest_problems`
  accepts expected_git_commit/expected_generated_at; a manifest with a
  different commit or timestamp is refused)
- Explicit provenance CLI: `--storage-label` describes the fixture/results
  storage (e.g. `windows-local-d-volume`, `github-actions-windows-temp`;
  no SSD/NVMe guessing) and `--runner-label` is either explicit or safely
  derived from the non-secret GitHub Actions variables
  (`GITHUB_ACTIONS`, `RUNNER_OS`, `RUNNER_ARCH`, `ImageOS`,
  `ImageVersion`) — local runs use the neutral `local` with no hostname,
  username or user paths. The JSON environment carries explicit `runner`
  and `storage` fields (storage may be null for fast runs); a full
  `--baseline` REQUIRES both and the manifest records them; fast runs work
  without labels (storage: null)
- Controlled comparison errors: every missing, corrupt, unreadable,
  non-UTF-8 or identity-mismatched artifact (AFTER JSON/Markdown/manifest,
  SHA/run_id/contract/profile/commit/generated_at) yields a clean
  `COMPARISON REFUSED` with exit 2 — raw `OSError`, `FileNotFoundError`,
  `UnicodeDecodeError`, `KeyError` or `struct` tracebacks never escape, and
  the companion Markdown must repeat the run identity
- The duplicated legacy comparability implementation was removed from
  `benchmarks/compare_baselines.py`; the three-state semantics now live
  exclusively in `benchmarks.contract.environment_comparability`
- Atomic Phase 1 baseline publication (`benchmarks/artifacts.py`): staged
  `.partial` JSON/Markdown pair published back-to-back with an in-memory
  round-trip check and post-write SHA-256 verification; a failure rolls the
  baselines directory back exactly (never half a pair, never a leftover
  `.partial`); an existing baseline is never overwritten and there is no
  force/overwrite flag (re-baselining needs an explicit, separate decision)
- stdlib-only BEFORE/AFTER comparison CLI `benchmarks/compare_baselines.py`:
  recognises the legacy Phase 0 BEFORE (no contract field) and requires
  `phase-1-direct-read-v1` for AFTER; compares common `MEASURED` scenarios
  (wall/CPU, records/s, source MiB/s, peak RSS, amplifications, temporary
  and output bytes with `NOT_AVAILABLE` handling and no division by zero);
  marks the four former `NOT_IMPLEMENTED` Direct Read scenarios as
  NEWLY_MEASURED without inventing a speedup; surfaces environment
  differences and never labels a change an improvement across unlike
  environments; refuses swapped, broken or wrong-contract artifacts with
  non-zero exit codes

### Fixed
- Phase 1B hardening: `memo="inline"` requires and opens the FPT only when the
  effective projection really contains memo fields (a projection of non-memo
  fields reads fine without any FPT), `memo="skip"` trims memo fields from the
  effective projection **before** the supported-type validation (a skipped
  unsupported memo field — e.g. a VFP Blob `W`, which is an FPT pointer —
  never blocks the read), and `null` renders selected memo fields as `None`
  without reading any payload
- FPT open race closed: a real `memo="inline"` stream opens the companion
  strictly (`ignore_missing_memofile=False`); a companion that vanishes
  between the eager validation and the first `next()` raises
  `FPT_REQUIRED_MISSING` (path + `policy` in the JSON-safe context) instead of
  silently streaming nulls; `_open_memofile` also maps `MissingMemoFile` and
  ENOENT to `FPT_REQUIRED_MISSING` while broken header/payload stays
  `FPT_INVALID` (no raw dbfread/struct/OSError exceptions escape)
- Premature record-area end (EOF or a `0x1A` marker before the header's
  declared record count) is now `DBF_TRUNCATED` with `record_index`,
  `declared_records` and `records_read` in the context; EOF is normal only
  after the whole declared area, and invalid markers stay
  `DBF_RECORD_INVALID`
- `DirectRecord.values` takes a defensive read-only snapshot in projection
  order (mutating the caller's dict no longer leaks into the record; item
  assignment raises `TypeError`); `RecordPage.records` is always snapshotted
  as a tuple; `to_dict()` remains a fresh, independently mutable, JSON-safe
  dict
- `iter_raw_records()` is a pure forensic stream: no field is parsed or
  decoded (even damaged text bytes never hide the record image), the FPT is
  never opened, `values` is an empty read-only mapping, and truncation/invalid
  markers are still reported; decoded values plus the raw image stay
  available via `iter_records(..., raw=True)`
- The benchmark `field_projection` scenario no longer materializes 190k
  tuples: the reference digest of the full unprojected read is computed once
  (outside the measured window) and every warm-up/repetition accumulates an
  O(1)-memory SHA-256 digest of the projected values only; `memo_lazy`
  instruments the **real backend memo boundary** (memofile open with
  `use_memofile=True`, explicit `read_memo_payload` calls, adapter-level FPT
  opens — never just `Path.open`) and every counter must stay zero; all
  instrumentation is restored in `finally`

### Fixed
- VFP autoincrement semantics corrected: for Visual FoxPro tables (0x30/
  0x31/0x32) `is_autoincrement` is derived from the field-flags mask 0x0C on
  an Integer (`I`) field (physical VFP type), not from the dBASE Level 7
  type `+`; the next value and step stay in descriptor bytes 19-22 (LE) and
  23. Bit 0x04 inside the autoincrement mask no longer reports an
  autoincrement Integer as `nocptrans` or semantic binary, while real 0x04
  flags on Character/Memo fields keep the NOCPTRANS meaning; `index_field_flag`
  (byte 31) is documented as migration-compatibility metadata only (VFP
  reserves bytes 24-31, so it is not reliable CDX-membership evidence)
- FPT header validation rewritten to the actual VFP rules: a full FPT header
  record is 512 bytes, the 8-byte prefix is enough for next-free/block-size
  reporting, files shorter than 512 bytes warn as structurally suspicious, a
  prefix shorter than 8 bytes warns as unreadable, and the stored block size
  must simply be nonzero (0 is invalid; 1-32 select 512-byte units —
  `SET BLOCKSIZE TO 0` stores 1 — and values above 32 are plain byte sizes,
  so 64/96/16384 are valid). The previous power-of-two 64-4096 assumption is
  removed
- FPT health validation now runs only for FPT companions. DBT/SMT companions
  (dBASE IV / HiPer-Six) are reported with their correct format and an
  explicit "not supported" warning, and their headers are never interpreted
  as FPT (no spurious "unreadable FPT header" warnings; exact warning sets
  are covered by tests)
- DBC backlink decoding uses the encoding resolved from the language driver
  (or the explicit override) instead of forcing UTF-8. A non-empty backlink
  that cannot be decoded keeps `dbc_bound = true`, reports
  `dbc_backlink_path` as `null` (never raw bytes) and adds a diagnostic
  warning naming the encoding; cp1250 backlinks with Polish characters
  decode correctly
- `TableSchema.memo_companion_format` reports the format implied by the DBF
  version (e.g. FPT) even when the companion file itself is missing, as
  long as memo fields or the memo table flag say a companion is expected;
  presence, path, and size stay separate, honest fields
- Companion metadata I/O is fully typed: every stat/open/read around a
  companion file converts `OSError` into `DbfIoError` (`DBF_IO_ERROR`) with
  path and JSON-safe context, and a single `read_schema` call opens a given
  FPT header at most once (the same details feed the model and the
  validation instead of reading the header twice)
- Companion discovery boundary normalized: the exact-path candidate check
  uses explicitly protected `stat` (ENOENT/ENOTDIR mean "companion absent";
  any other OSError — incl. access denied — becomes a `DbfIoError` naming
  the specific companion with JSON-safe `errno`/`operation="stat"` context,
  never a leaked `PermissionError`), and during the case-insensitive scan
  both `os.scandir` failures and `DirEntry.is_file()` failures on matching
  entries raise `DbfIoError` reporting the concrete entry path. A genuinely
  missing companion and an inaccessible one are distinct states
- Header table-flags byte (offset 28) is now treated as a bit mask
  (0x01 structural CDX / 0x02 memo / 0x04 database container); a memo-only
  0x02 value no longer implies a structural CDX. The raw value stays
  available as `structural_index_flag` for migration-schema compatibility
- DBC backlink semantics corrected: the 263-byte VFP extension after the
  field terminator holds a null-terminated relative DBC path (first byte
  0x00 = standalone); the previous little-endian record-number
  interpretation (`dbc_backlink_record`) is removed, `dbc_bound` is kept,
  and `TableSchema.dbc_backlink_path` carries the decoded path when bound
- Descriptor scan is now bounded by the declared header length: only 0x0D
  is a valid terminator (0x0A is rejected), a descriptor crossing the header
  is truncated, and a malformed header can no longer be read as record data;
  a VFP 263-byte backlink area that does not fit before the record area is
  rejected
- Header last-update date is `1900 + year_byte` (no century pivot); an
  impossible month/day reports `last_update = None` plus a warning
- Memo companion format now follows the DBF version (VFP/FoxPro `.fpt`,
  dBASE III+/IV `.dbt`, HiPer-Six `.smt`); DBT/SMT are explicitly marked
  unsupported for reading in Direct Read, a short/invalid FPT header yields a
  diagnostic warning, a structural-CDX flag without a `.cdx` companion
  warns, and a `.cdx` companion without the flag is reported without
  setting `has_structural_cdx`
- Raw `PermissionError`/`OSError` from open/stat/read/directory scan no
  longer leak out of the core: they become `DbfIoError`
  (`DBF_IO_ERROR`) with path/context, and a failed directory scan is never
  disguised as "companion missing"
- Companion lookup performs at most one case-insensitive directory scan per
  call, checking the direct exact-name paths first (no scan in the common
  case)
- Export now passes the header-resolved encoding to `dbfread` when the user
  did not override it, so Mazovia (LDID 0x69) tables export correct Polish
  characters instead of failing on ASCII fallback

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

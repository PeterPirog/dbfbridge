# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - Unreleased

> Release preparation: this date is set in the final release commit at
> publication time. The section below already documents the complete 0.3.0
> change set.
>
> Explicit non-goals for 0.3 (decided by the Phase 3 benchmark policy): a
> native DBF reader was NOT introduced, because the measured dbfread-backed
> iterator showed no real bottleneck; and a writer rewrite was NOT
> introduced — direct-write research stays preserved on a deferred branch,
> outside this release.

### Added

User-visible:

- Optional dependency split: the base wheel installs with exactly one
  mandatory runtime dependency (`dbfread`) and covers `import dbfbridge`,
  the complete read-only Direct Read surface, and DBF → JSONL/JSON/CSV
  migration. Heavy capabilities became opt-in extras:
  - `[write]` — DBF/FPT reconstruction (`reconstruct_dbf`,
    `check_conversion_quality`);
  - `[xlsx]` — XLSX export and XLSX input reading;
  - `[fast]` — optional `orjson`/`polars` accelerators (pure speed:
    identical logical results, absence never raises);
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
- - 0.2 → 0.3 migration guide: `docs/migration-0.3.md`.
- 0.2 → 0.3 migration guide: `docs/migration-0.3.md`.
- Fixed canonical reconstruction of NULL-bearing Visual FoxPro Varchar
  tables: a set `_NullFlags` NULL bit now resolves to `None` on both sides
  of the round trip, so `reconstruct_dbf` verification matches the source
  instead of reporting a false blank-vs-NULL mismatch.
- Unified `_NullFlags` semantics across Direct Read, reconstruction
  checksum, writer NULL detection, and diagnostics: one bit-allocation
  engine (varlength bits of `V`/`Q` fields included) instead of parallel
  importer arithmetic; verification re-reads the rebuilt table through the
  shared physical record loop with the configured loss-aware parser.
- Mixed nullable Varchar/ordinary fields now reconstruct canonically,
  including interleaved deleted records; ordinary nullable `C`/`I`/`Y`
  round trips are regression-protected.
- VFP/FPT compatibility matrix: `docs/compatibility-vfp.md` — an
  evidence-based status per physical field type and FPT edge case, built on
  authentic Visual FoxPro 0x32 fixtures.
- Varchar/`_NullFlags` correctness hardening: authentic VFP 0x32 Varchar
  decoding now honours the physical contract — per-record varlength bits
  (the last payload byte carries the actual value length), NULL bits, and
  the full-width storage form; significant trailing spaces in Varchar
  values are preserved instead of being stripped.
- Nullable VFP fields resolve through the `_NullFlags` bitmap: a set NULL
  bit yields `None` for ordinary nullable `C`/`I`/`Y` fields (and NULL
  memos) instead of blank/zero storage values, and NULL memos never touch
  the FPT.
- Varchar text decoding honours the configured encoding policy: the shared
  read loop isolates the exact logical bytes and delegates text decoding to
  the configured parser, so the export path's loss-aware Polish fallback
  works for Varchar too — the exact logical Unicode is written to JSONL and
  the original raw bytes are retained under
  `__dbfbridge_raw_text_fields__`.
- VFP B (Double) correctness: a table whose only `B` column is an inline
  VFP double no longer spuriously requires an FPT companion to export or
  reconstruct; tables with real memo fields remain strict about a missing
  FPT (typed `FPT_REQUIRED_MISSING` / structured per-table failure).
- FPT corruption boundaries are typed and covered: pointer beyond EOF,
  declared payload length beyond EOF, truncated block header (lazy read
  fails only at `load()`), empty payload, non-default block sizes,
  multiple memo fields per record, deleted records with memos, per-block
  text/binary typing, and atomic reconstruction failures that publish
  nothing and leave no `.partial` residue.

Known limitation (documented, not hidden): Varchar reconstruction is
canonically correct, but exact physical Varchar DBF layout reconstruction
remains a documented limitation — `reconstruct_dbf` matches the original
table logically (canonical checksums) and not yet byte-for-byte.

Developer/infrastructure:

- Performance regression CI: canonical Phase 3 BEFORE baseline, measured
  regression policy with calibration provenance, strict workflow-ID and
  policy-parameter integrity validation.
- Windows Server 2025 / Python 3.12.10 performance recipe recorded as the
  canonical benchmark environment provenance.
- PyPI install-profile wheel smokes: fresh-venv verification of every
  install profile (base, `[write]`, `[xlsx]`, `[write,xlsx]`, `[fast]`,
  `[all]`, `[import]`) outside the repository checkout.
- Release workflow hardening: wheel smokes read the expected version from
  `pyproject.toml` (no hardcoded historical version), the publish build job
  runs both wheel smokes on the exact artifact, and the same artifact is
  uploaded and published (build once → smoke → publish the same files).

### Changed

- Base-wheel dependency contract: installing `dbfbridge` no longer pulls
  `dbf`, `openpyxl`, `xlsxwriter`, `orjson`, or `polars`. Reconstruction
  requires `pip install "dbfbridge[write]"`, XLSX support requires `[xlsx]`,
  and `pip install "dbfbridge[all]"` restores the full pre-0.3 capability
  set. Missing extras fail with the typed error above — they never change
  the read/export behavior.
- Base-wheel explicit Polish encoding correctness: cp1250 → cp852 → Mazovia
  decoding works in the minimal base install; explicit overrides no longer
  depend on caller-side codec registration order.

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

[0.3.0]: https://github.com/PeterPirog/dbfbridge/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/PeterPirog/dbfbridge/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/PeterPirog/dbfbridge/releases/tag/v0.1.0
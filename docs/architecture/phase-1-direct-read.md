# dbfbridge — Phase 1: Direct Read Core (inspection, schema, record streaming)

- Base commit (Phase 0 end): `49500785444ffa2e798146c14a158d926c158c34`
  (branch `bench/phase-0-baseline`, stacked on unmerged PR #1)
- Package: `dbfbridge 0.1.0` (alpha), Python >= 3.10, MIT
- Scope: **Phase 1A** — `inspect_table()` / `read_schema()` and the
  `dbf_bridge.core` foundation; **Phase 1B** (this document's record layer) —
  `iter_records()` / `read_records()` / `iter_raw_records()` streaming over
  the backend abstraction. Lazy memo payload *reading* exists only through the
  explicit `LazyMemoValue.load()`; there is still no bulk "read whole memo
  ahead" mode.

This document records the architectural contract implemented in this phase and
links it to the code.

## 1. What Phase 1A and 1B deliver

| Symbol | Kind | Home |
|---|---|---|
| `inspect_table(path)` | function | `src/dbf_bridge/core/inspect.py` |
| `read_schema(path)` | function | `src/dbf_bridge/core/inspect.py` |
| `iter_records(path, ...)` | function (Phase 1B) | `src/dbf_bridge/core/records.py` |
| `read_records(path, ...)` | function (Phase 1B) | `src/dbf_bridge/core/records.py` |
| `iter_raw_records(path)` | function (Phase 1B) | `src/dbf_bridge/core/records.py` |
| `DirectRecord` / `RecordPage` / `LazyMemoValue` | frozen dataclasses (Phase 1B) | `src/dbf_bridge/core/records.py` |
| `TableInfo` / `TableSchema` / `FieldInfo` | frozen dataclasses | `src/dbf_bridge/core/models.py` |
| `ErrorCode` + typed errors | str enum (machine codes) | `src/dbf_bridge/core/errors.py` |
| backend protocols + dbfread adapter | internal boundary (Phase 1B) | `src/dbf_bridge/core/backend.py` |

All entry points are exported from `dbfbridge` and the historical
`dbf_bridge` namespace (`src/dbfbridge/__init__.py`,
`src/dbf_bridge/__init__.py`) with synchronized `__all__` lists.

## 2. Architectural boundary: `dbf_bridge.core`

```
src/dbf_bridge/core/
├── __init__.py       public core surface (inspection, schema, record streaming, models, errors)
├── errors.py         ErrorCode + DirectReadError subclasses (JSON-safe to_dict)
├── codecs.py         Mazovia/PIAST table + registration + driver resolution
├── fields.py         pure field classification (memo/binary/supported) + type names
├── header.py         single pure DBF header parser (O(header), read-only)
├── models.py         FieldInfo / TableInfo / TableSchema (frozen, to_dict)
├── backend.py        Phase 1B backend boundary: capability protocols + dbfread reference adapter
├── records.py        Phase 1B: DirectRecord / RecordPage / LazyMemoValue + iter_records /
│                     read_records / iter_raw_records
└── inspect.py        public inspect_table / read_schema + companion discovery
```

Hard rules for the core layer:

- no CLI, no reporting, no migration orchestration, no reconstruction imports;
- no output files, no reports, no locks, no `.partial` artifacts;
- no Polars, OpenPyXL, XlsxWriter, orjson, and no `dbf` writer package
  (core requires only the standard library plus the pure-Python `dbfread`
  codepage table);
- no network, no COM, no VFP;
- no printing, no `sys.exit`;
- the DBF read is bounded by the declared `header_length` and independent of
  the number of records; the descriptor scan never runs past it, so a
  malformed header can never be mistaken for record data; plus at most one
  companion-file lookup per call in the table's directory (direct exact-name
  paths are checked first, so the common case performs no directory scan);
- no duplicate parser: the migration exporter delegates to
  `core.header.parse_header` (`src/dbf_bridge/exporter/reader.py`), and the
  Mazovia table exists in exactly one place (`core/codecs.py`;
  `exporter/polish_codecs.py` is a compatibility re-export);
- **one record loop** (Phase 1B): the physical/decoded record streaming lives
  exactly once, in `core/backend.py` (the dbfread reference adapter). The
  migration exporter's `iter_physical_records` delegates to it, producing the
  same `(record, is_deleted, raw_image)` tuples as before. Private `dbfread`
  API (`DBF._open_memofile`, the `dbfread.memo` submodule,
  `FieldParser._parse_memo_index`) is confined to `core/backend.py`; no other
  module — including the exporter — may reach into it.

`src/dbfbridge` remains a thin public facade; there is no second, parallel
header parser and no second record loop.

## 3. Import side-effect contract

`import dbfbridge` (and `import dbf_bridge`):

- registers **no** codepage (the Mazovia/PIAST codec is registered
  explicitly by the code paths that decode with it —
  `core.codecs.register_polish_codecs()` / `driver_to_encoding()`);
- creates no files;
- imports no Polars, OpenPyXL, XlsxWriter, orjson, and no `dbf` package;
- loads no CLI/reporting modules.

Public symbols are resolved lazily (PEP 562 `__getattr__`) while staying
import-compatible: `from dbfbridge import export_dbf` and
`from dbf_bridge import export_dbf` behave exactly as before. Verified in a
fresh interpreter by `tests/test_direct_read_schema.py::test_fresh_interpreter_import_has_no_side_effects`.

## 4. Public model contract

- immutable (`dataclass(frozen=True)`), fully typed, CLI-independent;
- JSON-serializable through an explicit `to_dict()`; the payload is
  exclusively JSON-safe (str/int/float/bool/list/dict/None) — no `bytes`, no
  `Path`;
- no raw header Base64 and no memo payloads: Direct Read must not pay the
  `FORENSIC_ROUNDTRIP` cost.

`FieldInfo` keeps the physical VFP facts an MCP consumer needs: `ordinal`,
`name`, `dbf_type` + readable `dbf_type_name`, `length`, `decimal_count`,
`address`, raw `flags` as int plus derived `system` / `nullable` /
`nocptrans` (the binary flag **only where VFP documents it**: Character/
Varchar and memo fields — bit 0x04 is inside the autoincrement mask 0x0C and
never makes an autoincrement Integer NOCPTRANS-binary), `index_field_flag`
(descriptor byte 31, kept for migration-schema compatibility only — VFP
reserves bytes 24-31, so it is **not** reliable evidence of CDX membership),
`is_autoincrement` plus `autoincrement_next_value` (descriptor bytes 19-22,
little-endian) and `autoincrement_step` (byte 23), the semantic `is_binary`
classification (true for binary C/V, G/P memos, binary B, null flags),
`is_memo`, `supported`, `unsupported_reason`.

`is_autoincrement` follows the actual format semantics: for a Visual FoxPro
table (version bytes 0x30/0x31/0x32) a field is autoincrementing when its
field-flags mask is 0x0C **and** its physical type is Integer (`I`) — the
next value and step live in descriptor bytes 19-22 (LE) and 23. The dBASE
Level 7 type `+` remains an autoincrement marker outside VFP only; it is
never presented as evidence of VFP autoincrement semantics.

`TableInfo` carries: `path`, `record_count`, `header_length`,
`record_length`, `language_driver`, `encoding`, `has_memo`, `has_memo_flag`,
`has_structural_cdx`, `is_database_container`, the raw `table_flags`
(byte 28) plus its hex form, `dbc_bound`, `fields`, `warnings`.

`TableSchema` additionally carries: `dbversion_byte` / `dbversion_name`,
`last_update`, `incomplete_transaction`, `encryption_flag`, the raw
`table_flags` plus its hex form, `dbc_backlink_path` (the decoded relative
DBC path when bound), companion memo details (`memo_companion_format`,
`memo_companion_present`, `memo_companion_path`, `memo_companion_size_bytes`,
`memo_block_size`, `memo_next_free_block`) and structural CDX companion
presence (`companion_cdx_present`, `companion_cdx_path`).

Semantics:

- the header table-flags byte (offset 28) is a **bit mask**: 0x01 structural
  CDX (`has_structural_cdx`), 0x02 memo in use (`has_memo_flag`), 0x04
  database container (`is_database_container`); the raw value stays
  available as `table_flags` / `table_flags_hex` on the public models (and
  as `structural_index_flag` in the migration exporter for schema
  compatibility). In particular a memo-only 0x02 value never implies a
  structural CDX;
- `has_memo` = the table declares memo fields; companion presence is
  separate metadata — a missing companion is a **structured warning** in
  `warnings`, not an error, as long as the header itself is safe, and
  `memo_companion_format` still reports the format implied by the DBF
  version (e.g. FPT) whenever memo fields or the memo table flag say a
  companion is expected, with presence/path/size as separate fields;
- memo companion format depends on the DBF version: VFP/FoxPro use `.fpt`,
  dBASE III+/IV use `.dbt`, HiPer-Six uses `.smt`. Only FPT is supported for
  reading in Direct Read; DBT/SMT companions are reported with their format
  plus an explicit "not supported" warning, and their headers are **never**
  interpreted as FPT (no FPT-header warnings for them);
- FPT health checks apply to FPT companions only. A full FPT header record
  is 512 bytes; the 8-byte prefix (next-free block, bytes 0-3 big-endian,
  block size, bytes 6-7 big-endian) is sufficient for reporting, and a file
  shorter than the full header record is a "structurally suspicious"
  warning. The stored block size must be nonzero: 0 is invalid, 1-32 select
  512-byte units (`SET BLOCKSIZE TO 0` stores 1) and values above 32 are
  plain byte sizes (64 and 96 are valid). There is **no** power-of-two or
  4096-byte restriction; a missing 8-byte prefix is warned about as well;
- each `read_schema` call reads a given FPT header at most once (the same
  details feed the model and the validation), and every companion
  `stat`/`open`/`read` failure becomes a typed `DbfIoError`
  (`DBF_IO_ERROR`), never a raw `OSError`;
- the structural CDX flag without a `.cdx` companion yields a warning; a
  `.cdx` companion without the flag is reported as a companion but never
  sets `has_structural_cdx`;
- companion `.fpt`/`.cdx` discovery is case-insensitive, at most one
  directory scan per call (direct exact-name paths first). The whole
  discovery boundary is typed: the exact-path candidate check uses
  explicitly protected `stat`, and during the scan both `os.scandir` and
  `DirEntry.is_file()` failures raise `DbfIoError` (`DBF_IO_ERROR`)
  reporting the specific offending path (the companion candidate or the
  scanned entry) with a JSON-safe context (`errno`, `operation`).
  Missing and inaccessible are different states: ENOENT/ENOTDIR on a
  candidate means "companion absent" (`present=False`), while every other
  `OSError` — incl. access denied — is an error, never a silent "missing";
- `dbc_bound` is derived from the VFP database-container backlink stored in
  the 263-byte header extension after the field terminator: the first byte
  is 0x00 for a standalone table, otherwise the area holds a null-terminated
  relative DBC path (`dbc_backlink_path`). It is **not** the mere existence
  of a `.dbc` file, and the first two bytes are never interpreted as a
  little-endian record number. The path is decoded with the encoding
  resolved from the language driver (or the explicit override); a non-empty
  backlink that cannot be decoded keeps `dbc_bound = true`, reports the path
  as `null` (never raw bytes) and adds a diagnostic warning naming the
  encoding;
- the header last-update date is `1900 + year_byte` (no century pivot); an
  impossible month/day yields `last_update = None` plus a warning;
- CDX reporting is structural only: dbfbridge does **not** declare CDX tag
  expression / order / primary-tag parsing;
- the Mazovia language driver byte (0x69) resolves to the custom `mazovia`
  codec via `core.codecs.driver_to_encoding`, and the migration exporter
  passes that resolved encoding to `dbfread` explicitly (a manual
  `encoding=` override still wins), so LDID 0x69 tables export correct
  Polish characters even though dbfread falls back to ASCII for driver bytes
  it does not know.

## 4b. Phase 1B: streaming direct record read

`core/backend.py` defines the internal backend boundary as **capability
protocols** — header inspection, physical record streaming, and memo payload
reading — whose reference implementation is the dbfread adapter
(`DbfreadBackend`). Private `dbfread` API is confined there, `core` never
imports `exporter`, and the exporter delegates its physical record
iteration to the backend (one shared record loop; no second parser).

`core/records.py` composes the public streaming API:

- `iter_records(path, *, fields=None, include_deleted=False, memo="lazy",
  raw=False, encoding="auto", decode_errors="strict")` — O(1) streaming of
  `DirectRecord`(s);
- `read_records(path, *, offset=0, limit=100, ...)` — one physical page
  (`RecordPage`) with O(limit) memory;
- `iter_raw_records(path)` — every physical record (deleted included) with
  its exact raw image; the FPT is never opened.

Contract:

- `physical_index`, `offset`, and `next_offset` are **zero-based physical
  record indices**; `offset` is resolved by seek (records before it are not
  scanned), and the record order stays physical;
- premature end (EOF **or** a `0x1A` marker before the declared record count)
  raises `DBF_TRUNCATED` with `record_index`, `declared_records`, and
  `records_read` in the context — EOF is normal only after the whole declared
  record area has been processed; an invalid marker byte still raises
  `DBF_RECORD_INVALID`;
- `limit` must be positive and `offset` non-negative (`ARGUMENT_INVALID`
  otherwise); `read_records` materializes only O(limit) records — nothing
  beyond the page is parsed or decoded;
- `include_deleted=False` skips deleted records **within the same pass** (a
  deleted record costs one physical scan without parsing, never a second
  run-through);
- `iter_raw_records` is a **pure forensic stream**: no field is parsed or
  decoded, the FPT is never opened, `values` is an empty read-only mapping,
  and damaged text bytes cannot hide the exact `raw_record` image — while
  truncation and invalid markers are still detected. Decoded values together
  with the raw image are available through `iter_records(..., raw=True)`;
- `fields` is a projection: validated case-insensitively, values use schema
  names in the caller's order, unselected fields are **never parsed**;
  unknown or duplicate names raise `FIELD_PROJECTION_INVALID`; a selected
  unsupported field raises `FIELD_TYPE_UNSUPPORTED` while an unsupported
  unselected field never blocks the read. `memo="skip"` trims memo fields
  (incl. the unsupported VFP Blob `W`, which is an FPT pointer field) from
  the effective projection **before** this validation, so a skipped
  unsupported memo field cannot block either;
- memo policy semantics: `skip` — the memo field is absent from `values`;
  `null` — the field is present with `None` (the payload is not read); `lazy`
  — the value is a `LazyMemoValue` (table/field/physical block) and the FPT
  is not opened during iteration; `inline` — the payload is read through the
  backend immediately. `skip`/`null`/`lazy` never open or read any FPT
  payload; `inline` **only when the effective projection really contains
  memo fields** requires a companion (missing → `FPT_REQUIRED_MISSING`,
  broken → `FPT_INVALID`) — otherwise `inline` over non-memo projections
  works without any FPT. The inline open is **strict**
  (`ignore_missing_memofile=False`): a companion that vanishes between the
  eager validation and the first `next()` raises `FPT_REQUIRED_MISSING`
  (with `path` and `policy` in the JSON-safe context) instead of silently
  reading nulls; `LazyMemoValue.load()` raises the same typed errors;
- costs: `lazy` iteration never touches the FPT; only explicit `load()`
  performs a short per-value read through the backend, so lazy is the right
  default for bounded scans and inspection, while `inline` trades payload
  reads for immediate values (the migration exporter's `inline` export keeps
  its historic behaviour);
- public models are really immutable: `DirectRecord` takes a **defensive
  read-only snapshot** of `values` in projection order (mutating the
  caller's dict never leaks in, item assignment raises `TypeError`),
  `RecordPage.records` is always a tuple, and `to_dict()` returns a fresh,
  independently mutable, JSON-safe dict;
- `encoding="auto"` uses the Phase 1A language-driver resolution; a manual
  override wins; strict decode failures raise `TEXT_DECODE_ERROR` (never a
  raw `UnicodeDecodeError`); `replace`/`ignore` are passed through;
- resource guarantees: the DBF and FPT handles live inside the generator and
  are closed after the full pass, on error, on `iterator.close()` (and via
  garbage collection only as a safety net); after `close()` the DBF can be
  moved/removed even on Windows; inspection and record reads create no files
  and leave the source byte-identical (SHA-256 covered by tests).

The four Phase 1 benchmark scenarios (`direct_read_bounded`,
`field_projection`, `memo_lazy`, `raw_mode_none`) measure these contracts:
the bounded scenario proves `limit=100` does not scan the 190k table (read
amplification far below 1), `memo_lazy` instruments the **real backend memo
boundary** (`backend._open_memofile(use_memofile=True)`,
`backend.read_memo_payload`, and the adapter's `dbfread open_memofile`
binding) and requires all counters to stay zero across warm-ups and
repetitions (a controlled real memo read is proven to increment them),
`field_projection` verifies the same logical result with an O(1)-memory
digest (the reference full read is computed exactly once, outside every
measured window), and `raw_mode_none` verifies no raw bytes in any record.

## 5. Structured errors

Tainted or unsupported files never yield a partially-trusted model. Detected
conditions include: missing/non-file path, fixed header shorter than 32
bytes, header length outside the file bounds, a descriptor section that does
not terminate with 0x0D inside the declared header (0x0A is never a
terminator), a field descriptor crossing the header length, a VFP 263-byte
backlink area that does not fit before the record area, zero record length,
field-length/record-length inconsistency, physical record area shorter than
the header count, unknown DBF version, and unknown language driver.

Raw filesystem failures — companion exact-path stat, `os.scandir`,
`DirEntry.is_file()`, open, header read, FPT header read — are converted to a
typed `DbfIoError` instead of leaking `PermissionError`/`OSError`. A missing
companion (ENOENT/ENOTDIR) is reported as absent, not denied. During record
streaming the same boundary holds for open/seek/read, memo payload reads
(`FPT_INVALID` for broken companions, `FPT_REQUIRED_MISSING` for absent ones)
and strict text decoding (`TEXT_DECODE_ERROR`, including
`LazyMemoValue.load()`).

Machine codes (stable): `PATH_NOT_FOUND`, `DBF_HEADER_INVALID`,
`DBF_TRUNCATED`, `DBF_FORMAT_UNSUPPORTED`, `ENCODING_UNKNOWN`,
`DBF_IO_ERROR`, `DBF_RECORD_INVALID`, `TEXT_DECODE_ERROR`,
`FPT_REQUIRED_MISSING`, `FPT_INVALID`, `ARGUMENT_INVALID`,
`FIELD_PROJECTION_INVALID`, `FIELD_TYPE_UNSUPPORTED`.

Every exception carries `code`, `path`, a readable `message`, and a JSON-safe
`context` mapping; `to_dict()` is JSON-serializable even when the context
carries `Path`, `bytes`, enum, or tuple values (paths are reported in POSIX
form). No generic `RuntimeError`; MCP never needs to parse error text.

## 6. Explicit non-goals of this phase

- bulk memo prefetching (a "read whole memo ahead" mode beyond the explicit
  `LazyMemoValue.load()`), field-index acceleration, and a second (native)
  read backend — possible later steps, justified only by benchmarks;
- new writer, CDX tag-expression parser, MCP adapter;
- package version change (stays `0.1.0`);
- re-running the full benchmark profile or saving a Phase 1 `--baseline` (the
  stored Phase 0 baseline stays unchanged and remains the **BEFORE**
  reference; a Phase 1 AFTER baseline does not exist yet).

## 7. Verification

Phase 1A historical record: when Phase 1A landed,
`tests/test_direct_read_schema.py` carried 65 direct-read schema tests and the
full suite was 157 tests; GitHub Actions CI run **#18** on
`feat/phase-1-inspect-schema` is green
(https://github.com/PeterPirog/dbfbridge/actions/runs/33363553636).

Phase 1B verification status: local results on
`feat/phase-1-record-read`; the GitHub CI green run for this branch is still
pending (Phase 1A is not re-declared verified here, and Phase 1B will not be
declared verified until its full CI run is green).

- `tests/test_direct_read_schema.py` — 65 integration tests against real
  fixture DBF/FPT files (happy paths, table-flags bitmask values plus the
  raw `table_flags` exposure, DBC backlink path semantics including
  codepage-resolved decoding and undecodable backlinks, 1900+year date
  expansion, descriptor boundary hardening, VFP autoincrement Integer
  semantics with the 0x0C mask plus G/P binary memos, Mazovia LDID 0x69
  end-to-end export, DBT/SMT format reporting with exact warning sets,
  FPT block-size/nonzero and 512-byte header-record warnings, missing-FPT
  format reporting, typed companion I/O errors across exact-path stat /
  scandir / `DirEntry.is_file()` (missing vs. access-denied), the
  single-FPT-read guarantee, JSON-safe error payloads, read-only
  guarantees, fresh-interpreter import side effects, codec-on-demand
  registration, exporter delegation);
- `tests/test_direct_read_records.py` — 45 streaming record tests against
  real fixtures (empty/single/multi-record tables, active+deleted physical
  order in a single pass, offset/limit/`next_offset`/`exhausted` pages,
  O(limit) non-materialization, projection that provably skips the parser,
  unknown/duplicate projection errors, `FIELD_TYPE_UNSUPPORTED` vs.
  unselected-unsupported, all supported VFP value types with NULL/empty
  values, cp1250/cp852/Mazovia diacritics, decode `strict`/`replace`/
  `ignore`, all four memo policies with the FPT-open guard, lazy-vs-inline
  and exporter parity, missing/broken FPT, binary memo bytes, raw split
  with exact-byte checks, invalid record marker, mid-stream truncation,
  typed open/read I/O errors, early break/gc handle release, SHA-256 and
  no-output guarantees, JSON-safe payloads, exporter parity, fresh
  interpreter, both namespaces);
- full suite: 202 tests green (the pre-existing export, reconstruction,
  Polish codecs, benchmark infrastructure suites stay green and the exporter
  produces the same results after the backend refactor);
- `ruff check` + `ruff format --check` clean; `python -m build` +
  `twine check` clean; all four CLI `--help` entry points start;
- fast benchmark regression: **19 MEASURED / 0 FAILED / 0 NOT_IMPLEMENTED**,
  exit code 0 (no `--baseline`); the Phase 0 baseline
  `benchmarks/baselines/phase-0-full.json` / `phase-0-full.md` remain the
  **BEFORE** reference and are byte-identical to commit 4950078; a Phase 1
  full AFTER baseline does not exist yet;
- Phase 1 artifacts are **contract-named** (`benchmarks/artifacts.py` is the
  single source): local reports live at
  `benchmarks/results/phase-1-direct-read-<profile>[-<scenarios>].{json,md}`
  and the future versioned AFTER baseline at
  `benchmarks/baselines/phase-1-direct-read-full.{json,md}`; the preserved
  Phase 0 `phase-0-full.{json,md}` names are RESERVED for the BEFORE pair
  (publication to them is impossible). `--baseline` publishes an
  **exception-safe transaction**: the ACTUAL source JSON is fully validated
  with the frozen Phase 1 AFTER contract (an independently passed payload is
  never trusted), the Markdown must carry the same `run_id`, contract and
  profile, and the staged `.partial` trio (JSON + Markdown + manifest) is
  published with a post-write pass that verifies bytes, SHA-256 hashes, the
  manifest and a JSON re-validation, removing all three new files on any
  failure. The manifest (`phase-1-direct-read-full.manifest.json`, published
  last) is the crash-consistency marker: an AFTER baseline is committed ONLY
  when JSON, Markdown and a corroborating manifest all exist with an
  identical `run_id`. Two unrelated `os.replace` calls are not
  crash-atomic between themselves; no force/overwrite flag exists
  (re-baselining is an explicit, separate decision). The future AFTER
  comparison tool is `benchmarks/compare_baselines.py` (BEFORE = legacy
  Phase 0 artifact validated by the frozen Phase 0 contract, AFTER =
  `phase-1-direct-read-v1` with manifest verification, the 20 common
  `MEASURED` scenarios compared, NEWLY_MEASURED restricted to the four
  documented placeholders without invented speedups, and a three-state
  environment verdict: COMPARABLE / PARTIALLY_COMPARABLE / NOT_COMPARABLE,
  where contract, commit, branch and the dbfbridge version are never
  environment mismatches and the legacy Phase 0 file can never be
  retro-fitted with storage provenance). Phase 0 BEFORE SHA-256 (unchanged):
  `phase-0-full.json` = `d3b5ab45...f07453aa6`, `phase-0-full.md` =
  `137ade61...4f06eff0c` (full values recorded in `phase-0-audit.md` §17).

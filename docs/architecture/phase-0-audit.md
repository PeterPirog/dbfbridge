# dbfbridge — Phase 0 technical audit

- Baseline commit: `addbadb9281914661bf742924f45039e46a895cd` (== `origin/main` at audit time)
- Package: `dbfbridge 0.1.0` (alpha), Python >= 3.10, MIT
- Audited environment: CPython 3.14.0 on Windows 11 (10.0.26200), x64, 32 logical CPUs, 63 GiB RAM
- Scope: audit + Phase 0 benchmark baseline. No production refactor was performed in this phase.

This document links every technical claim to a concrete file, function or class.
Performance statements that are not yet backed by the baseline measurements in
`benchmarks/results/` are explicitly labelled **hypothesis** and are to be
confirmed, not to be quoted as fact.

## 1. Public API

The stable public surface (both `dbfbridge` and `dbf_bridge` export the same
symbols, see `src/dbfbridge/__init__.py:1-55` and `src/dbf_bridge/__init__.py:1-70`):

| Symbol | Kind | Notes |
|---|---|---|
| `export_dbf()` | function | `src/dbf_bridge/api.py:35-129` → `ExportRunResult` |
| `reconstruct_dbf()` | function | `src/dbf_bridge/api.py:132-200` → `ReconstructionRunResult` |
| `verify_conversion()` | function | `src/dbf_bridge/api.py:203-246` → `VerificationRunResult` |
| `check_conversion_quality()` | function | `src/dbf_bridge/api.py:249-297` → `QualityRunResult` |
| `ExportOptions`, `ReconstructionOptions` | dataclasses | `src/dbf_bridge/api_models.py` |
| `ProgressEvent`, `ProgressCallback` | typing | `src/dbf_bridge/api_models.py`, `src/dbf_bridge/api.py:30` |
| `ExportRunResult`, `ReconstructionRunResult`, `VerificationRunResult`, `QualityRunResult` | dataclasses | `src/dbf_bridge/api_models.py` |
| `DBFBridgeRunError` | exception | `src/dbf_bridge/api_models.py` |
| `ExportFormat`, `InputFormat`, `MemoPolicy`, `DeletedPolicy`, `DecodeErrors`, `MissingMemoPolicy` | Literal types | `src/dbf_bridge/exporter/models.py`, `src/dbf_bridge/importer/models.py` |

Notes:
- The API functions are thin adapters that delegate to the CLI orchestration
  (`api.py:111-129` → `cli.run_export`), so one behavior path is shared.
- No direct-read API exists yet (`inspect_table`, `read_schema`,
  `iter_records`, `read_records` are all absent from `dbfbridge` in 0.1.0).
  This is the target of the planned Phase 1 Direct Read Core.

## 2. Module structure

```
src/dbf_bridge/
├── __init__.py            re-exports the stable API (src/dbf_bridge/__init__.py:1-70)
├── api.py                 public functions (src/dbf_bridge/api.py)
├── api_models.py          options / results / progress / DBFBridgeRunError
├── cli.py                 `dbf-bridge` CLI + run_export orchestration (cli.py:224-496)
├── import_cli.py          `dbf-bridge-import` CLI
├── verifier.py            `dbf-bridge-verify` CLI + verification logic
├── quality.py             `dbf-bridge-quality` CLI + round-trip logic
├── converters.py          JSONL -> JSON/CSV/XLSX (polars fast path for CSV, orjson)
├── exporter/
│   ├── config.py          make_config / validate_config
│   ├── discovery.py       discover_tables (recursive DBF + sibling FPT/CDX)
│   ├── incremental.py     conversion_checksums.json manifest + revalidation
│   ├── models.py          dataclasses: ExportConfig, TableResult, StreamStats, FieldMetadata
│   ├── polish_codecs.py   register_polish_codecs (Mazovia, PIAST, cp852)
│   ├── reader.py          dbfread-based reader, LosslessFieldParser, RawHeader
│   ├── serialization.py   JSON-safe serialization + RAW_RECORD_KEY
│   ├── validation.py      output re-parse, SHA-256, StatsCollector
│   ├── writer.py          AtomicTextWriter, export_table
│   └── reporting.py       migration_report.jsonl/.csv + atomic_write_text
└── importer/
    ├── checksum.py        CanonicalChecksum (schema-aware value equality)
    ├── models.py          ImportConfig, ReconstructionResult
    ├── readers.py         JSONL/JSON/CSV/XLSX input streams + schema_path_for
    ├── reconstruct.py     reconstruct_tree + canonical compare
    ├── writer.py          dbf.Table-based writer + restore_raw_layout (raw DBF/FPT repair)
    └── reporting.py       reconstruction_report.jsonl
```

The `dbfbridge` package is a one-file re-export shim (`src/dbfbridge/__init__.py:5-29`).

## 3. Dependency graph (production)

```
                ┌────────────────── api.py ──────────────────┐
                │                                            │
    cli.py ────► exporter.writer ──► exporter.reader ──► dbfread
                │            ▲            │
                │            │            ├─► exporter.serialization
                │            │            ├─► exporter.validation
                │            │            └─► exporter.polish_codecs (codec registry)
                │            │
                │            └─► exporter.models / discovery / incremental / reporting
                │
    import_cli.py ──► importer.reconstruct ──► importer.writer ──► dbf
                │            │                       │
                │            ├─► importer.readers    ├─► exporter.serialization (RAW_RECORD_KEY)
                │            ├─► importer.checksum   └─► exporter.validation (sha256_file)
                │            └─► importer.reporting
                │
    verifier.py ──► dbfread (schema compare) + jsonl/json/csv/xlsx readers (own logic)
    quality.py  ──► cli.run_export + importer.reconstruct + verifier (diagnostic)
    converters.py ──► orjson, polars (lazy imports), xlsxwriter, openpyxl (lazy)
```

Key observation: `api.py` imports from `.cli` and `.importer` **lazily inside the
functions** (`api.py:111-129`, `api.py:162-173`, `api.py:222-225`, `api.py:265-267`).
This means `import dbfbridge` does not load `polars`, `orjson` or `xlsxwriter` —
only `dbfread` is loaded at import time (via the `exporter.reader` chain).
This is verified at runtime in the Phase 0 diagnostic:
`import dbfbridge; sys.modules` shows `dbfread` present and `polars`/`orjson`
absent. Good property to keep in Phase 1.

## 4. `dbfread` usage — public and private surface

| Location | Import | Private surface used |
|---|---|---|
| `src/dbf_bridge/exporter/reader.py:10-13` | `dbfread.DBF`, `dbfread.MissingMemoFile`, `dbfread.codepages.{codepages, guess_encoding}`, `dbfread.dbversions.get_dbversion_string`, `dbfread.field_parser.FieldParser` | `FieldParser` is a semi-public class (not documented as API but not prefixed with `_`); `codepages`, `guess_encoding`, `get_dbversion_string` are private modules of `dbfread`. |
| `src/dbf_bridge/exporter/reader.py:200-222` | `iter_physical_records` | Calls `table._open_memofile()` (private) and reads `table.filename`, `table.header`, `table.parserclass`, `table.fields`, `table.recfactory` (public-ish attributes of the `DBF` instance). |
| `src/dbf_bridge/verifier.py:31-32` | `dbfread.DBF`, `dbfread.codepages.guess_encoding` | — |
| `src/dbf_bridge/importer/reconstruct.py:11` | `dbfread.DBF` | `table.records`, `table.deleted` (public-ish). |

`LosslessFieldParser` (reader.py:48-100) subclasses `dbfread.field_parser.FieldParser`
and overrides `decode_text`, `parse`, `parseY`. This is a strong coupling to
`dbfread` internals: `FieldParser` is not a documented public API, and the
fallback chain (cp1250 → cp852 → mazovia) is implemented here, not in `dbfread`.

**Implication for Phase 1**: the planned backend abstraction
(`DBFReadBackend` protocol from the architecture document) must be introduced
*before* attempting to replace or shadow `dbfread` — otherwise the fallback
chain and the `LosslessFieldParser` hook both break. The raw-record iterator
`iter_physical_records` (reader.py:197-222) is the only place in the codebase
that reads the DBF byte layout directly, and it uses `table._open_memofile()`
(private). Any new direct-read core will need to replace or wrap that path.

## 5. Passes over DBF/FPT per export (validate=True)

For one table, `export_table` (writer.py:68-330) performs, per format:

1. `read_table_metadata` (reader.py:120-183) — reads the DBF header (raw),
   opens the `DBF` object for header fields, reads FPT header (512 B prefix,
   `_memo_file_details` reader.py:367-378), SHA-256s the DBF source
   (`sha256_file` validation.py:116-121) and the FPT file. This is **pass #1**.
2. `open_table` (reader.py:186-194) + `iter_physical_records` (reader.py:197-222)
   — streams all records once, serializing each to JSONL. **Pass #2** (data).
3. If `validate=True` (writer.py:247-268) → `validate_output` (validation.py:79-113)
   — re-reads the **output** JSONL file, re-parses every line, recomputes
   stats and SHA-256. **Pass #3** (output re-read).
4. `sha256_file(schema_path)` (writer.py:305) + `sha256_file(deleted_path)` if
   present (writer.py:309-311) — small files, negligible.

So a JSONL export with validation does **three full data passes** over different
files (source DBF, source FPT for memo, output JSONL) and **two SHA-256** of
source artifacts plus one of the output. A `--no-validate` export drops pass #3.

Hypothesis to be measured: pass #3 (re-parse) is a meaningful fraction of wall
time for large tables; the Phase 0 baseline `export_jsonl_validate_on/off`
pair measures this directly (the fast-profile result on the 190k-record fixture
is a single-run observation, not a confirmed constant).

For `check_conversion_quality` (quality.py:1-513) the pipeline is
DBF → JSONL → DBF → JSONL again, i.e. **two full export passes** plus the
reconstruction pass. This is by design (diagnostics) and is not a bug.

## 6. Temporary / partial files

`AtomicTextWriter` (writer.py:23-56) is the canonical atomic writer:
`<name>.partial` → `flush` → `os.fsync` → `os.replace`. Used for:

- data output (`writer.py:151-245`)
- schema output (`writer.py:135-146`)
- deleted output when `--deleted separate` (writer.py:204-236)

The importer uses `.partial` naming for DBF/FPT too
(`importer/writer.py:83-159`) and a raw-layout partial pair for byte-exact
reconstruction (`importer/writer.py:196-202`).

`converters.py` uses a `<name>.partial` + `os.fsync` + `os.replace` pattern at
lines 501, 873-877 for JSON/CSV/XLSX outputs. `api._atomic_json`
(api.py:314-322) and `reporting.py:130-149` do the same for reports.

There is no shared atomic-write helper — the pattern is duplicated in ~6 places.
This is a refactoring opportunity for Phase 1, not a bug.

**Risk**: all of these `os.replace` calls assume the destination directory is
already writable and that no other process is mid-write to the same path. The
`OutputExistsError` guard (writer.py:19-21) only checks `final_path.exists()`.

## 7. Raw-record Base64 (current default)

`exporter/serialization.py:15` defines `RAW_RECORD_KEY = "__dbfbridge_raw_record__"`.
`writer.py:175` and `writer.py:197` attach
`base64.b64encode(raw_record).decode("ascii")` to **every** record, for both
`deleted=include` and `deleted=skip` paths.

Consequences:
- JSONL output size ≈ 4/3 × (raw DBF record length) + serialized logical values.
  The actual Base64 share of the JSONL bytes is reported by the
  `raw_record_metadata_default` scenario (`raw_base64_share_of_jsonl`); it is
  data-dependent and is not asserted as a fixed range here.
- The importer *requires* this key for byte-exact reconstruction
  (`importer/writer.py:224-260`, `restore_raw_layout`). Removing it from the
  default would break the documented `raw_dbf_match: true` guarantee.
- The Phase 1 plan (`raw_mode="none" | "metadata" | "full-record"`) needs to be
  additive: keep the current default for backward compatibility.

Hypothesis: the `base64.b64encode` call per record is a measurable CPU cost on
the 1M-record fixture; the Phase 0 `raw_record_metadata_default` baseline
records the share, but not the CPU cost of the encode itself (the encode is
inside the measured export pass).

## 8. Memo / deleted / encoding handling

**Memo** (`memo` policy, writer.py:152-245 + serialization.py:120-127):
- `skip` → memo fields written as `None` (CSV default).
- `null` → memo fields written as `None` even if present (JSON/JSONL).
- `inline` → memo text is written into the record (JSON/JSONL/XLSX default).
- `lazy` does not exist in 0.1.0 (Phase 1 target).

**Deleted** (`deleted` policy, writer.py:151-245):
- `skip` → deleted rows are not written; `len(table.deleted)` is still counted
  (writer.py:238) so reports can show the delta.
- `separate` → a second file `<name>.deleted.<ext>` is written
  (writer.py:203-236).
- `include` → all rows are written, each with a `__deleted__` boolean
  (serialization.py:148-149).

The `iter_physical_records` reader (reader.py:197-222) preserves physical order
and marks `is_deleted` per row. This is the only place in the codebase that
walks the raw DBF record stream directly.

**Encoding** (reader.py:12-13, 62-88, 318-322 + polish_codecs.py):
- `LosslessFieldParser.decode_text` tries the declared codepage first, then
  walks `POLISH_FALLBACK_ENCODINGS` (cp852, mazovia, pki) on
  `UnicodeDecodeError`. On total failure it falls back to
  `LosslessText(..., errors="replace")` and attaches the raw bytes so the
  importer can restore them.
- `register_polish_codecs()` is called at import time in
  `src/dbf_bridge/__init__.py:16` and `exporter/reader.py:23` — this is a
  side effect of `import dbfbridge`. It is idempotent (codec registry) and
  cheap, but it is worth flagging in the Phase 1 "no import-time side effects"
  requirement of the architecture document.
- `--encoding cp1250|cp852|mazovia` forces the codepage for the whole export.

## 9. Dependencies

Runtime (mandatory, `pyproject.toml:32-39`):

| Package | Version (Phase 0 env) | Used by |
|---|---|---|
| `dbfread>=2.0.7` | 2.0.7 | exporter.reader, verifier, importer.reconstruct |
| `dbf>=0.99.11` | 0.99.11 | importer.writer, fixtures |
| `openpyxl>=3.1.5` | 3.1.5 | importer.readers (XLSX) |
| `orjson>=3.10` | 3.12.0 | converters (JSONL), benchmark |
| `polars>=1.0` | 1.44.1 | converters (CSV fast path) |
| `xlsxwriter>=3.2` | 3.2.9 | converters (XLSX) |

Optional extras today: `import` and `xlsx` are empty no-ops
(`pyproject.toml:41-45`). `dev` includes pytest, build, twine, ruff.

Candidates for future extras (per architecture document §11, to be decided
after the Phase 0 baseline and the Phase 1 direct-read core):

- `dbfbridge[write]` → `dbf` (reconstruction)
- `dbfbridge[xlsx]` → `openpyxl` + `xlsxwriter`
- `dbfbridge[fast]` → `orjson` + `polars` (only if benchmarks prove the gain)
- `dbfbridge[bench]` → `psutil` (RSS/IO counters for the benchmark runner —
  see §12 below; benchmark-only, not a runtime dependency)

## 10. Existing tests

`tests/` (45 tests at baseline, all passing before and after the Phase 0
benchmark infrastructure changes):

| File | Purpose |
|---|---|
| `tests/conftest.py` | Session fixture generating the DBF/FPT fixtures via `tests/fixtures/generate_sample_dbf.py`. |
| `tests/test_converters.py` | JSONL → JSON/CSV/XLSX conversion, schema inference, progress callback. |
| `tests/test_documentation.py` | README/CHANGELOG/package consistency. |
| `tests/test_export_example.py` | End-to-end export with the sample fixture. |
| `tests/test_importer.py` | Reconstruction from JSONL/JSON/CSV/XLSX, raw layout, memo. |
| `tests/test_incremental.py` | `conversion_checksums.json` skip / re-export logic. |
| `tests/test_packaging.py` | `pyproject.toml` metadata, entry points. |
| `tests/test_public_api.py` | `dbfbridge` vs `dbf_bridge` surface, `raise_for_errors()`. |
| `tests/test_reporting.py` | Migration / reconstruction report content. |
| `tests/test_schema.py` | Schema JSON content (fields, header, memo metadata). |
| `tests/test_benchmark_infrastructure.py` | **New in Phase 0** — regression tests for the benchmark runner (see §13). |

`tests/fixtures/generate_sample_dbf.py` is the canonical deterministic fixture
generator used by the test suite. The Phase 0 benchmark runner uses its own
generator in `benchmarks/fixtures.py` (separate, larger, and written with
`dbf` directly to control record count / deleted fraction / memo payload size).

## 11. Gaps vs. the planned Direct Read Core

The architecture document (`DBFBRIDGE_TARGET_ARCHITECTURE(1).md`) §5 defines a
minimal 0.2.x API. None of the following exist in 0.1.0:

- `inspect_table(path)` — header-only, JSON-serializable table description.
- `read_schema(path)` — full field + physical descriptor, no `_schema.json` side
  file.
- `iter_records(path, fields=None, include_deleted=False, memo="lazy", raw=False, encoding="auto", decode_errors="strict")` —
  streaming, O(1)-memory, with field projection and lazy memo.
- `read_records(path, offset=0, limit=100, fields=None, ...)` — bounded-memory
  helper for MCP/UI.
- `iter_raw_records(path)` — raw physical image, for diagnostics.
- `memo="lazy"` policy — metadata (length/hash) only, no FPT read until needed.
- `raw_mode="none" | "metadata" | "full-record"` — explicit opt-in for the
  Base64 raw record.
- Typed exception hierarchy (architecture document §17): `DBF_FORMAT_UNSUPPORTED`,
  `DBF_HEADER_INVALID`, `DBF_TRUNCATED`, `FPT_REQUIRED_MISSING`, `FPT_INVALID`,
  `ENCODING_UNKNOWN`, `TEXT_DECODE_ERROR`, `FIELD_TYPE_UNSUPPORTED`,
  `OPTIONAL_DEPENDENCY_MISSING`, `OUTPUT_EXISTS`, `RECONSTRUCTION_FAILED`,
  `ROUNDTRIP_MISMATCH`.
- Backend abstraction (`DBFReadBackend` protocol) isolating `dbfread` behind a
  documented boundary.

These are all **NOT_IMPLEMENTED** in the Phase 0 baseline report and are
intentionally not simulated.

## 12. Root cause of the Phase 0 benchmark-runner crash (0xC0000005)

The first version of `benchmarks/metrics.py` sampled the process working set by
calling `ctypes.windll.psapi.GetProcessMemoryInfo`. On this environment
(CPython 3.14.0, Windows 11 x64) that call **reliably crashed the process**
with `0xC0000005` (STATUS_ACCESS_VIOLATION) at a later allocation site
unrelated to the call (the faulthandler originally pointed at
`dbf.tables.append` / `importlib._bootstrap_external._compile_bytecode`, which
is what made the first reproduction confusing).

### Confirmed root cause — a ctypes FFI declaration bug in dbfbridge's own code

A/B test with a minimal standalone reproducer (no dbfbridge / dbf / dbfread):
`D:\Opencode projects\Project_dbfbridge\phase-0-rca-min.py`.

- **Variant A** — the exact declaration the original runner used:
  `argtypes = [HANDLE, POINTER(PROCESS_MEMORY_COUNTERS)]` — **only two
  parameters**, omitting the mandatory third `DWORD cb` argument of
  `GetProcessMemoryInfo`. **Crashes 5/5 with `0xC0000005`.**
- **Variant B** — the complete, correct signature
  `BOOL GetProcessMemoryInfo(HANDLE, LPPROCESS_MEMORY_COUNTERS, DWORD cb)` with
  `cb = sizeof(struct)`. **Returns `BOOL = 1`, `GetLastError() = 0`, 5/5 OK.**

The call violated the ABI by omitting the mandatory third `DWORD cb` argument.
On Windows x64 the third argument is passed in `R8`/`R8D`; the callee received
an undefined size value, which could lead to a write beyond the caller-supplied
buffer and the later `0xC0000005`. The A/B test proves the FFI signature is
faulty; the exact memory-corruption mechanism was **not** established with a
debugger. CPython is not implicated (the correct-signature variant never
crashed).

Classification recorded in persistent memory:
- **confirmed**: an unsafe/incorrect ctypes WinAPI declaration in dbfbridge's
  benchmark code (missing `DWORD cb`);
- **not a CPython bug** (the earlier "CPython candidate" hypothesis is
  superseded);
- the precise low-level corruption mechanism remains **unconfirmed** — it was
  not debugged, and no stack/heap overflow is asserted.

### Fix applied (Phase 0)

1. `benchmarks/metrics.py` no longer calls `psapi.GetProcessMemoryInfo`.
   RSS/IO sampling is delegated to `psutil` (an optional benchmark-only
   dependency; when absent, the metrics are honestly reported as
   `NOT_AVAILABLE` rather than crashing). `psutil` is **not** a runtime
   dependency of `dbfbridge`.
2. The runner was restructured so that each scenario runs in its **own worker
   subprocess** (`benchmarks/worker.py` invoked from
   `benchmarks/run_benchmark.py`). A crash in one scenario is reported as
   `FAILED` with the exit code and a reference to the diagnostic log, and does
   not take down the controller or the other scenarios. This is a robustness
   improvement on top of — not a substitute for — the declaration fix.
3. Regression tests in `tests/test_benchmark_infrastructure.py` assert that
   the unsafe call is absent from `benchmarks/metrics.py` (executable lines)
   and that the controller isolates workers in subprocesses.

**Verification**: 3 consecutive `python -m benchmarks.run_benchmark
--profile fast` runs, each `EXIT=0` with 14 `MEASURED` / 0 `FAILED` /
4 `NOT_IMPLEMENTED`.

## 13. Phase 0 benchmark infrastructure (new in this branch)

- `benchmarks/fixtures.py` — deterministic DBF/FPT generators (flat table,
   memo-heavy table, per-codepage encoding fixtures containing genuine Polish
   diacritics stored as the target codepage's raw bytes). The **flat fixtures
   are genuinely memo-free** (no memo field, no FPT, `require_fpt=False`,
   manifest `fpt_present=False`); memo data appears only in the dedicated
   memo-heavy fixtures (DBF + FPT). Every fixture is
   written with a `<name>.dbf.meta.json` sidecar (generator version, parameters,
   expected record/deleted counts, **measured** active/deleted/total counts,
   memo configuration, DBF/FPT presence, sizes, SHA-256); an incomplete or
   non-matching fixture is regenerated **before** measurement.
- `benchmarks/metrics.py` — measurement helpers (wall/CPU time, output bytes,
   RSS via `psutil`, IO counters). All metrics are honest: unmeasured values are
   `None` (rendered `NOT_AVAILABLE`). **Peak RSS** is the maximum of `psutil`
   samples taken on a background thread during the measured call (sampling
   interval recorded in the JSON); the sampler is always stopped/joined in
   `finally`. Two before/after snapshots are not sufficient to establish a peak.
   Without `psutil`, all RSS/IO metrics are `NOT_AVAILABLE`. If the current
   process cannot be opened or disappears while sampling, peak RSS is likewise
   `NOT_AVAILABLE` with a diagnostic reason rather than an unhandled sampler
   thread exception. Wall/CPU times are captured immediately after the measured
   call, before the sampler is stopped:
   they include the active sampler's small overhead but not the cost of
   stopping/joining it.
    **New metric contract (§13)**:
    - `read_amplification` = `io_read_bytes_delta / input_bytes` and
      `write_amplification` = `io_write_bytes_delta / output_bytes`, computed
      from the measured psutil process I/O counters (page-cache aware, platform
      dependent — a measured system ratio, never a logical read/write count).
      `NOT_AVAILABLE` when the counters or the denominator are unavailable.
    - `temporary_bytes_written` = logical size of the atomic `.partial` files at
      the moment of `os.replace` publish, measured by intercepting `os.replace`
      inside the worker subprocess only (no production code modified). A real
      sum (0 when no temporary file was created); `NOT_AVAILABLE` with a reason
      only when the platform forbids the read.
    - `temporary_files_left` / `temporary_bytes_left` = atomic-write residue
      observed after the measured window. Both must be zero for every warm-up
      and repetition; any remaining `.partial` name segment fails the sample,
      scenario and baseline gate.
    - `output_dbf_bytes` / `output_fpt_bytes` / `fpt_mib_per_second` are
      carried **only** by `reconstruction_memo_190k`: the final non-empty DBF
      and FPT sizes and the measured FPT publish throughput
      (`output_fpt_bytes / 2^20 / wall_seconds`). Scenarios without an FPT
      (flat `reconstruction_190k`, `reconstruction_jsonl_to_dbf`) are never
      given a separate FPT throughput — there is no FPT to attribute.
    - Aggregates over the successful measured samples (rendered as Markdown
      columns): `max_output_dbf_bytes` (max DBF MiB), `max_output_fpt_bytes`
      (max FPT MiB) and `median_fpt_mib_per_second` (median FPT MiB/s). They
      are `NOT_AVAILABLE` for every scenario that does not rebuild a memo
      table. The per-sample extras and the post-validation that attaches them
      run **outside** the wall/CPU measurement window (see the worker bullet).
- `benchmarks/worker.py` — in-process scenario executor; one scenario per
   worker invocation so a crash is contained. Runs an explicit warm-up (default
   1, excluded from results) followed by the measured repetitions; each
   execution writes into its own fresh `out/<scenario>/warmup-<n>/` or
   `out/<scenario>/rep-<n>/` directory prepared before the measured window, so
   `output_bytes` is the authoritative recursive size of that isolated tree
   (re-running an overwritten scenario never yields a zero). **Aggregation**:
   the median is computed only over successful
   measured samples; if ANY warm-up or measured repetition is FAILED the whole
   scenario is FAILED, raw samples and errors are preserved, `warmups_succeeded`
   / `warmups_failed` are recorded, and the aggregate is flagged
   `valid_baseline: false` (the Markdown never presents a partial median as a
    comparable baseline).
   Every DBF scenario passes the exact fixture file to the public API rather
   than the shared fixture directory. Its `input_bytes` is limited to that DBF
   plus its same-stem FPT, so neighbouring fixtures cannot leak into a run or
   distort the denominator. Reconstruction preparation first replaces its
   export directory with a fresh empty directory, preventing stale JSONL/schema
   artifacts from a previous run from adding extra tables.
  - `reconstruction_jsonl_to_dbf` and `reconstruction_190k` are the **flat /
    memo-free** reconstructions (small and 190,000-record fixtures respectively,
    no memo field, **no FPT** is produced). Their measured callable is only the
    public `reconstruct_dbf`; DBF discovery, size and physical record-count
    checks, the no-FPT assertion and temporary-file checks run afterwards for
    every warm-up and measured repetition.
  - `reconstruction_memo_190k` (full profile) is the **real DBF+FPT
    reconstruction**: the 190,000-record memo-heavy fixture is exported to JSONL
    *outside* the measured window, then the **measured callable is only the
    public `reconstruct_dbf`**.  All post-validation — DBF/FPT discovery and
    `stat`, the record-count check, the artifact validation
    (missing/empty FPT or a record-count mismatch fails the *sample*), and the
    per-sample extras `output_dbf_bytes` / `output_fpt_bytes` /
    `fpt_mib_per_second` — runs in a `post_validate` step **after** the
    wall/CPU window has closed, so it can never inflate the measured times.
    The output tree remains nested and isolated; recursive output sizing makes
    flattening/renaming unnecessary. Post-validation uses the same semantics
    for warm-ups and measured repetitions.
    It is the only scenario that reports those extras (see §13 metric
    contract). Its code path is validated by a small real integration test
    (`test_reconstruction_memo_real_integration`) that runs the genuine
    `reconstruct_dbf` on a 15-record memo DBF+FPT inside `metrics.run`,
    without mocking any production code, plus
    `test_reconstruction_memo_post_validate_outside_measured_window` and
    `test_reconstruction_memo_post_validate_failure_fails_sample` for the
    measurement boundary and the sample-failure semantics.
- `benchmarks/run_benchmark.py` — controller: runs scenarios in fresh
   subprocesses (diagnostic log opened via a context manager) with a
   configurable per-scenario timeout (`> 0` enforced; `repetitions >= 1` and
   `warmup >= 0` enforced), records environment (git commit, worktree state,
   Python, OS, CPU, physical RAM via `psutil`, dependency versions, fixture
   sizes), writes JSON + Markdown **always** (even when scenarios fail),
   distinguishes `MEASURED` / `FAILED` / `NOT_IMPLEMENTED` / `NOT_AVAILABLE`,
    and exits non-zero when any scenario is `FAILED`. **Baseline gate**:
    `--baseline` refuses (non-zero, nothing copied) unless the run is the **full**
    profile, `psutil` is available, no scenario FAILED, the report is exactly the
    full contract (20 unique `MEASURED`, 4 unique `NOT_IMPLEMENTED`, 0 `FAILED`,
    no unknown status, no duplicate name, no name outside the contract, no name
    in more than one status category), the payload is well-formed (a
    dict `environment` and `environment.git` block, a list `scenarios`, and
    every scenario entry a dict with a usable name — malformed entries are
    rejected, never silently dropped), `warmup >= 1` and `repetitions >= 3`,
    every `MEASURED` scenario has exactly `environment.repetitions` `MEASURED`
    samples **and** exactly `environment.warmup` `MEASURED` warm-up samples
    (a missing/extra/`FAILED` warm-up rejects the baseline independent of
    `valid_baseline`), every sample carries all required
    wall/CPU/throughput/output/peak-RSS (+ amplification/temporary where
    applicable) metrics, every `reconstruction_memo_190k` sample carries
    `output_dbf_bytes > 0`, `output_fpt_bytes > 0`, `fpt_mib_per_second > 0`,
    `temporary_publish_count >= 2` and
    `temporary_bytes_written >= output_dbf_bytes + output_fpt_bytes`, the
    worktree was clean, and the exact 40-hex commit SHA is recorded. `psutil`
    is benchmark-only (extra `dbfbridge[benchmark]`), not a runtime dependency.
- `benchmarks/__init__.py` — package marker.
- Diagnostic logs: `benchmark-data/logs/<profile>_<scenario>.log` (working
  directory; git-ignored). Reports: `benchmarks/results/` (git-ignored);
  `benchmarks/baselines/` only when `--baseline` is passed (Phase 0 runs do
  **not** create a versioned baseline).

The existing `benchmarks/benchmark_jsonl_conversion.py` is unchanged and its
conversion functions are imported and called **in the worker process** by the
`jsonl_conversion_json` / `jsonl_conversion_csv` scenarios (records/s from
`<input>.benchmark.json`). XLSX conversion is measured only in the full
profile (`jsonl_conversion_xlsx`) because it is much slower.

## 14. Bottlenecks — hypotheses to be confirmed by the baseline

These are **hypotheses**, each tagged with the Phase 0 scenario that will
confirm or refute it. They are not stated as facts until the corresponding
`benchmarks/results/phase-0-full.md` row is read.

| # | Hypothesis | Confirming scenario |
|---|---|---|
| H1 | Output re-parse (`validate=True`) is ~10-30% of export wall time on 190k rows. | `export_jsonl_validate_on` vs `export_jsonl_validate_off` |
| H2 | `base64.b64encode` of the raw record is a non-trivial CPU cost per record. | `export_1m_records` (full) CPU time vs `records/s` |
| H3 | Memo `inline` is materially slower than `skip`/`null` on a memo-heavy table. | `memo_skip` / `memo_null` / `memo_inline` |
| H4 | `deleted=include` is faster than `deleted=skip` on tables with many deleted rows (single pass vs. `len(table.deleted)` materialization). | `deleted_skip` vs `deleted_include` |
| H5 | Forced encoding (cp852 / mazovia) is ~equally fast as cp1250 when no fallback is triggered. | `encoding_cp1250` / `encoding_cp852` / `encoding_mazovia` |
| H6 | Reconstruction (JSONL → DBF) is dominated by the `dbf.Table.append` loop, not by the JSONL parse. | `reconstruction_jsonl_to_dbf` CPU vs. wall |
| H7 | The `check_conversion_quality` round-trip is ~2× a single export (it does export + reconstruct + re-export). | `roundtrip_quality` wall |
| H8 | Polars `scan_ndjson`/`sink_csv` is faster than the `orjson`-based fallback for CSV. Not measured by the Phase 0 scenarios: `jsonl_conversion_csv` times the legacy CSV path as a whole, so this remains a hypothesis until a dedicated A/B scenario is added. | (none in Phase 0) |

## 15. Security / safety invariants (unchanged in Phase 0)

- Atomic writes via `.partial` + `flush` + `fsync` + `os.replace` are preserved.
- `--no-overwrite` is honoured (writer.py:19-21).
- `conversion_checksums.json` skip logic is unchanged (incremental.py).
- Source files are only read, never written, by the exporter (verified by the
  existing test suite + the `--source`/`--output` separation in
  `config.validate_config`).
- No new runtime dependencies were added in Phase 0; `psutil` is a
  benchmark-only optional.

## 16. Definition-of-DoD gate for the RCA fix

Three consecutive `python -m benchmarks.run_benchmark --profile fast` runs in
fresh processes, all reporting `MEASURED` for every scenario that is expected
to be `MEASURED`, no `FAILED`, no `0xC0000005`, and a complete JSON + Markdown
report each time. `git diff --check` must be clean. The benchmark
infrastructure tests (`tests/test_benchmark_infrastructure.py`) must pass.

The Phase 0 commit is **not** created until this gate passes and the user
reviews the diff. (Gate passed: consecutive fast runs `EXIT=0`, **15
`MEASURED` / 0 `FAILED` / 4 `NOT_IMPLEMENTED`**; the first commit
`bench: add isolated phase-0 benchmark runner` was created and pushed on
`bench/phase-0-baseline`. The follow-up commit
`bench: correct phase-0 benchmark measurements` addressed the architectural
review: warm-up/repetition measurement, per-repetition output directories,
median aggregation, sampled peak RSS, timeout/FAILED handling, fixture
manifests, and the RCA wording corrected per the review. A third commit,
`bench: harden phase-0 fixture and aggregation semantics`, makes the flat
fixtures genuinely memo-free (no FPT), records **measured** active/deleted/
total counts, keeps shared scenario parameters identical across fast/full,
aggregates only successful samples (a FAILED scenario is never a valid
baseline), captures wall/CPU before the sampler join, validates CLI arguments,
and re-verifies with a fresh fast run. A fourth commit,
`bench: make phase-0 baseline release-ready`, hardens the DBF fixture scan
(strict raw-layout validation, `FixtureIntegrityError`), enforces the warm-up
failure semantics, strictly re-validates JSONL inputs before reuse, adds the
§13 metric contract (read/write amplification + `temporary_bytes_written`),
and installs the `--baseline` gate. A fifth commit,
`bench: add memo reconstruction coverage to phase-0 baseline`, adds the real
`reconstruction_memo_190k` scenario (full profile: 20 `MEASURED` + 4
`NOT_IMPLEMENTED`; fast stays 15 + 4), the `output_dbf_bytes` /
`output_fpt_bytes` / `fpt_mib_per_second` metric contract, strictens the
`--baseline` gate (exact sample and warm-up counts per scenario, the exact
scenario contract, per-sample `MEASURED` status, the memo-reconstruction
evidence), and replaces the emulated temporary-bytes test with a real
`reconstruct_dbf` integration test on a 15-record memo DBF+FPT. A sixth commit,
`bench: isolate phase-0 reconstruction measurement boundary`, isolates the
`reconstruction_memo_190k` measurement boundary: the measured callable is now
*only* the public `reconstruct_dbf`, while DBF/FPT discovery and `stat`, the
record-count check, the artifact validation and the
per-sample `output_dbf_bytes` / `output_fpt_bytes` / `fpt_mib_per_second`
extras all run in a `post_validate` step **after** the wall/CPU window has
closed (so they can never inflate the measured times; a post-validation
failure fails the sample and scenario without crashing the worker). It adds the
`max_output_dbf_bytes` /
`max_output_fpt_bytes` / `median_fpt_mib_per_second` aggregates and their
Markdown columns, hardens the `--baseline` gate against malformed payloads
(non-dict / unnamed scenario entries, a non-list scenario list, a missing
`environment`/`git` block — rejected, never silently dropped), and adds
regression tests for all of the above. A seventh commit,
`bench: validate every reconstruction outside measured window`, applies this
boundary to the flat reconstruction scenarios too and makes post-validation
identical for every warm-up and measured repetition. It removes unnecessary
flattening, requires exactly one valid DBF and the correct FPT presence policy,
checks physical record counts and leftover partial files outside the timed
window, preserves the raw measured times on validation failure, and records a
separate diagnostic `post_validation_seconds`. It also isolates every benchmark
to the explicitly selected DBF (+ same-stem FPT for input bytes), makes an
unavailable RSS process an explicit `NOT_AVAILABLE` result instead of an
unhandled sampler thread exception, and fixes a platform-dependent encoding
test that could select `migration_report.jsonl` instead of the table data.
Reconstruction preparation is also cleaned on every run so stale exports cannot
silently change the measured table set.)

**A versioned full baseline does NOT exist yet.**  The `--baseline` gate
requires a full, clean, complete, `psutil`-enabled run and has not been
satisfied; nothing has been copied into `benchmarks/baselines/`.  The full
profile and the baseline recording remain explicitly blocked until the
architect re-approves draft PR #1.

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
pair measures this directly. (Confirmed in the Phase 0 results: validate-off is
~10% faster on the 190k-record fixture.)

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
  For a wide table the Base64 share is ~70-90% of the JSONL bytes (measured
  in `raw_record_metadata_default` baseline).
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
(CPython 3.14.0, Windows 11 x64) that call **deterministically corrupted the
native heap** and the interpreter later crashed with `0xC0000005`
(STATUS_ACCESS_VIOLATION) inside the C garbage collector, at an allocation site
unrelated to the call (the faulthandler originally pointed at
`dbf.tables.append` / `importlib._bootstrap_external._compile_bytecode`, which
is what made the first reproduction confusing).

### Confirmed root cause — a ctypes FFI declaration bug in dbfbridge's own code

A/B test with a minimal standalone reproducer (no dbfbridge / dbf / dbfread):
`D:\Opencode projects\Project_dbfbridge\phase-0-rca-min.py`.

- **Variant A** — the exact declaration the original runner used:
  `argtypes = [HANDLE, POINTER(PROCESS_MEMORY_COUNTERS)]` — **only two
  parameters**, omitting the mandatory third `DWORD cb` argument of
  `GetProcessMemoryInfo`. On the x64 Windows calling convention that third
  argument is delivered in `EDX`; with only two ctypes-supplied arguments EDX
  is **uninitialised**, so `psapi!GetProcessMemoryInfo` computes an invalid
  output size and overruns the caller's heap. **Crashes 5/5 with
  `0xC0000005`.**
- **Variant B** — the complete, correct signature
  `BOOL GetProcessMemoryInfo(HANDLE, LPPROCESS_MEMORY_COUNTERS, DWORD cb)` with
  `cb = sizeof(struct)`. **Returns `BOOL = 1`, `GetLastError() = 0`, 5/5 OK.**

Because variant B (correct ABI) does not crash and variant A (the original,
incomplete declaration) does, the root cause is **the incomplete ctypes
declaration in dbfbridge's benchmark code — not a CPython defect and not a
`psapi` bug.** CPython is **not** implicated. The low-level mechanism is an
uninitialised `EDX` / output-size overrun in the ctypes interop.

Classification recorded in persistent memory:
- **confirmed**: an unsafe/incorrect ctypes WinAPI declaration in dbfbridge's
  benchmark code (missing `DWORD cb`);
- **not a CPython bug** (the earlier "CPython candidate" hypothesis is
  superseded);
- the exact low-level mechanism is an uninitialised-`EDX` heap overrun.

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
  memo-heavy table) with a fixed seed and ASCII-safe Polish text so that
  encoding scenarios compare code paths, not data.
- `benchmarks/metrics.py` — measurement helpers (wall/CPU time, output bytes,
  RSS via `psutil`, IO counters, read/write amplification). All metrics are
  honest: unmeasured values are `None` (rendered `NOT_AVAILABLE`).
- `benchmarks/worker.py` — in-process scenario executor; one scenario per
  worker invocation so a crash is contained.
- `benchmarks/run_benchmark.py` — controller: runs scenarios in fresh
  subprocesses, records environment (git commit, worktree state, Python, OS,
  CPU, physical RAM, dependency versions, fixture sizes), writes JSON +
  Markdown, distinguishes `MEASURED` / `FAILED` / `NOT_IMPLEMENTED` /
  `NOT_AVAILABLE`.
- `benchmarks/__init__.py` — package marker.
- `benchmarks/results/phase-0-fast.{json,md}` and
  `benchmarks/results/logs/*.log` — generated artifacts, **not committed**.
  The `benchmarks/results/` directory is added to `.gitignore`.

The existing `benchmarks/benchmark_jsonl_conversion.py` is unchanged and is
still exercised by the `jsonl_conversion_existing` scenario.

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
| H8 | Polars `scan_ndjson`/`sink_csv` is faster than the `orjson`-based fallback for CSV. (Already noted in `benchmarks/README.md`; not re-measured in Phase 0.) | `jsonl_conversion_existing` (CSV sub-mode) |

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
reviews the diff.

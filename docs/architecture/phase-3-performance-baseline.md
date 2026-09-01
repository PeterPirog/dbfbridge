# dbfbridge 0.3.0 — Phase 3 Performance Baseline (BEFORE only)

## 1. Motivation

This baseline measures the **released 0.2.0 Direct Read Core** (the code at
tag `v0.2.0` / `main`).  This document establishes the measured performance
baseline and the architecture/dependency audits that will justify (or reject)
specific 0.3 optimizations.

**No optimization implementation is performed in this phase.**  Every future
0.3 change must have BEFORE (this document and its canonical artifacts) and
AFTER (later measurement) with the same logical results and safety
guarantees.  Hard rule: **no production optimization before the canonical
Phase 3 BEFORE run is recorded.**

The measurement contract is `phase-3-performance-v1` (profile `phase3`), a
separate scenario contract from the frozen Phase 0/1 name sets.  The saved
artifact is validated by
`benchmarks.contract.validate_saved_phase3_before`, published by
`benchmarks.artifacts.publish_baseline_pair` (which dispatches the validator
by the payload's own `benchmark_contract`), and gated by
`run_benchmark.check_baseline_gate` (same dispatch).  The manifest check uses
the *expected* contract of the run — never a hardcoded Phase 1 value.

## 2. Architecture audit

### A. Does public core depend on concrete DbfreadBackend?

`core.backend` defines `DbfreadBackend` (the reference adapter) plus
`Protocol` classes.  The concrete singleton (`dbfread_backend`) is assigned at
module scope.  The public `core.records._stream_records()` resolves
`dbfread_backend` at call time from the module import.  A replacement adapter
can be injected by assigning `backend.dbfread_backend = OtherBackend(...)` —
but there is no factory/registration mechanism, so **HARDEN_EXISTING** (add a
registration hook in 0.3).

### B. Do Protocol boundaries suffice for backend swap?

Yes: `HeaderInspectionBackend`, `RecordStreamBackend`, `MemoPayloadBackend`
cover all public call paths.  **OK**.

### C. Do inspect/schema and record stream use the same abstraction?

`inspect_table`/`read_schema` call `parse_header()` directly, not through the
backend protocols.  `iter_records` calls `_stream_records` which calls
backend.  **HARDEN** in 0.3 (add `HeaderInspectionBackend` usage to `inspect.py`).

### D. Implicit dbfread dependency outside `core.backend`?

`dbfread` is imported in **six** source modules (all internal, all loaded
lazily with their importing module — `import dbfbridge` itself loads none of
them, verified below):

- `core/backend.py` — the reference adapter (intentional, the only private-API user);
- `core/codecs.py` — driver table source (`dbfread.codepages`);
- `exporter/reader.py` — lossless field parser for the export path;
- `exporter/writer.py` — raw-record metadata for export;
- `importer/reconstruct.py` — legacy DBF read for reconstruction checks;
- `verifier.py` — verification read path.

Plus the benchmark harness (`benchmarks/worker.py` imports `dbfread.memo` for
the memo-boundary guard — instrumentation only).  **OK** (documented).

### E. Migration layer bypasses backend?

`reconstruct_tree` calls `write_dbf` implemented in
`src/dbf_bridge/importer/writer.py`, which creates DBF/FPT files **directly
with the `dbf` package** (`dbf.Table`, `import dbf` at function scope).  There
is **no** `write/backend.py` module and no `write.backend.write_dbf`
delegation — earlier drafts of this document wrongly claimed such a
delegation.  The reconstruction writer is a self-contained write path by
design (read via `dbfread`, write via `dbf`).  **OK**.

### F. Second record parser?

No.  Only `core.backend._install_lossless_numeric_writer` (the exporter's
numeric-writer monkey-patch) and `core.header.parse_header` exist.  **OK**.

### G. Second header parser?

No.  `core.header.parse_header` is the only one.  **OK**.

### H. Memo path bypasses backend?

Memo payloads are always read through `MemoPayloadBackend` in `core.backend`.
LazyMemoValue calls `dbfread_backend.read_memo_payload`.  The writer's memo is
written via `dbf.Table` (the `dbf` package, not `dbfread`).  These are
separate read/write paths by design: `dbfread` reads, `dbf` writes.  **OK**.

### I. Error normalization leaking dbfread?

`core.backend` wraps `MissingMemoFile`, `UnicodeDecodeError`, `OSError`,
`DBFNotFound`, `struct.error` raised by `dbfread`.  All normalized to typed
`dbf_bridge.core.errors` types.  No leak.  **OK**.

### J. Backend lifecycle/resource ownership?

`iter_physical_records` handles live inside a generator; `finally` closes all
handles.  The backend does not expose handles.  **OK**.

### K. Explicit non-auto encoding in Direct Read (audit note)

`iter_records(..., encoding="mazovia")` requires the Polish Mazovia/PIAST
codecs to be registered **in the current process**: the auto path registers
them on demand (`core.codecs.resolve_driver_encoding`), the explicit
non-auto path does not.  The Phase 3 encoding scenarios therefore register
the Polish codecs outside the measured window (harness-side, documented).
Candidate 0.3 hardening: register the Polish codecs on demand in the explicit
encoding path too.  **NOT an optimization — deferred to 0.3 planning.**

## Multi-pass table

| Operation | DBF opens | FPT opens | directory scans | record passes | header parses |
|---|---|---|---|---|---|
| inspect_table | 1 (header) | 0 | 0–1 | 0 | 1 |
| read_schema (memo-free) | 1 (header) | 0 | 0–1 | 0 | 1 |
| read_schema (memo table) | 1 (header) | 1 (header only) | 0–1 | 0 | 1 |
| iter_records full | 1 (streaming) | 0 or 1 (inline) | 0–1 | 1 | 1 |
| read_records page | 1 (streaming) | 0 or 1 | 0–1 | 1 (seek + limit scan) | 1 |
| iter_raw_records | 1 | 0 | 0–1 | 1 | 1 |
| export_dbf | 1+ (per stage) | 0+ | 1 (discovery) | 1 (read) + 1 (verify) | 1 |
| reconstruct_dbf (write) | 0 reads | 0 | 1 (input scan) | 1 (write) | 0 |

There is no duplicate header parse for `read_schema` (single pass; a memo
table's FPT header is read at most once).  The reconstruction writer's FPT
header repair is a single pass.  Companion discovery uses protected stat with
direct paths first.

## Dependency audit (re-derived from code, not from documentation)

The table below was produced by an AST scan of `src/` plus a fresh-interpreter
check: `import dbfbridge` loads **none** of the six optional/heavy
dependencies; the Direct Read path loads only `dbfread` at call time.

| dependency | importing modules (src/) | import scope | required for `import dbfbridge`? | required for Direct Read? | required for migration? |
|---|---|---|---|---|---|
| dbfread | core/backend, core/codecs, exporter/reader, exporter/writer, importer/reconstruct, verifier | module scope of lazily-loaded internal modules | no | YES (read) | no |
| dbf | importer/writer (function body) | lazy (write time) | no | no | YES (reconstruct) |
| orjson | converters (module-scope `try` with `None` fallback) | optional module-scope | no | no | yes (JSONL/JSON/CSV/XLSX conversion pipeline) |
| polars | converters (function body) | lazy | no | no | yes (CSV conversion) |
| xlsxwriter | converters (function body) | lazy | no | no | yes (XLSX export) |
| openpyxl | importer/readers (function body) | lazy | no | no | yes (XLSX reconstruction) |

Verified facts (code, not docs):

- `import dbfbridge` → none of `dbf`, `dbfread`, `orjson`, `polars`,
  `openpyxl`, `xlsxwriter` in `sys.modules`;
- Direct Read public imports + one streamed record → only `dbfread` added;
- `dbf` is imported exactly once in `src/` (inside `write_dbf`,
  `importer/writer.py`);
- `orjson` is used only by `converters.py` (JSONL → JSON/CSV/XLSX stream
  conversion), not by the importer's JSONL reading;
- no `write/backend` module exists.

## Phase 3 scenario matrix (contract `phase-3-performance-v1`)

| # | scenario | what it measures |
|---|---|---|
| 1 | `inspect_schema_1` | 1× `inspect_table` + `read_schema` on the 300-record table |
| 2 | `inspect_schema_100` | 100× call loop |
| 3 | `inspect_schema_1000` | 1000× call loop |
| 4 | `direct_read_190k` | full `iter_records` stream, 190,000 records (count + ID-sum verified, O(1) memory) |
| 5 | `direct_read_1m` | full stream over 1,000,000 records |
| 6 | `direct_read_memo_heavy` | `memo="inline"` over the 190,000-record memo table (per-record FPT block reads) |
| 7 | `direct_read_deleted_include` | `include_deleted=True` over 1,000 records / 100 deleted |
| 8 | `direct_read_deleted_skip` | `include_deleted=False` (900 active) |
| 9 | `direct_read_cp1250` | forced cp1250, strict decode, logical text verified |
| 10 | `direct_read_cp852` | forced cp852 |
| 11 | `direct_read_mazovia` | forced Mazovia |
| 12 | `migration_dbf_to_jsonl` | DBF → JSONL export over the 190k table (validate on) |
| 13 | `migration_jsonl_to_dbf_fpt` | JSONL → DBF+FPT memo reconstruction over the 190k memo table |
| 14 | `migration_validate_off` | DBF → JSONL with output validation disabled |
| 15 | `migration_validate_on` | DBF → JSONL with output validation enabled |
| 16 | `direct_read_raw_none` | `raw=False` (no physical record images kept) |
| 17 | `direct_read_raw_full` | `raw=True` (every record image kept) |
| 18 | `direct_read_projection_selected` | `fields=("ID", "NAZWA", "KWOTA")` (unselected fields never parsed) |
| 19 | `direct_read_projection_all` | every schema field selected explicitly |
| 20 | `direct_read_memo_skip` | `memo="skip"` (memo field absent from values) |
| 21 | `direct_read_memo_lazy` | `memo="lazy"` (`LazyMemoValue` metadata, no FPT I/O) |
| 22 | `direct_read_memo_inline` | `memo="inline"` (decoded strings, real FPT reads) |
| 23 | `cold_import` | cold `import dbfbridge` in a fresh subprocess (asserts no heavy deps loaded) |

No LAN or network-storage latency is simulated anywhere: all scenarios run on
local runner storage, and every baseline records an explicit
`environment.storage` provenance label instead.

## Decision record

| Candidate | Measured cost | Expected benefit | Correctness risk | API risk | Implementation size | Decision |
|---|---|---|---|---|---|---|
| inspect_table reopens | header-only | Low | Low | Low | Small | **DEFER** |
| companion directory scans | direct stat first | Low | Low | Low | Small | **DEFER** |
| Reduced duplicate header work | already single pass | None | — | — | — | **REJECT** |
| Projected record parsing | already skips parser | Low | Low | Low | Small | **DEFER** |
| Memo read batching | per-record FPT block reads (measured by `direct_read_memo_heavy`) | Medium | Medium (caching) | Low | Medium | **PENDING canonical BEFORE** |
| Optional dependency split | install size only | Low runtime cost | Low | Low | Small | **PENDING canonical BEFORE** |
| Backend call overhead | Low | Low | Low | Low | Small | **REJECT** |
| Native reader | not measured as a bottleneck | unclear | HIGH | HIGH | Large | **DEFER** |

No **DO_NEXT** is chosen in this phase.  Every candidate marked PENDING is
decided only after the canonical Phase 3 BEFORE artifacts are published and
read; the final decision must cite the measured numbers.

## Performance regression CI proposal

Small deterministic smoke per PR (5 records, 3 scenarios) if wall drift
exceeds pre-established variance thresholds.  Scheduled/full benchmark weekly
or before releases.  No arbitrary threshold without analysing variance across
at least 5 GitHub-hosted runner measurements.
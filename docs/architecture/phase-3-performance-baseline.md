# dbfbridge 0.3.0 — Phase 3 Performance Baseline (BEFORE only)

## 1. Motivation

Phase 2 (Direct Write Core) is functionally correct on its own branch.  This
document establishes the measured performance baseline and
architecture/dependency audits that will justify (or reject) specific 0.3
optimizations.

**No optimization implementation is performed in this phase.**  Every future
0.3 change must have BEFORE (this document) and AFTER (later measurement) with
the same logical results and safety guarantees.

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

- `exporter/reader.py`: imports `dbfread` (LosslessFieldParser subclass).
  Intentional reference adapter.
- `benchmarks/worker.py`: imports from `dbfread.memo` (memo guard).
  Intentional instrumentation.
- No other modules.  **OK**.

### E. Migration layer bypasses backend?

`reconstruct_tree` calls `write_dbf` from `importer/writer.py` which delegates
to `write.backend.write_dbf`.  No bypass.  Legacy read → `parse_header`.  No
bypass.  **OK**.

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

## Multi-pass table

| Operation | DBF opens | FPT opens | directory scans | record passes | header parses |
|---|---|---|---|---|---|
| inspect_table | 1 (header) | 0 | 0–1 | 0 | 1 |
| read_schema | 1 (header) | 0 | 0–1 | 0 | 1 |
| read_schema (VFP backlink) | 1 (header) | 0 | 0–1 | 0 | 1 |
| iter_records full | 1 (streaming) | 0 or 1 (inline) | 0–1 | 1 | 1 |
| read_records page | 1 (streaming) | 0 or 1 | 0–1 | 1 (seek + limit scan) | 1 |
| iter_raw_records | 1 | 0 | 0–1 | 1 | 1 |
| export_dbf | 1+ (per stage) | 0+ | 1 (discovery) | 1 (read) + 1 (verify) | 1 |

There is no duplicate header parse for `read_schema` (single pass).  The
`write_dbf` FPT header repair is a single pass.  Companion discovery uses
protected stat with direct paths first.

## Dependency audit

### Import graph

| dependency | importing module | timing | required for `import dbfbridge`? | required for Direct Read? | required for migration? |
|---|---|---|---|---|---|
| dbfread | core.backend, exporter.reader | lazy (call time) | no | YES (read) | no |
| dbf | write.backend, tests.fixtures | lazy (write_time) | no | no | YES (reconstruct) |
| orjson | importer.readers | lazy | no | no | YES (JSONL reconstruction) |
| polars | exporters.converter | lazy | no | no | yes |
| openpyxl | importer.readers | lazy | no | no | yes (XLSX reconstruction) |
| xlsxwriter | exporters.writer | lazy | no | no | yes (XLSX export) |

### Decision matrix

| dependency | verdict | evidence |
|---|---|---|
| dbfread | **KEEP_BASE** | Direct Read is the core 0.2 feature; required at record-iteration time |
| dbf | **MOVE_TO_WRITE** | only imported inside `write_dbf(); no Direct Read dependency |
| openpyxl | **MOVE_TO_XLSX** | only imported in `importer/readers.py` for XLSX reconstruction |
| orjson | **MOVE_TO_ALL_ONLY** | used by exporter (JSONL validation) and importer (JSONL parse) |
| polars | **MOVE_TO_FAST** | only used by CSV conversion |
| xlsxwriter | **MOVE_TO_XLSX** | only used by XLSX writer |

**Evidence**: every import is inside a `try:` block or a function body, not at
module scope.  Fresh interpreter `import dbfbridge` loads none of these
(verified by tests/test_documentation.py and the one-shot import test).
Safe to split into optional extras in 0.3.

## Cancellation / progress proposal

0.3 design (not implemented): a lightweight `progress_callback` + optional
`cancellation_check` callable passed to `iter_records`/`read_records`.
Must remain a pure library API with no threading or global state.  No CLI or
MCP dependency.  Progress callback can report interval-based checkpoint
(e.g. every 10,000 records or every 1 MiB read).  Cancellation is a callable
checked per-record (zero overhead when not set).

## Backend abstraction proposal

`HARDEN_EXISTING_PROTOCOLS`: add a registration hook for alternate backends
without breaking the existing Protocol contracts.  No new `ADD_BACKEND_CONTAINER`:
the current single-protocol approach is sufficient for the expected 0.3
optimization scope.  No measured bottleneck justifies a native reader.

## Decision record

| Candidate | Measured cost | Expected benefit | Correctness risk | API risk | Implementation size | Decision |
|---|---|---|---|---|---|---|
| inspect_table reopens | Low (header only) | Low | Low | Low | Small | **DEFER** |
| companion directory scans | Low (direct stat first) | Low | Low | Low | Small | **DEFER** |
| Reduced duplicate header work | None (already single pass) | None | — | — | — | **REJECT** |
| Projected record parsing | Low (already skips parser) | Low | Low | Low | Small | **DEFER** |
| Memo read batching | Medium (inline reads per record) | Medium | Medium (caching) | Low | Medium | **INVESTIGATE** |
| Optional dependency split | Low runtime cost | Install size only | Low | Low | Small | **DO_NEXT** |
| Backend call overhead | Low | Low | Low | Low | Small | **REJECT** |
| Native reader | NOT MEASURED as bottleneck | unclear | HIGH | HIGH | Large | **DEFER** |

### Primary next candidate

**Optional dependency split.**  All heavy dependencies (`dbf`, `polars`,
`openpyxl`, `xlsxwriter`, `orjson`) are already lazy-imported.  The remaining
work is adding typed guards and split `pyproject.toml` extras.  This produces
measurable install-size reduction and a cleaner dependency contract without
runtime performance claims.

### Secondary fallback

**Memo read batching/caching** (investigate actual FPT I/O overhead in the
memo_lazy scenario; if per-block I/O dominates, batch small memos).

## Performance regression CI proposal

Small deterministic smoke per PR (5 records, 3 scenarios) if wall drift
exceeds pre-established variance thresholds.  Scheduled/full benchmark weekly
or before releases.  No arbitrary threshold without analysing variance across
at least 5 GitHub-hosted runner measurements.
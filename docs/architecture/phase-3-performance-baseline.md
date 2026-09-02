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

The canonical Phase 3 BEFORE baseline has been measured
(`phase-3-performance-v1`, commit `783428fb4e3055d15aa0d8f4669016673b84dea8`,
run `run-1db4c04a3d4cd661011631d749acaf1a`, Windows Server 2025 / Python
3.12.10, warmup 1 × 3 repetitions, 23 MEASURED / 0 FAILED).  The values below
are **runner-specific BEFORE evidence**, not guaranteed user benchmarks.

### Measured BEFORE evidence (GitHub-hosted Windows runner)

| path | median wall | derived records/s |
|---|---|---|
| Direct Read 190k (`direct_read_190k`) | ~3.05 s | ~62k rec/s |
| Direct Read 1M (`direct_read_1m`) | ~16.17 s | ~61.8k rec/s |
| projection selected (`direct_read_projection_selected`) | ~2.01 s | ~94k rec/s |
| projection all (`direct_read_projection_all`) | ~3.06 s | ~62k rec/s |
| memo skip (`direct_read_memo_skip`, 2k table) | ~0.015 s | ~135k rec/s |
| memo lazy (`direct_read_memo_lazy`, 2k table) | ~0.020 s | ~101k rec/s |
| memo inline (`direct_read_memo_inline`, 2k table) | ~0.035 s | ~56k rec/s |
| migration validate off (`migration_validate_off`) | ~6.51 s | ~29k rec/s |
| migration validate on (`migration_validate_on`) | ~7.47 s | ~25k rec/s |
| cold import (`cold_import`) | ~0.042 s | — |
| memo-heavy inline at scale (`direct_read_memo_heavy`) | ~3.31 s | ~57k rec/s |
| DBF→JSONL (`migration_dbf_to_jsonl`) | ~7.38 s | ~26k rec/s |
| JSONL→DBF+FPT (`migration_jsonl_to_dbf_fpt`) | ~54.86 s | ~3.5k rec/s |

Observations relevant to the decision:

- Direct Read throughput is essentially flat from 190k to 1M records
  (~62k rec/s both) — no scaling cliff, O(1) memory behaviour confirmed;
- field projection saves real work (~94k vs ~62k rec/s for 3 of 8 fields);
- the memo-policy triplet shows the expected ordering skip > lazy > inline
  (the inline policy performs one FPT block read per memo record);
- the heaviest measured paths are the migration writer paths
  (DBF→JSONL ~7 s, JSONL→DBF+FPT ~55 s per repetition), not Direct Read;
- cold import is already ~0.04 s with **no heavy dependency loaded**
  (`import dbfbridge` imports none of the optional dependencies).

### Decision

| Candidate | Evidence | Decision |
|---|---|---|
| Optional dependency split | all heavy deps already lazy; `import dbfbridge` loads none; cold import ~0.04 s; base contract should need only `dbfread` | **SELECTED as the first 0.3 change** (installation/dependency footprint + cleaner operation contract; explicitly **NOT** a runtime speedup claim) |
| Memo read batching | inline memo cost measured in isolation (`direct_read_memo_heavy` ~57k rec/s vs 62k unprojected; per-record FPT reads) | **DEFER** (candidate for a later 0.3 change with its own BEFORE/AFTER) |
| Backend registry / native reader / writer rewrite | no measured bottleneck justifies them | **REJECT for now** |
| inspect_table reopens / companion scans / projection work | already single-pass, projection already skips parsing | **REJECT** (no cost measured) |

No wall-time threshold is claimed for the dependency split: it is an
installation-footprint and operation-contract change, and its AFTER run is a
regression/equivalence check, not a performance claim.

## Performance regression CI (implemented)

The proposal below was implemented with measured evidence — see
`docs/architecture/phase-3-regression-ci-calibration.md` for the calibration
methodology, the five hosted-runner runs behind it, and the measured
hosted-runner drift (two of five runner instances were ~30 % faster across
ALL scenarios, which is why absolute wall time is advisory-only and same-run
relative ratios are the hard regression signals).

- `.github/workflows/performance-regression.yml` — PR smoke (path-filtered,
  four stable low-runtime scenarios incl. the
  `projection_selected_over_all` hard ratio gate), weekly full 23-scenario
  regression, `workflow_dispatch` smoke/full;
- `benchmarks/regression/phase-3-regression-policy-v1.json` — the committed
  evidence policy (generated by `benchmarks.calibrate_regression` from five
  independent hosted-runner runs; every envelope reproducible from the
  recorded calibration values);
- `benchmarks/compare_phase3_regression.py` — stdlib-only comparator with
  the exit-code contract (0 = correctness PASS and no hard regression; 1 =
  invalid report/policy, FAILED scenario, or hard regression on a
  comparable candidate).

The original proposal of "5 records / 3 scenarios" was REJECTED with
evidence: the calibration shows sub-0.1 s timings (inspect_schema_1,
cp1250/cp852 single-record reads) are timer-noise dominated (relative MAD
4-24 %).  The PR smoke instead uses `inspect_schema_1000` (0.68 s, relMAD
1.6 %), `direct_read_190k` (3.3 %) and the projection pair (1.0/1.8 %) —
stable on hosted runners and covering the direct-read hot path plus one
relative relation.  Policy updates require a dedicated review/commit; the
regression workflow never updates the policy itself.
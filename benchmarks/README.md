# dbfbridge benchmarks

Two things live in this directory:

1. **Phase 0 baseline runner** (`run_benchmark.py` + `worker.py` + `metrics.py`
   + `fixtures.py`) — repeatable, subprocess-isolated measurement of the existing
   `dbfbridge 0.1.0` code paths (export, reconstruction, round-trip, encodings,
   memo/deleted policies) on deterministic synthetic fixtures.
2. **Legacy JSONL conversion benchmark** (`benchmark_jsonl_conversion.py`) —
   kept unchanged; still runnable standalone and still exercised by the
   `jsonl_conversion_json` / `jsonl_conversion_csv` scenarios (the conversion
   functions are imported and called in the worker process).

## Dependencies

Benchmark-only, optional.  They are **not** runtime dependencies of `dbfbridge`.

```powershell
# from the repository root
python -m venv .venv
.venv\Scripts\activate
python -m pip install -e ".[dev]"
python -m pip install psutil     # optional: RSS/IO metrics; see "Peak RSS" below
```

`psutil` provides the RSS/IO counters used by `benchmarks/metrics.py`.  When it
is absent the runner still works: those metrics are honestly reported as
`NOT_AVAILABLE` (null in JSON), never estimated.  A versioned baseline should be
produced with `psutil` installed and its version recorded in the report.

## Running

```powershell
# Fast control profile (default); one documented command to repeat:
python -m benchmarks.run_benchmark --profile fast --warmup 1 --repetitions 3

# Full profile (adds the 1,000,000-record, 190k memo-heavy, 190k reconstruction
# and XLSX conversion scenarios):
python -m benchmarks.run_benchmark --profile full

# Single scenario (e.g. for triage or a quick regression gate):
python -m benchmarks.run_benchmark --profile fast --scenario export_jsonl_validate_on

# List scenarios available in a profile:
python -m benchmarks.run_benchmark --profile fast --list

# Per-scenario worker timeout (seconds); a timeout marks the scenario FAILED:
python -m benchmarks.run_benchmark --profile fast --timeout 600

# Also copy the report into benchmarks/baselines/ so it can be versioned:
python -m benchmarks.run_benchmark --profile fast --baseline
```

Every scenario runs in its **own worker subprocess** (`benchmarks/worker.py`),
so a crash in one scenario is captured as `FAILED` (with exit code + diagnostic
log) and does not take down the controller or the other scenarios.

## Measurement method

For each scenario the worker performs:

1. **Warm-up runs** (`--warmup`, default 1). These are excluded from the
   reported results; they only stabilise caches/page state.
2. **Measured repetitions** (`--repetitions`, default 3). Each execution is a
   separate sample written into its own fresh `out/<scenario>/rep-<n>/`
   directory, prepared **before** the measured window starts.

All per-repetition samples are preserved in the JSON report. The Markdown
summary reports the **median** of the measured repetitions
(`median_wall_seconds`, `median_cpu_seconds`,
`median_records_per_second`, `median_source_mib_per_second`), plus the maximum
observed peak RSS and output size. The number of warm-ups and repetitions is
recorded in the JSON (`environment.warmup`, `environment.repetitions`).

- **Aggregation only over successful samples.** If **any** warm-up or measured
  repetition is `FAILED`, the whole scenario is `FAILED`, the raw samples and
  errors are preserved, and the aggregate is flagged `valid_baseline: false`.
  The Markdown never presents a partial median of a `FAILED` scenario as a
  comparable baseline (those rows are labelled `NOT A VALID BASELINE` /
  `NOT_AVAILABLE`).

- **Peak RSS** is the maximum of `psutil` samples taken on a background thread
  **during** the measured call (sampling interval recorded as
  `rss_sample_interval_seconds`; the sampler is always stopped/joined in
  `finally`). Without `psutil` it is `NOT_AVAILABLE`.
- **`output_bytes`** is the final, authoritative size of the scenario's own
  output directory — never a before/after diff on a shared directory, so
  re-running an overwritten scenario still reports the real (non-zero) size.
- **`temporary_bytes`** is `NOT_AVAILABLE`: the library's atomic `.partial`
  files cannot be reliably attributed per operation from the outside, so they
  are never estimated.
- **Process I/O counters** (`io_*_delta`) are `psutil` process-level deltas
  around the measured call; they cover the worker process only.

## Where results go

- `benchmarks/results/` — **local, repeatable outputs; git-ignored.** Contains
  the per-scenario diagnostic logs and the working JSON/Markdown reports.
- `benchmarks/baselines/` — **the selected, versioned baseline.** When you run
  with `--baseline`, the controller copies the JSON + Markdown report here so it
  can be committed.  A versioned baseline must carry: git commit, worktree dirty
  state, OS/CPU/Python, dependency versions, fixture sizes and the status of
  every scenario — all of which the report already contains.
- `benchmark-data/` — generated fixtures and outputs; **git-ignored** (regenerated
  on demand; never committed).

## Statuses

| Status | Meaning |
|---|---|
| `MEASURED` | The code path exists in `dbfbridge 0.1.0` and was executed successfully; metrics are real. |
| `FAILED` | The scenario crashed or raised.  Reports the exit code and diagnostic log; **no metrics are invented.** |
| `NOT_IMPLEMENTED` | The feature does not exist in `dbfbridge 0.1.0` (e.g. direct read, field projection, `memo="lazy"`, `raw_mode="none"`).  Listed verbatim, never simulated. |
| `NOT_AVAILABLE` | The platform / optional dependency could not provide a specific metric (e.g. RSS without `psutil`).  Rendered `NOT_AVAILABLE` in Markdown, `null` in JSON.  Never fabricated. |

## Measurement boundary

- **Fixture generation is excluded** from measured time.  `fixtures.py` builds
  the DBF/FPT files up front; the measured wall/CPU clock starts only inside the
  `metrics.run()` wrapper around the target `dbfbridge` call.
- **Worker startup is NOT in the measured window.**  Each scenario is its own
  subprocess; `metrics.run()` begins timing *after* the process has imported and
  is inside the target call.  Python interpreter startup, imports, and fixture
  generation all happen before the timer starts.
- **Peak RSS** is the maximum of `psutil` RSS samples taken on a background
  thread **during** the measured call (sampling interval recorded in the JSON;
  the sampler is always stopped/joined in `finally`).  It is an observed
  maximum, not a guaranteed high-water mark.  Without `psutil` the RSS and IO
  columns are `NOT_AVAILABLE`.
- The controller records commit, dirty state, Python, OS, CPU, physical RAM,
  dependency versions and per-fixture byte sizes into every report.

## Reproducing the 190k and 1M fixtures

Fixtures are generated deterministically by `benchmarks/fixtures.py`.  The
encoding fixtures store genuine Polish diacritics (e.g. ``Żółw ąęłóńśćźż``) as
the raw bytes of the target codepage, so the forced-encoding path is exercised
on non-ASCII data, not just ASCII:

```python
from benchmarks import fixtures

fixtures.generate_flat(Path("benchmark-data/medium/medium.dbf"), 190_000)  # 190k
fixtures.generate_memo_heavy(Path("benchmark-data/memo/memo190000.dbf"), 190_000)  # memo 190k
fixtures.generate_flat(Path("benchmark-data/large/large.dbf"), 1_000_000)  # 1M
```

Or simply run the full profile once and the runner builds them for you into
`benchmark-data/` (git-ignored).  Re-running a profile reuses any fixture that
already exists on disk.

**The flat fixtures (`generate_flat`) are genuinely memo-free**: they contain
no memo field and create **no FPT** (`require_fpt=False`, manifest
`fpt_present=False`).  Memo data appears only in the dedicated memo-heavy
fixtures (`generate_memo_heavy`), which create DBF + FPT.  Fixture sidecars
record the **measured** `active_records` / `deleted_records` / `total_records`
counts and the validation requires them to match the file on disk, in addition
to expected counts, sizes and SHA-256.

## Profiles

`fast` is the control profile (15 scenarios).  `full` **extends** `fast` with
four additional, distinctly-named scenarios (`export_1m_records`,
`memo_heavy_190k`, `reconstruction_190k`, `jsonl_conversion_xlsx`) and never
changes the parameters of a scenario it shares with `fast` — a scenario name
means the same thing in both profiles.  The full profile generates the 1,000,000-
record flat fixture and the 190,000-record memo-heavy fixture before measuring;
on a slow machine give it a generous `--timeout` (default 600 s per scenario) so
fixture generation does not time out.

## Legacy JSONL conversion benchmark

The original `benchmark_jsonl_conversion.py` remains available and unchanged:

```bash
python benchmarks/benchmark_jsonl_conversion.py generate benchmark-data/input.jsonl --size-mb 100
python benchmarks/benchmark_jsonl_conversion.py json benchmark-data/input.jsonl benchmark-data/output.json
python benchmarks/benchmark_jsonl_conversion.py csv benchmark-data/input.jsonl benchmark-data/output.csv
python benchmarks/benchmark_jsonl_conversion.py xlsx benchmark-data/input.jsonl benchmark-data/output.xlsx
```

Results from Python 3.12, Polars 1.43.2, orjson 3.11.9, and XlsxWriter 3.2.9
(single local runs, not storage-isolated laboratory measurements):

| Input | Conversion | Engine | Time | Throughput | Peak RSS |
|---:|---|---|---:|---:|---:|
| 100 MB | JSON, before | materialized list | 3.601 s | 27.77 MB/s | 366.12 MB |
| 100 MB | JSON, streaming | binary stream | 0.999 s | 100.11 MB/s | 49.39 MB |
| 500 MB | JSON, streaming | binary stream | 5.169 s | 96.72 MB/s | 49.42 MB |
| 1,024 MB | JSON, streaming | binary stream | 10.738 s | 95.36 MB/s | 49.48 MB |
| 100 MB | CSV, streaming | Polars sink | 0.737 s | 135.67 MB/s | 276.30 MB |
| 500 MB | CSV, streaming | Polars sink | 3.743 s | 133.57 MB/s | 679.34 MB |
| 1,024 MB | CSV, streaming | Polars sink | 3.307 s | 309.64 MB/s | 1,210.79 MB |
| 100 MB | XLSX, streaming | XlsxWriter constant memory | 33.350 s | 3.00 MB/s | 38.46 MB |

The CSV fast path uses the DBF schema and validated record count, so it performs
one Polars streaming pass.  If a progress or cancellation callback is supplied,
CSV uses the slower Python streaming fallback.

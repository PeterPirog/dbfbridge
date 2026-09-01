# dbfbridge benchmark report

- run_id: `run-1db4c04a3d4cd661011631d749acaf1a` (identical in JSON, Markdown and the manifest)
- benchmark_contract: `phase-3-performance-v1`
- Profile: `phase3`
- Commit: `783428fb4e3055d15aa0d8f4669016673b84dea8` (origin/main: `origin/main`)
- Worktree: clean on branch `bench/phase-3-performance-baseline`
- generated_at: `2026-09-01T10:44:50.227661+00:00` (timezone-aware UTC, identical in JSON, Markdown and the manifest)
- Runner: `github-actions-windows-x64-win25-vs202620260824.214.3`
- Storage: `github-actions-windows-runner-temp`
- Warm-up: 1 (excluded from results); Repetitions: 3 (measured)
- Aggregation: median of measured repetitions (all samples preserved in JSON)
- Python: 3.12.10
- OS: Windows 2025Server 10.0.26100
- CPU: AMD64 Family 25 Model 17 Stepping 1, AuthenticAMD (4 logical CPUs), 16 GiB RAM
- Packages: dbfbridge 0.2.0, dbf 0.99.11, dbfread 2.0.7, orjson 3.12.0, polars 1.44.1, openpyxl 3.1.5, xlsxwriter 3.2.9, psutil 7.2.2

Statuses: `MEASURED` = all measured repetitions succeeded; `FAILED` = a repetition failed, the worker crashed/timed out, or output was invalid (no metrics invented); `NOT_IMPLEMENTED` = the feature does not exist in dbfbridge 0.1.0 (not simulated); `NOT_AVAILABLE` = a metric could not be provided (e.g. RSS without psutil).

| Scenario | Status | median wall (s) | median cpu (s) | median rec/s | median MiB/s | peak RSS (MiB) | output (MiB) | max DBF (MiB) | max FPT (MiB) | median FPT MiB/s | median read amp | median write amp | max temporary written (MiB) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `inspect_schema_1` | MEASURED | 0.001 | 0.000 | 1,405.877 | 46.594 | 29.61 | 0.00 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 0.471 | NOT_AVAILABLE | 0.00 |
| `inspect_schema_100` | MEASURED | 0.058 | 0.047 | 1,725.188 | 0.572 | 23.82 | 0.00 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 47.145 | NOT_AVAILABLE | 0.00 |
| `inspect_schema_1000` | MEASURED | 0.581 | 0.578 | 1,721.144 | 0.057 | 25.03 | 0.00 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 471.455 | NOT_AVAILABLE | 0.00 |
| `direct_read_190k` | MEASURED | 3.050 | 3.031 | 62,290.125 | 6.772 | 31.26 | 0.00 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 1.001 | NOT_AVAILABLE | 0.00 |
| `direct_read_1m` | MEASURED | 16.169 | 16.109 | 61,847.224 | 6.724 | 23.76 | 0.00 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 1.000 | NOT_AVAILABLE | 0.00 |
| `direct_read_memo_heavy` | MEASURED | 3.309 | 3.281 | 57,425.147 | 204.350 | 30.95 | 0.00 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 1.000 | NOT_AVAILABLE | 0.00 |
| `direct_read_deleted_include` | MEASURED | 0.017 | 0.016 | 59,577.358 | 6.509 | 28.97 | 0.00 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 1.138 | NOT_AVAILABLE | 0.00 |
| `direct_read_deleted_skip` | MEASURED | 0.015 | 0.016 | 58,521.741 | 7.104 | 23.78 | 0.00 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 1.138 | NOT_AVAILABLE | 0.00 |
| `direct_read_cp1250` | MEASURED | 0.000 | 0.000 | 2,152.853 | 0.300 | 23.92 | 0.00 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 2.555 | NOT_AVAILABLE | 0.00 |
| `direct_read_cp852` | MEASURED | 0.001 | 0.000 | 1,945.147 | 0.271 | 23.82 | 0.00 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 2.555 | NOT_AVAILABLE | 0.00 |
| `direct_read_mazovia` | MEASURED | 0.000 | 0.000 | 2,014.099 | 0.280 | 23.72 | 0.00 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 2.555 | NOT_AVAILABLE | 0.00 |
| `migration_dbf_to_jsonl` | MEASURED | 7.378 | 7.344 | 25,752.193 | 2.800 | 28.62 | 65.21 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 9.312 | 1.000 | 65.21 |
| `migration_jsonl_to_dbf_fpt` | MEASURED | 54.857 | 53.188 | 3,463.557 | 13.145 | 62.06 | 676.12 | 8.15 | 667.97 | 12.177 | 6.699 | 3.076 | 1,352.25 |
| `migration_validate_off` | MEASURED | 6.514 | 6.453 | 29,167.102 | 3.171 | 27.60 | 65.21 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 6.156 | 1.000 | 65.21 |
| `migration_validate_on` | MEASURED | 7.469 | 7.391 | 25,438.610 | 2.766 | 27.64 | 65.21 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 9.312 | 1.000 | 65.21 |
| `direct_read_raw_none` | MEASURED | 3.016 | 3.031 | 62,989.161 | 6.848 | 23.69 | 0.00 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 1.001 | NOT_AVAILABLE | 0.00 |
| `direct_read_raw_full` | MEASURED | 3.066 | 3.047 | 61,977.873 | 6.738 | 23.77 | 0.00 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 1.001 | NOT_AVAILABLE | 0.00 |
| `direct_read_projection_selected` | MEASURED | 2.012 | 2.016 | 94,440.253 | 10.268 | 23.75 | 0.00 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 1.001 | NOT_AVAILABLE | 0.00 |
| `direct_read_projection_all` | MEASURED | 3.057 | 3.062 | 62,142.741 | 6.756 | 23.66 | 0.00 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 1.001 | NOT_AVAILABLE | 0.00 |
| `direct_read_memo_skip` | MEASURED | 0.015 | 0.016 | 135,311.589 | 481.564 | 28.98 | 0.00 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 0.014 | NOT_AVAILABLE | 0.00 |
| `direct_read_memo_lazy` | MEASURED | 0.020 | 0.016 | 100,785.624 | 358.689 | 23.84 | 0.00 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 0.014 | NOT_AVAILABLE | 0.00 |
| `direct_read_memo_inline` | MEASURED | 0.035 | 0.031 | 56,396.331 | 200.711 | 23.71 | 0.00 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 1.003 | NOT_AVAILABLE | 0.00 |
| `cold_import` | MEASURED | 0.042 | 0.000 | NOT_AVAILABLE | 0.000 | 20.99 | 0.00 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 0.00 |

## Notes

- Each scenario runs in its own worker subprocess with a configurable timeout; one failed/timed-out scenario is `FAILED` and does not affect the others.
- Warm-up runs are excluded from aggregates; every warm-up and repetition writes into a fresh isolated directory and uses the same post-validation after the timed call.
- `output_bytes` is the authoritative final size of the scenario's own output directory, so re-running an overwritten scenario still reports the real size (never a zero diff).
- Peak RSS is the maximum of `psutil` samples taken on a background thread during the measured call (the sampler is always stopped/joined in `finally`). Without `psutil` it is `NOT_AVAILABLE`.
- `temporary_bytes_written` is the logical size of the atomic `.partial` files published by the measured call (the worker intercepts `os.replace` for that call only); it is **0** when the operation created no temporary file and `NOT_AVAILABLE` (with a reason) only if the platform forbids reading the temporary file.
- `temporary_files_left` and `temporary_bytes_left` are checked after timing and must both be zero; atomic-write residue fails the sample and baseline gate.
- `NOT_IMPLEMENTED` scenarios are listed verbatim and are not estimated.

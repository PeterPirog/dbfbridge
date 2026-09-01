# dbfbridge benchmark report

- run_id: `run-ceaf809d8b52a2b6873d7594dff4a769` (identical in JSON, Markdown and the manifest)
- benchmark_contract: `phase-1-direct-read-v1`
- Profile: `full`
- Commit: `df035de662f2d78a7a8d9d9a141a8235e1161382` (origin/main: `origin/main`)
- Worktree: clean on branch ``
- generated_at: `2026-08-31T15:17:43.248026+00:00` (timezone-aware UTC, identical in JSON, Markdown and the manifest)
- Runner: `github-actions-windows-2025-python-3.12.10`
- Storage: `github-actions-windows-temp`
- Warm-up: 1 (excluded from results); Repetitions: 3 (measured)
- Aggregation: median of measured repetitions (all samples preserved in JSON)
- Python: 3.12.10
- OS: Windows 2025Server 10.0.26100
- CPU: AMD64 Family 25 Model 1 Stepping 1, AuthenticAMD (4 logical CPUs), 16 GiB RAM
- Packages: dbfbridge 0.1.0, dbf 0.99.11, dbfread 2.0.7, orjson 3.12.0, polars 1.44.1, openpyxl 3.1.5, xlsxwriter 3.2.9, psutil 7.2.2

Statuses: `MEASURED` = all measured repetitions succeeded; `FAILED` = a repetition failed, the worker crashed/timed out, or output was invalid (no metrics invented); `NOT_IMPLEMENTED` = the feature does not exist in dbfbridge 0.1.0 (not simulated); `NOT_AVAILABLE` = a metric could not be provided (e.g. RSS without psutil).

| Scenario | Status | median wall (s) | median cpu (s) | median rec/s | median MiB/s | peak RSS (MiB) | output (MiB) | max DBF (MiB) | max FPT (MiB) | median FPT MiB/s | median read amp | median write amp | max temporary written (MiB) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `jsonl_conversion_json` | MEASURED | 0.239 | 0.234 | 499,742.224 | 83.694 | 55.34 | 20.11 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 1.000 | 1.000 | 20.11 |
| `jsonl_conversion_csv` | MEASURED | 0.057 | 0.156 | 2,111,339.375 | 353.595 | 100.23 | 14.08 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 0.000 | 1.000 | 14.08 |
| `raw_record_metadata_default` | MEASURED | 0.029 | 0.031 | 10,394.758 | 1.148 | 30.66 | 0.12 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 9.558 | 1.000 | 0.12 |
| `export_jsonl_validate_on` | MEASURED | 9.922 | 9.906 | 19,150.275 | 2.082 | 33.01 | 65.21 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 9.312 | 1.000 | 65.21 |
| `export_jsonl_validate_off` | MEASURED | 8.905 | 8.828 | 21,336.589 | 2.320 | 27.61 | 65.21 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 6.156 | 1.000 | 65.21 |
| `memo_skip` | MEASURED | 0.098 | 0.094 | 20,354.390 | 72.440 | 31.99 | 0.30 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 2.099 | 1.000 | 0.30 |
| `memo_null` | MEASURED | 0.098 | 0.094 | 20,447.682 | 72.772 | 26.21 | 0.30 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 2.099 | 1.000 | 0.30 |
| `memo_inline` | MEASURED | 0.231 | 0.219 | 8,674.366 | 30.871 | 27.79 | 7.56 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 4.139 | 1.000 | 7.56 |
| `deleted_skip` | MEASURED | 0.067 | 0.062 | 14,929.741 | 1.631 | 30.44 | 0.32 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 8.900 | 1.000 | 0.32 |
| `deleted_include` | MEASURED | 0.064 | 0.062 | 15,566.577 | 1.701 | 25.51 | 0.37 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 8.878 | 1.000 | 0.37 |
| `encoding_cp1250` | MEASURED | 0.012 | 0.000 | 84.842 | 0.012 | 25.36 | 0.01 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 42.740 | 1.000 | 0.01 |
| `encoding_cp852` | MEASURED | 0.011 | 0.016 | 88.915 | 0.012 | 25.50 | 0.01 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 42.644 | 1.000 | 0.01 |
| `encoding_mazovia` | MEASURED | 0.011 | 0.016 | 88.809 | 0.012 | 25.38 | 0.01 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 42.836 | 1.000 | 0.01 |
| `reconstruction_jsonl_to_dbf` | MEASURED | 0.081 | 0.078 | 3,687.162 | 1.459 | 30.48 | 0.03 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 3.774 | 8.489 | 0.07 |
| `roundtrip_quality` | MEASURED | 0.142 | 0.141 | 2,116.646 | 0.234 | 30.24 | 0.27 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 42.883 | 1.961 | 0.30 |
| `direct_read_bounded` | MEASURED | 0.003 | 0.000 | 36,183.377 | 7,474.441 | 23.84 | 0.00 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 0.002 | NOT_AVAILABLE | 0.00 |
| `field_projection` | MEASURED | 3.566 | 3.531 | 53,282.623 | 5.793 | 23.82 | 0.00 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 1.001 | NOT_AVAILABLE | 0.00 |
| `memo_lazy` | MEASURED | 0.023 | 0.031 | 88,808.369 | 316.063 | 23.58 | 0.00 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 0.014 | NOT_AVAILABLE | 0.00 |
| `raw_mode_none` | MEASURED | 3.643 | 3.656 | 52,150.838 | 5.670 | 23.79 | 0.00 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 1.001 | NOT_AVAILABLE | 0.00 |
| `export_1m_records` | MEASURED | 52.915 | 52.562 | 18,898.184 | 2.055 | 31.66 | 344.05 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 9.327 | 1.000 | 344.05 |
| `memo_heavy_190k` | MEASURED | 20.186 | 20.078 | 9,412.480 | 33.495 | 33.58 | 717.48 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 4.134 | 1.000 | 717.48 |
| `reconstruction_190k` | MEASURED | 47.993 | 47.172 | 3,958.895 | 1.434 | 61.02 | 20.66 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 3.799 | 8.841 | 41.32 |
| `reconstruction_memo_190k` | MEASURED | 65.056 | 63.297 | 2,920.550 | 11.084 | 61.68 | 676.12 | 8.15 | 667.97 | 10.268 | 6.699 | 3.076 | 1,352.25 |
| `jsonl_conversion_xlsx` | MEASURED | 7.362 | 7.156 | 16,222.274 | 2.717 | 42.93 | 3.21 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 5.356 | 28.144 | 3.21 |

## Notes

- Each scenario runs in its own worker subprocess with a configurable timeout; one failed/timed-out scenario is `FAILED` and does not affect the others.
- Warm-up runs are excluded from aggregates; every warm-up and repetition writes into a fresh isolated directory and uses the same post-validation after the timed call.
- `output_bytes` is the authoritative final size of the scenario's own output directory, so re-running an overwritten scenario still reports the real size (never a zero diff).
- Peak RSS is the maximum of `psutil` samples taken on a background thread during the measured call (the sampler is always stopped/joined in `finally`). Without `psutil` it is `NOT_AVAILABLE`.
- `temporary_bytes_written` is the logical size of the atomic `.partial` files published by the measured call (the worker intercepts `os.replace` for that call only); it is **0** when the operation created no temporary file and `NOT_AVAILABLE` (with a reason) only if the platform forbids reading the temporary file.
- `temporary_files_left` and `temporary_bytes_left` are checked after timing and must both be zero; atomic-write residue fails the sample and baseline gate.
- `NOT_IMPLEMENTED` scenarios are listed verbatim and are not estimated.

# dbfbridge Phase 0 benchmark baseline

- Profile: `full`
- Commit: `542961981e0062cdc977d1b7a4eec721e1f16fd4` (origin/main: `addbadb9281914661bf742924f45039e46a895cd`)
- Worktree: clean on branch ``
- Warm-up: 1 (excluded from results); Repetitions: 3 (measured)
- Aggregation: median of measured repetitions (all samples preserved in JSON)
- Python: 3.12.10
- OS: Windows 2025Server 10.0.26100
- CPU: AMD64 Family 25 Model 1 Stepping 1, AuthenticAMD (4 logical CPUs), 16 GiB RAM
- Packages: dbfbridge 0.1.0, dbf 0.99.11, dbfread 2.0.7, orjson 3.12.0, polars 1.44.1, openpyxl 3.1.5, xlsxwriter 3.2.9, psutil 7.2.2

Statuses: `MEASURED` = all measured repetitions succeeded; `FAILED` = a repetition failed, the worker crashed/timed out, or output was invalid (no metrics invented); `NOT_IMPLEMENTED` = the feature does not exist in dbfbridge 0.1.0 (not simulated); `NOT_AVAILABLE` = a metric could not be provided (e.g. RSS without psutil).

| Scenario | Status | median wall (s) | median cpu (s) | median rec/s | median MiB/s | peak RSS (MiB) | output (MiB) | max DBF (MiB) | max FPT (MiB) | median FPT MiB/s | median read amp | median write amp | max temporary written (MiB) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `jsonl_conversion_json` | MEASURED | 0.238 | 0.234 | 501,639.058 | 84.012 | 56.70 | 20.11 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 1.000 | 1.000 | 20.11 |
| `jsonl_conversion_csv` | MEASURED | 0.055 | 0.156 | 2,167,293.687 | 362.966 | 98.36 | 14.08 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 0.000 | 1.000 | 14.08 |
| `raw_record_metadata_default` | MEASURED | 0.026 | 0.016 | 11,384.422 | 1.258 | 29.71 | 0.12 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 9.558 | 1.000 | 0.12 |
| `export_jsonl_validate_on` | MEASURED | 11.421 | 9.750 | 16,636.064 | 1.809 | 32.58 | 65.21 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 9.312 | 1.000 | 65.21 |
| `export_jsonl_validate_off` | MEASURED | 7.875 | 7.875 | 24,126.993 | 2.623 | 26.87 | 65.21 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 6.156 | 1.000 | 65.21 |
| `memo_skip` | MEASURED | 0.091 | 0.094 | 21,960.574 | 78.156 | 31.78 | 0.30 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 2.099 | 1.000 | 0.30 |
| `memo_null` | MEASURED | 0.092 | 0.094 | 21,729.754 | 77.335 | 24.89 | 0.30 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 2.099 | 1.000 | 0.30 |
| `memo_inline` | MEASURED | 0.222 | 0.219 | 9,026.566 | 32.125 | 26.81 | 7.56 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 4.139 | 1.000 | 7.56 |
| `deleted_skip` | MEASURED | 0.059 | 0.062 | 16,893.319 | 1.846 | 29.84 | 0.32 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 8.900 | 1.000 | 0.32 |
| `deleted_include` | MEASURED | 0.061 | 0.062 | 16,461.393 | 1.798 | 26.02 | 0.37 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 8.878 | 1.000 | 0.37 |
| `encoding_cp1250` | MEASURED | 0.011 | 0.000 | 91.313 | 0.013 | 24.97 | 0.01 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 42.740 | 1.000 | 0.01 |
| `encoding_cp852` | MEASURED | 0.011 | 0.016 | 89.722 | 0.012 | 24.78 | 0.01 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 42.644 | 1.000 | 0.01 |
| `encoding_mazovia` | MEASURED | 0.011 | 0.016 | 90.780 | 0.013 | 24.60 | 0.01 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 42.836 | 1.000 | 0.01 |
| `reconstruction_jsonl_to_dbf` | MEASURED | 0.081 | 0.078 | 3,703.315 | 1.466 | 29.69 | 0.03 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 3.774 | 8.489 | 0.07 |
| `roundtrip_quality` | MEASURED | 0.141 | 0.125 | 2,128.195 | 0.235 | 30.15 | 0.27 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 42.883 | 1.961 | 0.30 |
| `export_1m_records` | MEASURED | 48.162 | 47.859 | 20,763.251 | 2.257 | 32.02 | 344.05 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 9.327 | 1.000 | 344.05 |
| `memo_heavy_190k` | MEASURED | 19.459 | 19.312 | 9,764.134 | 34.746 | 33.70 | 717.48 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 4.134 | 1.000 | 717.48 |
| `reconstruction_190k` | MEASURED | 45.990 | 45.625 | 4,131.299 | 1.497 | 59.38 | 20.66 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 3.799 | 8.841 | 41.32 |
| `reconstruction_memo_190k` | MEASURED | 61.201 | 59.797 | 3,104.532 | 11.783 | 60.02 | 676.12 | 8.15 | 667.97 | 10.914 | 6.699 | 3.076 | 1,352.25 |
| `jsonl_conversion_xlsx` | MEASURED | 6.804 | 6.750 | 17,551.403 | 2.939 | 43.94 | 3.21 | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | 5.356 | 28.144 | 3.21 |
| `direct_read_bounded` | NOT_IMPLEMENTED | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE |
| `field_projection` | NOT_IMPLEMENTED | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE |
| `memo_lazy` | NOT_IMPLEMENTED | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE |
| `raw_mode_none` | NOT_IMPLEMENTED | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE | NOT_AVAILABLE |

## Notes

- Each scenario runs in its own worker subprocess with a configurable timeout; one failed/timed-out scenario is `FAILED` and does not affect the others.
- Warm-up runs are excluded from aggregates; every warm-up and repetition writes into a fresh isolated directory and uses the same post-validation after the timed call.
- `output_bytes` is the authoritative final size of the scenario's own output directory, so re-running an overwritten scenario still reports the real size (never a zero diff).
- Peak RSS is the maximum of `psutil` samples taken on a background thread during the measured call (the sampler is always stopped/joined in `finally`). Without `psutil` it is `NOT_AVAILABLE`.
- `temporary_bytes_written` is the logical size of the atomic `.partial` files published by the measured call (the worker intercepts `os.replace` for that call only); it is **0** when the operation created no temporary file and `NOT_AVAILABLE` (with a reason) only if the platform forbids reading the temporary file.
- `temporary_files_left` and `temporary_bytes_left` are checked after timing and must both be zero; atomic-write residue fails the sample and baseline gate.
- `NOT_IMPLEMENTED` scenarios are listed verbatim and are not estimated.

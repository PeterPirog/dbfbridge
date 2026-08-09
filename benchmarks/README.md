# JSONL conversion benchmark

The benchmark uses synthetic, flat UTF-8 JSONL records and reports wall-clock time,
throughput, and process peak RSS. Generated datasets and outputs are not committed.

Run it with:

```bash
python benchmarks/benchmark_jsonl_conversion.py generate benchmark-data/input.jsonl --size-mb 100
python benchmarks/benchmark_jsonl_conversion.py json benchmark-data/input.jsonl benchmark-data/output.json
python benchmarks/benchmark_jsonl_conversion.py csv benchmark-data/input.jsonl benchmark-data/output.csv
python benchmarks/benchmark_jsonl_conversion.py xlsx benchmark-data/input.jsonl benchmark-data/output.xlsx
```

Results from Python 3.12, Polars 1.43.2, orjson 3.11.9, and XlsxWriter 3.2.9:

| Input | Conversion | Engine | Time | Throughput | Peak RSS |
|---:|---|---|---:|---:|---:|
| 100 MB | JSON, before | materialized list | 3.601 s | 27.77 MB/s | 366.12 MB |
| 100 MB | JSON, streaming | binary stream | 0.999 s | 100.11 MB/s | 49.39 MB |
| 500 MB | JSON, before | materialized list | 19.140 s | 26.12 MB/s | 1,737.95 MB |
| 500 MB | JSON, streaming | binary stream | 5.169 s | 96.72 MB/s | 49.42 MB |
| 1,024 MB | JSON, streaming | binary stream | 10.738 s | 95.36 MB/s | 49.48 MB |
| 100 MB | CSV, before | materialized Polars | 0.260 s | 384.83 MB/s | 316.90 MB |
| 100 MB | CSV, streaming | Polars sink | 0.737 s | 135.67 MB/s | 276.30 MB |
| 500 MB | CSV, before | materialized Polars | 1.742 s | 287.07 MB/s | 1,145.01 MB |
| 500 MB | CSV, streaming | Polars sink | 3.743 s | 133.57 MB/s | 679.34 MB |
| 1,024 MB | CSV, before | materialized Polars | 3.631 s | 281.99 MB/s | 2,256.67 MB |
| 1,024 MB | CSV, streaming | Polars sink | 3.307 s | 309.64 MB/s | 1,210.79 MB |
| 100 MB | XLSX, streaming | XlsxWriter constant memory | 33.350 s | 3.00 MB/s | 38.46 MB |

The CSV fast path uses the DBF schema and validated record count, so it performs one
Polars streaming pass. Unknown-schema or untrusted JSONL is inspected first without
retaining records. If a progress or cancellation callback is supplied, CSV uses the
slower Python streaming fallback; a 500 MB run took 20.824 seconds and 48.55 MB peak RSS.

These are single local runs rather than storage-isolated laboratory measurements.
The materialized XLSX baseline is absent because the repository did not contain a
working XLSX converter before this change.

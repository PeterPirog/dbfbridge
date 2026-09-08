# dbfbridge Direct Write measured profile (dbfbridge-direct-write-v1)

Mode: `full` · git: `82087a3aa55992acb2beaaf5e879dd11f817cac9` · Python: `3.14.0` · platform: `win32` · run: `run-f1974b574cf750989ca3a690e114b0dd`

| scenario | kind | status | records | wall (s) | rec/s | peak RSS (MiB) | temp written (B) | residue (B) | JSONL (B) | final out (KiB) |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| direct_write_190k_flat | throughput | MEASURED | 190000 | 12.43544 | 15279 | 62.1 | 5700424 | 0 | 0 | 5566.8 |
| direct_write_1m_flat | throughput_bounded_memory | MEASURED | 1000000 | 64.133062 | 15593 | 179.7 | 30000424 | 0 | 0 | 29297.3 |
| direct_write_varchar_nullflags | throughput_replay_path | MEASURED | 100000 | 7.354703 | 13597 | 61.9 | 6600424 | 0 | 0 | 6445.7 |
| cancellation_cleanup_smoke | functional_cleanup | MEASURED | None | 0.004299 | n/a | 59.4 | 0 | 0 | 0 | 0.0 |

W12 is functional cleanup evidence (`functional_cleanup`), not a throughput claim.  `private_spool_bytes` is NOT_AVAILABLE without production instrumentation; `intermediate_jsonl_bytes` is 0 for every scenario.  These are MEASURED EVIDENCE, not a regression baseline and not an optimization claim.

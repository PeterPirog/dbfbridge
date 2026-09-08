# dbfbridge Direct Write measured profile (dbfbridge-direct-write-v1)

Mode: `smoke` · git: `59360662162cea19bd5e6a3833f76de45733f05a` · Python: `3.14.0` · platform: `win32` · run: `run-8406616fc5abedecd91240708cd49857`

| scenario | kind | status | records | wall (s) | rec/s | peak RSS (MiB) | temp written (B) | residue (B) | JSONL (B) | final out (KiB) |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| direct_write_190k_flat | throughput | MEASURED | 2000 | 0.186494 | 10724 | 31.9 | 60424 | 0 | 0 | 59.0 |
| direct_write_1m_flat | throughput_bounded_memory | MEASURED | 5000 | 0.33442 | 14951 | 33.0 | 150424 | 0 | 0 | 146.9 |
| direct_write_varchar_nullflags | throughput_replay_path | MEASURED | 500 | 0.051904 | 9633 | 34.6 | 33424 | 0 | 0 | 32.6 |
| cancellation_cleanup_smoke | functional_cleanup | MEASURED | None | 0.004468 | n/a | 34.6 | 0 | 0 | 0 | 0.0 |

W12 is functional cleanup evidence (`functional_cleanup`), not a throughput claim.  `private_spool_bytes` is NOT_AVAILABLE without production instrumentation; `intermediate_jsonl_bytes` is 0 for every scenario.  These are MEASURED EVIDENCE, not a regression baseline and not an optimization claim.

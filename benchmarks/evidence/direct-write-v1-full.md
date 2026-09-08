# dbfbridge Direct Write measured profile (dbfbridge-direct-write-v1)

Mode: `full` · measured at: `8555edd1bfbd18fa3437f69cea893b686c748723` · Python: `3.14.0` · platform: `win32` · run: `run-d8fb4528a1dbd91e5057f1a39ec732d8`

| scenario | kind | status | records | wall (s) | rec/s | peak Δ RSS (MiB) | publish temp (B) | spool (B) | temp total (B) | residue (B) | JSONL (B) | final out (KiB) |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| direct_write_190k_flat | throughput | MEASURED | 190000 | 12.726834 | 14929 | 36.1 | 5700424 | 0 | 5700424 | 0 | 0 | 5566.8 |
| direct_write_1m_flat | throughput_bounded_memory | MEASURED | 1000000 | 66.639277 | 15006 | 140.3 | 30000424 | 0 | 30000424 | 0 | 0 | 29297.3 |
| direct_write_varchar_nullflags | throughput_replay_path | MEASURED | 100000 | 7.647009 | 13077 | 5.5 | 6600424 | 10150000 | 16750424 | 0 | 0 | 6445.7 |
| cancellation_cleanup_smoke | functional_cleanup | MEASURED | None | 0.009854 | n/a | 0.0 | 0 | 0 | 0 | 0 | 0 | 0.0 |

Memory facts (assessment: MEASURED_FACTS_ONLY): record ratio 5.2632 · peak RSS ratio 2.8848 · peak Δ ratio 3.8848.

W12 is functional cleanup evidence (`functional_cleanup`), not a throughput claim.  `intermediate_jsonl_bytes` is 0 for every scenario.  These are MEASURED EVIDENCE, not a regression baseline and not an optimization claim.  No Direct Write RSS regression threshold has been established by this first measured profile.

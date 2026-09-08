# dbfbridge Direct Write measured profile (dbfbridge-direct-write-v1)

Mode: `smoke` · measured at: `5ac57ad100d266ae0e8736f90dd8b00f7d54faf3` · Python: `3.14.0` · platform: `win32` · run: `run-03b6f1872f923c491838454eb63f8067`

| scenario | kind | status | records | wall (s) | rec/s | peak Δ RSS (MiB) | publish temp (B) | spool (B) | temp total (B) | residue (B) | JSONL (B) | final out (KiB) |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| direct_write_190k_flat | throughput | MEASURED | 2000 | 0.189349 | 10563 | 6.4 | 60424 | 0 | 60424 | 0 | 0 | 59.0 |
| direct_read_transform_write_190k | throughput_transform_pipeline | MEASURED | 2000 | 0.181595 | 11013 | 0.1 | 60424 | 0 | 60424 | 0 | 0 | 59.0 |
| direct_write_1m_flat | throughput_bounded_memory | MEASURED | 5000 | 0.35122 | 14236 | 0.2 | 150424 | 0 | 150424 | 0 | 0 | 146.9 |
| direct_write_character_heavy | throughput_character_heavy | MEASURED | 1000 | 0.095091 | 10516 | 0.0 | 176488 | 0 | 176488 | 0 | 0 | 172.4 |
| direct_write_memo_heavy | throughput_memo_heavy | MEASURED | 300 | 0.124841 | 2403 | 0.0 | 103616 | 0 | 103616 | 0 | 0 | 101.2 |
| direct_write_deleted_include | throughput_deleted_records | MEASURED | 1000 | 0.07503 | 13328 | 0.0 | 21360 | 0 | 21360 | 0 | 0 | 20.9 |
| direct_write_cp1250 | throughput_encoding_cp1250 | MEASURED | 500 | 0.041356 | 12090 | 0.2 | 31892 | 0 | 31892 | 0 | 0 | 31.1 |
| direct_write_cp852 | throughput_encoding_cp852 | MEASURED | 500 | 0.042974 | 11635 | 0.1 | 31892 | 0 | 31892 | 0 | 0 | 31.1 |
| direct_write_mazovia | throughput_encoding_mazovia | MEASURED | 500 | 0.043016 | 11624 | 0.1 | 31892 | 0 | 31892 | 0 | 0 | 31.1 |
| direct_write_varchar_nullflags | throughput_replay_path | MEASURED | 500 | 0.05106 | 9792 | 0.0 | 33424 | 0 | None | 0 | 0 | 32.6 |
| overwrite_transaction_staging_cost | transaction_staging_cost | MEASURED | 300 | 0.126037 | 2380 | 0.0 | 103616 | 0 | 103616 | 0 | 0 | 101.2 |
| cancellation_cleanup_smoke | functional_cleanup | MEASURED | None | 0.004589 | n/a | 0.0 | 0 | 0 | 0 | 0 | 0 | 0.0 |

Memory facts (assessment: MEASURED_FACTS_ONLY): record ratio 2.5 · peak RSS ratio 1.042 · peak Δ ratio 0.0263.

W12 is functional cleanup evidence (`functional_cleanup`), not a throughput claim.  `intermediate_jsonl_bytes` is 0 for every scenario.  These are MEASURED EVIDENCE, not a regression baseline and not an optimization claim.  No Direct Write RSS regression threshold has been established by this first measured profile.

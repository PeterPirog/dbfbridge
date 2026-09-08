# dbfbridge Direct Write measured profile (dbfbridge-direct-write-v1)

Mode: `full` · measured at: `62a7867ceb9f03364ffd231688e70ef40d4adb87` · Python: `3.14.0` · platform: `win32` · run: `run-43a408e9f707b7e8f879a23acb146557`

| scenario | kind | status | records | wall (s) | rec/s | peak Δ RSS (MiB) | publish temp (B) | spool (B) | temp total (B) | residue (B) | JSONL (B) | final out (KiB) |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| direct_write_190k_flat | throughput | MEASURED | 190000 | 18.730577 | 10144 | 35.9 | 5700424 | 0 | 5700424 | 0 | 0 | 5566.8 |
| direct_read_transform_write_190k | throughput_transform_pipeline | MEASURED | 190000 | 25.970665 | 7316 | 23.2 | 5700424 | 0 | 5700424 | 0 | 0 | 5566.8 |
| direct_write_1m_flat | throughput_bounded_memory | MEASURED | 1000000 | 103.76861 | 9637 | 137.4 | 30000424 | 0 | 30000424 | 0 | 0 | 29297.3 |
| direct_write_character_heavy | throughput_character_heavy | MEASURED | 100000 | 10.7797 | 9277 | 5.1 | 17600488 | 0 | 17600488 | 0 | 0 | 17188.0 |
| direct_write_memo_heavy | throughput_memo_heavy | MEASURED | 100000 | 46.005952 | 2174 | 5.3 | 35948864 | 0 | 35948864 | 0 | 0 | 35106.3 |
| direct_write_deleted_include | throughput_deleted_records | MEASURED | 100000 | 8.354739 | 11969 | 0.4 | 2100360 | 0 | 2100360 | 0 | 0 | 2051.1 |
| direct_write_cp1250 | throughput_encoding_cp1250 | MEASURED | 50000 | 3.916278 | 12767 | 2.7 | 3150392 | 0 | 3150392 | 0 | 0 | 3076.6 |
| direct_write_cp852 | throughput_encoding_cp852 | MEASURED | 50000 | 4.136433 | 12088 | 2.7 | 3150392 | 0 | 3150392 | 0 | 0 | 3076.6 |
| direct_write_mazovia | throughput_encoding_mazovia | MEASURED | 50000 | 4.314551 | 11589 | 2.7 | 3150392 | 0 | 3150392 | 0 | 0 | 3076.6 |
| direct_write_varchar_nullflags | throughput_replay_path | MEASURED | 100000 | 11.072402 | 9031 | 5.2 | 6600424 | 10150000 | 16750424 | 0 | 0 | 6445.7 |
| overwrite_transaction_staging_cost | transaction_staging_cost | MEASURED | 20000 | 9.217228 | 2170 | 0.9 | 7190464 | 0 | 7190464 | 0 | 0 | 7021.9 |
| cancellation_cleanup_smoke | functional_cleanup | MEASURED | None | 0.006478 | n/a | 0.0 | 0 | 0 | 0 | 0 | 0 | 0.0 |

Memory facts (assessment: MEASURED_FACTS_ONLY): record ratio 5.2632 · peak RSS ratio 2.8873 · peak Δ ratio 3.8215.

W12 is functional cleanup evidence (`functional_cleanup`), not a throughput claim.  `intermediate_jsonl_bytes` is 0 for every scenario.  These are MEASURED EVIDENCE, not a regression baseline and not an optimization claim.  No Direct Write RSS regression threshold has been established by this first measured profile.

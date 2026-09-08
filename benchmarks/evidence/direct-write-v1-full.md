# dbfbridge Direct Write measured profile (dbfbridge-direct-write-v1)

Mode: `full` · measured at: `556cf095092376c9cae54186494956a9db982d2c` · Python: `3.14.0` · platform: `win32` · run: `run-4208844d02afa8b82d0fbc9ca16a6258`

| scenario | kind | status | records | wall (s) | rec/s | peak Δ RSS (MiB) | publish temp (B) | spool (B) | temp total (B) | residue (B) | JSONL (B) | final out (KiB) |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| direct_write_190k_flat | throughput | MEASURED | 190000 | 12.685896 | 14977 | 36.3 | 5700424 | 0 | 5700424 | 0 | 0 | 5566.8 |
| direct_read_transform_write_190k | throughput_transform_pipeline | MEASURED | 190000 | 15.923506 | 11932 | 24.3 | 5700424 | 0 | 5700424 | 0 | 0 | 5566.8 |
| direct_write_1m_flat | throughput_bounded_memory | MEASURED | 1000000 | 66.121717 | 15124 | 135.5 | 30000424 | 0 | 30000424 | 0 | 0 | 29297.3 |
| direct_write_character_heavy | throughput_character_heavy | MEASURED | 100000 | 7.907984 | 12645 | 5.6 | 17600488 | 0 | 17600488 | 0 | 0 | 17188.0 |
| direct_write_memo_heavy | throughput_memo_heavy | MEASURED | 100000 | 33.145608 | 3017 | 5.6 | 35852863 | 0 | 35852863 | 0 | 0 | 35012.6 |
| direct_write_deleted_include | throughput_deleted_records | MEASURED | 100000 | 5.508605 | 18153 | 0.0 | 2100360 | 0 | 2100360 | 0 | 0 | 2051.1 |
| direct_write_cp1250 | throughput_encoding_cp1250 | MEASURED | 50000 | 2.70019 | 18517 | 2.5 | 3150392 | 0 | 3150392 | 0 | 0 | 3076.6 |
| direct_write_cp852 | throughput_encoding_cp852 | MEASURED | 50000 | 2.716164 | 18408 | 2.8 | 3150392 | 0 | 3150392 | 0 | 0 | 3076.6 |
| direct_write_mazovia | throughput_encoding_mazovia | MEASURED | 50000 | 2.881248 | 17354 | 2.8 | 3150392 | 0 | 3150392 | 0 | 0 | 3076.6 |
| direct_write_varchar_nullflags | throughput_replay_path | MEASURED | 100000 | 7.638369 | 13092 | 5.5 | 6600424 | 10150000 | 16750424 | 0 | 0 | 6445.7 |
| overwrite_transaction_staging_cost | transaction_staging_cost | MEASURED | 20000 | 6.495809 | 3079 | 1.3 | 7171263 | 0 | 7171263 | 0 | 0 | 7003.2 |
| cancellation_cleanup_smoke | functional_cleanup | MEASURED | None | 0.004415 | n/a | 0.0 | 0 | 0 | 0 | 0 | 0 | 0.0 |

Memory facts (assessment: MEASURED_FACTS_ONLY): record ratio 5.2632 · peak RSS ratio 2.8792 · peak Δ ratio 3.7305.

W12 is functional cleanup evidence (`functional_cleanup`), not a throughput claim.  `intermediate_jsonl_bytes` is 0 for every scenario.  These are MEASURED EVIDENCE, not a regression baseline and not an optimization claim.  No Direct Write RSS regression threshold has been established by this first measured profile.

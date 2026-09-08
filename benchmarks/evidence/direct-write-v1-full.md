# dbfbridge Direct Write measured profile (dbfbridge-direct-write-v1)

Mode: `full` · measured at: `5ac57ad100d266ae0e8736f90dd8b00f7d54faf3` · Python: `3.14.0` · platform: `win32` · run: `run-7ec9dab4c3fd2d955953c6e31bd478a7`

| scenario | kind | status | records | wall (s) | rec/s | peak Δ RSS (MiB) | publish temp (B) | spool (B) | temp total (B) | residue (B) | JSONL (B) | final out (KiB) |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| direct_write_190k_flat | throughput | MEASURED | 190000 | 13.274672 | 14313 | 36.3 | 5700424 | 0 | 5700424 | 0 | 0 | 5566.8 |
| direct_read_transform_write_190k | throughput_transform_pipeline | MEASURED | 190000 | 16.554054 | 11478 | 24.2 | 5700424 | 0 | 5700424 | 0 | 0 | 5566.8 |
| direct_write_1m_flat | throughput_bounded_memory | MEASURED | 1000000 | 65.99736 | 15152 | 134.8 | 30000424 | 0 | 30000424 | 0 | 0 | 29297.3 |
| direct_write_character_heavy | throughput_character_heavy | MEASURED | 100000 | 7.672967 | 13033 | 5.4 | 17600488 | 0 | 17600488 | 0 | 0 | 17188.0 |
| direct_write_memo_heavy | throughput_memo_heavy | MEASURED | 100000 | 32.967028 | 3033 | 5.3 | 35948864 | 0 | 35948864 | 0 | 0 | 35106.3 |
| direct_write_deleted_include | throughput_deleted_records | MEASURED | 100000 | 5.461795 | 18309 | 0.4 | 2100360 | 0 | 2100360 | 0 | 0 | 2051.1 |
| direct_write_cp1250 | throughput_encoding_cp1250 | MEASURED | 50000 | 2.582901 | 19358 | 3.5 | 3150392 | 0 | 3150392 | 0 | 0 | 3076.6 |
| direct_write_cp852 | throughput_encoding_cp852 | MEASURED | 50000 | 2.655643 | 18828 | 2.7 | 3150392 | 0 | 3150392 | 0 | 0 | 3076.6 |
| direct_write_mazovia | throughput_encoding_mazovia | MEASURED | 50000 | 2.821896 | 17719 | 1.1 | 3150392 | 0 | 3150392 | 0 | 0 | 3076.6 |
| direct_write_varchar_nullflags | throughput_replay_path | MEASURED | 100000 | 7.45364 | 13416 | 4.4 | 6600424 | 10150000 | 16750424 | 0 | 0 | 6445.7 |
| overwrite_transaction_staging_cost | transaction_staging_cost | MEASURED | 20000 | 6.541774 | 3057 | 0.9 | 7190464 | 0 | 7190464 | 0 | 0 | 7021.9 |
| cancellation_cleanup_smoke | functional_cleanup | MEASURED | None | 0.004443 | n/a | 0.0 | 0 | 0 | 0 | 0 | 0 | 0.0 |

Memory facts (assessment: MEASURED_FACTS_ONLY): record ratio 5.2632 · peak RSS ratio 2.8893 · peak Δ ratio 3.7102.

W12 is functional cleanup evidence (`functional_cleanup`), not a throughput claim.  `intermediate_jsonl_bytes` is 0 for every scenario.  These are MEASURED EVIDENCE, not a regression baseline and not an optimization claim.  No Direct Write RSS regression threshold has been established by this first measured profile.

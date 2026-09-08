# dbfbridge Direct Write measured profile (dbfbridge-direct-write-v1)

Mode: `smoke` · measured at: `62a7867ceb9f03364ffd231688e70ef40d4adb87` · Python: `3.14.0` · platform: `win32` · run: `run-ee56ad0e3de74f2b1ddc1cb72948675e`

| scenario | kind | status | records | wall (s) | rec/s | peak Δ RSS (MiB) | publish temp (B) | spool (B) | temp total (B) | residue (B) | JSONL (B) | final out (KiB) |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| direct_write_190k_flat | throughput | MEASURED | 2000 | 0.315923 | 6331 | 6.3 | 60424 | 0 | 60424 | 0 | 0 | 59.0 |
| direct_read_transform_write_190k | throughput_transform_pipeline | MEASURED | 2000 | 0.300143 | 6663 | 0.0 | 60424 | 0 | 60424 | 0 | 0 | 59.0 |
| direct_write_1m_flat | throughput_bounded_memory | MEASURED | 5000 | 0.568216 | 8799 | 0.2 | 150424 | 0 | 150424 | 0 | 0 | 146.9 |
| direct_write_character_heavy | throughput_character_heavy | MEASURED | 1000 | 0.153851 | 6500 | 0.0 | 176488 | 0 | 176488 | 0 | 0 | 172.4 |
| direct_write_memo_heavy | throughput_memo_heavy | MEASURED | 300 | 0.163754 | 1832 | 0.0 | 103616 | 0 | 103616 | 0 | 0 | 101.2 |
| direct_write_deleted_include | throughput_deleted_records | MEASURED | 1000 | 0.121039 | 8262 | 0.0 | 21360 | 0 | 21360 | 0 | 0 | 20.9 |
| direct_write_cp1250 | throughput_encoding_cp1250 | MEASURED | 500 | 0.075489 | 6624 | 0.2 | 31892 | 0 | 31892 | 0 | 0 | 31.1 |
| direct_write_cp852 | throughput_encoding_cp852 | MEASURED | 500 | 0.065917 | 7585 | 0.0 | 31892 | 0 | 31892 | 0 | 0 | 31.1 |
| direct_write_mazovia | throughput_encoding_mazovia | MEASURED | 500 | 0.071553 | 6988 | 0.0 | 31892 | 0 | 31892 | 0 | 0 | 31.1 |
| direct_write_varchar_nullflags | throughput_replay_path | MEASURED | 500 | 0.093008 | 5376 | 0.0 | 33424 | 0 | None | 0 | 0 | 32.6 |
| overwrite_transaction_staging_cost | transaction_staging_cost | MEASURED | 300 | 0.168918 | 1776 | 0.0 | 103616 | 0 | 103616 | 0 | 0 | 101.2 |
| cancellation_cleanup_smoke | functional_cleanup | MEASURED | None | 0.006614 | n/a | 0.0 | 0 | 0 | 0 | 0 | 0 | 0.0 |

Memory facts (assessment: MEASURED_FACTS_ONLY): record ratio 2.5 · peak RSS ratio 1.0462 · peak Δ ratio 0.0262.

W12 is functional cleanup evidence (`functional_cleanup`), not a throughput claim.  `intermediate_jsonl_bytes` is 0 for every scenario.  These are MEASURED EVIDENCE, not a regression baseline and not an optimization claim.  No Direct Write RSS regression threshold has been established by this first measured profile.

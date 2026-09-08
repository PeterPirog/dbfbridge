# dbfbridge Direct Write measured profile (dbfbridge-direct-write-v1)

Mode: `full` · measured at: `cbc7d6e20a697ec39e33823f91615843412c669f` · Python: `3.14.0` · platform: `win32` · run: `run-d47357ab10a284b9b23af424db1125fa`

| scenario | kind | status | records | wall (s) | rec/s | peak Δ RSS (MiB) | publish temp (B) | spool (B) | temp total (B) | residue (B) | JSONL (B) | final out (KiB) |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| direct_write_190k_flat | throughput | MEASURED | 190000 | 13.06044 | 14548 | 36.2 | 5700424 | 0 | 5700424 | 0 | 0 | 5566.8 |
| direct_write_1m_flat | throughput_bounded_memory | MEASURED | 1000000 | 69.41443 | 14406 | 140.4 | 30000424 | 0 | 30000424 | 0 | 0 | 29297.3 |
| direct_write_varchar_nullflags | throughput_replay_path | MEASURED | 100000 | 7.634951 | 13098 | 6.6 | 6600424 | 10150000 | 16750424 | 0 | 0 | 6445.7 |
| cancellation_cleanup_smoke | functional_cleanup | MEASURED | None | 0.005979 | n/a | 0.0 | 0 | 0 | 0 | 0 | 0 | 0.0 |

Memory comparison (INCONCLUSIVE): record ratio 5.2632 · peak RSS ratio 2.8869 · peak Δ ratio 3.8817.

W12 is functional cleanup evidence (`functional_cleanup`), not a throughput claim.  `intermediate_jsonl_bytes` is 0 for every scenario.  These are MEASURED EVIDENCE, not a regression baseline and not an optimization claim.

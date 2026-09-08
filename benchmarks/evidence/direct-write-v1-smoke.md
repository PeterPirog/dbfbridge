# dbfbridge Direct Write measured profile (dbfbridge-direct-write-v1)

Mode: `smoke` · measured at: `cbc7d6e20a697ec39e33823f91615843412c669f` · Python: `3.14.0` · platform: `win32` · run: `run-94224ec7343c277e2bb6744b993ad048`

| scenario | kind | status | records | wall (s) | rec/s | peak Δ RSS (MiB) | publish temp (B) | spool (B) | temp total (B) | residue (B) | JSONL (B) | final out (KiB) |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| direct_write_190k_flat | throughput | MEASURED | 2000 | 0.187709 | 10655 | 6.2 | 60424 | 0 | 60424 | 0 | 0 | 59.0 |
| direct_write_1m_flat | throughput_bounded_memory | MEASURED | 5000 | 0.339524 | 14727 | 0.3 | 150424 | 0 | 150424 | 0 | 0 | 146.9 |
| direct_write_varchar_nullflags | throughput_replay_path | MEASURED | 500 | 0.053047 | 9426 | 0.0 | 33424 | 0 | None | 0 | 0 | 32.6 |
| cancellation_cleanup_smoke | functional_cleanup | MEASURED | None | 0.003954 | n/a | 0.0 | 0 | 0 | 0 | 0 | 0 | 0.0 |

Memory comparison (NO_INPUT_MATERIALIZATION_EVIDENCE): record ratio 2.5 · peak RSS ratio 1.0409 · peak Δ ratio 0.0449.

W12 is functional cleanup evidence (`functional_cleanup`), not a throughput claim.  `intermediate_jsonl_bytes` is 0 for every scenario.  These are MEASURED EVIDENCE, not a regression baseline and not an optimization claim.

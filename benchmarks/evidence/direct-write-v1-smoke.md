# dbfbridge Direct Write measured profile (dbfbridge-direct-write-v1)

Mode: `smoke` · measured at: `8555edd1bfbd18fa3437f69cea893b686c748723` · Python: `3.14.0` · platform: `win32` · run: `run-05f8584626c0a5ccb2578dc5ee12b404`

| scenario | kind | status | records | wall (s) | rec/s | peak Δ RSS (MiB) | publish temp (B) | spool (B) | temp total (B) | residue (B) | JSONL (B) | final out (KiB) |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| direct_write_190k_flat | throughput | MEASURED | 2000 | 0.191383 | 10450 | 6.2 | 60424 | 0 | 60424 | 0 | 0 | 59.0 |
| direct_write_1m_flat | throughput_bounded_memory | MEASURED | 5000 | 0.35263 | 14179 | 0.3 | 150424 | 0 | 150424 | 0 | 0 | 146.9 |
| direct_write_varchar_nullflags | throughput_replay_path | MEASURED | 500 | 0.054126 | 9238 | 0.0 | 33424 | 0 | None | 0 | 0 | 32.6 |
| cancellation_cleanup_smoke | functional_cleanup | MEASURED | None | 0.004471 | n/a | 0.0 | 0 | 0 | 0 | 0 | 0 | 0.0 |

Memory facts (assessment: MEASURED_FACTS_ONLY): record ratio 2.5 · peak RSS ratio 1.0368 · peak Δ ratio 0.0426.

W12 is functional cleanup evidence (`functional_cleanup`), not a throughput claim.  `intermediate_jsonl_bytes` is 0 for every scenario.  These are MEASURED EVIDENCE, not a regression baseline and not an optimization claim.  No Direct Write RSS regression threshold has been established by this first measured profile.

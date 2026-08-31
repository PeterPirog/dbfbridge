# BEFORE/AFTER comparison report

## Artifacts

- **before** (sha256 `d3b5ab454706b5e7085811c49fc06f8a421f127498695ae1178a1efc07453aa6`): Windows 2025Server 10.0.26100 / Python 3.12.10 / AMD64 / commit '542961981e00' / contract=None / profile='full'
- **after** (sha256 `e13015eb49d30444f26c47bafd6619d09e5ed710b75b08246bd8e19137710709`): Windows 2025Server 10.0.26100 / Python 3.12.10 / AMD64 / commit 'df035de662f2' / contract='phase-1-direct-read-v1' / profile='full'

## Environment comparability
Verdict: **PARTIALLY_COMPARABLE**
**PARTIALLY COMPARABLE**: storage descriptor missing or different; runner descriptor missing or different. Numbers and ratios are shown, but I/O-sensitive results do not prove improvement without a shared storage provenance.

- BEFORE run_id: `N/A (legacy Phase 0)`
- AFTER run_id: `run-ceaf809d8b52a2b6873d7594dff4a769`

| field | BEFORE | AFTER |
|---|---|---|
| python | 3.12.10 | 3.12.10 |
| os | Windows 2025Server 10.0.26100 | Windows 2025Server 10.0.26100 |
| arch | AMD64 | AMD64 |
| processor | AMD64 Family 25 Model 1 Stepping 1, AuthenticAMD | AMD64 Family 25 Model 1 Stepping 1, AuthenticAMD |
| cpu_count | 4 | 4 |
| physical_memory_bytes | 17174360064 | 17174360064 |
| packages | {'dbfbridge': '0.1.0', 'dbf': '0.99.11', 'dbfread': '2.0.7', 'orjson': '3.12.0', 'polars': '1.44.1', 'openpyxl': '3.1.5', 'xlsxwriter': '3.2.9', 'psutil': '7.2.2'} | {'dbfbridge': '0.1.0', 'dbf': '0.99.11', 'dbfread': '2.0.7', 'orjson': '3.12.0', 'polars': '1.44.1', 'openpyxl': '3.1.5', 'xlsxwriter': '3.2.9', 'psutil': '7.2.2'} |
| benchmark_contract | NOT_AVAILABLE | phase-1-direct-read-v1 |

## Common MEASURED scenarios

| scenario | metric | BEFORE | AFTER | AFTER/BEFORE | change % |
|---|---|---|---|---|---|
| jsonl_conversion_json | median wall time (s) | 0.238064 | 0.238967 | 1.004 | +0.4 |
| jsonl_conversion_json | median CPU time (s) | 0.234375 | 0.234375 | 1.000 | +0.0 |
| jsonl_conversion_json | records/s | 501639 | 499742 | 0.996 | -0.4 |
| jsonl_conversion_json | source MiB/s | 84.0117 | 83.6941 | 0.996 | -0.4 |
| jsonl_conversion_json | peak RSS (bytes) | 59457536 | 58023936 | 0.976 | -2.4 |
| jsonl_conversion_json | read amplification (measured) | 1 | 1 | 1.000 | +0.0 |
| jsonl_conversion_json | write amplification (measured) | 1 | 1 | 1.000 | +0.0 |
| jsonl_conversion_json | output bytes (max) | 21091089 | 21091089 | 1.000 | +0.0 |
| jsonl_conversion_json | temporary bytes written (max) | 21091089 | 21091089 | 1.000 | +0.0 |
| jsonl_conversion_csv | median wall time (s) | 0.055102 | 0.056562 | 1.026 | +2.6 |
| jsonl_conversion_csv | median CPU time (s) | 0.15625 | 0.15625 | 1.000 | +0.0 |
| jsonl_conversion_csv | records/s | 2.16729e+06 | 2.11134e+06 | 0.974 | -2.6 |
| jsonl_conversion_csv | source MiB/s | 362.966 | 353.595 | 0.974 | -2.6 |
| jsonl_conversion_csv | peak RSS (bytes) | 103137280 | 105095168 | 1.019 | +1.9 |
| jsonl_conversion_csv | read amplification (measured) | 0 | 0 | NOT_AVAILABLE | NOT_AVAILABLE |
| jsonl_conversion_csv | write amplification (measured) | 1 | 1 | 1.000 | +0.0 |
| jsonl_conversion_csv | output bytes (max) | 14761752 | 14761752 | 1.000 | +0.0 |
| jsonl_conversion_csv | temporary bytes written (max) | 14761752 | 14761752 | 1.000 | +0.0 |
| raw_record_metadata_default | median wall time (s) | 0.026352 | 0.028861 | 1.095 | +9.5 |
| raw_record_metadata_default | median CPU time (s) | 0.015625 | 0.03125 | 2.000 | +100.0 |
| raw_record_metadata_default | records/s | 11384.4 | 10394.8 | 0.913 | -8.7 |
| raw_record_metadata_default | source MiB/s | 1.25768 | 1.14835 | 0.913 | -8.7 |
| raw_record_metadata_default | peak RSS (bytes) | 31154176 | 32153600 | 1.032 | +3.2 |
| raw_record_metadata_default | read amplification (measured) | 9.5579 | 9.5579 | 1.000 | +0.0 |
| raw_record_metadata_default | write amplification (measured) | 1 | 1 | 1.000 | +0.0 |
| raw_record_metadata_default | output bytes (max) | 124518 | 124498 | 1.000 | -0.0 |
| raw_record_metadata_default | temporary bytes written (max) | 124518 | 124498 | 1.000 | -0.0 |
| export_jsonl_validate_on | median wall time (s) | 11.421 | 9.92153 | 0.869 | -13.1 |
| export_jsonl_validate_on | median CPU time (s) | 9.75 | 9.90625 | 1.016 | +1.6 |
| export_jsonl_validate_on | records/s | 16636.1 | 19150.3 | 1.151 | +15.1 |
| export_jsonl_validate_on | source MiB/s | 1.8087 | 2.08205 | 1.151 | +15.1 |
| export_jsonl_validate_on | peak RSS (bytes) | 34164736 | 34615296 | 1.013 | +1.3 |
| export_jsonl_validate_on | read amplification (measured) | 9.312 | 9.312 | 1.000 | +0.0 |
| export_jsonl_validate_on | write amplification (measured) | 1 | 1 | 1.000 | +0.0 |
| export_jsonl_validate_on | output bytes (max) | 68374795 | 68374774 | 1.000 | -0.0 |
| export_jsonl_validate_on | temporary bytes written (max) | 68374795 | 68374774 | 1.000 | -0.0 |
| export_jsonl_validate_off | median wall time (s) | 7.875 | 8.90489 | 1.131 | +13.1 |
| export_jsonl_validate_off | median CPU time (s) | 7.875 | 8.82812 | 1.121 | +12.1 |
| export_jsonl_validate_off | records/s | 24127 | 21336.6 | 0.884 | -11.6 |
| export_jsonl_validate_off | source MiB/s | 2.62313 | 2.31975 | 0.884 | -11.6 |
| export_jsonl_validate_off | peak RSS (bytes) | 28176384 | 28950528 | 1.027 | +2.7 |
| export_jsonl_validate_off | read amplification (measured) | 6.1559 | 6.1559 | 1.000 | +0.0 |
| export_jsonl_validate_off | write amplification (measured) | 1 | 1 | 1.000 | +0.0 |
| export_jsonl_validate_off | output bytes (max) | 68374798 | 68374777 | 1.000 | -0.0 |
| export_jsonl_validate_off | temporary bytes written (max) | 68374798 | 68374777 | 1.000 | -0.0 |
| memo_skip | median wall time (s) | 0.091072 | 0.098259 | 1.079 | +7.9 |
| memo_skip | median CPU time (s) | 0.09375 | 0.09375 | 1.000 | +0.0 |
| memo_skip | records/s | 21960.6 | 20354.4 | 0.927 | -7.3 |
| memo_skip | source MiB/s | 78.1561 | 72.4398 | 0.927 | -7.3 |
| memo_skip | peak RSS (bytes) | 33325056 | 33542144 | 1.007 | +0.7 |
| memo_skip | read amplification (measured) | 2.099 | 2.099 | 1.000 | +0.0 |
| memo_skip | write amplification (measured) | 1 | 1 | 1.000 | +0.0 |
| memo_skip | output bytes (max) | 309884 | 309864 | 1.000 | -0.0 |
| memo_skip | temporary bytes written (max) | 309884 | 309864 | 1.000 | -0.0 |
| memo_null | median wall time (s) | 0.09204 | 0.097811 | 1.063 | +6.3 |
| memo_null | median CPU time (s) | 0.09375 | 0.09375 | 1.000 | +0.0 |
| memo_null | records/s | 21729.8 | 20447.7 | 0.941 | -5.9 |
| memo_null | source MiB/s | 77.3347 | 72.7719 | 0.941 | -5.9 |
| memo_null | peak RSS (bytes) | 26095616 | 27488256 | 1.053 | +5.3 |
| memo_null | read amplification (measured) | 2.099 | 2.099 | 1.000 | +0.0 |
| memo_null | write amplification (measured) | 1 | 1 | 1.000 | +0.0 |
| memo_null | output bytes (max) | 309884 | 309864 | 1.000 | -0.0 |
| memo_null | temporary bytes written (max) | 309884 | 309864 | 1.000 | -0.0 |
| memo_inline | median wall time (s) | 0.221568 | 0.230564 | 1.041 | +4.1 |
| memo_inline | median CPU time (s) | 0.21875 | 0.21875 | 1.000 | +0.0 |
| memo_inline | records/s | 9026.57 | 8674.37 | 0.961 | -3.9 |
| memo_inline | source MiB/s | 32.1249 | 30.8715 | 0.961 | -3.9 |
| memo_inline | peak RSS (bytes) | 28114944 | 29134848 | 1.036 | +3.6 |
| memo_inline | read amplification (measured) | 4.1388 | 4.1388 | 1.000 | +0.0 |
| memo_inline | write amplification (measured) | 1 | 1 | 1.000 | +0.0 |
| memo_inline | output bytes (max) | 7922093 | 7922073 | 1.000 | -0.0 |
| memo_inline | temporary bytes written (max) | 7922093 | 7922073 | 1.000 | -0.0 |
| deleted_skip | median wall time (s) | 0.059195 | 0.06698 | 1.132 | +13.2 |
| deleted_skip | median CPU time (s) | 0.0625 | 0.0625 | 1.000 | +0.0 |
| deleted_skip | records/s | 16893.3 | 14929.7 | 0.884 | -11.6 |
| deleted_skip | source MiB/s | 1.84552 | 1.631 | 0.884 | -11.6 |
| deleted_skip | peak RSS (bytes) | 31293440 | 31916032 | 1.020 | +2.0 |
| deleted_skip | read amplification (measured) | 8.8996 | 8.8996 | 1.000 | +0.0 |
| deleted_skip | write amplification (measured) | 1 | 1 | 1.000 | +0.0 |
| deleted_skip | output bytes (max) | 331488 | 331467 | 1.000 | -0.0 |
| deleted_skip | temporary bytes written (max) | 331488 | 331467 | 1.000 | -0.0 |
| deleted_include | median wall time (s) | 0.060748 | 0.06424 | 1.057 | +5.7 |
| deleted_include | median CPU time (s) | 0.0625 | 0.0625 | 1.000 | +0.0 |
| deleted_include | records/s | 16461.4 | 15566.6 | 0.946 | -5.4 |
| deleted_include | source MiB/s | 1.79833 | 1.70057 | 0.946 | -5.4 |
| deleted_include | peak RSS (bytes) | 27287552 | 26746880 | 0.980 | -2.0 |
| deleted_include | read amplification (measured) | 8.8779 | 8.8779 | 1.000 | +0.0 |
| deleted_include | write amplification (measured) | 1 | 1 | 1.000 | +0.0 |
| deleted_include | output bytes (max) | 387149 | 387128 | 1.000 | -0.0 |
| deleted_include | temporary bytes written (max) | 387149 | 387128 | 1.000 | -0.0 |
| encoding_cp1250 | median wall time (s) | 0.010951 | 0.011787 | 1.076 | +7.6 |
| encoding_cp1250 | median CPU time (s) | 0 | 0 | NOT_AVAILABLE | NOT_AVAILABLE |
| encoding_cp1250 | records/s | 91.3134 | 84.8421 | 0.929 | -7.1 |
| encoding_cp1250 | source MiB/s | 0.012714 | 0.011813 | 0.929 | -7.1 |
| encoding_cp1250 | peak RSS (bytes) | 26181632 | 26591232 | 1.016 | +1.6 |
| encoding_cp1250 | read amplification (measured) | 42.7397 | 42.7397 | 1.000 | +0.0 |
| encoding_cp1250 | write amplification (measured) | 1 | 1 | 1.000 | +0.0 |
| encoding_cp1250 | output bytes (max) | 7283 | 7263 | 0.997 | -0.3 |
| encoding_cp1250 | temporary bytes written (max) | 7283 | 7263 | 0.997 | -0.3 |
| encoding_cp852 | median wall time (s) | 0.011145 | 0.011247 | 1.009 | +0.9 |
| encoding_cp852 | median CPU time (s) | 0.015625 | 0.015625 | 1.000 | +0.0 |
| encoding_cp852 | records/s | 89.7223 | 88.915 | 0.991 | -0.9 |
| encoding_cp852 | source MiB/s | 0.012493 | 0.01238 | 0.991 | -0.9 |
| encoding_cp852 | peak RSS (bytes) | 25985024 | 26734592 | 1.029 | +2.9 |
| encoding_cp852 | read amplification (measured) | 42.6438 | 42.6438 | 1.000 | +0.0 |
| encoding_cp852 | write amplification (measured) | 1 | 1 | 1.000 | +0.0 |
| encoding_cp852 | output bytes (max) | 7257 | 7237 | 0.997 | -0.3 |
| encoding_cp852 | temporary bytes written (max) | 7257 | 7237 | 0.997 | -0.3 |
| encoding_mazovia | median wall time (s) | 0.011016 | 0.01126 | 1.022 | +2.2 |
| encoding_mazovia | median CPU time (s) | 0.015625 | 0.015625 | 1.000 | +0.0 |
| encoding_mazovia | records/s | 90.7795 | 88.8092 | 0.978 | -2.2 |
| encoding_mazovia | source MiB/s | 0.01264 | 0.012365 | 0.978 | -2.2 |
| encoding_mazovia | peak RSS (bytes) | 25792512 | 26611712 | 1.032 | +3.2 |
| encoding_mazovia | read amplification (measured) | 42.8356 | 42.8356 | 1.000 | +0.0 |
| encoding_mazovia | write amplification (measured) | 1 | 1 | 1.000 | +0.0 |
| encoding_mazovia | output bytes (max) | 7309 | 7289 | 0.997 | -0.3 |
| encoding_mazovia | temporary bytes written (max) | 7309 | 7289 | 0.997 | -0.3 |
| reconstruction_jsonl_to_dbf | median wall time (s) | 0.081009 | 0.081363 | 1.004 | +0.4 |
| reconstruction_jsonl_to_dbf | median CPU time (s) | 0.078125 | 0.078125 | 1.000 | +0.0 |
| reconstruction_jsonl_to_dbf | records/s | 3703.32 | 3687.16 | 0.996 | -0.4 |
| reconstruction_jsonl_to_dbf | source MiB/s | 1.4659 | 1.45926 | 0.995 | -0.5 |
| reconstruction_jsonl_to_dbf | peak RSS (bytes) | 31129600 | 31965184 | 1.027 | +2.7 |
| reconstruction_jsonl_to_dbf | read amplification (measured) | 3.7736 | 3.7742 | 1.000 | +0.0 |
| reconstruction_jsonl_to_dbf | write amplification (measured) | 8.4888 | 8.4888 | 1.000 | +0.0 |
| reconstruction_jsonl_to_dbf | output bytes (max) | 35961 | 35961 | 1.000 | +0.0 |
| reconstruction_jsonl_to_dbf | temporary bytes written (max) | 70713 | 70713 | 1.000 | +0.0 |
| roundtrip_quality | median wall time (s) | 0.140965 | 0.141734 | 1.005 | +0.5 |
| roundtrip_quality | median CPU time (s) | 0.125 | 0.140625 | 1.125 | +12.5 |
| roundtrip_quality | records/s | 2128.2 | 2116.65 | 0.995 | -0.5 |
| roundtrip_quality | source MiB/s | 0.235109 | 0.233834 | 0.995 | -0.5 |
| roundtrip_quality | peak RSS (bytes) | 31612928 | 31707136 | 1.003 | +0.3 |
| roundtrip_quality | read amplification (measured) | 42.8834 | 42.8834 | 1.000 | +0.0 |
| roundtrip_quality | write amplification (measured) | 1.9614 | 1.9614 | 1.000 | +0.0 |
| roundtrip_quality | output bytes (max) | 280118 | 280108 | 1.000 | -0.0 |
| roundtrip_quality | temporary bytes written (max) | 314870 | 314860 | 1.000 | -0.0 |
| export_1m_records | median wall time (s) | 48.162 | 52.9151 | 1.099 | +9.9 |
| export_1m_records | median CPU time (s) | 47.8594 | 52.5625 | 1.098 | +9.8 |
| export_1m_records | records/s | 20763.3 | 18898.2 | 0.910 | -9.0 |
| export_1m_records | source MiB/s | 2.25737 | 2.0546 | 0.910 | -9.0 |
| export_1m_records | peak RSS (bytes) | 33574912 | 33202176 | 0.989 | -1.1 |
| export_1m_records | read amplification (measured) | 9.3273 | 9.3273 | 1.000 | +0.0 |
| export_1m_records | write amplification (measured) | 1 | 1 | 1.000 | +0.0 |
| export_1m_records | output bytes (max) | 360762670 | 360762650 | 1.000 | -0.0 |
| export_1m_records | temporary bytes written (max) | 360762670 | 360762650 | 1.000 | -0.0 |
| memo_heavy_190k | median wall time (s) | 19.459 | 20.186 | 1.037 | +3.7 |
| memo_heavy_190k | median CPU time (s) | 19.3125 | 20.0781 | 1.040 | +4.0 |
| memo_heavy_190k | records/s | 9764.13 | 9412.48 | 0.964 | -3.6 |
| memo_heavy_190k | source MiB/s | 34.7461 | 33.4947 | 0.964 | -3.6 |
| memo_heavy_190k | peak RSS (bytes) | 35332096 | 35209216 | 0.997 | -0.3 |
| memo_heavy_190k | read amplification (measured) | 4.1344 | 4.1344 | 1.000 | +0.0 |
| memo_heavy_190k | write amplification (measured) | 1 | 1 | 1.000 | +0.0 |
| memo_heavy_190k | output bytes (max) | 752334298 | 752334278 | 1.000 | -0.0 |
| memo_heavy_190k | temporary bytes written (max) | 752334298 | 752334278 | 1.000 | -0.0 |
| reconstruction_190k | median wall time (s) | 45.9904 | 47.9932 | 1.044 | +4.4 |
| reconstruction_190k | median CPU time (s) | 45.625 | 47.1719 | 1.034 | +3.4 |
| reconstruction_190k | records/s | 4131.3 | 3958.9 | 0.958 | -4.2 |
| reconstruction_190k | source MiB/s | 1.49664 | 1.43419 | 0.958 | -4.2 |
| reconstruction_190k | peak RSS (bytes) | 62263296 | 63979520 | 1.028 | +2.8 |
| reconstruction_190k | read amplification (measured) | 3.7991 | 3.7991 | 1.000 | +0.0 |
| reconstruction_190k | write amplification (measured) | 8.8415 | 8.8415 | 1.000 | +0.0 |
| reconstruction_190k | output bytes (max) | 21661769 | 21661768 | 1.000 | -0.0 |
| reconstruction_190k | temporary bytes written (max) | 43322321 | 43322320 | 1.000 | -0.0 |
| reconstruction_memo_190k | median wall time (s) | 61.2009 | 65.0562 | 1.063 | +6.3 |
| reconstruction_memo_190k | median CPU time (s) | 59.7969 | 63.2969 | 1.059 | +5.9 |
| reconstruction_memo_190k | records/s | 3104.53 | 2920.55 | 0.941 | -5.9 |
| reconstruction_memo_190k | source MiB/s | 11.7826 | 11.0843 | 0.941 | -5.9 |
| reconstruction_memo_190k | peak RSS (bytes) | 62939136 | 64671744 | 1.028 | +2.8 |
| reconstruction_memo_190k | read amplification (measured) | 6.6993 | 6.6993 | 1.000 | +0.0 |
| reconstruction_memo_190k | write amplification (measured) | 3.0756 | 3.0756 | 1.000 | +0.0 |
| reconstruction_memo_190k | output bytes (max) | 708968181 | 708968180 | 1.000 | -0.0 |
| reconstruction_memo_190k | temporary bytes written (max) | 1417934997 | 1417934996 | 1.000 | -0.0 |
| jsonl_conversion_xlsx | median wall time (s) | 6.80413 | 7.36161 | 1.082 | +8.2 |
| jsonl_conversion_xlsx | median CPU time (s) | 6.75 | 7.15625 | 1.060 | +6.0 |
| jsonl_conversion_xlsx | records/s | 17551.4 | 16222.3 | 0.924 | -7.6 |
| jsonl_conversion_xlsx | source MiB/s | 2.93941 | 2.71682 | 0.924 | -7.6 |
| jsonl_conversion_xlsx | peak RSS (bytes) | 46075904 | 45015040 | 0.977 | -2.3 |
| jsonl_conversion_xlsx | read amplification (measured) | 5.356 | 5.356 | 1.000 | +0.0 |
| jsonl_conversion_xlsx | write amplification (measured) | 28.1437 | 28.1437 | 1.000 | +0.0 |
| jsonl_conversion_xlsx | output bytes (max) | 3365535 | 3365536 | 1.000 | +0.0 |
| jsonl_conversion_xlsx | temporary bytes written (max) | 3365535 | 3365536 | 1.000 | +0.0 |

## Newly measured scenarios (no BEFORE measurement exists)

The BEFORE baseline listed these as `NOT_IMPLEMENTED`; they are `MEASURED` in AFTER. **No speedup is claimed for them** — there is no BEFORE number to compare against, and they are never 'infinitely faster' than a missing feature.

- `direct_read_bounded` (NEWLY_MEASURED)
- `field_projection` (NEWLY_MEASURED)
- `memo_lazy` (NEWLY_MEASURED)
- `raw_mode_none` (NEWLY_MEASURED)

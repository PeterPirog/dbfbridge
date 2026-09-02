# Phase 3 regression-CI calibration

This document records the evidence behind the committed Phase 3 regression
policy (`benchmarks/regression/phase-3-regression-policy-v1.json`).  Every
threshold in the policy is derived from the measurements listed here — no
percentage was chosen by hand.

## Calibration setup

| property | value |
|---|---|
| reference commit | `1ba135ad18fef291ef104795606a5860d15b0c4e` (merged main, 0.3 foundation) |
| benchmark contract | `phase-3-performance-v1` (profile `phase3`, all 23 scenarios) |
| workflow | `benchmark-phase3.yml` (`workflow_dispatch`, `canonical=false` — never a baseline) |
| OS | Windows Server 2025 (10.0.26100), AMD64 |
| Python | 3.12.10 |
| runner | `github-actions-windows-x64-win25-vs202620260824.214.3` (4 vCPU, 16 GiB) |
| storage | `github-actions-windows-runner-temp` |
| dependencies | dbfbridge 0.2.0, dbfread 2.0.7, dbf 0.99.11, orjson 3.12.0, polars 1.44.1, psutil 7.2.2 |
| recipe | warmup 1, repetitions 3, per-scenario timeout 900 s |

## Independent workflow runs (5 × separate hosted runner instances)

| workflow run | run_id (report) |
|---|---|
| [33546526573](https://github.com/PeterPirog/dbfbridge/actions/runs/33546526573) | see `provenance` in the calibration inputs |
| [33546534607](https://github.com/PeterPirog/dbfbridge/actions/runs/33546534607) | see `provenance` |
| [33546542658](https://github.com/PeterPirog/dbfbridge/actions/runs/33546542658) | see `provenance` |
| [33546551328](https://github.com/PeterPirog/dbfbridge/actions/runs/33546551328) | see `provenance` |
| [33546559054](https://github.com/PeterPirog/dbfbridge/actions/runs/33546559054) | see `provenance` |

All five runs: `23 MEASURED / 0 FAILED`, zero temporary residue, identical
commit/Python/dependencies.  The extracted per-run medians (auditable
inputs for the policy generator) are committed as
`benchmarks/regression/phase-3-regression-calibration-inputs.json`.

## Per-scenario wall variance (center = median of run medians)

| scenario | center (s) | MAD (s) | rel MAD | max/min |
|---|---|---|---|---|
| direct_read_1m | 19.3509 | 0.0460 | 0.2 % | 1.549 |
| direct_read_memo_inline | 0.0433 | 0.0002 | 0.6 % | 1.428 |
| direct_read_mazovia | 0.0006 | 0.0000 | 0.8 % | 1.389 |
| direct_read_projection_selected | 2.1147 | 0.0209 | 1.0 % | 1.383 |
| migration_jsonl_to_dbf_fpt | 65.7113 | 0.8367 | 1.3 % | 1.511 |
| direct_read_deleted_skip | 0.0187 | 0.0003 | 1.3 % | 1.625 |
| inspect_schema_1000 | 0.6840 | 0.0111 | 1.6 % | 1.888 |
| direct_read_projection_all | 3.5507 | 0.0642 | 1.8 % | 1.490 |
| inspect_schema_1 | 0.0007 | 0.0000 | 2.2 % | 1.922 |
| migration_validate_on | 10.2150 | 0.2761 | 2.7 % | 1.632 |
| direct_read_deleted_include | 0.0198 | 0.0006 | 2.9 % | 1.588 |
| direct_read_memo_heavy | 3.7966 | 0.1117 | 2.9 % | 1.379 |
| migration_validate_off | 8.9337 | 0.2753 | 3.1 % | 1.764 |
| direct_read_190k | 3.6988 | 0.1229 | 3.3 % | 1.572 |
| direct_read_memo_skip | 0.0158 | 0.0005 | 3.4 % | 1.922 |
| direct_read_raw_full | 3.5856 | 0.1305 | 3.6 % | 1.537 |
| migration_dbf_to_jsonl | 10.2289 | 0.3977 | 3.9 % | 1.729 |
| direct_read_cp852 | 0.0006 | 0.0000 | 4.1 % | 1.422 |
| direct_read_raw_none | 3.5012 | 0.1816 | 5.2 % | 1.521 |
| cold_import | 0.0421 | 0.0034 | 8.2 % | 1.364 |
| inspect_schema_100 | 0.0694 | 0.0068 | 9.7 % | 2.038 |
| direct_read_memo_lazy | 0.0198 | 0.0022 | 11.2 % | 1.604 |
| direct_read_cp1250 | 0.0005 | 0.0001 | 23.6 % | 1.765 |

## Hosted-runner global drift (the key measurement)

Comparing every scenario of each run against the slowest run:

| run | median wall ratio | scenarios > 1.02 | scenarios < 0.98 |
|---|---|---|---|
| 33546534607 | 0.674 | 0/23 | 23/23 |
| 33546542658 | 0.692 | 2/23 | 21/23 |
| 33546551328 | 1.000 | 8/23 | 3/23 |
| 33546559054 | 0.999 | 4/23 | 3/23 |

Two of five runner instances were **~30 % faster across all scenarios**
(21-23 of 23 scenarios moved together).  This is runner-wide drift, not a
functional regression — and it proves **absolute wall time can never be a
hard merge gate** on hosted runners.  That is why every absolute wall in
the policy is `advisory_only`.

## Same-run relative ratios (drift-immune)

Generated from the committed policy - do not edit values by hand.

| ratio | center | MAD | rel MAD | envelope upper | envelope/center | classification |
|---|---|---|---|---|---|---|
| memo_lazy_over_inline | 0.4574 | 0.0279 | 6.1 % | 0.5816 | 1.272 | hard_gate |
| memo_skip_over_lazy | 0.7798 | 0.0113 | 1.4 % | 1.7362 | 2.226 | advisory_only |
| migration_validate_on_over_off | 1.1550 | 0.0458 | 4.0 % | 1.4611 | 1.265 | hard_gate |
| projection_selected_over_all | 0.6000 | 0.0054 | 0.9 % | 0.7317 | 1.220 | hard_gate |
| read_1m_over_190k | 5.1993 | 0.0977 | 1.9 % | 6.0915 | 1.172 | hard_gate |

`memo_skip_over_lazy` shows one outlier run (1.510 vs ~0.77) - its
data-derived envelope is therefore non-discriminating (2.23 x center),
so it is honestly classified `advisory_only` instead of inflating
a threshold.


## Derivation algorithm

- `center` = median of the per-run `aggregated.median_wall_seconds`
  (≥ 5 independent hosted-runner workflow runs on one source commit);
- `mad` = median absolute deviation of those run medians;
- `envelope_upper = max(center + max(3 × mad, max_observed_deviation),
  max_observed_value × 1.15)` (covers the observed spread AND a documented
  small-sample safety factor over the worst observation — five runs
  under-estimate the inter-run tail, and a first self-test run on
  identical source indeed landed beyond the too-tight 5-sample envelope,
  which would have been a false positive without this factor);
- a same-run ratio is a **hard regression signal** when the candidate ratio
  exceeds `envelope_upper` on a `COMPARABLE` candidate; a ratio qualifies as
  a hard gate only when `envelope_upper <= center × 1.5`;
- every absolute scenario wall is `advisory_only` (see global drift above).

## Why no absolute wall gate

The calibration shows cross-instance wall drift of −33 % to 0 % on ALL
scenarios including untouched ones (`cold_import`, migration writer paths).
A scenario moving 20 % with its whole profile is runner drift, not a code
regression.  The policy therefore only hard-fails **relative, same-run
ratios**, which measure the relationship inside one measurement and are
immune to that drift (measured ratio dispersion 0.9-6.7 % against 15-33 %
absolute drift).
# dbfbridge documentation

Start here. This is the map of the maintained documentation and what each
document is authoritative for.

## Start here

| Document | Role |
|---|---|
| [README.md](../README.md) | project overview, install profiles, quick starts |
| [pypi-usage.md](pypi-usage.md) | complete installed-distribution user guide |

## Complete Python API examples

[python-api-examples.md](python-api-examples.md) — copy/paste examples for
all nine stable public operations (`inspect_table`, `read_schema`,
`iter_records`, `read_records`, `iter_raw_records`, `export_dbf`,
`reconstruct_dbf`, `verify_conversion`, `check_conversion_quality`), the
install profiles, progress/cancellation, and the JSON-safe boundary.

**USER GUIDE** — describes how the installed package is used.

## Tool-server and MCP integration

[tool-server-integration.md](tool-server-integration.md) — transport-neutral
patterns for MCP servers, JSON-RPC services, agent tools, and job workers:
bounded paging, projection, memo/raw policy, JSON boundary, error mapping,
progress/cancellation, path-security responsibility, offline/vendored
deployment. **INTEGRATION GUIDE** — generic; no specific downstream project
is referenced.

## Stable 1.x API contract

[api-1.0.md](api-1.0.md) — **NORMATIVE API CONTRACT**: import boundary, the
nine stable operations, RawMode contract, the machine-code error vocabulary
and payload families, JSON boundary key policy, SemVer + deprecation policy,
compatibility aliases.

## VFP compatibility

[compatibility-vfp.md](compatibility-vfp.md) — the authoritative per-type
format support truth (`SUPPORTED`, `SUPPORTED_WITH_LIMITATION`, `RAW_ONLY`,
`UNSUPPORTED`, `SYSTEM_INTERNAL`, `PARSER_COMPATIBILITY_ONLY`,
`NOT_YET_VERIFIED`), with exact test evidence per type.

## Migrating from 0.x

[migration-1.0.md](migration-1.0.md) — **USER GUIDE** for moving from an
earlier 0.x release to the declared 1.x API.

## Maintainers and architecture

| Document | Role |
|---|---|
| [architecture-closure.md](architecture-closure.md) | architecture closure matrix, final main lineage, blocker list — **MAINTAINER EVIDENCE** |
| [architecture/phase-0-audit.md](architecture/phase-0-audit.md) | historical Phase 0 technical audit (0.1.0 state) |
| [architecture/phase-1-direct-read.md](architecture/phase-1-direct-read.md) | historical Phase 1A/1B direct read design evidence |
| [architecture/direct-write-next.md](architecture/direct-write-next.md) | **RESEARCH / NOT RELEASED** — next-version Direct Write contract (not part of the stable 1.x API) |
| [architecture/phase-3-performance-baseline.md](architecture/phase-3-performance-baseline.md) | canonical Phase 3 performance baseline history |
| [architecture/phase-3-regression-ci-calibration.md](architecture/phase-3-regression-ci-calibration.md) | regression-policy calibration evidence |
| [benchmarks/README.md](../benchmarks/README.md) | benchmark suite, policy, and baseline history |
| [PUBLISHING.md](../PUBLISHING.md) | release checklist and Trusted Publishing configuration |

Historical architecture documents carry explicit `HISTORICAL PHASE EVIDENCE`
banners; they describe the repository state at their base commit, not the
current one. The current architecture status is
[architecture-closure.md](architecture-closure.md).
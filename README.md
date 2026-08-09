# dbfbridge

**DBF (Visual FoxPro) → CSV / JSON / JSONL / XLSX** exporter with automatic Polish encoding fallback (cp1250 → cp852 → Mazovia).

Lossless, streaming, atomic export of DBF tables (with FPT memo and CDX index files) to modern interchange formats. Designed for migrating legacy FoxPro/Clipper databases to modern systems (Neo4j, PostgreSQL, data warehouses).

> **Status: 0.1.0 (alpha) — export only.** Round-trip import (CSV/JSON/JSONL → DBF) is planned for a later release (see [Roadmap](#roadmap)).

## Features

- **Lossless**: SHA-256 verification, schema preservation, Decimal precision for numbers
- **Streaming & atomic**: handles multi-GB FPT memo files without loading everything into RAM; writes via `.partial` + rename
- **Large-file conversion**: binary JSON streaming, Polars lazy CSV sinks, and constant-memory XLSX writing
- **Excel-safe XLSX**: automatic `Dane_1`, `Dane_2`, ... sheet splitting, formula-safe text, and explicit row/column/cell limits
- **Polish encoding auto-fallback**: detects cp1250 from DBF header, falls back to cp852 → Mazovia when data doesn't match the declared codepage (common in legacy Polish FoxPro/Clipper data)
- **Memo-safe CSV**: memo fields (M) are omitted in CSV (null) to avoid separator/newline issues; full memo content preserved in JSON/JSONL
- **Schema files**: `.schema.jsonl` alongside each output preserves DBF field types, lengths, codepage, version
- **Migration reports**: `migration_report.jsonl` + `.csv` with SHA-256, record counts, null stats, memo hashes
- **Verification tool**: `dbf-bridge-verify` checks completeness, record counts, SHA-256, schema, and syntax

## Install

```bash
pip install dbfbridge
# with XLSX output:
pip install "dbfbridge[xlsx]"
# with test fixtures generator (synthetic DBF for tests/examples):
pip install "dbfbridge[import]"
# full (all optional features + dev tools):
pip install "dbfbridge[import,dev]"
```

## Requirements

- **Python**: 3.10+ (tested on 3.10, 3.11, 3.12, 3.13)
- **`dbfread`** (>=2.0.7) — core dependency for reading DBF files (streaming, low-memory)
- **`dbf`** (>=0.99.11, optional `[import]` extra) — for generating synthetic test DBF fixtures only

### Notes on dependencies

| Package | Version | Last release | Status | Used for |
|---------|---------|-------------|--------|----------|
| `dbfread` | 2.0.7 | 2016-11-25 | Stable, no longer actively developed (40 open issues on GitHub, last push 2024) | **Reading** DBF (streaming, FPT memo, codepage detection) |
| `orjson` | 3.10+ | active | Maintained | Per-record JSONL validation and parsing |
| `polars` | 1.0+ | active | Maintained | Lazy/streaming JSONL → CSV |
| `xlsxwriter` | 3.2+ | active | Maintained | Constant-memory JSONL → XLSX (`[xlsx]`) |
| `dbf` | 0.99.11 | 2025-09-02 | Actively maintained by Ethan Furman (supports Python 3.10-3.13) | **Generating** synthetic DBF fixtures (`[import]` extra) |

`dbfread` is stable and battle-tested but hasn't had a release since 2016. It remains the best choice for streaming DBF reads (low memory, large FPT files). `dbfbridge` extends it with:
- Automatic Polish encoding fallback (Mazovia/cp852) via a custom `FieldParser`
- Memo field policies (skip/inline/null)
- Atomic, streaming output with SHA-256 validation

If `dbfread` becomes unmaintained or incompatible with future Python versions, the reader layer (`dbf_bridge/exporter/reader.py`) can be replaced with an alternative (e.g., `dbf` or a custom parser) without changing the public API.

## Quick start

```bash
# Export all DBF files in a directory tree to CSV + JSON + JSONL
dbf-bridge --source <DBF_DIR> --output <OUT_DIR>

# Verify the conversion
dbf-bridge-verify --source <DBF_DIR> --output <OUT_DIR>
```

### Python API

```python
from dbf_bridge.exporter.config import make_config
from dbf_bridge.exporter.discovery import discover_tables
from dbf_bridge.exporter.writer import export_table
from dbf_bridge.exporter.reporting import write_reports

config = make_config(
    source="<DBF_DIR>",
    output="<OUT_DIR>",
    export_format="jsonl",
    memo="inline",
    overwrite=True,
)
results = []
for table in discover_tables(config.source):
    result = export_table(table, config)
    print(result.table, result.status)
    results.append(result)

write_reports(config.output, results)
```

## CLI reference

### `dbf-bridge` — export

```
dbf-bridge --source <DBF_DIR> --output <OUT_DIR> [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--source` | required | Source directory **or single DBF file** |
| `--output` | required | Output directory |
| `--formats` | `jsonl` | Comma-separated list of `csv,json,jsonl,xlsx` |
| `--memo` | per-format | `skip` (null), `inline` (full text), `null` |
| `--encoding` | `auto` | DBF codepage or `auto` (detect from header) |
| `--decode-errors` | `strict` | `strict`, `ignore`, `replace` |
| `--deleted` | `skip` | `skip`, `separate`, `include` deleted records |
| `--missing-memo` | `fail` | `fail`, `null-with-warning` |
| `--strip-spaces` / `--no-strip-spaces` | off | Trim trailing spaces from Character (C) fields |
| `--overwrite` / `--no-overwrite` | on | Overwrite existing output files |
| `--no-validate` | off | Skip SHA-256 round-trip validation |

### `dbf-bridge-verify` — verify

```
dbf-bridge-verify --source <DBF_DIR> --output <OUT_DIR> [options]
```

Checks: file completeness, record counts, SHA-256, schema, syntax, FPT/CDX presence.

Exit codes: `0` = OK, `1` = errors, `2` = warnings (with `--strict`).

## Output formats

| Format | Memo | Structure | Use case |
|--------|------|-----------|----------|
| **CSV** | null (skip) | RFC-compatible quoting, configurable converter separator | Excel, Power Query, BI tools |
| **JSON** | inline | Single JSON array | Archival, small tables |
| **JSONL** | inline | 1 line = 1 JSON object | Streaming, round-trip, Neo4j import |
| **XLSX** | inline | Constant-memory sheets, 1 row = 1 record | Excel |

Each output file has a companion `.schema.jsonl` with DBF metadata (field types, lengths, codepage, version).

## Polish encoding fallback

Legacy Polish FoxPro/Clipper databases often declare cp1250 in the DBF header but contain data encoded in **Mazovia** (a Polish OEM codepage from the DOS era). `dbfbridge` automatically detects decode failures and falls back through:

```
cp1250 (declared) → cp852 (DOS Latin-2) → Mazovia (Polish OEM)
```

This is transparent — no user configuration needed. See `dbf_bridge/exporter/polish_codecs.py` for the Mazovia table implementation.

## Roadmap

The following features are planned for future releases (not yet implemented):

- **`0.2.0`** — Round-trip import: CSV/JSON/JSONL → DBF (with FPT memo creation), via the `dbf` library (Ethan Furman). Extras `[import]` will be required.
- **`0.x`** — Higher-level Python API (`from dbf_bridge import convert, verify`).

## License

MIT

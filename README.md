# dbfbridge

Bidirectional **DBF (Visual FoxPro) ↔ CSV / JSON / JSONL / XLSX** converter with automatic Polish encoding fallback (cp1250 → cp852 → Mazovia).

Lossless, streaming, atomic export of DBF tables (with FPT memo and CDX index files) to modern interchange formats — and back. Designed for migrating legacy FoxPro/Clipper databases to modern systems (Neo4j, PostgreSQL, data warehouses).

## Features

- **Bidirectional**: DBF → CSV/JSON/JSONL/XLSX and back (round-trip)
- **Lossless**: SHA-256 verification, schema preservation, Decimal precision for numbers
- **Streaming & atomic**: handles multi-GB FPT memo files without loading everything into RAM; writes via `.partial` + rename
- **Polish encoding auto-fallback**: detects cp1250 from DBF header, falls back to cp852 → Mazovia when data doesn't match the declared codepage (common in legacy Polish FoxPro/Clipper data)
- **Memo-safe CSV**: memo fields (M) are omitted in CSV (null) to avoid separator/newline issues; full memo content preserved in JSON/JSONL
- **Schema files**: `.schema.jsonl` alongside each output preserves DBF field types, lengths, codepage, version
- **Migration reports**: `migration_report.jsonl` + `.csv` with SHA-256, record counts, null stats, memo hashes
- **Verification tool**: `dbf-bridge-verify` checks completeness, record counts, SHA-256, schema, and syntax

## Install

```bash
pip install dbfbridge
# with XLSX support:
pip install "dbfbridge[xlsx]"
```

## Quick start

```bash
# Export all DBF files in a directory tree to CSV + JSON + JSONL
dbf-bridge --source "K:\Logis" --output "K:\Logis_out"

# Verify the conversion
dbf-bridge-verify --source "K:\Logis" --output "K:\Logis_out"
```

### Python API

```python
from dbf_bridge.exporter.config import make_config
from dbf_bridge.exporter.discovery import discover_tables
from dbf_bridge.exporter.writer import export_table
from dbf_bridge.exporter.reporting import write_reports

config = make_config(
    source="K:/Logis",
    output="K:/Logis_out",
    export_format="jsonl",
    memo="inline",
    overwrite=True,
)
for table in discover_tables(config.source):
    result = export_table(table, config)
    print(result.table, result.status)

write_reports(config.output, results)
```

## CLI reference

### `dbf-bridge` — export

```
dbf-bridge --source <DBF_DIR> --output <OUT_DIR> [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--source` | `tests/fixtures/synthetic_data/input` | Source directory with DBF files |
| `--output` | `tests/fixtures/synthetic_data/output` | Output directory |
| `--formats` | `csv,json,jsonl` | Comma-separated list of formats |
| `--memo` | per-format | `skip` (null), `inline` (full text), `null` |
| `--encoding` | `auto` | DBF codepage or `auto` (detect from header) |
| `--decode-errors` | `strict` | `strict`, `ignore`, `replace` |
| `--deleted` | `skip` | `skip`, `separate`, `include` deleted records |
| `--missing-memo` | `fail` | `fail`, `null-with-warning` |
| `--overwrite` | `True` | Overwrite existing output files |
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
| **CSV** | null (skip) | 1 row = 1 record, JSON-quoted cells | Excel, Power Query, BI tools |
| **JSON** | inline | Single JSON array | Archival, small tables |
| **JSONL** | inline | 1 line = 1 JSON object | Streaming, round-trip, Neo4j import |

Each output file has a companion `.schema.jsonl` with DBF metadata (field types, lengths, codepage, version).

## Polish encoding fallback

Legacy Polish FoxPro/Clipper databases often declare cp1250 in the DBF header but contain data encoded in **Mazovia** (a Polish OEM codepage from the DOS era). `dbfbridge` automatically detects decode failures and falls back through:

```
cp1250 (declared) → cp852 (DOS Latin-2) → Mazovia (Polish OEM)
```

This is transparent — no user configuration needed. See `dbf_bridge/exporter/polish_codecs.py` for the Mazovia table implementation.

## License

MIT
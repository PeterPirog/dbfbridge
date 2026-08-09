# dbfbridge

**DBF (Visual FoxPro) ↔ CSV / JSON / JSONL / XLSX** migration toolkit with automatic Polish encoding fallback (cp1250 → cp852 → Mazovia).

Lossless, streaming, atomic export of DBF tables (with FPT memo and CDX index files) to modern interchange formats. Designed for migrating legacy FoxPro/Clipper databases to modern systems (Neo4j, PostgreSQL, data warehouses).

> **Status: 0.1.0 (alpha).** Export, schema-driven DBF/FPT reconstruction, and diagnostic round-trip verification are available.

## Features

- **Lossless**: SHA-256 verification, schema preservation, Decimal precision for numbers
- **Streaming & atomic**: handles multi-GB FPT memo files without loading everything into RAM; writes via `.partial` + rename
- **Large-file conversion**: binary JSON streaming, Polars lazy CSV sinks, and constant-memory XLSX writing
- **Excel-safe XLSX**: automatic `Dane_1`, `Dane_2`, ... sheet splitting, formula-safe text, and lossless overflow sheets for values longer than Excel's cell limit
- **Polish encoding auto-fallback**: detects cp1250 from DBF header, falls back to cp852 → Mazovia when data doesn't match the declared codepage (common in legacy Polish FoxPro/Clipper data)
- **Memo-safe CSV**: memo fields (M) are omitted in CSV (null) to avoid separator/newline issues; full memo content preserved in JSON/JSONL
- **Schema files**: `<table>_schema.json` preserves exact DBF field descriptors, header/codepage details, and FPT memo reconstruction parameters
- **Migration reports**: `migration_report.jsonl` + `.csv` with per-format status,
  SHA-256, record counts, schema hashes, XLSX data/overflow sheet counts, failures, and run configuration
- **Verification tool**: `dbf-bridge-verify` checks completeness, record counts, SHA-256, schema, and syntax
- **DBF/FPT reconstruction**: one selected JSONL, JSON, CSV, or XLSX tree can be rebuilt using companion schemas
- **Quality diagnostics**: DBF → JSONL → DBF checks raw and canonical SHA-256 and identifies differing fields or binary offsets

## Install

```bash
pip install dbfbridge
# XLSX output is included by default (the legacy [xlsx] extra remains accepted)
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
| `xlsxwriter` | 3.2+ | active | Maintained | Constant-memory JSONL → XLSX (installed by default) |
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

# Reconstruct the same directory tree from exactly one format
dbf-bridge-import --source <OUT_DIR> --output <REBUILT_DIR> \
  --formats jsonl --memo inline --overwrite --progress

# Run a retained, diagnostic DBF -> JSONL -> DBF round-trip
dbf-bridge-quality --source <DBF_DIR> --output <QUALITY_DIR> --overwrite --progress
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
| `--xlsx-long-text` | `overflow` | `overflow` preserves long values in `Dlugie_teksty_*`; `error` rejects them |

### `dbf-bridge-verify` — verify

```
dbf-bridge-verify --source <DBF_DIR> --output <OUT_DIR> [options]
```

Checks: file completeness, record counts, SHA-256, schema, syntax, FPT/CDX presence,
and row counts across all `Dane_*` worksheets in XLSX files.

Exit codes: `0` = OK, `1` = errors, `2` = warnings (with `--strict`).

### `dbf-bridge-import` — reconstruct DBF/FPT

`--formats` must contain exactly one of `jsonl`, `json`, `csv`, or `xlsx`.
For each data file the importer requires a sibling `<table>_schema.json`, preserves
the relative path and original DBF/FPT filename casing, writes atomically, and creates
`reconstruction_report.jsonl`. The report contains:

- schema SHA-256;
- schema-aware canonical SHA-256 before and after reconstruction;
- reconstructed DBF/FPT SHA-256;
- original raw SHA-256 recorded by newer schemas and raw match flags;
- record/deleted-record counts, warnings, errors, and bounded field-level differences.

The canonical checksum normalizes values according to DBF type, length, precision,
field order, flags, and deleted status. It therefore detects data or structure loss
without treating harmless JSON whitespace as a difference.

For an unchanged JSON/JSONL round-trip, current schemas retain the complete DBF header
region and the exporter records otherwise ambiguous binary memo and fallback-codepage
bytes. JSON/JSONL also carries a reserved `__dbfbridge_raw_record__` base64 value;
the importer uses it only after logical reconstruction, relocates generated memo blocks
to their original pointers, and then verifies the result. This allows `raw_dbf_match`
(and, where unused/orphan FPT regions are reproducible,
`raw_fpt_match`) to prove byte identity. A checksum can verify identity but cannot
recreate missing bytes: schemas generated by older versions, which have a null source
SHA-256 or only a 32-byte header snapshot, must be regenerated from the original DBF.
Use `--deleted include` during export when byte identity is required; it retains deleted
records and their original physical order.

`raw_fpt_match: false` with `canonical_match: true` can remain valid for an old FPT that
contains unreferenced/orphan blocks. Such bytes are not part of any exported memo value;
the reconstructed FPT remains readable, while exact archival preservation of those
unreferenced bytes would require copying the original FPT itself.

### `dbf-bridge-quality` — diagnostic round-trip

This command retains three trees under the selected output directory:

1. `01_forward_jsonl` — source DBF converted to JSONL and schemas;
2. `02_reconstructed_dbf` — reconstructed DBF/FPT;
3. `03_reexported_jsonl` — reconstructed DBF exported again for field comparison.

`conversion_quality_report.jsonl` reports raw DBF/FPT checksums, canonical checksums,
the first differing record fields (with bounded previews and value hashes), and the
first differing binary offsets categorized as header, descriptor, record, or FPT areas.
CDX files cannot be reconstructed because a DBF schema does not contain index tag
expressions. This is reported separately; the structural-index header flag is preserved
for DBF byte identity, but the missing companion CDX must be rebuilt in Visual FoxPro.

## Output formats

| Format | Memo | Structure | Use case |
|--------|------|-----------|----------|
| **CSV** | null (skip) | RFC-compatible quoting, configurable converter separator | Excel, Power Query, BI tools |
| **JSON** | inline | Single JSON array | Archival, small tables |
| **JSONL** | inline | 1 line = 1 JSON object | Streaming, round-trip, Neo4j import |
| **XLSX** | inline | Constant-memory sheets, 1 row = 1 record; long values use lossless overflow rows | Excel |

Excel cells are limited to 32,767 UTF-16 code units. When an inline memo exceeds
that limit, its data cell contains `[[DBFBRIDGE_OVERFLOW:<id>]]`, while the complete
value is split into ordered rows in `Dlugie_teksty_1`, `Dlugie_teksty_2`, ... . Each
overflow row records the data sheet, Excel row, JSONL source line, DBF column,
value type, part number, total parts, and text chunk. Concatenating `text` in `part`
order for an `overflow_id` recreates the original value exactly.

Each source table has a companion `<table>_schema.json`. It records DBF field order,
type, length, decimal count, address and flags; DBF/VFP header and codepage details;
and FPT block size, pointer layout, memo types, encoding, and export policy. These are
the structural details needed to recreate fields and memo storage in Visual FoxPro 9 SP2.

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

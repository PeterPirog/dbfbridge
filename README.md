# dbfbridge

`dbfbridge` is a migration toolkit for Visual FoxPro DBF/FPT data:

- export DBF directory trees to CSV, JSON, JSONL, and XLSX;
- reconstruct DBF/FPT directory trees from one exported format and companion schemas;
- verify exported files and run diagnostic DBF → JSONL → DBF round trips;
- preserve Polish legacy text with cp1250 → cp852 → Mazovia fallback;
- expose the same operations as a typed Python API through `from dbfbridge import ...`.

> Status: **0.1.0 (alpha)**. Test the result on a copy of production data before using it as an archival replacement. CDX index definitions are not reconstructed.

## Installation

Python 3.10 or newer is required.

```bash
python -m pip install dbfbridge
```

A standard installation includes DBF reading and reconstruction, JSON/CSV conversion,
and XLSX reading/writing. The historical `import` and `xlsx` extras remain accepted as
compatibility no-ops. For development tools use:

```bash
python -m pip install -e ".[dev]"
```

The main runtime dependencies are:

| Package | Purpose |
|---|---|
| `dbfread` | streaming DBF/FPT reading |
| `dbf` | schema-driven DBF/FPT writing |
| `orjson` | JSONL parsing and validation |
| `polars` | streaming JSONL → CSV conversion |
| `xlsxwriter` | constant-memory XLSX writing |
| `openpyxl` | read-only XLSX reconstruction |

## Quick start

Installation provides four commands: `dbf-bridge`, `dbf-bridge-verify`,
`dbf-bridge-import`, and `dbf-bridge-quality`.

```bash
# Default export format is JSONL
dbf-bridge --source <DBF_DIR> --output <OUT_DIR>

# Request several formats explicitly
dbf-bridge --source <DBF_DIR> --output <OUT_DIR> \
  --formats csv,json,jsonl,xlsx --memo inline --overwrite --progress

# On later runs, convert only new, changed, missing, or damaged tables
dbf-bridge --source <DBF_DIR> --output <OUT_DIR> \
  --formats csv,json,jsonl,xlsx --incremental

# Verify exported formats against their DBF sources and migration report
dbf-bridge-verify --source <DBF_DIR> --output <OUT_DIR> \
  --formats csv,json,jsonl,xlsx

# Reconstruct from exactly one format
dbf-bridge-import --source <OUT_DIR> --output <REBUILT_DIR> \
  --formats jsonl --memo inline --overwrite --progress

# Retain a diagnostic DBF → JSONL → DBF round trip
dbf-bridge-quality --source <DBF_DIR> --output <QUALITY_DIR> \
  --overwrite --progress
```

The equivalent Python call is silent and returns a structured result:

```python
from dbfbridge import export_dbf

run = export_dbf(
    "<DBF_DIR>",
    "<OUT_DIR>",
    formats=("csv", "json", "jsonl", "xlsx"),
    memo="inline",
    incremental=True,
)

print(run.ok, run.skipped, run.failed)
run.raise_for_errors()
```

Windows PowerShell examples are available in the
[examples guide](https://github.com/PeterPirog/dbfbridge/blob/main/examples/README.md).

### Choose the right interface and format

| Need | Recommended choice | Why |
|---|---|---|
| one-off migration or PowerShell job | CLI commands | direct progress and process exit codes |
| integration with an application, GUI, or worker | Python API | typed results and structured progress callbacks |
| large, loss-aware export or later DBF reconstruction | JSONL | streaming, inline memo support, and raw-record metadata |
| exchange with spreadsheet users | XLSX | readable workbooks and lossless overflow sheets for long text |
| simple tabular integration | CSV | broad compatibility; memo is skipped unless requested |
| compact JSON for a smaller table | JSON | one conventional JSON array, but not streaming for consumers |

JSONL is the safest default for migration and reconstruction. CSV and XLSX are useful
exchange formats, but they cannot retain every DBF-specific binary detail.

## Export

```text
dbf-bridge --source <DBF_DIR_OR_FILE> --output <OUT_DIR> [options]
```

| Option | Default | Description |
|---|---|---|
| `--source` | required | source directory or one DBF file |
| `--output` | required | output directory; it cannot be inside the source tree |
| `--formats` | `jsonl` | comma-separated `csv,json,jsonl,xlsx` |
| `--memo` | per format | `skip`, `inline`, or `null`; CSV defaults to `skip`, other formats to `inline` |
| `--encoding` | `auto` | DBF codepage or automatic header detection |
| `--decode-errors` | `strict` | `strict`, `ignore`, or `replace` |
| `--deleted` | `skip` | `skip`, `separate`, or `include` deleted records |
| `--missing-memo` | `fail` | `fail` or `null-with-warning` |
| `--strip-spaces` | off | trim trailing spaces in Character fields |
| `--overwrite` | on | overwrite existing outputs; use `--no-overwrite` to disable |
| `--progress` | on | display per-table progress; use `--no-progress` to disable |
| `--no-validate` | off | skip output SHA-256 and parse validation |
| `--xlsx-long-text` | `overflow` | preserve long values in overflow sheets or fail with `error` |
| `--incremental` | off | reuse verified results recorded in `conversion_checksums.json` |

The output directory preserves the source directory tree. Each table can produce:

| File | Contents |
|---|---|
| `<table>.jsonl`, `.json`, `.csv`, `.xlsx` | requested data formats |
| `<table>_schema.json` | DBF fields, exact descriptors/header metadata, codepage and FPT reconstruction data |
| `migration_report.jsonl` / `.csv` | run summary, status, counts, hashes, warnings, errors and converter statistics |
| `conversion_checksums.json` | atomic manifest used by incremental export |

Every successful run writes the checksum manifest, even without `--incremental`.
An incremental run skips a table only when its DBF/FPT/CDX fingerprint, export settings,
schema, and all requested outputs still match. Removed source tables are removed from the
new manifest, but their old output files are deliberately not deleted.

### Format behavior

| Format | Memo default | Notes |
|---|---|---|
| CSV | `skip` | memo values are null by default because embedded newlines complicate simple consumers |
| JSON | `inline` | one JSON array; suitable for smaller tables |
| JSONL | `inline` | one object per line; preferred for streaming and reconstruction |
| XLSX | `inline` | constant-memory `Dane_*` sheets with lossless `Dlugie_teksty_*` overflow sheets |

Excel limits a cell to 32,767 UTF-16 code units. With the default
`--xlsx-long-text overflow`, a marker is stored in the data cell and the complete value
is split into ordered overflow rows. The importer joins those rows during reconstruction.

## Reconstruction

```text
dbf-bridge-import --source <EXPORT_DIR> --output <DBF_DIR> \
  --formats {jsonl,json,csv,xlsx} [options]
```

`--formats` must select exactly one format. Every input file needs its sibling
`<table>_schema.json`; the importer preserves relative directories and original DBF/FPT
filename casing. `--memo` accepts `inline` (default) or `null`, `--overwrite` is off by
default, and progress is on by default.

The generated `reconstruction_report.jsonl` contains canonical and raw checksums,
record counts, warnings, errors, and bounded field-level differences. Canonical hashes
compare values using DBF type, length, decimal precision, field order, flags, and deleted
status. Raw hashes compare complete file bytes.

For the best chance of byte-identical JSON/JSONL reconstruction:

- generate fresh schemas with the current exporter;
- export with `--deleted include` to retain deleted rows and physical record order;
- keep the reserved `__dbfbridge_raw_record__` property in JSON/JSONL unchanged;
- use `--memo inline`.

`raw_dbf_match: true` or `raw_fpt_match: true` proves byte identity. A false raw FPT
match with a true canonical match may be caused by unreferenced/orphan blocks in the old
FPT; exported memo values cannot recreate bytes that no record references. CSV and XLSX
are interchange formats and generally cannot preserve every raw DBF byte.

CDX files are not reconstructed because DBF field metadata does not contain index tag
names and expressions. The DBF structural-index flag is preserved when possible, but the
companion index must be rebuilt in Visual FoxPro or another index-aware tool.

## Verification and diagnostics

`dbf-bridge-verify` checks output presence, row counts, syntax, schema consistency, and
SHA-256 values from `migration_report.jsonl`. It writes
`<OUT_DIR>/verification_report.json` unless `--report` specifies another path. Exit code
`0` means success, `1` means an error, and `2` means warnings in strict mode; pass
`--no-strict` if warnings should not affect the exit code.

`dbf-bridge-quality` writes three retained artifact trees:

1. `01_forward_jsonl` — source DBF exported with inline memo and deleted rows included;
2. `02_reconstructed_dbf` — reconstructed DBF/FPT;
3. `03_reexported_jsonl` — reconstructed data exported again.

Its `conversion_quality_report.jsonl` records canonical/raw matches, differing fields,
first differing binary offsets, and probable causes. Exit codes are `0` for all OK, `1`
for failures, and `2` for warnings.

## Python API

The installed distribution is named `dbfbridge`, and the recommended import is also
`dbfbridge` (without an underscore). The historical internal package name
`dbf_bridge` exports the same public symbols for compatibility.

### Operations

| Function | Result type | Purpose |
|---|---|---|
| `inspect_table()` | `TableInfo` | read-only inspection of one DBF header (no files created) |
| `read_schema()` | `TableSchema` | full safe header/memo/CDX-companion schema (no files created) |
| `export_dbf()` | `ExportRunResult` | DBF/FPT tree → one or more modern formats |
| `reconstruct_dbf()` | `ReconstructionRunResult` | one exported format + schemas → DBF/FPT tree |
| `verify_conversion()` | `VerificationRunResult` | exported files vs source DBF and migration report |
| `check_conversion_quality()` | `QualityRunResult` | retained DBF → JSONL → DBF diagnostics |

Functions accept `str`, `pathlib.Path`, or another `os.PathLike`. They do not print by
default. A completed operation returns table-level objects, aggregate counters, report
paths, and the CLI-compatible `exit_code` (`0` OK, `1` error, `2` warning). Per-table
failures are data, not immediate exceptions, so an application can inspect every table.
Call `result.raise_for_errors()` after the run to turn failures into a
`DBFBridgeRunError`. Warnings do not raise; inspect `exit_code`, `successful`, and the
table results when warnings must also block the calling application.

### API option reference

The high-level functions use the same behavior as their CLI counterparts, with no
console output unless a progress callback is supplied.

| `export_dbf()` keyword | Default | Accepted values / behavior |
|---|---|---|
| `formats` | `("jsonl",)` | iterable or comma-separated `csv,json,jsonl,xlsx` |
| `memo` | per format | `skip`, `inline`, `null`, or `None` for format default |
| `strip_spaces` | `False` | trim trailing spaces in Character fields |
| `encoding` | `"auto"` | DBF codepage name or automatic header detection |
| `decode_errors` | `"strict"` | `strict`, `ignore`, or `replace` |
| `deleted` | `"skip"` | `skip`, `separate`, or `include` |
| `missing_memo` | `"fail"` | `fail` or `null-with-warning` |
| `overwrite` / `validate` | `True` / `True` | replace outputs; validate hashes and syntax |
| `xlsx_long_text` | `"overflow"` | `overflow` or `error` |
| `incremental` | `False` | reuse only fully verified results from the manifest |
| `progress` | `None` | callback receiving `ProgressEvent` |
| `options` | `None` | reusable `ExportOptions`; do not combine with option keywords |

| Operation | Important defaults and controls |
|---|---|
| `reconstruct_dbf()` | `input_format="jsonl"`, `memo="inline"`, `overwrite=False`; also accepts `ReconstructionOptions` |
| `verify_conversion()` | all four formats, `strict=True`, writes `<output>/verification_report.json`; set `write_report=False` for an in-memory check |
| `check_conversion_quality()` | `overwrite=False`, `max_differences=20`; retains all three diagnostic trees |

#### Direct read (Phase 1A)

`inspect_table()` and `read_schema()` implement the Phase 1A direct read core.
They are strictly read-only: they parse the DBF header only (no record
iteration), never create files, never open memo payloads, and leave the source
byte-identical.

```python
from dbfbridge import FieldInfo, TableInfo, TableSchema, inspect_table, read_schema

info: TableInfo = inspect_table("K:/dbf_source/klienci.dbf")
print(info.record_count, info.encoding, info.has_memo, info.dbc_bound)
for field in info.fields:
    print(field.ordinal, field.name, field.dbf_type, field.dbf_type_name)

schema: TableSchema = read_schema("K:/dbf_source/klienci.dbf")
print(schema.dbversion_name, schema.last_update)
print(schema.memo_companion_present, schema.companion_cdx_present)
print(json.dumps(info.to_dict()))  # JSON-safe: no bytes, no Path
```

Structured failures carry a machine code instead of free text:

```python
from dbfbridge import (
    DbfFormatUnsupportedError,
    DbfHeaderInvalidError,
    DbfPathError,
    DbfTruncatedError,
    DirectReadError,
    EncodingUnknownError,
    ErrorCode,
)

try:
    inspect_table("K:/damaged/dane.dbf")
except DirectReadError as error:
    print(error.code)   # e.g. ErrorCode.DBF_TRUNCATED
    print(error.to_dict())  # JSON-safe: code, message, path, context
```

Phase 1A scope notes:

- record reading (`iter_records`/`read_records`), field projection, and lazy
  memo reading are the next step and are **not** implemented yet;
- the benchmark scenarios `direct_read_bounded`, `field_projection`,
  `memo_lazy`, and `raw_mode_none` therefore remain `NOT_IMPLEMENTED` in the
  Phase 0 baseline;
- CDX presence is reported structurally (`has_structural_cdx`,
  `companion_cdx`); CDX tag expressions are **not** parsed;
- `dbc_bound` comes from the VFP database-container backlink stored in the
  header, not from the mere existence of a `.dbc` file;
- the Phase 0 benchmark results remain the BEFORE reference and are not
  regenerated by this phase.

A complete executable example is in
[`examples/inspect_table.py`](examples/inspect_table.py).

#### Export and incremental export

```python
from dbfbridge import ExportOptions, export_dbf

options = ExportOptions(
    formats=("csv", "json", "jsonl", "xlsx"),
    memo="inline",
    deleted="include",
    overwrite=True,
    incremental=True,
)
run = export_dbf("K:/dbf_source", "K:/dbf_output", options=options)

for table in run.results:
    print(table.table, table.format, table.status, table.sha256)

print(run.migration_report_jsonl)
print(run.checksum_manifest)
run.raise_for_errors()
```

Every `ExportOptions` field is also available directly as a keyword of `export_dbf()`.
Use either the reusable options object or individual option keywords in one call.

#### Structured progress

```python
from dbfbridge import ProgressEvent, export_dbf

def show_progress(event: ProgressEvent) -> None:
    print(event.operation, event.current, event.total, event.table, event.records)

run = export_dbf(
    "K:/dbf_source",
    "K:/dbf_output",
    formats="jsonl,xlsx",
    progress=show_progress,
)
```

The callback receives `ProgressEvent` objects and is independent of CLI output. This
makes it suitable for GUI progress bars, web jobs, queues, logs, or monitoring systems.

#### Reconstruction, verification, and quality

```python
from dbfbridge import (
    check_conversion_quality,
    reconstruct_dbf,
    verify_conversion,
)

reconstruction = reconstruct_dbf(
    "K:/dbf_output",
    "K:/dbf_reconstructed",
    input_format="jsonl",
    memo="inline",
    overwrite=True,
)

verification = verify_conversion(
    "K:/dbf_source",
    "K:/dbf_output",
    formats=("csv", "json", "jsonl", "xlsx"),
)

quality = check_conversion_quality(
    "K:/dbf_source",
    "K:/dbf_quality",
    overwrite=True,
    max_differences=20,
)

for result in (reconstruction, verification, quality):
    print(type(result).__name__, result.exit_code, result.report_path)
    result.raise_for_errors()
```

Invalid global arguments, unsafe paths, and missing source directories raise standard
`ValueError` or `FileNotFoundError` immediately. `DBFBridgeRunError` is raised only by
`raise_for_errors()` and keeps the complete run object in its `result` attribute.

The lower-level modules under `dbf_bridge.exporter` and `dbf_bridge.importer` remain
available for custom pipelines, but the functions above are the supported high-level API.
A complete executable example is in
[`examples/python_api.py`](https://github.com/PeterPirog/dbfbridge/blob/main/examples/python_api.py).

## Development

```bash
python -m pip install -e ".[dev]"
pytest
ruff check src tests benchmarks examples
python -m build
twine check dist/*
```

Continuous integration runs linting and the test suite on Python 3.10–3.13 on Linux,
plus Python 3.12 on Windows. Release archives are built separately and published through
PyPI Trusted Publishing; no long-lived PyPI token is stored in the repository. The exact
versioning, publisher configuration, release, and post-publication checks are documented
in [PUBLISHING.md](PUBLISHING.md).

Tests generate their deterministic DBF/FPT fixtures automatically. To create a reusable
fixture tree manually:

```bash
python tests/fixtures/generate_sample_dbf.py
python tests/fixtures/generate_sample_dbf.py --output <FIXTURE_DIR>
```

Synthetic benchmark instructions and their environment-specific sample results are in
the [benchmark guide](https://github.com/PeterPirog/dbfbridge/blob/main/benchmarks/README.md).

## Troubleshooting and issue reports

| Symptom | What to check |
|---|---|
| missing memo/FPT error | keep the sibling `.FPT`, or deliberately use `--missing-memo null-with-warning` |
| exit code `2` | the operation completed with warnings; inspect its report before accepting the result |
| CDX warning | rebuild the index in Visual FoxPro; the exported data is not an index definition |
| raw hash differs but canonical hash matches | values and schema match, but unused bytes, memo block layout, or metadata differ |
| incremental table is converted again | its source fingerprint, settings, schema, manifest entry, or an output hash changed |
| an existing output blocks reconstruction | pass `--overwrite` only after confirming the destination may be replaced |

When reporting a reproducible defect, open a
[GitHub issue](https://github.com/PeterPirog/dbfbridge/issues) and include the command or
API call, Python and operating-system versions, the relevant migration/reconstruction/
quality report entry, and the generated schema. Do not attach production records or memo
contents; reduce the problem to synthetic data whenever possible.

## Known limitations and roadmap

- CDX tag definitions are not reconstructed.
- Exact raw FPT reconstruction is not possible for unreferenced source blocks.
- Future high-level operations will follow the existing function + typed-result model.

## License

[MIT](https://github.com/PeterPirog/dbfbridge/blob/main/LICENSE)

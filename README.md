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
| `export_dbf()` | `ExportRunResult` | DBF/FPT tree → one or more modern formats |
| `reconstruct_dbf()` | `ReconstructionRunResult` | one exported format + schemas → DBF/FPT tree |
| `verify_conversion()` | `VerificationRunResult` | exported files vs source DBF and migration report |
| `check_conversion_quality()` | `QualityRunResult` | retained DBF → JSONL → DBF diagnostics |

Functions accept `str`, `pathlib.Path`, or another `os.PathLike`. They do not print by
default. A completed operation returns table-level objects, aggregate counters, report
paths, and the CLI-compatible `exit_code` (`0` OK, `1` error, `2` warning). Per-table
failures are data, not immediate exceptions, so an application can inspect every table.
Call `result.raise_for_errors()` when fail-fast behavior is preferred.

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

## Known limitations and roadmap

- CDX tag definitions are not reconstructed.
- Exact raw FPT reconstruction is not possible for unreferenced source blocks.
- Future high-level operations will follow the existing function + typed-result model.

## License

[MIT](https://github.com/PeterPirog/dbfbridge/blob/main/LICENSE)

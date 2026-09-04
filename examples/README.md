# Examples

## A. Usage after installing from PyPI (the normal case)

After installing:

```bash
python -m pip install dbfbridge
```

you do not need this repository. You use the installed commands
(`dbf-bridge`, `dbf-bridge-verify`, `dbf-bridge-import`, `dbf-bridge-quality`)
and the public API `from dbfbridge import ...`. The complete guide is
[docs/pypi-usage.md](../docs/pypi-usage.md); complete Python examples for all
nine public operations are in
[docs/python-api-examples.md](../docs/python-api-examples.md). The scripts in
this directory are **repository examples** — the installed package works
without them and without a `src` directory.

## B. Repository / development examples

The scripts in this directory run the same interfaces that are available
after installation as `dbf-bridge`, `dbf-bridge-verify`, `dbf-bridge-import`,
and `dbf-bridge-quality`. They insert the local `src` directory on
`sys.path`, so they can also be used before installing the package — that is
a convenient development helper while working on the repository code, not the
normal installation path for a user.

| Script | Installed equivalent | Purpose |
|---|---|---|
| `export_dbf.py` | `dbf-bridge` | DBF → CSV/JSON/JSONL/XLSX |
| `verify_dbf.py` | `dbf-bridge-verify` | export file verification |
| `export_from_file_to_dbf.py` | `dbf-bridge-import` | DBF/FPT reconstruction from one format |
| `check_conversion_quality.py` | `dbf-bridge-quality` | diagnostic DBF → JSONL → DBF round trip |
| `python_api.py` | public API | complete flow through `from dbfbridge import ...` |
| `inspect_table.py` | public API (historical: Phase 1A) | read-only header and schema inspection |
| `read_records.py` | public API (historical: Phase 1B) | streaming record read (projection, memo policies, raw) |
| `direct_copy.py` | RESEARCH API (`write_table`, next version — not released) | streaming DBF/FPT copy without any intermediate JSONL |

## Running in PowerShell

Export requires an explicit source and output directory:

```powershell
python examples/export_dbf.py --source "K:\dbf_source" `
  --output "K:\dbf_output" --overwrite --progress `
  --memo inline --formats csv,json,jsonl,xlsx
```

On a later run the `--incremental` option checks
`conversion_checksums.json` and converts only new, changed, missing, or
damaged tables:

```powershell
python examples/export_dbf.py --source "K:\dbf_source" `
  --output "K:\dbf_output" --formats csv,json,jsonl,xlsx --incremental
```

Verification of the requested formats:

```powershell
python examples/verify_dbf.py --source "K:\dbf_source" `
  --output "K:\dbf_output" --formats csv,json,jsonl,xlsx
```

Reconstruction of a tree from exactly one format:

```powershell
python examples/export_from_file_to_dbf.py --source "K:\dbf_output" `
  --output "K:\dbf_output_reconstructed" --formats jsonl `
  --memo inline --overwrite --progress
```

Full quality round trip:

```powershell
python examples/check_conversion_quality.py --source "K:\dbf_source" `
  --output "K:\dbf_quality" --overwrite --progress
```

Inspection of a single table (header/schema only):

```powershell
python examples/inspect_table.py --dbf "K:\dbf_source\klienci.dbf" --json
```

Streaming record read (paging, field projection, memo policies, raw):

```powershell
python examples/read_records.py --dbf "K:\dbf_source\klienci.dbf" `
  --offset 0 --limit 20 --memo lazy --fields ID_KL,NAZWA,NOTATKA
```

Every script exposes its full parameter list through `--help`. The same
arguments can be added to a PyCharm Run/Debug configuration; the scripts
contain no hidden paths to user data.

## Test data

After installing the development dependencies you can generate a safe test
set:

```powershell
python tests/fixtures/generate_sample_dbf.py
python examples/export_dbf.py --source "tests\fixtures\input" `
  --output "tests\fixtures\output" --formats csv,json,jsonl,xlsx
python examples/verify_dbf.py --source "tests\fixtures\input" `
  --output "tests\fixtures\output" --formats csv,json,jsonl,xlsx
```

DBF/FPT/CDX files and conversion results are ignored by Git so that
production data is never published accidentally. To compare the raw DBF
checksum during export use `--deleted include`, because it preserves deleted
records and their physical order.

## Use as a library

After `pip install dbfbridge` you do not need to run CLI subprocesses. The
same operations are available as functions returning typed results:

```python
from dbfbridge import export_dbf, reconstruct_dbf, verify_conversion

export = export_dbf(
    r"K:\dbf_source",
    r"K:\dbf_output",
    formats=("csv", "json", "jsonl", "xlsx"),
    memo="inline",
)
export.raise_for_errors()

verification = verify_conversion(
    r"K:\dbf_source",
    r"K:\dbf_output",
    formats=("csv", "json", "jsonl", "xlsx"),
)

reconstruction = reconstruct_dbf(
    r"K:\dbf_output",
    r"K:\dbf_output_reconstructed",
    input_format="jsonl",
    overwrite=True,
)
```

The `python_api.py` script shows progress handling, incremental export,
verification, and reconstruction in one application. The functions are
silent by default; a `progress` callback receiving `ProgressEvent` objects
can be passed for GUI or logging use.

> **Note:** these repository-development examples insert `src/` on `sys.path`
> so they can run before the package is installed — that is a repository
> development convenience, not part of the installed-distribution contract.
> Installed-package examples live in
> [docs/python-api-examples.md](../docs/python-api-examples.md).
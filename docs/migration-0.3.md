# Migrating from dbfbridge 0.2 to 0.3

This guide is for users of the existing 0.2 API who are moving to 0.3.
The Python API you already call keeps working; the main change is how
dependencies are installed.

## The one change that affects every 0.2 user: install profiles

Before 0.3, installing `dbfbridge` pulled every capability dependency
(`dbfread`, `dbf`, `orjson`, `polars`, `xlsxwriter`, `openpyxl`) together.

In 0.3 the base installation is minimal:

```bash
pip install dbfbridge
```

This installs exactly one mandatory runtime dependency (`dbfread`) and
covers everything read-only plus DBF → JSONL/JSON/CSV export:

- `inspect_table()`, `read_schema()`, `iter_records()`, `read_records()`,
  `iter_raw_records()`;
- `export_dbf()` for the JSONL/JSON/CSV formats;
- `verify_conversion()`.

Everything heavier is now opt-in:

| Capability | Install command |
|---|---|
| DBF/FPT reconstruction (`reconstruct_dbf`, `check_conversion_quality`) | `pip install "dbfbridge[write]"` |
| XLSX export and XLSX input reading | `pip install "dbfbridge[xlsx]"` |
| XLSX → DBF reconstruction (writer + XLSX reader together) | `pip install "dbfbridge[write,xlsx]"` |
| Optional accelerators (`orjson`, `polars`) | `pip install "dbfbridge[fast]"` |
| Everything user-facing | `pip install "dbfbridge[all]"` |
| Old `[import]` extra (compatibility) | `pip install "dbfbridge[import]"` |

The historical `[import]` extra remains an alias: it installs the same
reconstruction dependency set as `[write]`.

`[fast]` is a pure accelerator. Without it, JSON conversion uses the
stdlib `json` module and CSV conversion uses the Python streaming engine;
the logical results are identical and its absence never raises.

## API compatibility

- **Existing 0.2 call forms remain valid.** Existing parameters and
  defaults are unchanged.
- **0.3 only adds optional keyword-only `progress=` and `cancel_check=`
  parameters** to the streaming/read entry points (`iter_records`,
  `read_records`, `iter_raw_records`). Existing callers do not change and
  default behaviour remains compatible (no progress callback, no
  cancellation).
- **Explicit Polish encodings no longer require caller codec
  registration.** `encoding="mazovia"` (and `piast`, `pki`, `cp1250`,
  `cp852`) is handled at operation time by the library itself. Do not
  register the historical Mazovia/PIAST codecs manually anymore; an unknown
  explicit codec raises the typed `EncodingUnknownError`.
- **The public import remains `import dbfbridge`.** The historical
  `dbf_bridge` namespace stays as a compatibility namespace exporting the
  same public symbols. Do not import private modules such as
  `dbf_bridge.core...`, `dbf_bridge.importer...`, or
  `dbf_bridge.exporter...` in application code.
- Export, verification, and quality results keep the same typed result
  objects and `raise_for_errors()` contract.

## Reconstruction without the write extra: typed failure

If you call `reconstruct_dbf()` (or `check_conversion_quality()`) without
`[write]`, 0.3 raises a typed error instead of the dependency being present
by default:

```python
from dbfbridge import OptionalDependencyMissingError, reconstruct_dbf

try:
    reconstruct_dbf("output", "rebuilt", input_format="jsonl")
except OptionalDependencyMissingError as error:
    print(error.code)  # OPTIONAL_DEPENDENCY_MISSING
    print(error.dependency)  # dbf
    print(error.extra)  # write
    print(error.operation)  # reconstruct_dbf
    print(error.install_command)  # python -m pip install "dbfbridge[write]"
```

The failure is deliberately strict:

- it happens **before any output creation** — no partial directories, no
  `.partial` files, no half-written DBF/FPT;
- it does **not auto-install** anything;
- it does **not access the Internet** — the payload only tells you which
  command to run yourself.

The same contract applies to XLSX export without `[xlsx]` and to XLSX →
DBF reconstruction without `[write,xlsx]`.

## CDX: what dbfbridge reports and what it does not do

This is unchanged in 0.3 and stated explicitly:

- dbfbridge **reports structural CDX presence** (the header flag and a
  companion `.cdx` file lookup) on `TableInfo`/`TableSchema`;
- it does **not parse or reconstruct full CDX definitions** — DBF field
  metadata does not contain index tag names or expressions;
- it does **not pretend to be a CDX engine**: rebuild indexes in Visual
  FoxPro or another index-aware tool.

## Checklist for a 0.2 → 0.3 upgrade

1. If you only read/export DBF data: keep `pip install dbfbridge` — your
   code runs unchanged and the install gets smaller.
2. If you reconstruct DBF/FPT files: add
   `pip install "dbfbridge[write]"` (or `[all]`).
3. If you use XLSX export or XLSX input: add `[xlsx]`
   (plus `[write]` for XLSX → DBF reconstruction).
4. Remove any manual Mazovia/PIAST codec registration; pass
   `encoding="mazovia"` (or the others) directly instead.
5. Optionally adopt `progress=` / `cancel_check=` on streaming reads for
   GUIs, workers, and long jobs.
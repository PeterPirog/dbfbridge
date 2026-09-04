# Complete Python API examples

Complete, copy/paste examples for the nine stable public operations of the
declared 1.x contract (see
[docs/api-1.0.md](api-1.0.md) for the normative guarantees). These examples
describe the **code-complete declared 1.x installed-distribution contract**;
the command shown is the normal installation path **after an official PyPI
publication**:

```bash
python -m pip install dbfbridge
```

This document itself does **not** claim that the current `main` state is
already downloadable from PyPI — the historical GitHub Release v0.2.0 exists,
but its PyPI publication did not complete successfully. See
[pypi-usage.md](pypi-usage.md) for the publication/install-status note and
the install profiles in full detail.

or the appropriate extra (see the install-profile table below). No example
requires Git, a `src/` directory, `PYTHONPATH`, `sys.path` manipulation, or
private `dbf_bridge.*` modules.

The examples use small synthetic file names:

- `KLIENCI.DBF` — a single DBF table (with `KLIENCI.FPT` when a memo field is present);
- `data/` — a source directory tree holding `*.dbf` files (with optional `.fpt` companions);
- `exported/`, `rebuilt/`, `quality/` — output directories created by the examples.

## API at a glance

| # | Operation | Install | Result | Memory | Writes output? |
|---|---|---|---|---|---|
| 1 | `inspect_table()` | base | `TableInfo` | O(header) | no |
| 2 | `read_schema()` | base | `TableSchema` | O(fields) | no |
| 3 | `iter_records()` | base | iterator of `DirectRecord` | O(1) streaming | no |
| 4 | `read_records()` | base | `RecordPage` | O(limit) | no |
| 5 | `iter_raw_records()` | base | iterator of `DirectRecord` | streaming, never opens the FPT | no |
| 6 | `export_dbf()` | base (+`[xlsx]` for XLSX) | `ExportRunResult` | streaming per table | yes (declared output tree) |
| 7 | `reconstruct_dbf()` | `[write]` (+`[xlsx]` for XLSX input) | `ReconstructionRunResult` | streaming per table | yes (declared output tree) |
| 8 | `verify_conversion()` | base (+`[xlsx]` for XLSX) | `VerificationRunResult` | per-file checks | report only with `write_report=True` |
| 9 | `check_conversion_quality()` | `[write]` | `QualityRunResult` | per-table round trip | yes (retained diagnostic workspace) |

This table is navigation only — the normative guarantees (signatures, error
contracts, JSON boundary, SemVer policy) live in
[docs/api-1.0.md](api-1.0.md).

## Install profiles

| Operation group | Install |
|---|---|
| Direct Read + JSONL/JSON/CSV export + verification | `pip install dbfbridge` |
| Reconstruction + quality round trip | `pip install "dbfbridge[write]"` |
| XLSX export / XLSX-format reading and verification support | `pip install "dbfbridge[xlsx]"` |
| XLSX → DBF/FPT reconstruction | `pip install "dbfbridge[write,xlsx]"` |
| Optional accelerators (`orjson`, `polars`) | `pip install "dbfbridge[fast]"` |
| Everything user-facing | `pip install "dbfbridge[all]"` |

A missing optional dependency raises the typed
`OptionalDependencyMissingError` **before any output is created** — classify
failures by its machine code (`code == "OPTIONAL_DEPENDENCY_MISSING"`), never
by parsing the message:

```python
from dbfbridge import OptionalDependencyMissingError, reconstruct_dbf

try:
    reconstruct_dbf("exported", "rebuilt", overwrite=True)
except OptionalDependencyMissingError as error:
    print(error.to_dict())
    # {"code": "OPTIONAL_DEPENDENCY_MISSING", "dependency": "dbf",
    #  "extra": "write", "operation": "reconstruct_dbf",
    #  "install_command": 'python -m pip install "dbfbridge[write]"',
    #  "purpose": "DBF/FPT reconstruction"}
```

## 1. `inspect_table()` — cheap table header overview

**Install:** base.
**Input:** one DBF path.
**Result:** `TableInfo` — JSON-safe through `to_dict()`.
**Side effects:** none (strictly read-only; header-only read).
**Best use:** cheap discovery/listing of a table's shape.

```python
from dbfbridge import inspect_table

info = inspect_table("KLIENCI.DBF")

payload = info.to_dict()  # JSON-safe boundary
print(payload["record_count"], payload["language_driver"])
print([field["name"] for field in payload["fields"]])
```

`inspect_table` reads only the DBF header/descriptors — it never reads record
data and never touches the memo file.

## 2. `read_schema()` — full safe table schema inspection

**Install:** base.
**Input:** one DBF path.
**Result:** `TableSchema` — JSON-safe through `to_dict()`.
**Side effects:** none (strictly read-only).
**Best use:** full header/field/companion metadata for inspection and services.

```python
from dbfbridge import read_schema

schema = read_schema("KLIENCI.DBF")

for field in schema.fields:
    print(field.name, field.dbf_type, field.length, field.decimal_count)

payload = schema.to_dict()  # JSON-safe boundary
print(payload["record_count"], payload["dbversion_name"])
print([field["name"] for field in payload["fields"]])
```

The schema object is the inspection/service payload for header, field, and
companion metadata.

> **Warning — two different schema concepts.** `TableSchema.to_dict()` is an
> inspection/service payload. It is **not** a replacement for the migration
> schema artifact `<table>_schema.json` generated by `export_dbf()` — that
> artifact (with `schema_format`/`schema_version`/`table`/`relative_path`/
> `source`/`dbf`/`text_encoding`/`memo`/`fields` and RawMode-dependent
> reconstruction metadata) is the **authority consumed by
> `reconstruct_dbf()`**. Never manufacture reconstruction input from
> `read_schema(...).to_dict()`; run `export_dbf()` instead. This distinction
> matters especially for tool/MCP consumers.

## 3. `iter_records()` — streaming records (O(1) memory)

**Install:** base.
**Input:** one DBF path; options: `fields` (projection), `memo`
(`skip`/`lazy`/`null`/`inline`), `include_deleted`, `raw`, `progress`,
`cancel_check`.
**Result:** iterator of `DirectRecord` in physical order.
**Side effects:** none (read-only); close the iterator explicitly on early exit.
**Best use:** local full-table/streaming processing with O(1) memory.

```python
from dbfbridge import iter_records

# Field projection: only the selected fields are parsed and returned.
for record in iter_records("KLIENCI.DBF", fields=["KOD", "NAZWA"], memo="skip"):
    print(record.physical_index, record.values)

# Early iterator close releases the file handles immediately:
stream = iter_records("KLIENCI.DBF", memo="lazy")
first = next(stream)
stream.close()  # do not read the rest of the table
```

With `memo="lazy"`, memo fields are returned as `LazyMemoValue` handles; call
`.load()` on one to read the FPT content on demand. With `memo="skip"` no FPT
is ever opened.

## 4. `read_records()` — bounded paging (service-friendly)

**Install:** base.
**Input:** one DBF path; `offset` (physical record index), `limit`,
`fields`, `include_deleted`, `memo`, `progress`, `cancel_check`.
**Result:** one `RecordPage` — JSON-safe through `to_dict()`
(`offset`/`limit`/`scanned`/`next_offset`/`exhausted`/`records`).
**Side effects:** none (read-only).
**Best use:** the recommended bounded pattern for services, tools, and
remote/agent boundaries (see
[docs/tool-server-integration.md](tool-server-integration.md)).

`read_records` never materializes the whole table.

```python
from dbfbridge import read_records

def read_all_pages(path: str) -> None:
    offset = 0
    while True:
        page = read_records(path, offset=offset, limit=100, fields=["KOD", "NAZWA"])
        for record in page.records:
            print(record.values)

        payload = page.to_dict()  # JSON-safe: offset/limit/next_offset/exhausted/records
        if payload["exhausted"]:
            break
        offset = payload["next_offset"]

read_all_pages("KLIENCI.DBF")
```

`page.next_offset` is `None` when the table is exhausted; otherwise it is the
offset of the next page. `page.scanned` counts the **physical record frames
consumed by this call** — deleted physical slots count toward `scanned` even
when `include_deleted=False`, because they were physically scanned and then
omitted from `records`. Keep the distinction in mind for paging/progress
semantics: `records` = returned logical result records; `scanned` = physical
frames consumed.

## 5. `iter_raw_records()` — forensic stream

**Install:** base.
**Input:** one DBF path; options: `progress`, `cancel_check`.
**Result:** iterator of `DirectRecord` — every physical record (deleted
included), physical order, `values` empty, `raw_record` bytes.
**Side effects:** none (read-only); the FPT is never opened.
**Best use:** forensic/byte-level inspection where decoded values are not
needed.

```python
from dbfbridge import iter_raw_records

for record in iter_raw_records("KLIENCI.DBF"):
    print(
        record.physical_index,
        record.deleted,          # True for deletion-marker records
        record.raw_record[:8],   # exact physical bytes (bytes | None)
    )
```

`record.raw_record` is `bytes` in Python; `record.to_dict()` serializes it as
Base64 under the **Direct Read JSON boundary key `raw_record`**. Do not
conflate that with the migration forensic format's reserved record key
`__dbfbridge_raw_record__` — that key belongs to the JSONL/JSON **migration**
intermediate produced by `export_dbf`, not to the Direct Read JSON boundary.
Always pass `record.to_dict()` — not raw Python bytes — to a JSON transport.

## 6. `export_dbf()` — DBF tree → JSONL/JSON/CSV/XLSX

**Install:** base (JSONL/JSON/CSV); `[xlsx]` for XLSX output.
**Input:** one DBF file or a source directory tree; an output directory
outside the source tree; format and policy options.
**Result:** `ExportRunResult` — aggregate run payload; JSON-safe through
`to_dict()`.
**Side effects:** creates the declared output tree (data files +
`<table>_schema.json` + reports) with atomic publication.
**Best use:** loss-aware DBF migration into modern exchange formats.

```python
from dbfbridge import export_dbf

result = export_dbf(
    "data",            # one DBF file or a directory tree
    "exported",        # output directory; final output files are atomically published
    formats=("jsonl", "csv"),
    memo="inline",     # read memo values (default policy is per-format)
    overwrite=True,
    raw_mode="full-record",  # default; see RawMode below
)

# Per-table failures are returned as run DATA first:
for table in result.results:
    print(table.table, table.status, table.active_records)

payload = result.to_dict()  # JSON-safe run payload
if result.failed:
    result.raise_for_errors()  # explicit policy: fail the caller on failures
```

`raise_for_errors()` raises `DBFBridgeRunError` (machine code + structured
details). Treat it as an explicit host policy — the run result alone already
contains everything needed to report partial success.

`result.ok` is a **count** of OK table results, not an aggregate-success
boolean — a multi-table run with one OK and one FAILED table has `ok == 1`.
Aggregate success for a multi-table run is the **absence of failures**:
`result.failed == 0` (the tool-server guide's adapter examples use exactly
that).

`raw_mode` chooses the raw-retention level of the JSONL/JSON intermediate:
`"full-record"` (default, forensic), `"metadata"`, or `"none"`. All modes
preserve canonical reconstruction for supported cases; only `full-record`
retains the per-record physical image.

## 7. `reconstruct_dbf()` — JSONL/JSON/CSV/XLSX → DBF/FPT

**Install:** `[write]` for JSONL/JSON/CSV input; `[write,xlsx]` for XLSX input.
**Input:** an exported format tree (data files + `<table>_schema.json`
generated by `export_dbf`) and an output directory separate from the source.
**Result:** `ReconstructionRunResult` — per-table canonical/raw match data;
JSON-safe through `to_dict()`.
**Side effects:** creates the rebuilt DBF/FPT tree + reconstruction report,
published atomically (a failed table leaves no published output).
**Best use:** schema-driven DBF/FPT rebuilding from exported migration data.

```python
from dbfbridge import reconstruct_dbf

result = reconstruct_dbf(
    "exported",          # directory with *.jsonl + *_schema.json (generated by export_dbf)
    "rebuilt",           # output directory for the rebuilt DBF/FPT trees
    input_format="jsonl",
    memo="inline",
    overwrite=True,
)

for table in result.results:
    print(table.source, table.canonical_match, table.raw_layout_restored)

result.raise_for_errors()
```

Reconstruction is schema-driven, validates the rebuilt files canonically
(`canonical_match`), and publishes atomically — a failed table leaves no
published output and no staging residue.

## 8. `verify_conversion()` — exported files vs DBF sources

**Install:** base; `[xlsx]` when XLSX outputs are checked.
**Input:** the DBF source tree and the exported output tree; format list;
`strict`, `write_report`.
**Result:** `VerificationRunResult` — JSON-safe through `to_dict()`.
**Side effects:** none when `write_report=False`; with the default
`write_report=True` a verification report JSON is written next to the
outputs (the call is then not side-effect-free).
**Best use:** integrity checking of a completed migration (the response can
be the report when `write_report=False`).

```python
from dbfbridge import verify_conversion

result = verify_conversion(
    source="data",
    output="exported",
    formats=("jsonl",),
    strict=True,
    write_report=False,
)

payload = result.to_dict()
print(payload["summary"], payload["global_errors"])
result.raise_for_errors()
```

`write_report=False` keeps the call free of an extra verification-report file —
useful for service/tool calls whose response is the report. When
`write_report=True` (default) the verification report JSON is written next to
the outputs, so the call is not side-effect-free.

## 9. `check_conversion_quality()` — retained DBF → JSONL → DBF round trip

**Install:** `[write]`.
**Input:** a DBF source tree and a quality workspace directory.
**Result:** `QualityRunResult` — per-table canonical/raw match data and a
bounded difference list; JSON-safe through `to_dict()`.
**Side effects:** creates retained diagnostic output (forward export,
reconstructed DBF/FPT, re-export, quality report).
**Best use:** a dedicated diagnostic round trip — more expensive than Direct
Read, not a lightweight table-read request.

```python
from dbfbridge import check_conversion_quality

result = check_conversion_quality(
    source="data",
    output="quality",   # workspace for the retained diagnostic round trip
    overwrite=False,
    max_differences=20,
    progress=None,
)

payload = result.to_dict()
print(payload["summary"])
result.raise_for_errors()
```

Quality is a **write** operation creating retained diagnostic output and is
more expensive than Direct Read — it is a dedicated diagnostic call, not a
lightweight table-read request.

## Progress and cancellation

Direct Read operations accept `progress=` (a callback receiving
`ProgressEvent` with `operation`/`current`/`total`/`table`/`format`/
`records`/`message`) and `cancel_check=` (a callable returning `True` to stop
at the next record boundary, raising the typed `ReadCancelledError`):

```python
from dbfbridge import iter_records

def show(event):
    print(event.operation, event.current, event.total, event.records)

for record in iter_records("KLIENCI.DBF", progress=show, cancel_check=lambda: False):
    ...  # stop iterating whenever you like
```

## JSON-safe boundary

Ordinary result models expose `to_dict()` — `TableInfo`, `TableSchema`,
`DirectRecord`, `RecordPage`, `ReconstructionResult`,
`ReconstructionRunResult`, `VerificationRunResult`, `QualityRunResult`, and
every public error. Always serialize through `to_dict()`; never use
`dataclasses.asdict`, `__dict__`, or `repr` as an integration boundary.

## Special serialization cases (see
[docs/api-1.0.md](api-1.0.md) §5)

`ExportRunResult.to_dict()` is the normal aggregate run boundary — its nested
`TableResult` objects are already serialized through
`TableResult.to_report_dict()`; a standalone `TableResult` exposes
`to_report_dict()` (not `to_dict()`).

`ProgressEvent` is a typed public event object without `to_dict()` — the host
serializes its documented fields explicitly (see the progress example above).
# Using dbfbridge from PyPI

This is the canonical guide for the **installed** `dbfbridge` distribution —
for a user who has Python, `pip`, and DBF/FPT files, and who does **not**
have a repository checkout, a `src/` directory, or any development tools.

Everything in this guide works with the installed distribution only: no Git,
no `examples/` folder, no `PYTHONPATH`, no private-module imports.

> **Availability.** This guide defines the installed-distribution contract
> implemented on `main`; the install-profile extras documented here are the
> current contract, not an upcoming one. After an official PyPI publication,
> the normal installation command is `python -m pip install dbfbridge`. The
> historical GitHub Release/tag **v0.2.0 exists**, but its PyPI publication
> did **not** complete successfully (the publish step failed on Trusted
> Publisher verification), and the final `1.0.0` release lifecycle is still
> deferred — therefore this document does **not** claim that the
> code-complete 1.x repository state is already downloadable from PyPI.

## Which operation do I need? (capability table)

| Need | Operation | Install profile | Creates output? | Recommended starting point |
|---|---|---|---|---|
| inspect / read schema / stream records | `inspect_table`, `read_schema`, `iter_records`, `read_records`, `iter_raw_records` | base | no | [Inspect a DBF file](#inspect-a-dbf-file) |
| migrate DBF data to JSONL/JSON/CSV | `export_dbf` | base | yes (data files + schemas + report) | [Export DBF data](#export-dbf-data) |
| exchange with spreadsheet users | `export_dbf(formats=("xlsx",))` | `[xlsx]` | yes | [XLSX](#xlsx) |
| rebuild DBF/FPT from exported JSONL/JSON/CSV | `reconstruct_dbf` | `[write]` | yes (DBF/FPT + report) | [Reconstruct DBF/FPT files](#reconstruct-dbffpt-files) |
| rebuild DBF/FPT from XLSX exports | `reconstruct_dbf` | `[write,xlsx]` | yes | [XLSX](#xlsx) |
| verify exported files against sources | `verify_conversion` | base (+`[xlsx]` when checking XLSX) | only with `write_report=True` | [Command-line interface](#command-line-interface) (`dbf-bridge-verify`) |
| diagnostic DBF → JSONL → DBF round trip | `check_conversion_quality` | `[write]` | yes (retained workspace) | [Reconstruct DBF/FPT files](#reconstruct-dbffpt-files) |

## Contents

> **Further reading:** complete copy/paste examples for all nine public
> operations are in [python-api-examples.md](python-api-examples.md), and the
> transport-neutral integration patterns for tool servers and MCP backends
> are in [tool-server-integration.md](tool-server-integration.md). The
> normative stable API contract is [api-1.0.md](api-1.0.md); the
> documentation map is [README.md](README.md).

1. [Requirements](#requirements)
2. [Create a virtual environment](#create-a-virtual-environment)
3. [Installation](#installation)
4. [Install profiles](#install-profiles)
5. [Verify the installation](#verify-the-installation)
6. [5-minute quick start](#5-minute-quick-start)
7. [Inspect a DBF file](#inspect-a-dbf-file)
8. [Read the full schema](#read-the-full-schema)
9. [Stream records](#stream-records)
10. [Page through records](#page-through-records)
11. [Deleted records](#deleted-records)
12. [Memo policies](#memo-policies)
13. [Raw reads](#raw-reads)
14. [Export DBF data](#export-dbf-data)
15. [JSON and CSV without the fast extra](#json-and-csv-without-the-fast-extra)
16. [Reconstruct DBF/FPT files](#reconstruct-dbffpt-files)
17. [XLSX](#xlsx)
18. [Full installation](#full-installation)
19. [Command-line interface](#command-line-interface)
20. [Structured error handling](#structured-error-handling)
21. [Progress and cancellation](#progress-and-cancellation)
22. [Polish encodings](#polish-encodings)
23. [What dbfbridge does not support](#what-dbfbridge-does-not-support)
24. [Further reading](#further-reading)

## Requirements

- Python **3.10–3.14** (3.12 recommended)
- `pip`
- your DBF files; sibling `.FPT` memo files when the tables use memos

## Create a virtual environment

Windows PowerShell (any Python from 3.10 to 3.14 works; the example uses 3.12):

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

## Installation

After an official PyPI publication, the normal installation command is:

```bash
python -m pip install dbfbridge
```

The base installation has exactly **one** runtime dependency (`dbfread`) and
covers: `import dbfbridge`, the complete read-only Direct Read surface, and
DBF → JSONL/JSON/CSV migration.

## Install profiles

| Command | Capabilities | When to use |
|---|---|---|
| `pip install dbfbridge` | `import dbfbridge`; Direct Read: `inspect_table`, `read_schema`, `iter_records`, `read_records`, `iter_raw_records`; DBF → JSONL/JSON/CSV migration (stdlib/Python engines); verification | reading and exporting DBF data |
| `pip install "dbfbridge[write]"` | everything above **plus** DBF/FPT reconstruction (`reconstruct_dbf`) and quality round trips (`check_conversion_quality`) | rebuilding DBF files from exported formats |
| `pip install "dbfbridge[xlsx]"` | XLSX export (`xlsxwriter`) and XLSX-format reading/verification support (`openpyxl`) | Excel interchange |
| `pip install "dbfbridge[write,xlsx]"` | XLSX → DBF/FPT reconstruction (`[write]` + `[xlsx]` together) | XLSX → DBF round trips |
| `pip install "dbfbridge[fast]"` | optional accelerators: `orjson` (JSON) and `polars` (CSV) | large conversion jobs; identical logical results, only faster |
| `pip install "dbfbridge[all]"` | the complete feature set: Direct Read + migration + reconstruction + XLSX + accelerators | one-command complete install (not a development environment) |
| `pip install "dbfbridge[import]"` | historical compatibility alias — installs the same reconstruction dependency as `[write]` | scripts written against the pre-0.3 extras |

Rules of thumb:

- Direct Read and DBF → JSONL/JSON/CSV need **no extra**.
- Reconstruction (JSONL/JSON/CSV → DBF/FPT) needs `[write]`.
- XLSX export needs `[xlsx]`; XLSX → DBF reconstruction needs
  `[write,xlsx]` together.
- `[fast]` is a pure accelerator: without it, JSON uses the stdlib `json`
  module and CSV uses the Python streaming engine. Missing fast dependencies
  **never** raise an error and never change the logical result.
- `[all]` is the full user-facing feature set; it contains **no** development
  tooling (no `pytest`, `ruff`, `build`, `twine`, or benchmark tooling).

## Verify the installation

```bash
python -c "import dbfbridge; print(dbfbridge.__version__); print(dbfbridge.__file__)"
python -m pip show dbfbridge
dbf-bridge --help
```

- The **distribution name** is `dbfbridge`; the **recommended import** is
  also `dbfbridge`.
- `dbf_bridge` (with an underscore) is a **compatibility namespace** that
  exports the same public symbols. User code should use
  `from dbfbridge import ...` and should not import private modules such as
  `dbf_bridge.core...` or `dbf_bridge.exporter...`.
- `dbf-bridge` is an executable script installed into the active virtual
  environment — it needs no repository checkout and no `PYTHONPATH`.

## 5-minute quick start

Read the header, stream the records, export the tree — three calls, base
install:

```python
from dbfbridge import inspect_table, iter_records, export_dbf

info = inspect_table("data/customer.dbf")
print(info.record_count, info.encoding, info.has_memo)

for record in iter_records("data/customer.dbf", memo="skip"):
    print(record.physical_index, record.values)

result = export_dbf("data", "exported", formats=("jsonl",))
result.raise_for_errors()
```

Then decide by need (see the capability table): add `[write]` to rebuild
DBF/FPT files with `reconstruct_dbf`, `[xlsx]` for Excel output, or nothing
at all for pure reading.

## Inspect a DBF file

```python
from pathlib import Path
from dbfbridge import inspect_table

table = Path("data/customer.dbf")

info = inspect_table(table)

print(info.record_count)
print(info.encoding)
print(info.has_memo)

for field in info.fields:
    print(field.name, field.dbf_type)
```

- Strictly read-only: the DBF is opened for reading only, no file is ever
  created, and the source stays byte-identical.
- The DBF read is bounded by the declared header length, independent of the
  record count.

## Read the full schema

```python
from dbfbridge import read_schema

schema = read_schema("data/customer.dbf")

print(schema.dbversion_name)
print(schema.memo_companion_format)
print(schema.companion_cdx_present)
```

- No output files are created; nothing is written anywhere.
- CDX companion **presence** is reported structurally
  (`has_structural_cdx` / `companion_cdx_present`), but CDX tag names and
  expressions are **not parsed** — DBF field metadata does not contain them.

## Stream records

```python
iter_records(
    path,
    fields=None,
    include_deleted=False,
    memo="lazy",
    raw=False,
    encoding="auto",
    decode_errors="strict",
)
```

- **O(1) memory**: records are streamed one at a time; nothing proportional
  to the table size is kept.
- **Physical order**: records are yielded in physical storage order.
- **Projection avoids work**: unselected fields are never parsed.

```python
from dbfbridge import iter_records

for record in iter_records(
    "data/customer.dbf",
    fields=["ID", "NAME"],
    memo="skip",
):
    print(record.physical_index, record.values)
```

### Early termination and resource ownership

`iter_records` returns an iterator that owns open file handles. If you stop
early, close it explicitly — do not rely on garbage collection:

```python
records = iter_records("data/customer.dbf")

try:
    for index, record in enumerate(records):
        print(record.values)
        if index == 99:
            break
finally:
    records.close()
```

Exhausting the iterator, an error, or `close()` all release the handles.

## Page through records

```python
from dbfbridge import read_records

page = read_records(
    "data/customer.dbf",
    offset=0,
    limit=100,
    fields=["ID", "NAME"],
)
```

- `offset` is the **zero-based physical record index** (deleted slots
  included in the numbering) — not the index of the next active record;
- `limit` is the maximum number of records returned; memory is **O(limit)**;
- `page.scanned` includes physical records skipped because they are deleted;
- `page.next_offset` is again a **physical** index: pass it as the next
  `offset` to continue.

Pagination loop:

```python
from dbfbridge import read_records

offset = 0
while True:
    page = read_records("data/customer.dbf", offset=offset, limit=100)
    for record in page.records:
        print(record.physical_index, record.values)
    if page.exhausted or page.next_offset is None:
        break
    offset = page.next_offset
```

## Deleted records

- `include_deleted=False` (default): deleted records are skipped in the same
  single pass — no second scan of the record area happens.
- `include_deleted=True`: deleted records are yielded too, with
  `record.deleted = True`.
- **`physical_index` always counts deleted slots**: a deleted record keeps
  its physical position, and the indices of later records never shift. Never
  treat `physical_index` as a number of active records.

## Memo policies

| policy | `record.values` | FPT read during iteration | notes |
|---|---|---|---|
| `skip` | memo field **absent** | never | smallest footprint |
| `null` | memo field present as `None` | never | keeps column shape |
| `lazy` | `LazyMemoValue` metadata | never (until explicit `load()`) | you decide when to read |
| `inline` | decoded memo text | **yes**, per record | requires a readable FPT; missing FPT → `FPT_REQUIRED_MISSING`, damaged FPT → `FPT_INVALID` |

`LazyMemoValue` example:

```python
from dbfbridge import LazyMemoValue

for record in iter_records("data/customer.dbf", memo="lazy"):
    value = record.values.get("NOTES")

    if isinstance(value, LazyMemoValue):
        print(value.to_dict())   # metadata only: table, field, memo block
        text = value.load()      # explicit FPT read, per value
```

`load()` is explicit and reads the memo block on demand. It is **not** a
cached global memo store — calling `load()` twice performs two reads.

## Raw reads

Two different APIs — do not confuse them:

- `iter_records(..., raw=True)` yields **decoded fields plus** the exact
  physical record image in `record.raw_record` (the FPT is still used for
  `memo="inline"`).
- `iter_raw_records(path)` is the **pure forensic physical stream**: every
  physical record (deleted included), raw bytes only, no decoded fields
  (`values` is empty), and the FPT is never opened — even damaged text bytes
  cannot hide the raw image.

```python
from dbfbridge import iter_raw_records

for record in iter_raw_records("data/customer.dbf"):
    print(record.physical_index, record.deleted, record.raw_record)
```

Raw forensic mode has a higher cost: it carries one extra copy of every
record's bytes.

## Export DBF data

Works after the base install (no extra needed for JSONL/JSON/CSV):

```python
from dbfbridge import export_dbf

result = export_dbf(
    "data",
    "output",
    formats=("jsonl",),
)

result.raise_for_errors()
```

- `data` may be one DBF file or a directory tree; `output` is created if
  needed and must not be inside the source tree.
- The output tree preserves the source layout and contains, per table: the
  requested data files, `<table>_schema.json`, and
  `migration_report.jsonl`/`.csv`.
- `result.raise_for_errors()` turns per-table failures into a typed
  `DBFBridgeRunError`.
- JSONL is the preferred format for reconstruction (streaming, inline memo,
  raw-record metadata).

### Raw retention levels (`raw_mode`)

`export_dbf(raw_mode=...)` chooses how much raw forensic data the
JSONL/JSON intermediate retains (`RawMode = "none" | "metadata" |
"full-record"`):

| value | raw record images | canonical reconstruction | raw byte-identical reconstruction |
|---|---|---|---|
| `"full-record"` (**default**) | kept per record | yes | yes (raw-layout restoration) |
| `"metadata"` | omitted (schema/memo metadata kept) | yes | no |
| `"none"` | omitted (+ replay-only header blobs omitted) | yes | no |

All modes preserve canonical reconstruction for supported cases — only
`"full-record"` retains the per-record physical image needed for raw-layout
restoration. CSV/XLSX outputs never carry raw fields regardless of `raw_mode`.

## JSON and CSV without the fast extra

`[fast]` is **optional**. Without `orjson`, JSON conversion uses the stdlib
`json` module; without `polars`, CSV conversion uses the Python streaming
engine. The logical results are identical — only speed differs. Nothing in
the base install requires `orjson` or `polars` for correctness.

## Reconstruct DBF/FPT files

Install the write extra first:

```bash
python -m pip install "dbfbridge[write]"
```

```python
from dbfbridge import reconstruct_dbf

result = reconstruct_dbf(
    "output",
    "rebuilt",
    input_format="jsonl",
)

result.raise_for_errors()
```

- `source` is the exported format tree (data files + `<table>_schema.json`);
  the schema artifact is generated by `export_dbf` and is the **authority**
  consumed by `reconstruct_dbf`;
  `read_schema(...).to_dict()` is an inspection/service payload and is
  **not** that migration artifact — never manufacture reconstruction input
  from it;
- `destination` must be a **separate** directory — the source is never
  modified and the default is `overwrite=False`;
- the generated `reconstruction_report.jsonl` records canonical/raw checksums
  and any differences.
- XLSX → DBF reconstruction additionally needs the `[xlsx]` extra (see
  below), i.e. `pip install "dbfbridge[write,xlsx]"`.

Calling `reconstruct_dbf` (or `check_conversion_quality`) without `[write]`
raises `OptionalDependencyMissingError` **before any output directory,
partial file, or DBF/FPT is created**.

## XLSX

XLSX **export** (DBF → XLSX):

```bash
python -m pip install "dbfbridge[xlsx]"
```

XLSX **reconstruction** (XLSX → DBF) needs the writer **and** the XLSX
reader together:

```bash
python -m pip install "dbfbridge[write,xlsx]"
```

These are two distinct cases — export needs only `[xlsx]`, while
reconstruction from XLSX exports needs `[write,xlsx]`. Without the required
extra the operation fails with a typed `OptionalDependencyMissingError`
before any output is created.

## Full installation

```bash
python -m pip install "dbfbridge[all]"
```

`[all]` installs the complete user-facing feature set (Direct Read +
migration + reconstruction + XLSX + accelerators). It is equivalent to
`[write,xlsx,fast]` and is **not** a development environment: it contains no
`pytest`, `ruff`, `build`, `twine`, or benchmark tooling.

## Command-line interface

The installed distribution ships four executable scripts from the active
virtual environment — no repository checkout, no `examples/` folder, no
`PYTHONPATH`:

```bash
dbf-bridge --help
dbf-bridge-import --help
dbf-bridge-verify --help
dbf-bridge-quality --help
```

```bash
dbf-bridge --source data --output out --formats jsonl
dbf-bridge-verify --source data --output out --formats jsonl
dbf-bridge-import --source out --output rebuilt --formats jsonl --memo inline
dbf-bridge-quality --source data --output quality
```

## Structured error handling

```python
from dbfbridge import DirectReadError

try:
    ...
except DirectReadError as exc:
    print(exc.code)      # e.g. DBF_TRUNCATED, FPT_REQUIRED_MISSING
    print(exc.to_dict()) # JSON-safe: code, message, path, context
```

Missing optional dependencies (never for `[fast]`) raise:

```python
from dbfbridge import OptionalDependencyMissingError

try:
    ...
except OptionalDependencyMissingError as error:
    print(error.code)             # OPTIONAL_DEPENDENCY_MISSING
    print(error.dependency)       # e.g. dbf
    print(error.extra)            # e.g. write
    print(error.operation)        # e.g. reconstruct_dbf
    print(error.install_command)  # python -m pip install "dbfbridge[write]"
```

dbfbridge never installs, downloads, or opens anything automatically; the
error payload only tells you what to run.

## Progress and cancellation

The three Direct Read entry points accept two keyword-only callbacks:

```python
iter_records(path, progress=None, cancel_check=None)
read_records(path, offset=0, limit=100, progress=None, cancel_check=None)
iter_raw_records(path, progress=None, cancel_check=None)
```

Both are optional, keyword-only, and default to `None`; existing callers do
not change.  Progress and cancellation are independent of each other — either
can be used alone.

### Progress callback

```python
from dbfbridge import ProgressEvent, iter_records


def show_progress(event: ProgressEvent) -> None:
    print(event.current, event.total, event.records)


for record in iter_records(
    "data/customer.dbf",
    fields=["ID", "NAME"],
    memo="skip",
    progress=show_progress,
):
    ...
```

`ProgressEvent` fields for Direct Read (`operation="read"`):

| field | meaning |
|---|---|
| `current` | absolute **physical** position after the last processed record (`0 <= current <= total`); deleted records count toward it |
| `total` | declared physical record count of the table |
| `table` | the DBF path as a string |
| `records` | records yielded/returned by this call so far (deleted records do **not** count when `include_deleted=False`) |

- **Cadence**: progress is emitted at a bounded internal cadence — every
  1000 scanned physical records — plus one final event when the call/page
  completes normally.  It is not emitted per record.
- **Pagination**: for `read_records(offset=1000, limit=100)` the `current`
  value is still an absolute physical position in the table (the same
  physical index space as `offset`/`next_offset`), never an active-record
  number.  `current` follows the **scanned** physical cursor: deleted
  records after the last returned record still advance it (a page over
  4 physical records with the last 2 deleted and `include_deleted=False`
  returns 2 records but finishes at `current = 4`).
- **Callback exceptions are never swallowed**: if `progress(event)` raises,
  all DBF/FPT handles are closed and the original exception propagates to
  the caller.  The same is true for `cancel_check` exceptions.

### Cancellation

`cancel_check` is a plain callable you provide — the library creates **no
threads, event loops, background workers, or global state**.  It is called
cooperatively at **every physical record boundary, before the next record is
read or decoded** (this is independent of progress cadence):

```python
from dbfbridge import ReadCancelledError, iter_records

state = {"stop": False}


def should_cancel() -> bool:
    return state["stop"]


try:
    for record in iter_records(
        "data/customer.dbf",
        cancel_check=should_cancel,
    ):
        process(record)

        if some_condition():
            state["stop"] = True
except ReadCancelledError as exc:
    print(exc.code)       # READ_CANCELLED
    print(exc.to_dict())  # JSON-safe progress context
```

Semantics:

- `cancel_check() -> False` continues; `True` stops the read **before** the
  next physical record is read or decoded — records already yielded remain
  valid and no sentinel record is produced;
- cancelling **before the first record** consumes zero physical record
  frames — nothing is decoded, nothing is yielded, the backend
  physical-record loop is never entered, no streaming handle stays open,
  and the source stays unchanged.  (The normal eager argument/header/
  companion validation still runs before iteration starts, exactly as in
  previous releases; for `memo="inline"` that validation may briefly open
  the FPT companion to check its header metadata.)  The typed
  `ReadCancelledError` is raised with `scanned == 0`;
- `read_records` raises `ReadCancelledError` instead of returning a partially
  filled page that would pretend to be a completed page;
- `iter_raw_records` has the same guarantees, and the forensic semantics of
  records returned before the stop are unchanged;
- the typed error carries stable machine fields in `to_dict()["context"]`:

```python
{
    "code": "READ_CANCELLED",
    "path": "data/customer.dbf",
    "context": {
        "offset": 0,                # physical start index of this call
        "next_physical_index": 10000,
        "scanned": 10000,
        "yielded": 10000,
        "record_count": 190000,
    },
}
```

- **Resource cleanup**: after cancellation, a callback exception, or
  `iterator.close()`, every DBF/FPT handle is closed — the files can be
  renamed or deleted immediately (Windows-safe);
- the read stays read-only: no output files, no `.partial`, no locks, no
  network, and the source stays byte-identical.

## Polish encodings

```python
from dbfbridge import iter_records

# `encoding="auto"` is the normal case: the codepage is resolved from the
# DBF language-driver byte, including the Polish Mazovia driver (0x69).
for record in iter_records("data/customer.dbf"):
    ...
```

| value | meaning |
|---|---|
| `"auto"` | resolve from the DBF header language driver (preferred; handles cp1250, cp852 and Mazovia drivers) |
| `"cp1250"` | force Windows-1250 |
| `"cp852"` | force Latin-2 (DOS) |
| `"mazovia"` | force the historical Mazovia (Polish MS-DOS) page |
| `"piast"` / `"pki"` | aliases of the same Polish OEM page family |

An explicit value overrides the header-declared driver (precedence is
unchanged).  All explicit Polish overrides are handled **at operation time**
— no manual codec registration and no private-module import is ever needed:

```python
from dbfbridge import iter_records

for record in iter_records("data/legacy.dbf", encoding="mazovia"):
    print(record.values["TEKST"])
```

The same works for `read_records(..., encoding=...)` and for memo payloads
(`memo="lazy"` / `memo="inline"` decode with the same explicit encoding).

`decode_errors`:

| value | meaning |
|---|---|
| `"strict"` | undecodable bytes raise a typed `TEXT_DECODE_ERROR` (never a raw `UnicodeDecodeError`) |
| `"replace"` | undecodable bytes become the Unicode replacement character |
| `"ignore"` | undecodable bytes are dropped |

An unknown explicit codec raises the typed
`EncodingUnknownError` (machine code `ENCODING_UNKNOWN`, JSON-safe payload
carrying the offending `encoding`) — never a raw Python `LookupError`.

## What dbfbridge does not support

- **CDX definitions/tags are not reconstructed**: structural CDX presence is
  reported, but presence is not authoritative tag metadata; rebuild indexes
  in Visual FoxPro or another index-aware tool.
- **Direct Read memo payloads require VFP/FoxPro FPT**: DBT/SMT companions
  are described (and warned about) but are not Direct Read memo formats.
- **Raw forensic mode has a higher cost**: `iter_records(raw=True)` and
  `iter_raw_records` carry one extra copy of every record image.
- **Reconstruction modifies only the destination/output tree** — never the
  exported source; `overwrite=False` is the default.
- **Direct Read is read-only**: `inspect_table`, `read_schema`,
  `iter_records`, `read_records`, and `iter_raw_records` never create files,
  locks, reports, or partial artifacts, and never modify the source.

## Further reading

- [python-api-examples.md](python-api-examples.md) — copy/paste cookbook for
  all nine stable public operations;
- [tool-server-integration.md](tool-server-integration.md) — service/MCP
  integration guide (bounded paging, JSON boundary, progress/cancellation
  matrix, pinned deployment);
- [api-1.0.md](api-1.0.md) — the normative stable 1.x API contract;
- [migration-1.0.md](migration-1.0.md) — moving from an earlier 0.x release;
- [compatibility-vfp.md](compatibility-vfp.md) — authoritative per-type
  format support;
- [docs/README.md](README.md) — the documentation map.
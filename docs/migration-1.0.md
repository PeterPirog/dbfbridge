# Migrating to the dbfbridge 1.x public API

This guide is for users of earlier dbfbridge 0.x releases moving to the
declared 1.x public API. The stable contract is documented in
[docs/api-1.0.md](api-1.0.md); the authoritative format-support matrix is
[docs/compatibility-vfp.md](compatibility-vfp.md).

The public import stays the same: `import dbfbridge`. The historical
`dbf_bridge` namespace remains a compatibility alias exporting the same
symbols; do not import private modules such as `dbf_bridge.core` —
implementation structure may change between minor releases.

## Direct Read (unchanged, now declared stable)

The read-only core keeps its 0.2/0.3 call forms:

```python
from dbfbridge import (
    inspect_table,
    iter_raw_records,
    iter_records,
    read_records,
    read_schema,
)

info = inspect_table("KLIENCI.DBF")
schema = read_schema("KLIENCI.DBF")

for record in iter_records("KLIENCI.DBF", fields=["KOD", "NAZWA"], memo="inline"):
    print(record.values)

page = read_records("KLIENCI.DBF", offset=0, limit=50)
for record in iter_raw_records("KLIENCI.DBF"):
    print(record.physical_index, record.raw[:8])
```

Reading is source-read-only: no output files, no locks, no runtime network,
no VFP9 requirement. Streaming is O(1) memory and `read_records` is bounded.
Operations accept `progress=` callbacks and `cancel_check=` cooperative
cancellation:

```python
from dbfbridge import iter_records

def show(event):
    print(
        event.operation,
        event.current,
        event.total,
        event.records,
    )

for record in iter_records("KLIENCI.DBF", progress=show, cancel_check=lambda: False):
    ...
```

## Migration / export (unchanged call form)

`export_dbf` still writes the loss-aware JSONL intermediate plus the
requested formats:

```python
from dbfbridge import export_dbf

result = export_dbf("K:/dbf_source", "K:/dbf_output", formats=("jsonl", "csv"))
result.raise_for_errors()
```

## Install profiles (optional extras)

The base installation is minimal (only `dbfread`) and covers Direct Read plus
DBF → JSONL/JSON/CSV export and verification. Everything heavier is opt-in:

| Capability | Install command |
|---|---|
| DBF/FPT reconstruction (`reconstruct_dbf`, `check_conversion_quality`) | `pip install "dbfbridge[write]"` |
| XLSX export and XLSX input reading | `pip install "dbfbridge[xlsx]"` |
| XLSX → DBF reconstruction | `pip install "dbfbridge[write,xlsx]"` |
| Optional accelerators (`orjson`, `polars`) | `pip install "dbfbridge[fast]"` |
| Everything user-facing | `pip install "dbfbridge[all]"` |
| Historical `[import]` extra (compatibility) | `pip install "dbfbridge[import]"` |

The historical `[import]` extra remains an alias of `[write]`. `[fast]` is a
pure accelerator: without it the logical results are identical and its
absence never raises.

A missing optional dependency is a typed failure before any output is
created — classify it by code, never by parsing the message:

```python
from dbfbridge import OptionalDependencyMissingError, reconstruct_dbf

try:
    reconstruct_dbf("output", "rebuilt", overwrite=True)
except OptionalDependencyMissingError as error:
    print(error.to_dict())
    # {"code": "OPTIONAL_DEPENDENCY_MISSING", "dependency": "dbf",
    #  "extra": "write", "operation": "reconstruct_dbf",
    #  "install_command": 'python -m pip install "dbfbridge[write]"',
    #  "purpose": "DBF/FPT reconstruction"}
```

## Raw retention modes (`raw_mode`)

`export_dbf` accepts an explicit raw-retention level
(`RawMode = Literal["none", "metadata", "full-record"]`, default
`"full-record"`):

```python
from dbfbridge import export_dbf

export_dbf(
    source,
    output,
    raw_mode="none",
)
```

- `full-record` (default) — forensic mode: per-record raw physical record
  images + full schema structural metadata (the historical behaviour);
- `metadata` — logical values + full schema metadata, no per-record raw
  record images;
- `none` — logical values + loss-aware text fallback, no raw record images
  and no replay-only physical header blobs in the schema.

All three modes preserve **canonical reconstruction for every supported
case**; only `full-record` additionally guarantees the forensic raw-layout
restoration (`raw_dbf_match`/`raw_fpt_match` are reported separately).
Changing `raw_mode` invalidates the incremental `conversion_checksums.json`
cache identity.

## Structured errors and run results

Every public failure is machine-classifiable by `code` — never by parsing
English text. The vocabulary (`ErrorCode`) is stable for 1.x; new codes may
be added in minor releases. High-level operations return typed run results
with JSON-safe `to_dict()` and raise `DBFBridgeRunError` (machine code +
structured details) from `raise_for_errors()`:

```python
from dbfbridge import OperationError

# per-table failures carry structured details:
# result.results[i].error_details[j].to_dict() ==
# {"code": ..., "message": ..., "operation": ..., "path": ..., "table": ..., "context": ...}
```

Argument validation raises `OperationArgumentError` (still a `ValueError`),
missing paths raise `OperationPathError` (still a `FileNotFoundError`), and
output conflicts raise `OperationOutputExistsError` (still a
`FileExistsError`) — all with the machine code and a JSON-safe payload.

## VFP compatibility and accepted limitations

The compatibility matrix in
[docs/compatibility-vfp.md](compatibility-vfp.md) remains authoritative.
The library reports structural CDX presence but does not parse or reconstruct index tag
expressions. Accepted limitations stay documented:
exact raw Varchar DBF byte identity and exact G/P raw FPT block layout are
reported through `raw_dbf_match`/`raw_fpt_match`, not guaranteed. Q/W
varbinary/blob, binary `V`/`C` variants and the dBASE Level 7 dialect are
outside the supported set, reported with typed statuses. Polish encodings
are explicit and on-demand — `encoding="mazovia"` (and `cp1250`, `cp852`,
`piast`, `pki`) is resolved by the library at operation time; an unknown
explicit codec raises the typed `EncodingUnknownError`.
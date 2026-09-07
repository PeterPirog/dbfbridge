# Using dbfbridge in tool servers and MCP backends

This guide describes transport-neutral patterns for exposing the dbfbridge
public API from **application/service layers**: MCP servers, JSON-RPC
services, agent tools, local service backends, web APIs, and job workers.

> **Scope:** this is an application adapter guide. It documents how a host
> application should use the stable public `dbfbridge` API. It is **not** an
> MCP protocol implementation, and it does not reference any specific
> downstream MCP project.

## 1. Fundamental integration rule

```text
Transport adapter        (MCP / JSON-RPC / HTTP / job queue)
        |
        v
application/service layer        (path policy, authorization, paging limits)
        |
        v
public `dbfbridge` API           (import dbfbridge)
```

**Never:**

```text
MCP handler
        |
        v
dbf_bridge.core / private implementation modules
```

The transport adapter owns:

- tool registration and request validation;
- authorization and data-access policy;
- path policy (see [Path security](#16-path-security-host-responsibility));
- response-envelope shape;
- async scheduling, timeouts, request-cancellation mapping.

dbfbridge owns:

- DBF/FPT parsing (exactly one physical record loop);
- migration/export, schema-driven reconstruction;
- verification and quality diagnostics;
- typed result/error semantics (machine codes + JSON-safe `to_dict()`).

The adapter must stay **thin**: do not reimplement DBF parsing, RawMode
semantics, schema logic, or memo decoding, and never parse English exception
messages. All of that is the library's job.

## 2. Installation for service hosts

A server decides its capabilities at **deployment time**. dbfbridge never
installs missing dependencies at request time — a missing extra is a typed,
fail-before-output error.

| Server capability | Install |
|---|---|
| read/schema/data server | `pip install dbfbridge` |
| read + reconstruction | `pip install "dbfbridge[write]"` |
| XLSX export / XLSX-format reading and verification | `pip install "dbfbridge[xlsx]"` |
| XLSX → DBF/FPT reconstruction | `pip install "dbfbridge[write,xlsx]"` |
| full-feature service | `pip install "dbfbridge[all]"` |

## 3. VFP independence

dbfbridge public operations do **not** require Visual FoxPro, VFP COM
automation, or runtime network access. This holds for both read-only
operations (inspection/schema/records) and operations that create output
copies/reports (export, reconstruction, verification, quality). CDX index
tags are the one boundary: structural CDX presence is reported, but indexes
are not rebuilt (see the format-support guidance in the compatibility section below).

## 4. Import / capability probe (fail-closed)

`import dbfbridge` is side-effect free: it registers no codecs, creates no
files, and loads no CLI/reporting modules or heavy dependencies. A cheap
startup probe uses public metadata only — **never perform a DBF read merely
for service discovery**.

The probe must be **fail-closed** and must separate **four layers**. Direct
Write availability is the fail-closed conjunction of the first three — a
successful `import dbfbridge` alone is never sufficient to declare a
writable backend (DBFB-MCP-009):

1. **API surface available** — the public operation symbols exist on
   `import dbfbridge` (a *derived API fact*);
2. **`[write]` capability configured** — the host *deployment
   configuration* installed/targeted the write profile (a *host
   configuration fact*; the probe never installs, imports `dbf`, or runs a
   destructive test write to discover it);
3. **write exposed by host policy** — the host explicitly decided to
   expose write operations (an *authorization fact* owned by the host);
4. **optional dependency actually usable at operation time** — `[write]`
   provides the physical writer dependency lazily; a configured-but-broken
   environment still fails as a typed `OptionalDependencyMissingError` when
   an operation runs. Configured capability is therefore **not** a
   destructive runtime probe.

```python
import dbfbridge

DIRECT_READ_API = (
    "inspect_table",
    "read_schema",
    "iter_records",
    "read_records",
    "iter_raw_records",
)

WRITE_API = ("write_table", "WriteResult", "DirectWriteError")


def backend_status(
    *,
    write_enabled: bool = False,
    write_capability_configured: bool = False,
):
    # Fail-closed capability model (DBFB-MCP-009).
    # - API facts are DERIVED from public symbols;
    # - deployment/profile capability comes from HOST CONFIGURATION
    #   (defaults fail closed);
    # - authorization/exposure comes from HOST POLICY;
    # - the physical dependency may still fail typed at operation time
    #   (`OptionalDependencyMissingError`) — configured capability is not
    #   verified by a destructive runtime probe.
    direct_read_ok = all(hasattr(dbfbridge, name) for name in DIRECT_READ_API)
    write_api_ok = all(hasattr(dbfbridge, name) for name in WRITE_API)
    direct_write_ok = (
        write_api_ok and write_capability_configured and write_enabled
    )

    return {
        "available": direct_read_ok,
        "version": dbfbridge.__version__,
        "direct_read": direct_read_ok,
        "write_api_available": write_api_ok,
        "write_capability_configured": bool(write_capability_configured),
        "write_enabled_by_policy": bool(write_enabled),
        "direct_write_available": direct_write_ok,
        "public_api": {
            name: hasattr(dbfbridge, name) for name in DIRECT_READ_API
        },
        "write_api": {
            name: hasattr(dbfbridge, name) for name in WRITE_API
        },
    }
```

For write/XLSX capability discovery, do **not** perform destructive test
calls (no test DBF write, no runtime install, no network call). Treat the
deployment configuration / install profile as the capability declaration and
let the operation's typed `OptionalDependencyMissingError` provide the
authoritative runtime failure. dbfbridge intentionally exposes no
capability-registry API — do not invent one.

## 5. Bounded reads at the tool boundary

For a remote/tool boundary, prefer `read_records()` over returning an
unbounded `iter_records()` stream from a single tool call:

```python
from dbfbridge import read_records

def read_table_page(path, *, offset=0, limit=100, fields=None):
    limit = min(limit, 1000)  # HOST POLICY maximum, not a dbfbridge limit
    page = read_records(
        path,
        offset=offset,
        limit=limit,
        fields=fields,
        memo="skip",
    )
    return page.to_dict()
```

Drive paging with `offset` / `limit` / `next_offset` / `exhausted` from
`page.to_dict()`. The server-side page-size cap is **host policy**;
dbfbridge only guarantees that `read_records` is bounded by its `limit`.

## 6. Field projection

When the caller needs only selected columns, pass `fields=[...]`:

- less parsing (unselected fields are never parsed);
- smaller responses and lower serialization cost;
- a lower chance of exposing data the caller did not ask for.

Field projection is **not** an authorization mechanism — data-access policy
remains a host responsibility.

## 7. Memo policy for tool servers

- `memo="skip"` — best for discovery/listing calls where memo content is not
  needed; the FPT is never opened.
- `memo="lazy"` — useful inside local Python code: memo fields are returned
  as `LazyMemoValue` handles. A `LazyMemoValue` is a pointer/reference
  contract, **not** remote memo content — **never serialize it across a
  tool/MCP/JSON transport** (DBFB-MCP-004): it is a local Python handle only
  and carries no payload data.
- `memo="inline"` — when the response explicitly needs memo values.

For large remote results, avoid blindly inlining every memo. Where memo
content is genuinely required remotely, use a separate, explicitly bounded
memo-read policy over the public read APIs (`memo="inline"` with field
projection and a finite page size) instead of transporting handle objects.

## 8. Raw data policy

For ordinary service calls, `raw=False` (Direct Read) and migration exports
with `raw_mode="none"` are generally appropriate when physical forensic bytes
are not required. `raw_mode="full-record"` (the **library default**, which
this guide does not change) serves forensic/raw-layout/round-trip needs and
increases payload and storage cost. This is a host-level recommendation, not
a change to the API contract.

## 9. JSON boundary

Public models expose `to_dict()` as the supported JSON-safe boundary —
`TableInfo`, `TableSchema`, `DirectRecord`, `RecordPage`,
`ExportRunResult`, `ReconstructionRunResult`, `VerificationRunResult`,
`QualityRunResult`, `WriteResult` (v1.1), and every public error. Do not use
`dataclasses.asdict(...)`, `obj.__dict__`, or `repr(obj)` as the integration
contract.

### 9.1 `WriteResult` output schema (v1.1)

`WriteResult.to_dict()` is the intended tool-result body for a write
operation: `destination`, `fpt_path`, `fpt_published`, `records_written`,
`deleted_records`, `structural_cdx`, `index_rebuild_required`, `dbc_bound`,
`dbf_sha256`, `fpt_sha256`, `warnings` — JSON-safe, POSIX paths, and no
record/memo values. The maintained machine-readable example lives in
[docs/schemas/write-result.schema.json](schemas/write-result.schema.json)
and is regression-checked against a real runtime payload
(`tests/test_tool_server_integration_contract.py`). A host tool server may
declare this shape as its output schema (DBFB-MCP-010); it must never
serialize `__dict__`/`repr` instead.

**Intentional serialization exceptions** (frozen runtime contract):

- `TableResult` exposes `to_report_dict()` — not `to_dict()`. For the normal
  integration path, serialize the containing `ExportRunResult.to_dict()`
  (its per-table results are already rendered through `to_report_dict()`).
- `ProgressEvent` is a public typed event object with **no `to_dict()`** —
  hosts serialize its documented public fields themselves (see the progress-bridging section).

`DirectRecord.raw_record` is **bytes** in Python, but
`DirectRecord.to_dict()` serializes it as Base64 — so a tool adapter should
pass `record.to_dict()` to a JSON transport, never the raw Python bytes.

## 10. Run-result and failure policy

High-level operations (`export_dbf`, `reconstruct_dbf`,
`verify_conversion`, `check_conversion_quality`) return complete run
results. Two valid host strategies:

**Strategy A — preserve partial results:**

```python
result = export_dbf("data", "exported", formats=("jsonl",))
return result.to_dict()
```

The transport layer can inspect `exit_code`, `failed`, `warnings`, and the
per-table `error_details`.

**Strategy B — fail the tool when any table failed:**

```python
result = export_dbf("data", "exported", formats=("jsonl",))
result.raise_for_errors()  # raises DBFBridgeRunError
return result.to_dict()
```

Do not treat a warning state (`exit_code == 2`) as identical to a hard
failure — the run result carries both.

### Aggregate success semantics for multi-table runs

`result.ok` is a **count** of OK table results, and `result.failed` is a
count of FAILED table results — `ok > 0` is therefore **not** an aggregate
success signal for a multi-table run (1 OK + 1 FAILED would yield
`ok == 1`). Use the absence of failures:

```python
def export_tool(source: str, output: str) -> dict:
    result = dbfbridge.export_dbf(
        source,
        output,
        formats=("jsonl",),
        raw_mode="none",  # service-friendly raw-retention level (see the raw-data-policy section)
    )
    return {
        "ok": result.failed == 0,
        "exit_code": result.exit_code,
        "data": result.to_dict(),
    }
```

For result types with a different counter layout, rely on the documented
`exit_code` / `successful` property / structured payload of that result type
instead of inventing one uniform attribute. A forensic/round-trip tool can
explicitly request `raw_mode="full-record"` when it needs the physical
images.

## 11. Machine-readable error mapping

Classify failures by **`error.code`**, never by the English message. Use
`to_dict()` on any public exception:

```python
from dbfbridge import DBFBridgeRunError, DirectReadError

def error_payload(exc):
    if hasattr(exc, "to_dict"):
        return exc.to_dict()
    raise exc
```

The error payload **families differ intentionally** (see
[docs/api-1.0.md](api-1.0.md) §4 for the normative contract):

- Direct Read errors: `{code, message, path, context}`;
- high-level `OperationError` family: `{code, message, operation, path, table, context}`;
- `OptionalDependencyMissingError`: `{code, dependency, extra, operation, install_command, purpose?}`;
- `DBFBridgeRunError`: `{code, message, details: [...]}`.

### 11.1 Direct Write error mapping (v1.1)

The write family is mapped by the same rule — `error.code` + `to_dict()`,
never the message text:

```python
import dbfbridge
from dbfbridge import (
    DirectWriteError,
    OperationOutputExistsError,
    OptionalDependencyMissingError,
)

WRITE_ERROR_CODES = frozenset({
    "WRITE_SCHEMA_INVALID",
    "WRITE_FIELD_UNSUPPORTED",
    "WRITE_VALUE_INVALID",
    "WRITE_MEMO_FAILED",
    "WRITE_PUBLICATION_FAILED",
    "WRITE_CANCELLED",
    "DESTINATION_IO_ERROR",
    "OUTPUT_EXISTS",
    "OPTIONAL_DEPENDENCY_MISSING",
})


def write_error_payload(exc: Exception) -> dict:
    # Classify by the structured code; never by regex/startswith on the
    # English message (DBFB-MCP-008).
    if isinstance(exc, (OperationOutputExistsError, OptionalDependencyMissingError)):
        return exc.to_dict()
    if isinstance(exc, DirectWriteError):
        payload = exc.to_dict()
        assert payload["code"] in WRITE_ERROR_CODES
        return payload
    raise exc
```

The reused public codes (`OUTPUT_EXISTS` from `OperationOutputExistsError`,
`OPTIONAL_DEPENDENCY_MISSING`) keep their 1.0 shape; the write-family codes
come from `DirectWriteError.to_dict()` —
`{code, message, path, context}`, JSON-safe, with no record or memo values.
The reused families (`OperationOutputExistsError`:
`{code, message, path, operation, table, context}`,
`OptionalDependencyMissingError`: `{code, dependency, extra, operation,
install_command, purpose?}`) intentionally have their own shapes.

## 12. Complete transport-neutral example

```python
"""Thin application adapter over the public dbfbridge API.

This is an application adapter example. It is NOT an MCP protocol
implementation — a real server maps its own transport (MCP, JSON-RPC, HTTP)
onto these plain JSON-safe dictionaries.
"""

import dbfbridge

DIRECT_READ_API = (
    "inspect_table",
    "read_schema",
    "iter_records",
    "read_records",
    "iter_raw_records",
)

WRITE_API = ("write_table", "WriteResult", "DirectWriteError")


def backend_status(
    *,
    write_enabled: bool = False,
    write_capability_configured: bool = False,
) -> dict:
    # Fail-closed: the direct-read fact is DERIVED from public symbols; the
    # writable capability is the fail-closed conjunction of API presence,
    # host configuration and host policy (see the capability-probe section).
    direct_read_ok = all(hasattr(dbfbridge, name) for name in DIRECT_READ_API)
    write_api_ok = all(hasattr(dbfbridge, name) for name in WRITE_API)
    direct_write_ok = (
        write_api_ok and write_capability_configured and write_enabled
    )
    return {
        "available": direct_read_ok,
        "version": dbfbridge.__version__,
        "direct_read": direct_read_ok,
        "write_api_available": write_api_ok,
        "write_capability_configured": bool(write_capability_configured),
        "write_enabled_by_policy": bool(write_enabled),
        "direct_write_available": direct_write_ok,
        "public_api": {
            name: hasattr(dbfbridge, name) for name in DIRECT_READ_API
        },
        "write_api": {
            name: hasattr(dbfbridge, name) for name in WRITE_API
        },
    }


def inspect_table_tool(path: str) -> dict:
    info = dbfbridge.inspect_table(path)
    return {"ok": True, "data": info.to_dict()}


def read_table_page_tool(path: str, *, offset: int = 0, limit: int = 100,
                         fields: list[str] | None = None) -> dict:
    try:
        page = dbfbridge.read_records(
            path,
            offset=offset,
            limit=min(limit, 1000),  # host policy
            fields=fields,
            memo="skip",
        )
        return {"ok": True, "data": page.to_dict()}
    except dbfbridge.DirectReadError as exc:
        return {"ok": False, "error": exc.to_dict()}


def export_tool(source: str, output: str) -> dict:
    result = dbfbridge.export_dbf(
        source,
        output,
        formats=("jsonl",),
        raw_mode="none",  # service-friendly; a forensic tool may use "full-record"
    )
    # `ok` must be the ABSENCE OF FAILURES, never `ok > 0`:
    # result.ok is a COUNT of OK tables, so 1 OK + 1 FAILED would yield 1.
    return {"ok": result.failed == 0, "exit_code": result.exit_code,
            "data": result.to_dict()}


def reconstruct_tool(source: str, output: str) -> dict:
    result = dbfbridge.reconstruct_dbf(source, output, input_format="jsonl")
    return {"ok": result.failed == 0, "exit_code": result.exit_code,
            "data": result.to_dict()}
```

Everything above imports only `dbfbridge` / `from dbfbridge import ...` and
returns plain JSON-safe dictionaries.

## 13. Synchronous API / async host

dbfbridge API calls are **synchronous filesystem operations**. The library
creates no event loops, threads, background workers, or global request
state. If the hosting MCP/web framework is asynchronous, **the host owns
thread/process offloading and scheduling** (for example running blocking
calls in a worker pool). This guide does not claim universal thread safety
for the library and dbfbridge intentionally contains no asyncio.

## 14. Cancellation bridging

Direct Read operations accept `cancel_check: Callable[[], bool]`. A hosting
server can map its request-cancellation state into that callable:

```python
cancelled = False

def should_cancel():
    return cancelled

for record in iter_records(path, cancel_check=should_cancel):
    ...
```

When the callable returns `True`, the read stops at the next record boundary
and raises `ReadCancelledError` (`READ_CANCELLED`) — a normal,
machine-classifiable outcome carrying the resume context. The nine high-level
1.0 operations (`export_dbf`, `reconstruct_dbf`, …) do not expose
`cancel_check`; do not invent cancellation for them.

**Direct Write (v1.1) is the exception by contract:** `write_table()` accepts
`cancel_check` and `progress`. The host maps its request-cancellation state
into the callable; a cooperative cancellation stops at a record boundary or
before publication, raises `WriteCancelledError` (`WRITE_CANCELLED`), cleans
staging/spool, and **publishes nothing** — the previous pair stays intact
under `overwrite=True`:

```python
from dbfbridge import WriteCancelledError, write_table

cancelled = False


def write_bounded(destination, schema, records, *, cancel_check, progress):
    try:
        return write_table(
            destination,
            schema=schema,
            records=records,
            cancel_check=cancel_check,  # host maps request cancellation here
            progress=progress,
        )
    except WriteCancelledError as exc:
        # normal, machine-classifiable outcome: nothing was published
        return {"ok": False, "error": exc.to_dict()}
```

## 15. Progress bridging

Direct Read and long-running operations accept `progress=` callbacks
receiving `ProgressEvent` objects with the public fields
`operation`, `current`, `total`, `table`, `format`, `records`, `message`:

```python
def progress_payload(event):
    """Host-side serializer for one progress event.

    `ProgressEvent` is a public typed event object, but unlike the documented
    result/error models it currently does NOT expose a `to_dict()` method.
    The adapter owns the conversion of the event into its transport
    notification shape."""
    return {
        "operation": event.operation,
        "current": event.current,
        "total": event.total,
        "table": event.table,
        "format": event.format,
        "records": event.records,
        "message": event.message,
    }


def on_progress(event):
    queue_or_transport_progress(progress_payload(event))
```

The hosting adapter may map `ProgressEvent` to its own
progress/notification mechanism (for example an MCP progress notification);
dbfbridge does not assume any protocol-specific progress API. Direct Write
(v1.1) emits the same canonical `ProgressEvent` objects with
`operation="write"` — the same host-side serializer above covers them; no
second progress system exists.

## 16. Path security (host responsibility)

dbfbridge accepts filesystem paths. It is **not an authorization sandbox**.
The hosting server **must** define and enforce policies such as:

- allowed read roots;
- allowed output roots / workspace roots;
- maximum page size;
- allowed file extensions and operations;
- overwrite policy;
- authentication and authorization.

Canonicalize paths in the host and reject path traversal or
symlink/junction escapes according to the deployment model. Do not assume
that dbfbridge itself implements any of these server policies.

## 17. Source immutability vs write operations

### 17.1 Direct Write capability (v1.1, opt-in and host-controlled)

Since the v1.1 contract the public `write_table()` operation exists (see
`docs/api-1.1.md`). Exposing it through a tool server is a **host decision**;
the library imposes no transport and no default authorization:

- **opt-in** - do not register a write tool unless the deployment wants one;
  read-only servers simply never expose it;
- **host-controlled authorization** - the host decides which callers may
  write and under which policy;
- **path allowlists** - the host validates destinations (and staging
  directories) against its own allowlist; the library only enforces the
  same-volume atomicity rule for `staging_directory`;
- **source != destination** - Direct Write never reads or modifies a source
  table (the source is not even an argument); hosts should still reject
  destination paths that alias protected locations; for a copy/transform
  workflow the host **must** reject a destination that resolves (after
  canonicalization) to a protected source location **even when the caller
  requests `overwrite=True`** (DBFB-MCP-007) — overwrite policy and
  source-immutability policy are two separate host decisions;
- **overwrite policy** - `overwrite` defaults to `False` and returns the
  stable `OUTPUT_EXISTS` code; a host may hard-code it to `False` for
  append-only workflows;
- **bounded workflows** - cap the record stream (the iterable is consumed
  exactly once and streamed; flat tables are O(1)/O(batch) memory, and the
  Varchar/`_NullFlags` second pass is fed from a bounded private spool);
- **classify errors by `.code`** - map `DirectWriteError` subclasses and
  their structured codes (`WRITE_*`, `DESTINATION_IO_ERROR`,
  `WRITE_CANCELLED`, reused `OUTPUT_EXISTS` /
  `OPTIONAL_DEPENDENCY_MISSING`) to your transport's error objects; never
  parse the message text;
- **serialize `WriteResult.to_dict()`** - the JSON-safe payload (POSIX
  paths, counters, final SHA-256 digests, warnings list) is the intended
  tool-result body.

### 17.2 Bounded Direct Write workflow (host patterns)

`write_table` consumes its records iterable **exactly once** and streams it
(flat tables O(1)/O(batch); the Varchar/`_NullFlags` second pass replays a
bounded private spool). That contract is what makes safe host integration
possible — and it imposes one host rule:

> **Never accept an unbounded record array as ONE tool argument**
> (DBFB-MCP-006). A server adapter must not take millions of records as a
> single JSON/RPC list and hand `list(records)` to `write_table`.

Two transport-neutral host patterns — both keep the library API unchanged:

**Pattern A — service-layer stream (the host creates the iterator):**

```python
from dbfbridge import write_table

MAX_RECORDS_PER_WRITE = 50_000  # HOST POLICY, not a dbfbridge limit


def write_from_host_stream(schema, bounded_batches, *, destination):
    """The host already owns bounded batches; it yields ONE iterator that
    `write_table` consumes exactly once."""
    def records():
        produced = 0
        for batch in bounded_batches(bounded_batches_cap=10_000):
            for row in batch:
                if produced >= MAX_RECORDS_PER_WRITE:
                    raise RuntimeError("host record cap exceeded")
                produced += 1
                yield row

    return write_table(destination, schema=schema, records=records())


def bounded_batches(bounded_batches_cap):
    """Host-owned bounded source (request chunks, queue, files) — placeholder
    for the host's own transport; the library never sees the transport."""
    yield ()
```

**Pattern B — bounded job/workspace input** (for RPC/MCP hosts where one
request cannot stream records): the host prepares/uploads records into a
job/workspace input **under host control** (size-capped), then creates the
iterator over that bounded input and calls `write_table` inside its own
worker:

```python
from dbfbridge import write_table


def run_write_job(schema, job, *, destination, progress=None):
    """`job.records_path` is a bounded host-managed input file created before
    this job started; `iter_job_rows` is host-owned. dbfbridge receives a
    plain iterable — no JSONL transport, no spool on the caller side."""
    def records():
        for row in iter_job_rows(job.records_path):
            yield row

    return write_table(
        destination,
        schema=schema,
        records=records(),
        overwrite=False,
        progress=progress,
        cancel_check=job.cancel_check,
    )
```

Two boundaries this must not cross:

- **Direct Write does not require JSONL** — `write_table` takes any iterable
  of `DirectRecord`/mapping objects; do not introduce a mandatory JSONL
  conversion in front of it and do not add a transport spool to the library;
- **the iterable is consumed exactly once** — a host iterator must be a
  fresh, single-pass object per call (one-shot generators are supported).

### 17.3 Host request example (NOT an MCP protocol definition)

A generic host-side request payload for a write action is a host
integration example — it is **not** a dbfbridge public API, **not** an MCP
protocol object, and never embeds an unbounded record array:

```json
{
  "tool": "dbf_write_table",
  "destination": "exports/2024/klienci-copy.dbf",
  "overwrite": false,
  "staging_directory": "exports/2024/.staging",
  "schema_ref": "jobs/42/klienci.schema.json",
  "input_ref": "jobs/42/records.part-0001",
  "input_record_cap": 50000
}
```

Host responsibilities implied by this shape: validate and authorize the
request, resolve `destination` and `staging_directory` against the allowed
roots (same filesystem/volume for staging — the library refuses otherwise),
load the typed schema the host planned, stream the bounded input as the
records iterable, and surface `WriteResult.to_dict()` as the response
payload.

## 17a. Source immutability vs write operations (historical note)

**Source-read-only** (never create outputs, never touch source bytes):

- `inspect_table`, `read_schema`, `iter_records`, `read_records`,
  `iter_raw_records`.

**Write outputs / reports** (to the declared output/report locations):

- `export_dbf`, `reconstruct_dbf`, `check_conversion_quality`;
- `verify_conversion` writes a report only when `write_report=True`.

"Write operation" is not the same as "source mutation": dbfbridge writes
only to its declared output/report locations and never mutates its sources.
For a tool server, expose write-capable operations as **explicit
tools/actions** (never as read-only resources), require the caller to
provide a separate output path, and never infer write permission merely
because an OS path is writable.

## 18. Operation / side-effect matrix for server authors

| Operation | Typical server role | Install profile | Source mutation | Writes output/report | Bounded/streaming | JSON result boundary |
|---|---|---|---|---|---|---|
| `inspect_table` | table overview / discovery | base | none | no | O(header) | `TableInfo.to_dict()` |
| `read_schema` | full schema metadata | base | none | no | O(fields) | `TableSchema.to_dict()` |
| `iter_records` | local streaming read | base | none | no | streaming O(1) | `DirectRecord.to_dict()` |
| `read_records` | preferred bounded remote/tool call | base | none | no | O(limit) page | `RecordPage.to_dict()` |
| `iter_raw_records` | forensic stream | base | none | no | streaming, never opens the FPT | `DirectRecord.to_dict()` (Base64 raw) |
| `export_dbf` | migration/export action | base (+`[xlsx]` for XLSX) | none | yes (JSONL/JSON/CSV/XLSX + schema + reports) | streams; per-table results | `ExportRunResult.to_dict()` |
| `reconstruct_dbf` | reconstruction action | `[write]` (+`[xlsx]` for XLSX input) | none | yes (DBF/FPT + report) | per-table results | `ReconstructionRunResult.to_dict()` |
| `verify_conversion` | consistency check | base (+`[xlsx]` for XLSX) | none | **only when `write_report=True`** | per-file checks | `VerificationRunResult.to_dict()` |
| `check_conversion_quality` | diagnostic round-trip action | `[write]` | none | yes (retained workspace) | per-table | `QualityRunResult.to_dict()` |
| `write_table` *(v1.1)* | **host opt-in** write action | `[write]` | none | yes (fresh DBF/FPT pair) | iterable consumed exactly once; flat O(1)/O(batch) | `WriteResult.to_dict()` ([schema](schemas/write-result.schema.json)) |

Server-authors' notes:

- `verify_conversion(write_report=False)` writes **no** verification report
  (the response payload is the report);
- `read_records` is the preferred bounded remote/tool call;
  `iter_records` is a local streaming API — do not map one unbounded iterator
  to a single remote response;
- resource-vs-tool guidance (transport-neutral, no specific framework):
  read-only schema/page operations suit read tools or read-only resource
  implementations; write/report operations must be explicit tools/actions.

## 19. Request-scope object ownership

Do not share across unrelated requests:

- open `iter_records` iterators;
- `LazyMemoValue` objects;
- mutable request-cancellation state.

`read_records()` avoids long-lived iterator ownership and is therefore
preferred for bounded RPC/tool calls. This guide makes no global
thread-safety claims for the library — the host owns offloading and
request isolation (see the synchronous-API section).

## 20. Format support for server authors

The authoritative per-type support matrix is
[docs/compatibility-vfp.md](compatibility-vfp.md). Two rules for server
authors:

- **A host must not infer semantic support merely because raw bytes are
  readable.** `iter_raw_records()` may expose physical records (including
  unsupported/undecoded field types) without pretending semantic decoding
  support — forensic raw access is exactly that.
- For unsupported decoded field types, use the documented typed error /
  compatibility classification (`FIELD_TYPE_UNSUPPORTED`, per-table
  `UNSUPPORTED` status). Do not invent a decoder inside the adapter.

## 21. CDX limitation in server integration

dbfbridge **reports structural CDX presence**. It does **not** provide an
authoritative CDX tag/expression engine and does **not** rebuild CDX
indexes. A system that modifies indexed DBF data must use a separate
index-aware/VFP-capable layer where valid CDX output is required — do not
present a copied/stale CDX file as valid after changing indexed data.

## 22. Offline / pinned deployment with provenance

Normal service deployment is a **pinned package installation**:

```bash
python -m pip install "dbfbridge==<version>"
```

For a controlled offline/vendored deployment, install a complete pinned
wheel/wheelhouse — never copy implementation modules:

- during **deployment preparation** (never from a request handler): build or
  download the wheel; record version, hash, license, and upstream
  provenance; install that exact wheel into the service environment;
- import **only** `dbfbridge`;
- a request handler must never run `pip install`, `git clone`, or fetch
  "latest" at runtime.

**Fail-closed provenance policy (host-implemented).** For a pinned/offline
deployment the host SHOULD verify, at service startup:

- the expected dbfbridge version (`dbfbridge.__version__`);
- the artifact hash / provenance recorded at deployment preparation;
- the loaded module origin (`dbfbridge.__file__`) points at the intended
  environment/vendor location.

If provenance cannot be verified, the backend availability check should fail
closed (report the backend unavailable). This is generic deployment guidance
implemented by the host — dbfbridge intentionally exposes no provenance API.

Never copy individual `dbf_bridge.core` files, fork private parser modules
into the host, or treat private modules as a stable contract — that
guarantees architectural drift when dbfbridge evolves.

## 23. Adapter anti-drift rules

The adapter must remain thin:

- reimplementing DBF parsing: **NO**;
- reimplementing RawMode semantics: **NO**;
- parsing English exception messages: **NO** (classify by `code`);
- duplicating schema or memo decoding logic: **NO**;
- inventing a decoder for unsupported field types: **NO** (use the typed
  compatibility classification — see §20);
- importing MCP/JSON-RPC/HTTP protocol SDKs or session/token state INTO
  dbfbridge: **NO** (DBFB-MCP-011 / DBFB-NOGO-011 — the library stays
  transport-neutral; protocol state lives in the host adapter only).

All DBF/FPT domain knowledge stays inside dbfbridge; the transport owns only
transport concerns.

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
- typed result/error semantics with machine codes and documented JSON-safe
  serialization boundaries.

The adapter must stay **thin**: do not reimplement DBF parsing, RawMode
semantics, schema logic, or memo decoding, and never parse English exception
messages. All of that is the library's job.

## Production integration at a glance

The operating model in one table (consolidated from the detailed sections
below; the full per-operation matrix is in
[Operation / side-effect matrix](#18-operation-side-effect-matrix-for-server-authors)):

| Use case | Public API | Install | Bounded? | Writes? | JSON boundary | Recommended server exposure |
|---|---|---|---|---|---|---|
| discovery / listing | `inspect_table` | base | O(header) | no | `TableInfo.to_dict()` | read tool or read-only resource |
| schema metadata | `read_schema` | base | O(fields) | no | `TableSchema.to_dict()` | read tool or read-only resource |
| bounded page read | `read_records` | base | O(limit) | no | `RecordPage.to_dict()` | read tool (preferred remote read) |
| local streaming read | `iter_records` | base | streaming O(1) | no | `DirectRecord.to_dict()` | local streaming (not one remote response) |
| forensic byte access | `iter_raw_records` | base | streaming, no FPT | no | `DirectRecord.to_dict()` (Base64 raw) | read tool / admin-only exposure |
| migration export | `export_dbf` | base (+`[xlsx]` for XLSX) | streams per table | **yes** — explicit action | `ExportRunResult.to_dict()` | explicit tool/action + output authorization |
| reconstruction | `reconstruct_dbf` | `[write]` (+`[xlsx]` for XLSX input) | per-table | **yes** — explicit tool/action + output authorization | `ReconstructionRunResult.to_dict()` | explicit tool/action + output authorization |
| verification | `verify_conversion` | base (+`[xlsx]` for XLSX) | per-file | only with `write_report=True` | `VerificationRunResult.to_dict()` | read-type tool (report optional) |
| diagnostic round trip | `check_conversion_quality` | `[write]` | per-table | **yes** — retained workspace | `QualityRunResult.to_dict()` | explicit tool/action + output authorization |

Progress/cancellation availability is NOT uniform — the exact matrix
(enforced against the runtime signatures) is in the
[progress-bridging section](#15-progress-bridging). Serialization has three
intentional cases — normal result/error objects expose `to_dict()`, a
standalone `TableResult` exposes `to_report_dict()`, and `ProgressEvent` is
serialized by an explicit host-side serializer (see the
[JSON boundary](#9-json-boundary) and [progress bridging](#15-progress-bridging)).

> **Direct Write firewall.** Direct Write / `write_table` is
> experimental/unreleased next-version research and is **NOT** part of the
> current stable 1.x public API. Production adapters must not depend on or
> expose it until a future public contract explicitly promotes it — the
> stable surface is the nine documented operations above.

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

The probe must be **fail-closed**: a transport/service adapter must not
report a backend as available merely because `import dbfbridge` succeeded if
the public operations it requires are missing.

```python
import dbfbridge

DIRECT_READ_API = (
    "inspect_table",
    "read_schema",
    "iter_records",
    "read_records",
    "iter_raw_records",
)


def backend_status():
    direct_read_ok = all(hasattr(dbfbridge, name) for name in DIRECT_READ_API)

    return {
        "available": direct_read_ok,
        "version": dbfbridge.__version__,
        "direct_read": direct_read_ok,
        "public_api": {
            name: hasattr(dbfbridge, name) for name in DIRECT_READ_API
        },
    }
```

For write/XLSX capability discovery, do **not** perform destructive test
calls. Treat the deployment configuration / install profile as the capability
declaration and let the operation's typed `OptionalDependencyMissingError`
provide the authoritative runtime failure. dbfbridge intentionally exposes no
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
  contract, **not** remote memo content — do not send the handle object over
  a transport.
- `memo="inline"` — when the response explicitly needs memo values.

For large remote results, avoid blindly inlining every memo.

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
`QualityRunResult`, and every public error. Do not use
`dataclasses.asdict(...)`, `obj.__dict__`, or `repr(obj)` as the integration
contract.

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


def backend_status() -> dict:
    # Fail-closed: availability is DERIVED, never hardcoded.
    direct_read_ok = all(hasattr(dbfbridge, name) for name in DIRECT_READ_API)
    return {
        "available": direct_read_ok,
        "version": dbfbridge.__version__,
        "direct_read": direct_read_ok,
        "public_api": {
            name: hasattr(dbfbridge, name) for name in DIRECT_READ_API
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
machine-classifiable outcome carrying the resume context. High-level write
operations do not expose `cancel_check`; do not invent cancellation for them.

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
dbfbridge does not assume any protocol-specific progress API.

### Progress and cancellation capability matrix

Verified against the current public signatures (narrow regressions protect
it). `NO` means the parameter does not exist on that operation — do not pass
it and do not simulate it in the adapter:

| Operation | `progress=` | `cancel_check=` |
|---|---|---|
| `inspect_table` | NO | NO |
| `read_schema` | NO | NO |
| `iter_records` | YES | YES |
| `read_records` | YES | YES |
| `iter_raw_records` | YES | YES |
| `export_dbf` | YES | NO |
| `reconstruct_dbf` | YES | NO |
| `verify_conversion` | NO | NO |
| `check_conversion_quality` | YES | NO |

Cancellation is read-side only (`ReadCancelledError` at a physical record
boundary); the high-level write operations report progress but expose no
`cancel_check` — do not invent cancellation for them.

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

Deployment states differ; document both explicitly.

**After an official PyPI publication** the normal pinned deployment is:

```bash
python -m pip install "dbfbridge==X.Y.Z"
```

**Controlled pre-publication / offline validation** (also the honest state
today: the historical v0.2.0 GitHub Release exists, but its PyPI publication
did not complete successfully, so this guide does not claim that any
specific version is currently downloadable from PyPI): install an exact,
trusted wheel instead — never a "latest" fetch:

```bash
python -m pip install /trusted/wheelhouse/dbfbridge-X.Y.Z-py3-none-any.whl
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
  compatibility classification — see §20).

All DBF/FPT domain knowledge stays inside dbfbridge; the transport owns only
transport concerns.

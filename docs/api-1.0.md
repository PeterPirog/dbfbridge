# dbfbridge 1.0 Public API Contract

This document declares the **stable public API and guarantees** for the dbfbridge
1.x line. It is the repository-side closure of architecture blocker **BLK-03 —
1.0 API CONTRACT DECLARATION** (see `docs/architecture-closure.md`) under the
immutable architecture contract `DBFBRIDGE_TARGET_ARCHITECTURE(20260904-052528).md`.

The authoritative format-support matrix is `docs/compatibility-vfp.md`; this
document does not extend, weaken, or reinterpret it.

Everything below is already implemented and regression-proven — this document
declares it stable; it introduces no new behaviour.

## 1. Import boundary

```python
import dbfbridge
```

is the **preferred public boundary**. Every stable symbol listed in this
document is importable from `dbfbridge` and is declared in
`dbfbridge.__all__`.

`dbf_bridge` is the historical package name and remains a **compatibility
alias**: it exposes the same symbols with the same semantics for the 1.x line
via a lazy compatibility module sharing the same implementation — there is no
duplicate DBF/FPT engine behind it. New code should use `dbfbridge`.

Consumers — including a future transport-neutral MCP backend — never need to
import `dbf_bridge.core.*`, `dbf_bridge.exporter.*`, or
`dbf_bridge.importer.*` to perform any supported public operation. Those
modules are implementation structure, not public API, and may change in any
minor release.

`import dbfbridge` has no side effects: it registers no codecs, creates no
files, loads no CLI/reporting modules and no optional heavy dependencies.

## 2. Stable public operations

These nine operations form the frozen 1.x surface. The existing parameters,
their positional/keyword kinds and the documented defaults below form the
**1.0 compatibility baseline** (`str` or `os.PathLike` accepted wherever a
path is taken). Backward-compatible evolution in a MINOR release means:
new **optional keyword-only** parameters may be added; existing parameters
are never removed, never change kind incompatibly, and their documented
defaults never change incompatibly.

### Direct Read (read-only)

| Operation | Signature (essentials) | Result | Side effects |
|---|---|---|---|
| `inspect_table(path)` | — | `TableInfo` | none |
| `read_schema(path)` | — | `TableSchema` | none |
| `iter_records(path, *, fields=None, include_deleted=False, memo="lazy", raw=False, encoding="auto", decode_errors="strict", progress=None, cancel_check=None)` | streaming | iterator of `DirectRecord` | none |
| `read_records(path, *, offset=0, limit=100, fields=None, include_deleted=False, memo="lazy", raw=False, encoding="auto", decode_errors="strict", progress=None, cancel_check=None)` | bounded | `RecordPage` | none |
| `iter_raw_records(path, *, progress=None, cancel_check=None)` | forensic | iterator of `DirectRecord` | none |

Direct Read guarantees (declared stable for 1.x):

- READ is **source-read-only**: no output files, no locks, no `.partial`
  artifacts, no directory creation, no index rebuild; the source file bytes
  and modification time are unchanged;
- no runtime network and no VFP9/COM requirement (pure Python + `dbfread`);
- `iter_records` streams with O(1) memory; `read_records(offset, limit)` is
  O(limit) and never materializes the table;
- field projection is validated case-insensitively, preserves caller order,
  never parses unselected fields, and rejects unknown/duplicate names
  (`FIELD_PROJECTION_INVALID`) and unsupported selected types
  (`FIELD_TYPE_UNSUPPORTED`);
- memo policies `lazy` / `skip` / `null` / `inline`: only `inline` touches the
  FPT (`missing` → `FPT_REQUIRED_MISSING`, broken → `FPT_INVALID`); `lazy`
  returns `LazyMemoValue` with no FPT I/O until an explicit `load()`;
- deleted records are skipped or included **within the same single physical
  pass**; `iter_raw_records` yields every record in physical order and never
  opens the FPT;
- errors are structured and machine-readable (see §4); cancellation is
  cooperative through `cancel_check` and raises `ReadCancelledError`
  (`READ_CANCELLED`) at a record boundary.

### Migration / verification / reconstruction / quality

| Operation | Result | Optional dependency | Output behaviour |
|---|---|---|---|
| `export_dbf(source, output, *, formats=None, memo=None, strip_spaces=False, encoding="auto", decode_errors="strict", deleted="skip", missing_memo="fail", overwrite=True, validate=True, xlsx_long_text="overflow", incremental=False, raw_mode="full-record", progress=None, options=None)` | `ExportRunResult` | `[xlsx]` for XLSX output (typed, fail-before-output) | creates `<table>.(jsonl/json/csv/xlsx)` + `<table>_schema.json` + reports; atomic `.partial` + `os.replace`; per-table failures reported, never raised by default |
| `reconstruct_dbf(source, output, *, input_format="jsonl", memo="inline", overwrite=False, progress=None, options=None)` | `ReconstructionRunResult` | `[write]` for JSONL/JSON/CSV input; `[write,xlsx]` for XLSX input | schema-driven DBF/FPT reconstruction; atomic publish; failure leaves no published output and no staging residue; canonical verification per table |
| `verify_conversion(source, output, *, formats=..., strict=True, report=None, write_report=True, verbose=False)` | `VerificationRunResult` | xlsx check for xlsx formats | inspects the existing migration output tree; writes the JSON verification report **only when `write_report=True`** (default) — with `write_report=False` the result is returned in memory and no report file is created |
| `check_conversion_quality(source, output, *, overwrite=False, max_differences=20, progress=None)` | `QualityRunResult` | `[write]` | retained DBF → JSONL → DBF diagnostic round trip |

Run results expose `ok`/`failed`/`warnings`/`exit_code` accessors and
`raise_for_errors()`, which raises `DBFBridgeRunError` (machine code +
structured details) when a run contains failures. Operations are silent by
default; progress is delivered exclusively through `ProgressCallback`
callbacks (`ProgressEvent`).

## 3. RawMode contract (migration raw-retention levels)

`RawMode = Literal["none", "metadata", "full-record"]`, exported from
`dbfbridge`. Default: **`full-record`**.

| Feature | `none` | `metadata` | `full-record` (default) |
|---|---|---|---|
| logical values | yes | yes | yes |
| schema (logical field/type/length/encoding facts, descriptors, checksums) | yes | yes | yes |
| schema structural metadata (`dbf.header_base64`, `memo.header_base64`) | omitted | yes | yes |
| raw record image (`__dbfbridge_raw_record__`) | no | no | yes |
| loss-aware raw-text fallback (`__dbfbridge_raw_text_fields__`) | yes | yes | yes |
| binary-memo marker (`__dbfbridge_binary_memo_fields__`) | yes | yes | yes |
| canonical reconstruction | **yes — all supported cases** | **yes — all supported cases** | **yes — all supported cases** |
| raw physical reconstruction (`raw_layout_restored`) | no | no | yes |
| byte-identical physical layout | not guaranteed | not guaranteed | best forensic guarantee |

The loss-aware text-fallback and binary-memo markers are logical/canonical
aids (not physical record images) and are retained in every mode. `raw_mode`
participates in the incremental cache identity (`conversion_checksums.json`):
a mode change invalidates cached reuse.

## 4. Structured error contract

Consumers classify failures **by machine code, never by parsing the English
message**. `ErrorCode` values are stable 1.x machine codes:

```text
PATH_NOT_FOUND            DBF_FORMAT_UNSUPPORTED    READ_CANCELLED
DBF_HEADER_INVALID        DBF_TRUNCATED             DBF_IO_ERROR
DBF_RECORD_INVALID        ENCODING_UNKNOWN          TEXT_DECODE_ERROR
FPT_REQUIRED_MISSING      FPT_INVALID               FIELD_TYPE_UNSUPPORTED
FIELD_PROJECTION_INVALID  ARGUMENT_INVALID          OPERATION_FAILED
OPTIONAL_DEPENDENCY_MISSING                          OUTPUT_EXISTS
RECONSTRUCTION_FAILED     ROUNDTRIP_MISMATCH
```

Compatibility rules for the 1.x line:

- **removing, renaming, or repurposing an existing machine code is a breaking
  (major) API change**; the codes listed above form the protected **1.0
  stability baseline** — every one of them must remain available;
- **adding a new code is an additive (minor) change** — the vocabulary is not
  a closed set; consumers must treat unknown codes as
  `OPERATION_FAILED`-class failures;
- uniform machine classification is the contract, **not identical
  dictionaries**: each payload family serializes its own documented keys (see
  below), all JSON-safe, with POSIX-normalized paths.

### Error payload families (exact, verified against the runtime)

**1. Direct Read typed errors** — `DirectReadError` and its family
(`DbfPathError`, `DbfHeaderInvalidError`, `DbfTruncatedError`,
`DbfFormatUnsupportedError`, `DbfIoError`, `DbfRecordInvalidError`,
`EncodingUnknownError`, `TextDecodeError`, `FptRequiredMissingError`,
`FptInvalidError`, `ArgumentInvalidError`, `FieldProjectionInvalidError`,
`FieldTypeUnsupportedError`, `ReadCancelledError`):

```text
to_dict() → {code, message, path, context}
```

**2. High-level operation errors** — `OperationError` and the typed
public-boundary exceptions (`OperationArgumentError`, `OperationPathError`,
`OperationOutputExistsError`), plus every entry inside
`DBFBridgeRunError.details`:

```text
to_dict() → {code, message, operation, path, table, context}
```

**3. `OptionalDependencyMissingError`** — operation-specific payload:

```text
to_dict() → {code, dependency, extra, operation, install_command, purpose?}
```

(`purpose` is present when provided; it is intentionally **not** forced into
the `OperationError` shape — the dependency and install-command facts are the
useful contract here.)

**4. `DBFBridgeRunError`** — the run aggregate:

```text
to_dict() → {code, message, details: [<OperationError payload>...]}
```

`.code` is the primary (first) structured detail's code, else
`OPERATION_FAILED`; `.result` carries the original run result object.

All four families satisfy the one common requirement: **public failures are
machine-classifiable by `code` (or `to_dict()["code"]`) without parsing the
English message.**

Public exception families (all superclass-compatible, tested):

| Class | Inherits | Code | Raised when |
|---|---|---|---|
| `DirectReadError` family (`DbfPathError`, `DbfHeaderInvalidError`, `DbfTruncatedError`, `DbfFormatUnsupportedError`, `DbfIoError`, `DbfRecordInvalidError`, `EncodingUnknownError`, `TextDecodeError`, `FptRequiredMissingError`, `FptInvalidError`, `ArgumentInvalidError`, `FieldProjectionInvalidError`, `FieldTypeUnsupportedError`, `ReadCancelledError`) | `ValueError` (via `DirectReadError`) | its `ErrorCode` | Direct Read boundary |
| `OperationArgumentError` | `ValueError` | `ARGUMENT_INVALID` | public operation argument validation |
| `OperationPathError` | `FileNotFoundError` | `PATH_NOT_FOUND` | public operation missing paths |
| `OperationOutputExistsError` | `FileExistsError` | `OUTPUT_EXISTS` | write refused to overwrite an existing output |
| `OptionalDependencyMissingError` | `RuntimeError` | `OPTIONAL_DEPENDENCY_MISSING` | an optional extra is missing (fail-before-output; never for the `[fast]` accelerator) |
| `DBFBridgeRunError` | `RuntimeError` | primary detail code (else `OPERATION_FAILED`) | `raise_for_errors()` on a run with failures; carries `.result` and ALL underlying `OperationError` details |

## 5. JSON result boundary

The documented result objects expose JSON-safe `to_dict()`:
`TableInfo`, `TableSchema`, `FieldInfo`, `DirectRecord`, `RecordPage`,
`ReconstructionResult`, `ExportRunResult`, `ReconstructionRunResult`,
`VerificationRunResult`, `QualityRunResult`, `FileCheck`, `TableCheck`,
`OperationError`, every `DirectReadError` family member,
`OptionalDependencyMissingError`, and `DBFBridgeRunError`.

**Intentional serialization exceptions** (frozen runtime contract):

- `TableResult` exposes **`to_report_dict()`** — not `to_dict()`. For the
  normal integration path, serialize the containing
  `ExportRunResult.to_dict()` (its per-table results are already rendered
  through `to_report_dict()`); use `TableResult.to_report_dict()` directly
  only when serializing that one object.
- `ProgressEvent` is a public **typed event object** and has **no
  `to_dict()`** — hosts serialize its documented public fields
  (`operation`, `current`, `total`, `table`, `format`, `records`,
  `message`) themselves.

1.x key policy: **existing documented keys are not removed or repurposed
without a major version; new additive keys may appear**. No stronger key
immutability is promised. Paths serialize as POSIX strings; tuples as arrays;
structured errors as dictionaries.

## 6. Progress and cancellation contract

`ProgressCallback = Callable[[ProgressEvent], None]` — silent by default,
`ProgressEvent` carries the operation, table, record index, and
record-count context. `CancellationCheck = Callable[[], bool]` — cooperative,
checked at record boundaries before the next physical record is read;
cancellation raises `ReadCancelledError` (`READ_CANCELLED`) with the resume
context (`offset`, `next_physical_index`, `scanned`, `yielded`,
`record_count`).

## 7. Side-effect guarantees

- Direct Read: source-read-only, byte-identical source SHA before == after,
  no outputs, no temp/lock/partial artifacts.
- Migration/reconstruction write **only** to the declared output tree; atomic
  publish via sibling `.partial` + `os.replace`; failure cleanup removes
  staging residue; there is no implicit source overwrite and no source-
  overwrite option.
- No runtime network; no VFP9/COM; no process exit or printing from library
  operations (CLI layers own presentation).

## 8. Optional dependency behaviour

| Install | Capability |
|---|---|
| base | Direct Read + JSONL/JSON/CSV migration (stdlib/Python engines) |
| `[write]` | DBF/FPT reconstruction (`dbf`) |
| `[xlsx]` | XLSX export (`xlsxwriter`) and XLSX-format reading/verification support (`openpyxl`) |
| `[write,xlsx]` | XLSX → DBF/FPT reconstruction (both extras together) |
| `[fast]` | optional accelerators (`orjson`, `polars`) — never required |
| `[all]` | full optional feature set |
| `[import]` | compatibility alias of `[write]` |

A missing optional dependency affects **only the operation that needs it**:
`OptionalDependencyMissingError` (machine code + exact install command),
raised before any output is created. It must not break `import dbfbridge` or
any PURE_READ operation. There is no runtime installation and no network.

## 9. Compatibility aliases

- `dbf_bridge` — historical import name (full parity with `dbfbridge`).
- pip extra `[import]` — historical alias of `[write]`.

Both remain supported for the 1.x line.

## 10. Supported VFP contract

`docs/compatibility-vfp.md` is the **authoritative** per-type support matrix.
Its vocabulary means exactly what that document states: `SUPPORTED`,
`SUPPORTED_WITH_LIMITATION`, `RAW_ONLY`, `UNSUPPORTED`, `SYSTEM_INTERNAL`,
`PARSER_COMPATIBILITY_ONLY`, `NOT_YET_VERIFIED`. Macro-level API stability
does not promote any unsupported format.

## 11. Accepted limitations (not implementation work)

- exact raw Varchar DBF byte identity (canonical reconstruction is supported
  in every RawMode; `raw_dbf_match` is reported separately);
- exact G/P raw FPT block-layout identity (canonical payload + DBF identity
  proven);
- no full CDX tag-expression engine (presence-only reporting);
- Q/W varbinary/blob decoding, binary `V`/`C` decoders, and the dBASE Level 7
  dialect are outside the supported set (typed, truthful);
- writer throughput: revisit only with benchmark evidence.

## 12. Semantic versioning policy

- **PATCH** — bug fixes that preserve the public contract (behaviour,
  signatures, codes, documented keys).
- **MINOR** — backward-compatible additions (new operations, options, codes,
  additive JSON keys, new supported cases within the declared matrix).
- **MAJOR** — breaking public API or semantic contract changes: removing or
  repurposing a stable symbol or machine code, changing a documented result
  key's meaning, changing a documented default, or weakening a declared
  guarantee.

No custom version scheme; the public version is `dbfbridge.__version__`.

## 13. Deprecation policy

For the stable 1.x surface:

- an incompatible removal or semantic change requires a **major version**;
- deprecations are: documented (this file + README), recorded in
  `CHANGELOG.md`, accompanied by migration guidance, and use Python warning
  mechanisms (`DeprecationWarning`) where appropriate;
- no calendar support period and no release dates are promised.

## 14. Contract regression tests

`tests/test_api_1_0_contract.py` enforces this contract mechanically:
preferred import, symbol parity between `dbfbridge` and `dbf_bridge`, the
nine stable operations, `RawMode` choices, the architecture-required
`ErrorCode` values, JSON-safe run results, and the compatibility aliases.
It freezes PUBLIC behaviour, not implementation structure.
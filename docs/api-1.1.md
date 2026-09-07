# dbfbridge v1.1 API contract — Direct Write

**Status: approved public v1.1 contract (additive).** This document extends
[api-1.0.md](api-1.0.md) and inherits its baseline: all nine 1.0 operations
(`inspect_table`, `read_schema`, `iter_records`, `read_records`,
`iter_raw_records`, `export_dbf`, `reconstruct_dbf`, `verify_conversion`,
`check_conversion_quality`) remain protected with unchanged signatures,
defaults, and semantics. Nothing from 1.0 was renamed, removed, or
repurposed; v1.1 only adds Direct Write.

> Release wording: this page describes the **implemented public v1.1
> contract**. It is not a claim that a `1.1.0` package version is already
> published — the version bump and publication belong to the controlled
> release lifecycle (see the CHANGELOG `[Unreleased]` entry).

## 1. The new public operation

```python
from dbfbridge import write_table

write_table(
    destination,
    *,
    schema: TableSchema,                # from read_schema() or an equivalent typed schema
    records: Iterable[DirectRecord | Mapping[str, Any]],
    overwrite: bool = False,            # False is the default: existing output is refused
    staging_directory=None,             # optional; must be on the same volume as destination
    progress: ProgressCallback | None = None,
    cancel_check: CancellationCheck | None = None,
) -> WriteResult
```

- accepted inputs: `DirectRecord` (`values` + `deleted`) or plain mappings
  using the reconstruction `__deleted__` marker;
- the caller's iterable is consumed **exactly once**; the input physical
  order is the output physical order (deleted markers preserved);
- unknown mapping keys are rejected; missing non-NULLable fields are typed
  errors; missing NULLable fields are explicit NULLs;
- the `_NullFlags` system column is writer-managed — callers never compute
  bitmaps.

The compatibility alias `dbf_bridge` exports the identical objects
(`dbfbridge.write_table is dbf_bridge.write_table`).

## 2. Result: `WriteResult`

Immutable and JSON-safe via `to_dict()`:

`destination`, `fpt_path`, `fpt_published`, `records_written`,
`deleted_records`, `structural_cdx`, `index_rebuild_required`, `dbc_bound`,
`dbf_sha256`, `fpt_sha256`, `warnings` (serialized as a list; paths in POSIX
form). The digests describe the FINAL published files. The payload contains
metadata, counters, digests, and warnings only — never record or memo
values.

## 3. Errors

Direct Write failures are typed in a family **separate** from
`DirectReadError` — `except DirectReadError` never catches a write failure:

`DirectWriteError` → `DestinationIoError`, `WriteSchemaInvalidError`,
`WriteFieldUnsupportedError`, `WriteValueInvalidError`, `WriteMemoFailedError`,
`WritePublicationFailedError`, `WriteCancelledError`.

Codes are additive members of the canonical `ErrorCode` vocabulary:
`DESTINATION_IO_ERROR`, `WRITE_SCHEMA_INVALID`, `WRITE_FIELD_UNSUPPORTED`,
`WRITE_VALUE_INVALID`, `WRITE_MEMO_FAILED`, `WRITE_PUBLICATION_FAILED`,
`WRITE_CANCELLED`. The write-conflict case reuses the stable
`OUTPUT_EXISTS` code (`OperationOutputExistsError`) when `overwrite=False`
and the destination exists. Missing `[write]` raises the existing
`OPTIONAL_DEPENDENCY_MISSING` **before any output, staging, or spool is
created**. Classification is always structured (type/code/context) — never
by parsing the message text.

## 4. Dependencies and import behaviour

- `dbf` remains an **optional** dependency: the `[write]` extra (with the
  historical `[import]` alias). The base wheel does not require it.
- `import dbfbridge` stays lazy: no `dbf` import, no codepage registration,
  no files, no CLI/reporting modules. `write_table` is resolved lazily by
  the facades and imports no heavy state until a write actually runs.
- No runtime installation and no network access.

## 5. Correctness and safety semantics

- **`overwrite=False` is the default.** Existing DBF or companion FPT
  output is refused before publication and left untouched.
- **Source files are never modified.** Direct Write creates fresh
  output/copies only; it performs no in-place update, no PACK, no ZAP, no
  REINDEX.
- **Canonical equivalence ≠ raw byte identity.** Direct Write rebuilds the
  header and descriptors from the typed schema and does not claim source
  byte identity; raw forensic byte identity remains the domain of the
  reconstruction pipeline with `full-record` metadata
  (`docs/compatibility-vfp.md`).
- **Structural CDX is NOT reconstructed.** If the schema indicates a
  structural CDX, the DBF/FPT pair is written, the result reports
  `structural_cdx=True` and `index_rebuild_required=True`, and a truthful
  warning asks for an external index rebuild. No tag definitions are
  fabricated and no source `.cdx` is copied.
- **DBC limitations are truthful.** `dbc_bound` reports the schema state;
  the written table is standalone — DBC binding, triggers, rules,
  relations, and stored procedures are not restored.
- **Atomic publication.** Data is staged first; staged files are flushed
  and fsynced; final files are published with atomic `os.replace`. The
  DBF+FPT pair is treated as ONE logical transaction: a handled failure
  restores the previous pair exactly (no old/new mix, no `.partial` or
  backup residue). A caller-supplied `staging_directory` must be on the
  same volume as the destination or the call is refused (no silent
  non-atomic copy). A hard crash between the individual replaces can leave
  staging residue — handled failures cannot.
- **Bounded streaming.** The caller iterable is consumed exactly once;
  flat tables use O(1)/O(batch) additional memory. The Varchar/`_NullFlags`
  second logical pass is fed from a bounded private spool inside the
  staging area (spilled after a deterministic in-memory threshold, private
  format, removed after success, handled failure, and cancellation) — the
  caller's input is never materialized in RAM. The historical
  `intermediate_jsonl_bytes` of a write is conceptually zero.
- **Progress and cancellation.** `progress` receives the canonical
  `ProgressEvent` (`operation="write"`); `cancel_check` is honoured at
  record boundaries and immediately before final publication; cancellation
  raises `WRITE_CANCELLED`, publishes nothing, and cleans staging.

## 6. Versioning

The v1.1 surface is additive per SemVer: no 1.0 symbol was removed or
changed incompatibly, and the package version remains governed by the
existing release lifecycle (the `1.1.0` bump happens in a controlled
release step, not as a side effect of this promotion).
# Phase 2A — Direct Write Core

## Motivation

Phase 1 delivered a read-only Direct Read Core: `read_schema()`, `iter_records()`
/ `read_records()` stream a DBF/FPT without any intermediate transport.
Privacy engines such as the planned `DBF_Anonymizer` need the write side of
that contract:

```text
DBF/FPT
  -> inspect/read_schema
  -> iter_records
  -> transform record
  -> direct DBF/FPT writer
```

Today the only DBF/FPT writing implementation lives inside the reconstruction
pipeline (`importer/writer.py`) and consumes export-schema dictionaries built
from JSONL+schema JSON transport.  Phase 2A extracts that proven writer into a
shared backend and exposes a small, typed, transport-neutral `write_table()`
API, so an external engine can transform records in one pass and write a new
DBF/FPT **without generating an intermediate JSONL file**.

The anonymizer itself stays out of this repository.

## Relationship: Direct Read -> Direct Write

The intended usage is two direct passes:

1. `read_schema(source)` -> a public, immutable `TableSchema`.
2. `iter_records(..., memo="inline")` -> transform -> `write_table(destination,
   schema, records)`.

Direct Read is never involved in writing and the writer never reads the source
DBF.  The writer does not open the source FPT: memo values arrive decoded in
the record stream (`memo="inline"`) or are provided by the caller; a
`LazyMemoValue` left in the stream is resolved through its explicit `load()`
(whose loader was created by the caller's read phase — the writer never opens
a source FPT through hidden global state).

## Audit of the existing reconstruction writer

`src/dbf_bridge/importer/writer.py` (single proven implementation since Phase
0) was audited.  Elements:

| Element | Verdict |
|---|---|
| `write_dbf()` — staging loop, `dbf.Table` append, metadata patches, layout validation, fsync, publication | **factored into the shared backend** |
| `_field_spec`, `_coerce_value`, `_text_encodings`, `_encode_text` | **factored** (typed field mapping + text encoding) |
| `_patch_dbf_metadata`, `_patch_fpt_metadata`, `_patch_fpt_block_types`, `_validate_layout`, `_install_lossless_numeric_writer`, `_update_numeric`, `_fsync_file` | **factored** (post-write correction passes) |
| `memo_output_path`, `output_hashes` | **factored** (shared helpers) |
| `ReconstructionError` | stays the reconstruction-facing alias of the backend error family |
| `restore_raw_layout` (+ `_relocate_memo_block`, raw memo relocation) | **stays with reconstruction** (needs the raw Base64 record images of the forensic JSONL transport; Direct Write has no such transport) |
| `CanonicalChecksum` / `nullable_null_fields` / `canonical_record` | reconstruction oracle tooling, stays in the importer |

The reconstruction pipeline (`reconstruct_tree`, `dbf-bridge-import`,
quality/round-trip) keeps its JSONL reference path and its canonical oracle.
After the refactor it calls the same backend — there is exactly one physical
DBF/FPT writer in the codebase and exactly one set of DBF type rules.

## Public API

`write_table()` keeps the preferred name (no conflict exists in
`dbfbridge`/`dbf_bridge` namespaces).

```python
from dbfbridge import read_schema, iter_records, write_table

schema = read_schema(source)
result = write_table(
    destination,
    schema=schema,
    records=(
        transform(record) for record in iter_records(source, include_deleted=True, memo="inline")
    ),
)
if result.index_rebuild_required:
    ...  # schedule an authoritative CDX rebuild outside dbfbridge
```

- `write_table(destination, *, schema, records, overwrite=True,
  staging_directory=None, progress_callback=None) -> WriteResult`.
- `schema` accepts the public `TableSchema` directly — no
  `TableSchema -> export schema dict` conversion is required from the
  integrator.  Internally the typed schema is adapted to the backend writer
  contract in one adapter function.
- `records` accepts `DirectRecord` objects (preferred: `deleted` state, values
  mapping) **and** plain `Mapping[str, Any]` where the deleted state comes
  from the legacy `__deleted__` marker (the reconstruction convention).
- `WriteResult` is a frozen, JSON-safe model: `destination`, `fpt_path`,
  `record_count`, `deleted_record_count`, `structural_cdx`,
  `index_rebuild_required`, `dbf_sha256`, `fpt_sha256`, `warnings`.

Additive model changes to the public read models: **none were required** —
`TableSchema`/`FieldInfo` already carry everything the writer needs (field
type/length/decimals/flags/address/is_memo/is_binary, version byte, language
driver, last update, memo block size, structural CDX flag, resolved
encoding).  No raw Base64 artifacts enter the public schema contract, and
`to_dict()` stays JSON-safe.

## Schema contract

`write_table` adapts `TableSchema` into the backend schema mapping:

- per field: `name`, `dbf_type`, `length`, `decimal_count`, `flags`,
  `address`, `is_memo`, `is_binary`, `ordinal`;
- header: `version_byte`, `language_driver`, `last_update` (ISO date or
  `None`), `structural_index_flag := has_structural_cdx`;
- memo: `block_size_bytes` (default 64 when the recording table used none),
  companion name derived from `memo_companion_path` (or the `.fpt` default);
- text encoding: `encoding` (the resolved Phase 1 encoding; Direct Record
  values are already decoded text, so the writer encodes with exactly this
  codec — the legacy reconstruction fallback chain remains available only to
  the JSONL path).

Because `TableSchema` deliberately carries no raw header/descriptor Base64
artifacts, the writer **rebuilds** the header and descriptors from the typed
metadata: raw binary identity with the source is *not* promised by this path
(see equivalence section).  The forensic `raw_layout` restoration remains a
reconstruction-pipeline feature.

## Streaming / memory contract

- `records` is consumed lazily; the writer holds at most one working record,
  the canonical-checksum state (O(1) hashing state) and the structures required
  by the `dbf` backend.
- No additional full pass is made over the records by `write_table` itself.
- A regression test detects accidental `list(records)` / full materialization
  by feeding a generator that tracks exhaust events (raising when the
  generator would be consumed twice or fully drained twice).

## DBF/FPT staging and publication semantics

Three different notions are deliberately separated:

1. **Atomic file replacement** — a single DBF *or* FPT file is written to a
   staged name and moved onto its destination with one `os.replace()`.  This
   is the only atomicity the OS offers here.
2. **Table-level DBF+FPT publication** — a DBF and its memo companion
   cannot be replaced by a single call.  The writer stages both, finalizes
   and fsyncs them, then replaces the memo file and the DBF; on any handled
   failure the staging files are removed and the destination directory keeps
   its previous contents (never a half-written final pair).
3. **Dataset/directory publication** — replacing a whole table set unit is a
   decision of the higher layer (e.g. the privacy engine writing to a staging
   directory and publishing it as a whole); `staging_directory=` exists so the
   caller can keep staging outside the final tree.

The writer never touches the destination on failure and leaves `.partial`
residue only after a *hard* crash (documented honestly); every handled
failure cleans staging.

## CDX limitations

If the schema reports a structural CDX (`has_structural_cdx`), the writer
still produces the DBF/FPT into staging and returns
`index_rebuild_required=True` / `structural_cdx=True` with an explicit
warning.  It never fabricates a `.cdx`, never copies the source CDX, and
never declares the output a correct indexed table: an authoritative index
rebuild (e.g. in a VFP session) remains a decision of the higher layer.

## Error model

New additive error codes on the stable `ErrorCode` enum (Direct Read codes
untouched):

- `DESTINATION_EXISTS` — final DBF/FPT exists and `overwrite=False`;
- `DESTINATION_IO_ERROR` — filesystem I/O around staging/publication;
- `WRITE_SCHEMA_INVALID` — the schema is unusable for writing (e.g. no
  fields, unsupported combination);
- `WRITE_FIELD_UNSUPPORTED` — a field type the shared backend cannot
  represent;
- `WRITE_VALUE_INVALID` — a value does not fit/convert for its field
  (message carries path + field name + physical index, never the value);
- `WRITE_MEMO_FAILED` — memo payload could not be written/finalized;
- `WRITE_PUBLICATION_FAILED` — staging/replace/fsync failed.

`DirectWriteError` is a `DirectReadError` subclass with these codes; payloads
are JSON-safe (path, field name, physical index, operation) and never contain
record payloads, memo payloads, or personal values.

## Equivalence with existing reconstruction

Two separate notions, never conflated:

- **Canonical/logical equivalence** — a fresh `dbfread` pass over the output
  yields the same decoded values/order/deleted state as the source stream:
  proven by comparing against `dbfread` replay of both the source and the
  output.
- **Byte identity** — promised only by the reconstruction pipeline with raw
  forensic metadata.  Direct Write works from decoded `DirectRecord`s without
  a raw transport and therefore rebuilds header/descriptor bytes from typed
  metadata; byte identity with the source is *not* claimed.

Equivalence tests replay source DBF/FPT through Direct Read → Direct Write →
re-read and compare record count, physical/deleted order, decoded values
(NULL, Character byte-length, D/T, numeric identity within the writer's
guarantees, memo, encoding) and the schema, plus compare the same logical
data written through the reconstruction writer and through Direct Write.

## Benchmark contract

New scenarios (no Phase 0/1 artifact modified):

- `direct_read_write_roundtrip` — `read_schema` + `iter_records` →
  `write_table`; architecture assertion: **no intermediate JSONL bytes are
  written** (JSONL bytes = 0);
- optionally `direct_write_character_heavy` and `direct_write_memo_heavy`.

Metrics: wall, CPU, records/s, peak RSS, input bytes read, output DBF+FPT
bytes, temporary bytes written, JSONL bytes = 0.  No Phase 2 performance-
improvement claim is made — there is no comparable BEFORE.

## Error model

Direct Write adds additive codes (above) to the stable machine-code enum.
Payloads are JSON-safe: `path`, `destination`, `field name`, physical index,
operation.  No record values, no memo payload, no personal dumps, no secrets.
Existing Direct Read codes are unchanged.

## Out of scope (this phase)

CDX tag-expression parsing, CDX rebuild, VFP COM/REINDEX, DBC parser, MCP
adapter, `DBF_Anonymizer`, pseudonymization, dictionary SQLite, a native
replacement for `dbf`/`dbfread`, a second read backend, one-pass
anonymization, PyPI publication/GitHub release, version bump.
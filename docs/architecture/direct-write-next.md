# Direct Write — next-version research contract

> **STATUS: RESEARCH — NOT RELEASED — NOT PART OF THE CURRENT 1.x CONTRACT**
> This document does not extend the stable 1.x public API
> (`docs/api-1.0.md`).  A future explicit version decision will decide
> whether and how this capability is promoted.

## Goal

Phase 1 delivered a read-only Direct Read Core: `read_schema()`,
`iter_records()` / `read_records()` stream a DBF/FPT without any intermediate
transport. Direct Write completes the pair: an external engine transforms
records in one pass and writes a fresh DBF/FPT **without generating an
intermediate JSONL file**:

```text
DBF/FPT
  -> read_schema
  -> iter_records
  -> transform record
  -> write_table -> fresh DBF/FPT
```

The library capability stands independently: it is a transport-neutral,
typed, streaming DBF/FPT writer for migration and data-engineering pipelines.

## Non-goals

- CDX tag-expression parsing, CDX reconstruction, or CDX fabrication
  (CDX reconstruction remains a non-requirement — see `AGENTS.md`);
- VFP COM automation / REINDEX / DBC container parsing;
- an MCP adapter or tool-server surface for writing;
- pseudonymization engines, dictionary SQLite, one-pass anonymization —
  `DBF_Anonymizer` in particular stays outside this repository (it was never
  the design reason; it is merely one potential future consumer);
- a native replacement for the `dbf`/`dbfread` backends, or a second read
  backend;
- any claim of raw byte identity with a source table;
- a version bump, a GitHub release, or a PyPI publication (research carries
  no release metadata).

## Relationship: Direct Read -> Direct Write

The intended usage is two direct passes:

1. `read_schema(source)` -> a public, immutable `TableSchema`;
2. `iter_records(..., memo="inline")` -> transform -> `write_table(destination,
   schema, records)`.

Direct Read is never involved in writing and the writer never reads the source
DBF. The writer never opens the source FPT: memo values arrive decoded in the
record stream (`memo="inline"`) or are provided by the caller; a
`LazyMemoValue` left in the stream is resolved through its explicit `load()`
(loader created by the caller's read phase — the writer never opens a source
FPT through hidden global state). The source DBF/FPT bytes are never modified.

## Writer sharing rule (single physical writer)

The current reconstruction writer is the **correctness authority**. There is
exactly **one** physical DBF/FPT writer in the codebase:

```text
reconstruction path (JSONL + schema JSON)
          \
           -> dbf_bridge.write.backend  (one staging loop, one set of DBF
          /                              type rules, one publication transaction)
direct write path
```

- `src/dbf_bridge/write/backend.py` holds the physical writer; it is derived
  from the proven reconstruction writer on current `main` (canonical Varchar
  logical-layout repair, canonical `_NullFlags` allocation via
  `dbf_bridge.core.nullflags`, FPT boundary handling, lossless numeric
  writing, atomic publication);
- `src/dbf_bridge/importer/writer.py` is a compatibility delegation layer that
  re-exports the reconstruction-facing surface unchanged;
- there is **no** second copied direct writer, no duplicated field coercion,
  memo-writing, NullFlags, or publication logic;
- `restore_raw_layout` (raw record images) stays part of the reconstruction
  oracle path — Direct Write has no raw transport and never claims byte
  identity.

## Public candidate API

`write_table()` keeps the preferred name (no conflict exists in the
`dbfbridge` / `dbf_bridge` namespaces). While the capability is RESEARCH it is
**not** exported from the package root; the stable nine-operation contract is
untouched:

```python
from dbfbridge import iter_records, read_schema
from dbf_bridge.write import write_table

schema = read_schema(source)
result = write_table(
    destination,
    schema=schema,
    records=(
        transform(record)
        for record in iter_records(source, include_deleted=True, memo="inline")
    ),
)
if result.index_rebuild_required:
    ...  # schedule an authoritative CDX rebuild outside dbfbridge
```

- `write_table(destination, *, schema, records, overwrite=True,
  staging_directory=None, progress_callback=None) -> WriteResult`;
- `schema` accepts the public `TableSchema` directly — no
  `TableSchema -> export schema dict` conversion is required from the
  integrator; the typed schema is adapted to the backend writer contract in
  one deterministic adapter (`write.schema_adapter`);
- `records` accepts `DirectRecord` objects (preferred: `deleted` state +
  values mapping) **and** plain `Mapping[str, Any]` where the deleted state
  comes from the legacy `__deleted__` marker (the reconstruction convention).
  The stream is consumed lazily (see the streaming contract);
- `WriteResult` is a frozen, JSON-safe model with ONE naming convention:
  `destination`, `fpt_path`, `fpt_published`, `records_written`,
  `deleted_records`, `structural_cdx`, `index_rebuild_required`, `dbc_bound`,
  `dbf_sha256`, `fpt_sha256`, `warnings`.  (`records_written` /
  `deleted_records` — the names used by the prototype code and the
  reconstruction results; the old draft spellings `record_count` /
  `deleted_record_count` are retired.  Direct Write was never released, so no
  compatibility aliases exist.)

`TableSchema`/`FieldInfo` already carry everything the writer needs (field
type/length/decimals/flags/address/is_memo/is_binary, version byte, language
driver, last update, memo block size, structural CDX flag, resolved
encoding).  No raw Base64 artifacts enter the public schema contract and
`to_dict()` stays JSON-safe.

## Schema contract

`write_table` adapts `TableSchema` into the backend schema mapping:

- per field: `name`, `dbf_type`, `length`, `decimal_count`, `flags`,
  `address`, `is_memo`, `is_binary`, `ordinal`;
- header: `version_byte`, `language_driver`, `last_update` (ISO date or
  `None`), `structural_index_flag := has_structural_cdx`;
- memo: `block_size_bytes` (default 64 when the source recorded none); the
  memo companion is written **beside the destination** (`<destination-stem>.fpt`
  or the schema companion name) — never over the source companion;
- text encoding: `encoding` (the resolved Phase 1 encoding; Direct Record
  values are already decoded text, so the writer encodes with exactly this
  codec — the legacy reconstruction fallback chain remains available only to
  the JSONL path).

Because `TableSchema` deliberately carries no raw header/descriptor Base64
artifacts, the writer **rebuilds** the header and descriptors from the typed
metadata: raw binary identity with the source is *not* promised by this path
(see equivalence).  The forensic `restore_raw_layout` remains a
reconstruction-pipeline feature.

## Streaming / memory contract

- `records` is consumed lazily; the writer holds at most one working record,
  the canonical-checksum state (O(1) hashing state) and the structures
  required by the `dbf` backend.  No additional full pass is made over the
  records by `write_table` itself.
- **Varchar/`_NullFlags` exception (honest limitation):** tables that carry a
  `_NullFlags` system column (VFP nullable / Varchar dialect) require the
  canonical logical-layout repair, which needs the logical record values a
  second time.  For such schemas `write_table` materializes the caller's
  stream once internally (still consumed exactly once from the caller's
  point of view) and re-uses the materialized records for the repair pass;
  flat tables stay fully O(1).  A regression test proves a flat-table
  generator is iterated exactly once.
- A regression test detects accidental `list(records)` / full
  materialization on the flat path by feeding a one-shot iterable.

## DBF/FPT staging and publication semantics

Three different notions are deliberately separated:

1. **Atomic file replacement** — a single DBF *or* FPT file is written to a
   staged name and moved onto its destination with one `os.replace()`.  This
   is the only atomicity the OS offers here.
2. **Table-level DBF+FPT publication** — a DBF and its memo companion cannot
   be replaced by a single call.  The writer stages both, finalizes and
   fsyncs them, then replaces the memo file and the DBF; on any handled
   failure the staging files are removed and the destination directory keeps
   its previous contents (a previously published DBF+FPT pair stays
   byte-identical; never a half-written final pair).
3. **Dataset/directory publication** — replacing a whole table set is a
   decision of the higher layer; `staging_directory=` exists so the caller
   can keep staging outside the final tree.

Handled failures leave no `.partial` residue.  A *hard crash* (power loss /
`kill -9`) may leave staging residue — documented honestly, not hidden.

## Memo semantics

- memo values arrive decoded in the record stream (`memo="inline"`) or are
  provided by the caller; a `LazyMemoValue` is resolved through its explicit
  `load()`;
- the memo companion is written beside the destination (the source companion
  name is deliberately not reused blindly — dataset-level renaming stays a
  higher-layer decision);
- text and binary memo blocks already supported by the current writer are
  preserved (text `M`, binary `M`/`G`/`P` payloads, per-block content types
  restored after writing);
- memo failures surface as typed `WRITE_MEMO_FAILED` errors, never as raw
  backend exceptions.

## NullFlags (canonical VFP contract)

The single source of truth for the `_NullFlags` bit allocation is
`dbf_bridge.core.nullflags` (the engine locked by the Phase 1A/1B evidence):
`V`/`Q` fields take a varlength bit before their NULL bit, other NULLable
fields take one NULL bit, in descriptor order.  The shared writer reuses the
reconstruction writer's canonical Varchar logical-layout repair (varlength
payload form `value + padding + length byte`, canonical bitmap) — Direct Write
introduces **no** parallel NullFlags implementation.  Direct Read -> Direct
Write -> reread preserves NULLs and Varchar storage forms on the canonical
VFP 0x32 dialect fixtures.

## Encoding

Direct Record values are already decoded text; the writer re-encodes with the
schema's resolved encoding (`schema.encoding`), and the legacy Polish codecs
(cp1250/cp852/Mazovia) are registered on demand exactly as in the
reconstruction path (one codec registry, one codepage table).  A value that
cannot be encoded under the schema's encoding surfaces as a typed
`WRITE_MEMO_FAILED` / `WRITE_VALUE_INVALID` error — never as a raw
`UnicodeEncodeError`.

## Deleted records

`include_deleted=True` streams carry the deleted state; `DirectRecord.deleted`
and the legacy `__deleted__` mapping marker are both honoured, and deletion
markers are written in the PHYSICAL stream order (verified against raw file
bytes in the tests).

## CDX limitation

If the schema reports a structural CDX (`has_structural_cdx`), the writer
still produces the DBF/FPT and returns `structural_cdx=True` /
`index_rebuild_required=True` with an explicit warning.  It never fabricates a
`.cdx`, never copies the source CDX, and never declares the output a correct
indexed table: an authoritative index rebuild (e.g. in a VFP session) remains
a decision of the higher layer.

## Error model (machine-classified, additive)

The shared backend carries structured machine codes on its internal failure
family (`ReconstructionError.code` — a stable `ErrorCode` member).  The
`write_table` boundary maps those structured codes onto typed public errors;
**error classification never parses English message text** (the historical
prototype's `startswith("Cannot convert field")`-style prefix matching is
deliberately absent).

Additive research codes on the stable `ErrorCode` enum (Direct Read codes
untouched; `docs/api-1.0.md` unchanged):

- `OUTPUT_EXISTS` — **reused**, not re-invented: the existing stable code and
  `OperationOutputExistsError` already fully represent "the target output
  exists and was not marked overwrite-eligible";
- `DESTINATION_IO_ERROR` — filesystem I/O failure around staging/publication
  (`DestinationIoError`);
- `WRITE_SCHEMA_INVALID` — the schema is unusable for writing (e.g. no
  fields) (`WriteSchemaInvalidError`);
- `WRITE_FIELD_UNSUPPORTED` — a field type the shared backend cannot
  represent (`WriteFieldUnsupportedError`);
- `WRITE_VALUE_INVALID` — a value does not fit/convert for its field
  (`WriteValueInvalidError`);
- `WRITE_MEMO_FAILED` — a memo payload could not be written/finalized
  (`WriteMemoFailedError`);
- `WRITE_PUBLICATION_FAILED` — staging/fsync/replace failed or an unexpected
  backend failure occurred (`WritePublicationFailedError`).

All are `DirectWriteError` (a `DirectReadError` subclass) except the reused
`OperationOutputExistsError`.  Payloads are JSON-safe (`code`, `message`,
`path`, `context`) and never contain record payloads, memo payloads, or
personal values — verified by sentinel tests that serialize the actual raised
payload and assert the absence of record/memo values.

## Privacy

- the writer never writes record or memo payloads into error messages or
  error payloads;
- `WriteResult` carries sizes and SHA-256 digests only — no record values;
- sentinel tests feed secret values through failing writes and assert the
  serialized error payload (`json.dumps(exc.to_dict())`) contains none of
  them.

## Canonical vs raw equivalence

Two separate notions, never conflated:

- **Canonical/logical equivalence** — a fresh Direct Read (`dbfread`-based)
  pass over the output yields the same decoded values/order/deleted state as
  the source stream; proven by Direct Read -> Direct Write -> reread tests
  and by comparing the same logical data written through the reconstruction
  writer and through Direct Write.
- **Raw byte identity** — promised only by the reconstruction pipeline with
  raw forensic metadata.  Direct Write rebuilds header/descriptor bytes from
  typed metadata; byte identity with the source is **not** claimed.

## Benchmarks (separate research profile)

The `phase2` benchmark profile (`python -m benchmarks.run_benchmark --profile
phase2`) measures the direct path with no Phase 0/1/3 artifact modified and
no comparison against the canonical Phase 3 baseline (there is no comparable
BEFORE and no acceptance threshold yet):

- `direct_read_write_roundtrip` — `read_schema` + `iter_records` ->
  `write_table` over the 190k fixture; architecture assertion:
  **intermediate JSONL bytes = 0**;
- `direct_write_character_heavy` — character-heavy 190k write;
- `direct_write_memo_heavy` — memo-heavy DBF/FPT pair via the public API.

Metrics: wall, CPU, records/s, peak RSS, input bytes, output DBF+FPT bytes,
temporary bytes written, JSONL bytes = 0.  Reports carry the research
contract string `direct-write-research-v0` and are never baseline-eligible.

## Promotion criteria (future explicit decision)

Direct Write may only be promoted to a released version when ALL of:

1. the shared-writer rule holds (one physical writer; reconstruction
   behaviour byte-for-byte unchanged, proven by the existing reconstruction
   evidence);
2. the direct-write equivalence/safety suite passes on Linux and Windows CI;
3. the error contract is machine-classified end-to-end (no message parsing)
   and privacy-safe (sentinel tests);
4. a version decision exists (additive MINOR or the 1.x MAJOR policy), with
   `docs/api-1.0.md` extended deliberately, the top-level export added, and
   the research markers removed;
5. the benchmark evidence is recorded honestly (own profile, no Phase 3
   comparison) and any acceptance thresholds are defined separately.

## Out of scope (research)

CDX tag-expression parsing, CDX rebuild, VFP COM/REINDEX, DBC parser, MCP
adapter, `DBF_Anonymizer`, pseudonymization, dictionary SQLite, a native
replacement for `dbf`/`dbfread`, a second read backend, one-pass
anonymization, PyPI publication/GitHub release, version bump.
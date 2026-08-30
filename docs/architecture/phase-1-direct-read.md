# dbfbridge — Phase 1A: Direct Read Core (inspection and schema)

- Base commit (Phase 0 end): `49500785444ffa2e798146c14a158d926c158c34`
  (branch `bench/phase-0-baseline`, stacked on unmerged PR #1)
- Package: `dbfbridge 0.1.0` (alpha), Python >= 3.10, MIT
- Scope: **Phase 1A only** — `inspect_table()` / `read_schema()` and the
  `dbf_bridge.core` foundation. Record reading, field projection, and lazy memo
  are explicitly out of scope (next step).

This document records the architectural contract implemented in this phase and
links it to the code.

## 1. What Phase 1A delivers

| Symbol | Kind | Home |
|---|---|---|
| `inspect_table(path)` | function | `src/dbf_bridge/core/inspect.py` |
| `read_schema(path)` | function | `src/dbf_bridge/core/inspect.py` |
| `TableInfo` | frozen dataclass | `src/dbf_bridge/core/models.py` |
| `TableSchema` | frozen dataclass | `src/dbf_bridge/core/models.py` |
| `FieldInfo` | frozen dataclass | `src/dbf_bridge/core/models.py` |
| `ErrorCode` | str enum (machine codes) | `src/dbf_bridge/core/errors.py` |
| `DirectReadError` + typed subclasses | exceptions | `src/dbf_bridge/core/errors.py` |

Both entry points are exported from `dbfbridge` and the historical
`dbf_bridge` namespace (`src/dbfbridge/__init__.py`,
`src/dbf_bridge/__init__.py`) with synchronized `__all__` lists.

## 2. Architectural boundary: `dbf_bridge.core`

```
src/dbf_bridge/core/
├── __init__.py     public core surface (inspect_table, read_schema, models, errors)
├── errors.py       ErrorCode + DirectReadError subclasses (JSON-safe to_dict)
├── codecs.py       Mazovia/PIAST table + registration + driver resolution
├── fields.py       pure field classification (memo/binary/supported) + type names
├── header.py       single pure DBF header parser (O(header), read-only)
├── models.py       FieldInfo / TableInfo / TableSchema (frozen, to_dict)
└── inspect.py      public inspect_table / read_schema + companion discovery
```

Hard rules for the core layer:

- no CLI, no reporting, no migration orchestration, no reconstruction imports;
- no output files, no reports, no locks, no `.partial` artifacts;
- no Polars, OpenPyXL, XlsxWriter, orjson, and no `dbf` writer package
  (core requires only the standard library plus the pure-Python `dbfread`
  codepage table);
- no network, no COM, no VFP;
- no printing, no `sys.exit`;
- inspection runs in O(header) work and never iterates the record area;
- no duplicate parser: the migration exporter delegates to
  `core.header.parse_header` (`src/dbf_bridge/exporter/reader.py`), and the
  Mazovia table exists in exactly one place (`core/codecs.py`;
  `exporter/polish_codecs.py` is a compatibility re-export).

`src/dbfbridge` remains a thin public facade; there is no second, parallel
header parser.

## 3. Import side-effect contract

`import dbfbridge` (and `import dbf_bridge`):

- registers **no** codepage (the Mazovia/PIAST codec is registered
  explicitly by the code paths that decode with it —
  `core.codecs.register_polish_codecs()` / `driver_to_encoding()`);
- creates no files;
- imports no Polars, OpenPyXL, XlsxWriter, orjson, and no `dbf` package;
- loads no CLI/reporting modules.

Public symbols are resolved lazily (PEP 562 `__getattr__`) while staying
import-compatible: `from dbfbridge import export_dbf` and
`from dbf_bridge import export_dbf` behave exactly as before. Verified in a
fresh interpreter by `tests/test_direct_read_schema.py::test_fresh_interpreter_import_has_no_side_effects`.

## 4. Public model contract

- immutable (`dataclass(frozen=True)`), fully typed, CLI-independent;
- JSON-serializable through an explicit `to_dict()`; the payload is
  exclusively JSON-safe (str/int/float/bool/list/dict/None) — no `bytes`, no
  `Path`;
- no raw header Base64 and no memo payloads: Direct Read must not pay the
  `FORENSIC_ROUNDTRIP` cost.

`FieldInfo` keeps the physical VFP facts an MCP consumer needs: `ordinal`,
`name`, `dbf_type` + readable `dbf_type_name`, `length`, `decimal_count`,
`address`, raw `flags` as int plus derived `system` / `nullable` / `binary`,
`is_memo`, `supported`, `unsupported_reason`.

`TableInfo` carries: `path`, `record_count`, `header_length`, `record_length`,
`language_driver`, `encoding`, `has_memo`, `has_structural_cdx`, `dbc_bound`,
`fields`, `warnings`.

`TableSchema` additionally carries: `dbversion_byte` / `dbversion_name`,
`last_update`, `incomplete_transaction`, `encryption_flag`, companion FPT
details (`memo_companion_present`, `memo_companion_path`,
`memo_companion_size_bytes`, `memo_block_size`, `memo_next_free_block`) and
structural CDX companion presence (`companion_cdx_present`,
`companion_cdx_path`).

Semantics:

- `has_memo` = the table declares memo fields; companion `.fpt` presence is
  separate metadata — a missing FPT is a **structured warning** in
  `warnings`, not an error, as long as the header itself is safe;
- companion `.fpt`/`.cdx` discovery is case-insensitive (directory scan, no
  file creation);
- `dbc_bound` is derived from the VFP database-container backlink stored in
  the header extension (first two bytes after the field terminator,
  little-endian record number; zero = standalone) — **not** from the mere
  existence of a `.dbc` file;
- `has_structural_cdx` is the header structural-index flag (byte 0x1C);
- CDX reporting is structural only: dbfbridge does **not** declare CDX tag
  expression / order / primary-tag parsing;
- the Mazovia language driver byte (0x69) resolves to the custom `mazovia`
  codec via `core.codecs.driver_to_encoding`.

## 5. Structured errors

Tainted or unsupported files never yield a partially-trusted model. Detected
conditions include: missing/non-file path, fixed header shorter than 32
bytes, header length outside the file bounds, missing descriptor terminator,
truncated field descriptor, zero record length, field-length/record-length
inconsistency, physical record area shorter than the header count, unknown
DBF version, and unknown language driver.

Machine codes (stable): `PATH_NOT_FOUND`, `DBF_HEADER_INVALID`,
`DBF_TRUNCATED`, `DBF_FORMAT_UNSUPPORTED`, `ENCODING_UNKNOWN`.

Every exception carries `code`, `path`, a readable `message`, and a JSON-safe
`context` mapping; `to_dict()` is JSON-serializable. No generic
`RuntimeError`; MCP never needs to parse error text.

## 6. Explicit non-goals of this phase

- `iter_records`, `read_records`, field projection, lazy memo payload
  reading, `iter_raw_records` — next step;
- new writer, CDX tag-expression parser, MCP adapter;
- package version change (stays `0.1.0`);
- re-running the full benchmark profile or saving a new `--baseline`.

Consequently the benchmark scenarios `direct_read_bounded`,
`field_projection`, `memo_lazy`, and `raw_mode_none` remain
`NOT_IMPLEMENTED`, and `benchmarks/baselines/phase-0-full.json` /
`phase-0-full.md` are unchanged and remain the **BEFORE** reference
(see `phase-0-audit.md`).

## 7. Verification

- `tests/test_direct_read_schema.py` — 32 integration tests against real
  fixture DBF/FPT files (happy paths, VFP flags, language drivers including
  Mazovia, structured errors, read-only guarantees, fresh-interpreter import
  side effects, codec-on-demand registration, exporter delegation);
- full pre-existing suite stays green (export, reconstruction, Polish codecs,
  benchmark infrastructure);
- `ruff check` + `ruff format --check` clean; `python -m build` +
  `twine check` clean; both CLI `--help` entry points start;
- fast benchmark regression: 15 MEASURED / 0 FAILED / 4 NOT_IMPLEMENTED,
  exit code 0 (no `--baseline`).

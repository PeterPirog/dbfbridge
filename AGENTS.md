# AGENTS.md — dbfbridge project context

Read this file before changing the repository. User-facing behavior is documented in
`README.md`; this file records the implementation map and maintenance rules.

## Project

`dbfbridge` is a Python 3.10+ toolkit for Visual FoxPro DBF/FPT migration:

- DBF → CSV/JSON/JSONL/XLSX export;
- checksum-based incremental re-export;
- schema-driven CSV/JSON/JSONL/XLSX → DBF/FPT reconstruction;
- export verification and diagnostic DBF → JSONL → DBF round trips;
- cp1250/cp852/Mazovia decoding for legacy Polish data;
- typed high-level API available from both `dbfbridge` and `dbf_bridge`.

Repository: <https://github.com/PeterPirog/dbfbridge>

License: MIT

Current package version/status: 0.1.0, alpha.

## Architecture

```text
src/dbf_bridge/
├── core/                  Phase 1 direct read core (read-only, stdlib + dbfread)
│   ├── errors.py          ErrorCode (+DBF_IO_ERROR, record/memo/argument codes) + typed errors, JSON-safe to_dict
│   ├── codecs.py          Mazovia/PIAST table + registration + driver resolution (single source)
│   ├── fields.py          pure field classification (memo/binary/supported) + type names
│   ├── header.py          single pure DBF header parser (O(header), read-only)
│   ├── models.py          FieldInfo / TableInfo / TableSchema (frozen, to_dict)
│   ├── backend.py         backend boundary: capability protocols + dbfread reference adapter
│   │                      (the only place allowed to touch private dbfread API; one shared
│   │                      physical/decoded record loop)
│   ├── records.py         public DirectRecord / RecordPage / LazyMemoValue + iter_records /
│   │                      read_records / iter_raw_records (streaming, projection, memo policies)
│   └── inspect.py         public inspect_table / read_schema + companion discovery
├── api.py                 stable high-level Python functions
├── api_models.py          options, progress events, and run results
├── cli.py                 export CLI and multi-format orchestration
├── converters.py          streaming JSONL → JSON/CSV/XLSX converters
├── verifier.py            exported-file verifier
├── import_cli.py          reconstruction CLI
├── quality.py             retained round-trip diagnostics
├── exporter/
│   ├── config.py          export validation
│   ├── discovery.py       recursive DBF/FPT/CDX discovery
│   ├── incremental.py     conversion_checksums.json cache
│   ├── models.py          export dataclasses and type aliases
│   ├── polish_codecs.py   Mazovia/PIAST codec
│   ├── reader.py          header metadata + encoding fallback; physical iteration DELEGATES to core.backend
│   ├── serialization.py   JSON-safe DBF value serialization
│   ├── validation.py      output parsing and SHA-256
│   ├── writer.py          atomic DBF → JSONL/schema export
│   └── reporting.py       migration_report.jsonl/.csv
└── importer/
    ├── checksum.py        schema-aware canonical checksums
    ├── models.py          reconstruction configuration/results
    ├── readers.py         JSONL/JSON/CSV/XLSX input streams
    ├── reconstruct.py     directory-tree orchestration
    ├── writer.py          DBF/FPT creation and raw-layout restoration
    └── reporting.py       reconstruction_report.jsonl
src/dbfbridge/
└── __init__.py            recommended public import name
```

Other important paths:

- `examples/` — thin executable wrappers and PowerShell examples;
- `examples/python_api.py` — complete programmatic API example;
- `examples/inspect_table.py` — Phase 1A read-only inspection example;
- `examples/read_records.py` — Phase 1B streaming record-read example;
- `docs/architecture/phase-1-direct-read.md` — Phase 1A/1B direct read contract;
- `tests/test_direct_read_schema.py` — Phase 1A direct read integration tests;
- `tests/test_direct_read_records.py` — Phase 1B streaming record tests;
- `tests/fixtures/generate_sample_dbf.py` — deterministic fixture generator;
- `tests/conftest.py` — generates fixtures in pytest temporary storage;
- `benchmarks/` — Phase 0/1 benchmark runner (fast = 19 MEASURED scenarios,
  full = 24; Phase 0 baseline unchanged, Phase 1 AFTER baseline not yet saved);
- `.github/workflows/ci.yml` — Linux/Windows compatibility checks;
- `.github/workflows/publish.yml` — release build and PyPI Trusted Publishing;
- `PUBLISHING.md` — release checklist and one-time PyPI configuration;
- `pyproject.toml` — package metadata, runtime/dev dependencies, console scripts.

## Command-line interfaces

All real-data commands require explicit source/output paths except the verifier, whose
defaults point at generated test fixtures.

```powershell
dbf-bridge --source "K:\dbf_source" --output "K:\dbf_output" --formats csv,json,jsonl,xlsx
dbf-bridge --source "K:\dbf_source" --output "K:\dbf_output" --formats csv,json,jsonl,xlsx --incremental
dbf-bridge-verify --source "K:\dbf_source" --output "K:\dbf_output" --formats csv,json,jsonl,xlsx
dbf-bridge-import --source "K:\dbf_output" --output "K:\dbf_rebuilt" --formats jsonl --memo inline --overwrite
dbf-bridge-quality --source "K:\dbf_source" --output "K:\dbf_quality" --overwrite
```

Console entry points in `pyproject.toml` must stay synchronized with README and examples:

- `dbf-bridge` → `dbf_bridge.cli:main`;
- `dbf-bridge-verify` → `dbf_bridge.verifier:main`;
- `dbf-bridge-import` → `dbf_bridge.import_cli:main`;
- `dbf-bridge-quality` → `dbf_bridge.quality:main`.

The public Python interface must stay synchronized as well:

- `export_dbf()` → `ExportRunResult`;
- `reconstruct_dbf()` → `ReconstructionRunResult`;
- `verify_conversion()` → `VerificationRunResult`;
- `check_conversion_quality()` → `QualityRunResult`;
- `inspect_table()` → `TableInfo` (Phase 1A, read-only);
- `read_schema()` → `TableSchema` (Phase 1A, read-only);
- `iter_records()` / `read_records()` / `iter_raw_records()` → `DirectRecord`
  / `RecordPage` / `LazyMemoValue` (Phase 1B, read-only streaming).

Phase 1A + 1B direct read core (`src/dbf_bridge/core/`) is a hard boundary:
no CLI, no reporting, no output files, no `.partial` artifacts, no Polars/
OpenPyXL/XlsxWriter/orjson/`dbf`, no network/COM/VFP, no printing or
`sys.exit`. Its DBF read is bounded by the declared header length (independent
of the record count; the descriptor scan never runs past it) plus at most one
case-insensitive companion-file lookup per call (direct exact-name paths are
checked first, so the common case performs no directory scan). The header
table-flags byte (offset 28) is a bit mask: 0x01 structural CDX, 0x02 memo,
0x04 database container; the raw value is exposed as `table_flags`/
`table_flags_hex` on the public models; `dbc_bound` comes from the 263-byte
VFP backlink path, never from a neighbouring `.dbc` file. VFP autoincrement
is the field-flags mask 0x0C on an Integer (`I`) field — type `+` is a
dBASE Level 7 marker outside VFP only; next value/step are descriptor bytes
19-22 (LE) and 23. `index_field_flag` (byte 31) is kept only for migration
compatibility: VFP reserves bytes 24-31 and the byte is not reliable CDX
evidence. The 0x04 bit means NOCPTRANS only for Character/Varchar and memo
fields. FPT health rules: the header record is 512 bytes, the 8-byte prefix
carries next-free block and block size, block size 0 is invalid, 1-32 mean
512-byte units and >32 are plain byte sizes (no power-of-two rule);
DBT/SMT companions are never parsed as FPT, and one `read_schema` call reads
a given FPT header at most once (all companion stat/open/read/scandir
failures — exact-path stat, directory scan, and entry checks — are typed
`DbfIoError`; a genuinely absent companion is `present=False`, while an
inaccessible one raises, never disguised as missing).

Phase 1B record streaming (`core/backend.py` + `core/records.py`):

- `backend.py` is the ONLY module allowed to import `dbfread` — including
  private parts (`DBF._open_memofile`, `dbfread.memo`,
  `FieldParser._parse_memo_index`). It exposes capability protocols
  (header inspection, physical record streaming, memo payloads) with the
  dbfread adapter as the reference implementation;
- there is exactly one physical/decoded record loop: the shared backend loop
  seeks by physical record index, parses only the projected fields and yields
  decoded values plus the optional raw image in one pass. The exporter
  `iter_physical_records` DELEGATES to it (no second read loop, no second
  header/type parser);
- public semantics: `physical_index`/`offset`/`next_offset` are zero-based
  PHYSICAL indices; `read_records` is O(limit); iterators O(1) and close all
  handles (exhaustion, error, `close()`, GC); `include_deleted=False` skips
  deleted records in the same pass; `iter_raw_records` returns every record,
  deleted included, and never opens the FPT; `raw=False` keeps no raw bytes;
- memo policies `skip`/`null`/`lazy`/`inline`: only `inline` reads the FPT
  (missing → `FPT_REQUIRED_MISSING`, broken → `FPT_INVALID`); `lazy` returns
  `LazyMemoValue` metadata without any FPT I/O until an explicit `load()`;
- field projection is validated case-insensitively, uses schema names in the
  caller's order, never parses unselected fields, and rejects unknown or
  duplicate names (`FIELD_PROJECTION_INVALID`) and selected unsupported types
  (`FIELD_TYPE_UNSUPPORTED`);
- strict decode failures raise `TEXT_DECODE_ERROR` (never a raw
  `UnicodeDecodeError`); record-stream inconsistency raises
  `DBF_RECORD_INVALID`; argument violations (`offset`/`limit`/policies) raise
  `ARGUMENT_INVALID`.

The exporter delegates its header parse to
`core.header.parse_header` and its Mazovia table to `core.codecs` —
there is exactly one header parser and one codepage table in the codebase.
`import dbfbridge` must register no codepage, create no files, and load no
CLI/reporting or heavy dependency.

Use `from dbfbridge import ...` in user documentation. `dbf_bridge` exposes the same
symbols for compatibility. CLI modules should delegate to these functions wherever
possible instead of creating a second behavior path.

## Data and reconstruction rules

- DBF is read with `dbfread` in streaming mode and written with `dbf`.
- The exporter always creates an internal JSONL representation; requested JSON, CSV,
  and XLSX outputs are streamed from it.
- JSONL is the preferred reconstruction format. CSV omits memo by default and spreadsheet
  formats are not intended to preserve every raw DBF byte.
- `<table>_schema.json` is the authority for field descriptors, header/codepage details,
  memo layout, and original checksums.
- JSON/JSONL carries reserved raw-record metadata used for exact layout restoration.
- `--deleted include` is required when deleted record order matters for raw identity.
- CDX tag expressions are not stored in DBF field metadata and cannot be reconstructed.
- `raw_*_match` proves byte identity; canonical checksums prove schema-aware value
  equivalence. Canonical equality does not imply byte equality.
- Atomic writes use a sibling `.partial` file, flush/fsync, and `os.replace`.
- `conversion_checksums.json` skips a table only after source, settings, schema, and every
  requested output are revalidated. It never deletes stale output files automatically.

## Dependencies

Runtime dependencies are installed by default:

- `dbfread` — DBF/FPT reading;
- `dbf` — DBF/FPT reconstruction and fixture generation;
- `orjson` — JSONL parsing/validation;
- `polars` — streaming CSV conversion;
- `xlsxwriter` — constant-memory XLSX writing;
- `openpyxl` — read-only XLSX reconstruction.

The empty `import` and `xlsx` extras are compatibility aliases. Development setup is:

```powershell
python -m venv .venv
.venv\Scripts\activate
python -m pip install -e ".[dev]"
```

## Validation before publishing

Run from a clean checkout; tests generate their own fixture files:

```powershell
pytest
ruff check src tests benchmarks examples
python -m build
twine check dist/*
python -m dbf_bridge.cli --help
python -m dbf_bridge.import_cli --help
python -m dbf_bridge.verifier --help
python -m dbf_bridge.quality --help
```

Before a release, also follow `PUBLISHING.md`. A GitHub release tag must exactly match
`v<project.version>`; `publish.yml` rejects mismatches before uploading immutable PyPI
artifacts.

Also inspect `git diff --check` and do not commit generated DBF/FPT/CDX, conversion
outputs, reports, `build/`, `dist/`, virtual environments, or user data.

## Code conventions

- Use `from __future__ import annotations` and complete type hints.
- Keep large-data paths streaming and bounded-memory.
- Preserve relative paths and filename casing.
- Never silently weaken checksum, atomic-write, encoding, or reconstruction guarantees.
- Add a regression test for every fixed defect.
- Keep README, examples, `--help`, changelog, and `pyproject.toml` consistent whenever
  behavior, options, defaults, entry points, or dependencies change.
- User-facing reports must include enough context to diagnose a failed table without the
  original Python traceback.
- Public API functions are silent by default, accept `str` and `PathLike`, return typed
  run results, and expose structured progress through `ProgressEvent` callbacks.
- Keep `src/dbfbridge/__init__.py`, `src/dbf_bridge/__init__.py`, their `__all__` lists,
  type markers, README API tables, and API tests synchronized.

## Known follow-up work

- index-aware CDX reconstruction, if a reliable source of tag definitions is added.
